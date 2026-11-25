"""Bounded private native tests, not production acceptance thresholds."""

from dataclasses import replace
import statistics
import subprocess
import threading
import time

import rti.connextdds as dds

from policy import Policy, TOPICS


class Streaming:
    def __init__(self, experiment, sequence):
        self.experiment = experiment
        self.sequence = sequence
        self.offers = []
        self.errors = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.produce)
        self.relay_thread = threading.Thread(target=self.relay)

    def produce(self):
        try:
            while not self.stop.is_set():
                for topic in TOPICS:
                    self.sequence += 1
                    identity = self.experiment.offer(topic, self.sequence)
                    self.offers.append((identity, topic, self.experiment.offered_at[identity]))
                self.stop.wait(0.02)
        except Exception as error:
            self.errors.append(error)
            self.stop.set()

    def relay(self):
        try:
            while not self.stop.is_set():
                self.experiment.relay_originals()
                self.stop.wait(0.005)
        except Exception as error:
            self.errors.append(error)
            self.stop.set()

    def __enter__(self):
        self.experiment.relay_in_background = True
        self.relay_thread.start()
        self.thread.start()
        return self

    def __exit__(self, kind, error, traceback):
        self.stop.set()
        self.thread.join(timeout=5)
        self.relay_thread.join(timeout=5)
        self.experiment.relay_in_background = False
        if self.thread.is_alive() or self.relay_thread.is_alive():
            raise RuntimeError("owned streaming producer/relay did not stop")
        if self.errors:
            raise RuntimeError("streaming producer failed") from self.errors[0]
        self.experiment.relay_originals()


def reconcile(experiment, policy):
    for family in experiment.probes["B"].readers:
        experiment.control.apply(family, policy.parameters())
    expected = tuple("'" + value + "'" for value in policy.parameters())
    assert all(experiment.control.read(family) == expected
               for family in experiment.probes["B"].readers)
    experiment.policy = replace(policy, revision=experiment.policy.revision + 1)
    experiment.degraded = False
    experiment.degraded_error = ""
    experiment.publish_applied()


def transitions(experiment):
    rows = []
    experiment.mission("ORBIT")
    assert experiment.request("Auto", [])["accepted"]
    with Streaming(experiment, 20000) as producer:
        experiment.settle(0.6)
        operations = (
            ("narrow", lambda: experiment.request("User", ["ContactReport"])),
            ("different_team", lambda: experiment.commit(experiment.policy.change_team("TEAM_B"))),
            ("restore_team", lambda: experiment.commit(experiment.policy.change_team("TEAM_A"))),
            ("terminal_mission", lambda: experiment.mission("CANCELLED")),
            ("empty_user", lambda: experiment.request("User", [])),
            ("widen", lambda: (experiment.mission("ORBIT"), experiment.request("Auto", []))),
        )
        for label, operation in operations:
            before = time.monotonic()
            before_epoch = time.time()
            result = operation()
            if isinstance(result, dict):
                assert result["accepted"], result
            committed = time.monotonic()
            committed_epoch = time.time()
            experiment.settle(0.8)
            end = time.monotonic()
            offers = [row for row in producer.offers if committed + 0.35 <= row[2] <= end]
            assert offers, label
            expected = [identity for identity, topic, _ in offers
                        if topic in experiment.policy.selected() and
                        (topic == "PlatformStatus" or experiment.policy.team == "TEAM_A")]
            required = [identity for identity, topic, _ in offers
                        if identity in expected and TOPICS[topic] == "State"]
            experiment.spin(lambda: all(identity in experiment.received for identity in required),
                            label + " reliable selected stream")
            received = [identity for identity, _, _ in offers if identity in experiment.received]
            assert set(received) <= set(expected), (label, "settled exclusion failed")
            for topic in {topic for identity, topic, _ in offers if identity in expected}:
                assert any(identity in experiment.received for identity, name, _ in offers
                           if name == topic), (label, "selected stream absent", topic)
            assert all(experiment.received[identity] == 1 for identity in received)
            excluded = set(TOPICS) - set(experiment.policy.selected())
            if experiment.policy.team != "TEAM_A":
                excluded |= {"ContactReport", "PlatformMissionStatus"}
            late = [identity for identity, topic, _ in producer.offers
                    if topic in excluded and committed <= experiment.received_at.get(identity, -1) <= end]
            rows.append(dict(label=label, local_operation_ms=(committed - before) * 1000,
                operation_start_epoch=before_epoch, operation_end_epoch=committed_epoch,
                settled_end_epoch=time.time(),
                in_update_offers=[identity for identity, _, offered in producer.offers
                                  if before <= offered <= committed],
                settled_after_ms=350, offered=[identity for identity, _, _ in offers],
                expected=expected, received=received,
                transition_excluded_deliveries=late,
                settled_reliable_missing=[]))
        experiment.settle(0.3)
    delays = [(experiment.received_at[identity] - offered) * 1000
              for identity, _, offered in producer.offers if identity in experiment.received_at]
    return dict(offers=len(producer.offers), phases=rows,
                source_relay_continues_during_receiver_updates=True,
                receive_latency_ms=dict(median=statistics.median(delays), max=max(delays)),
                observation_only="350 ms settling is tested, not a worst-case propagation guarantee")


def outage(experiment):
    experiment.mission("ORBIT")
    assert experiment.request("Auto", [])["accepted"]
    rule = ["OUTPUT", "-p", "udp", "--dport", "35150:35399",
            "-m", "comment", "--comment", "owned-topic-subscriptions", "-j", "DROP"]
    subprocess.run(["iptables", "-I", *rule], check=True)
    before = experiment.policy.revision
    try:
        queued = experiment.send_request("User", [], request_id="during-outage", revision=before)
        experiment.mission("CANCELLED", wait_remote=False)
        experiment.commit(experiment.policy.select(dict(
            session=experiment.policy.session, revision=experiment.policy.revision,
            mode="User", topics=["ContactReport"])))
        experiment.publish_applied()
        experiment.settle(2)
        assert queued not in experiment.results
    finally:
        restored = time.monotonic()
        subprocess.run(["iptables", "-D", *rule], check=True)
    experiment.spin(lambda: queued in experiment.results, "outage reliable request repair")
    assert not experiment.results[queued]["accepted"]
    assert "revision" in experiment.results[queued]["error"]
    assert experiment.policy.mode == "User" and experiment.policy.selected() == ["ContactReport"]
    repaired = time.monotonic()
    result = experiment.request("User", ["ContactReport"])
    assert result["accepted"]
    experiment.measure("outage_latest_selection")
    return dict(wan_only_bidirectional_drop_seconds=2,
                stale_request_repair_ms=(repaired - restored) * 1000,
                latest_local_selection_preserved=True, reliable_request_repaired=True,
                limitation="local operator changes during outage; no numerical recovery budget")


def concurrency(experiment):
    results = []
    for index in range(4):
        revision = experiment.policy.revision
        first = experiment.send_request("User", ["ContactReport"],
            request_id=f"race-{index}-first", revision=revision)
        second = experiment.send_request("User", [], request_id=f"race-{index}-second", revision=revision)
        experiment.spin(lambda: first in experiment.results and second in experiment.results,
                        "competing manual revisions")
        outcomes = [experiment.results[first], experiment.results[second]]
        assert sum(value["accepted"] for value in outcomes) == 1, outcomes
        assert experiment.policy.revision == revision + 1
        winner = next(value for value in outcomes if value["accepted"])
        assert winner["user_topics"] == list(experiment.policy.user_topics)
        results.append(dict(kind="two_manual_requests", accepted=1, rejected=1))
    revision = experiment.policy.revision
    stale = experiment.send_request("User", [], request_id="race-mission", revision=revision)
    experiment.commit(experiment.policy.transition("HOLDING"))
    experiment.spin(lambda: stale in experiment.results, "mission versus pending manual request")
    assert not experiment.results[stale]["accepted"]
    revision = experiment.policy.revision
    stale = experiment.send_request("User", [], request_id="race-team", revision=revision)
    experiment.commit(experiment.policy.change_team("TEAM_B"))
    experiment.spin(lambda: stale in experiment.results, "team versus pending manual request")
    assert not experiment.results[stale]["accepted"]
    experiment.commit(experiment.policy.change_team("TEAM_A"))
    return dict(manual_races=results, mission_race_rejected=True, team_race_rejected=True,
                limitation="serialized receiver fixture; not production multi-user authorization")


def failures(experiment):
    assert experiment.request("User", ["ContactReport"])["accepted"]
    results = []
    for family in experiment.probes["B"].readers:
        for fault in ("apply", "rollback"):
            previous = experiment.policy
            experiment.inject_family = family
            experiment.inject = fault
            result = experiment.request("User", [])
            assert not result["accepted"], (family, fault)
            assert experiment.policy == previous
            expected = tuple("'" + value + "'" for value in previous.parameters())
            if fault == "apply":
                assert not experiment.degraded
                assert all(experiment.control.read(name) == expected
                           for name in experiment.probes["B"].readers)
            else:
                assert experiment.degraded and not result["confirmed"]
                assert experiment.control.read(family) != expected
                experiment.settle(1.1)
                assert not experiment.latest_applied["confirmed"]
                assert experiment.latest_applied["error"]
                experiment.inject = None
                reconcile(experiment, previous)
            results.append(dict(family=family, fault=fault, rejection_confirmed=True,
                                rollback_restored=fault == "apply"))
            experiment.inject = None
    experiment.inject_family = "State"
    return dict(cases=results, reconciliation="test-only all-family restore plus native readback",
                limitation="injected failure after mutation, not spontaneous SDK failure")


def capacity(experiment, make_frame):
    assert experiment.request("User", list(TOPICS))["accepted"]
    checks = []
    for typename, field, maximum, value in (
            ("Selection", "topics", 3, "ContactReport"),
            ("Selection", "request_id", 64, "x"),
            ("Report", "values", 16, 1.25)):
        sample = dds.DynamicData(experiment.provider.type(typename))
        accepted = [value] * maximum if field != "request_id" else value * maximum
        sample[field] = accepted
        assert sample[field] == accepted
        overflow = [value] * (maximum + 1) if field != "request_id" else value * (maximum + 1)
        try:
            sample[field] = overflow
        except (dds.Error, dds.IllegalOperationError, dds.InvalidArgumentError, ValueError, IndexError):
            checks.append(dict(type=typename, field=field, maximum=maximum, overflow_rejected=True))
        else:
            raise AssertionError((typename, field, "overflow accepted"))
    inventory = dds.DynamicData(experiment.provider.type("Inventory"))
    entries = []
    for index in range(8):
        entry = dds.DynamicData(experiment.provider.type("TopicEntry"))
        entry["topic"] = "x" * 64
        entry["type_name"] = "Report"
        entries.append(entry)
    inventory["entries"] = entries
    envelope = make_frame(experiment.provider, "A", "Inventory", "inventory", inventory, "bounds")
    assert envelope.get_complex("payload.inventory") == inventory
    try:
        inventory["entries"] = entries + entries[:1]
    except (dds.Error, dds.IllegalOperationError, dds.InvalidArgumentError, ValueError, IndexError):
        checks.append(dict(type="Inventory", field="entries", maximum=8, overflow_rejected=True))
    else:
        raise AssertionError("inventory overflow accepted")
    experiment.measure("maximum_supported_selection")
    return dict(bounds=checks, native_filter_parameters_read_back=True,
                limitation="three prepared topics only; large generalized inventories remain unqualified")


def lifecycle(experiment, make_frame):
    experiment.mission("CANCELLED")
    assert experiment.request("User", ["ContactReport"])["accepted"]
    experiment.settle()
    report = dds.DynamicData(experiment.provider.type("Report"))
    report["sequence"] = 90000
    report["values"] = [1.0, 2.0, 3.0]
    identity = "lifecycle-contact"
    experiment.expected[identity] = dds.DynamicData(report)
    value = make_frame(experiment.provider, "A", "ContactReport", "report", report, identity, "TEAM_A")
    writer = experiment.probes["A"].writers["State"]
    writer.write(value)
    experiment.spin(lambda: identity in experiment.received, "selected contact materialization")
    handle = writer.register_instance(value)
    marker = len(experiment.lifecycle)
    writer.dispose_instance(handle)
    experiment.spin(lambda: ("State", dds.InstanceState.NOT_ALIVE_DISPOSED) in experiment.lifecycle[marker:],
                    "selected contact disposal")
    writer.write(value)
    experiment.spin(lambda: experiment.received[identity] == 2, "selected contact revival")
    marker = len(experiment.lifecycle)
    writer.unregister_instance(handle)
    experiment.spin(lambda: ("State", dds.InstanceState.NOT_ALIVE_NO_WRITERS) in experiment.lifecycle[marker:],
                    "selected contact unregister")
    writer.write(value)
    experiment.spin(lambda: experiment.received[identity] == 3, "contact re-registration")
    return dict(dispose=True, revive=True, unregister=True, reregister=True,
                scope="typed WAN frame lifecycle; not original unkeyed Report producer lifecycle")


def restart_receiver(experiment, processes, start, suffix, selected=False):
    import runner as rig
    for reader in experiment.probes["B"].readers.values():
        reader.take()
    rig.stop(processes["B"])
    experiment.control.discard_pending()
    previous_session = experiment.policy.session
    policy = Policy(session=previous_session + 1)
    if selected:
        policy = policy.select(dict(session=policy.session, revision=policy.revision,
                                    mode="User", topics=["ContactReport"]))
    start("B", suffix, initial_policy=policy)
    rig.wait(lambda: experiment.probes["B"].matched(), "new WAN receiver LAN matches")
    experiment.policy = policy
    experiment.seen_requests.clear()
    experiment.cached_inventory = None
    experiment.latest_applied = None
    expected = tuple("'" + value + "'" for value in experiment.policy.parameters())
    assert all(experiment.control.read(family) == expected
               for family in experiment.probes["B"].readers)
    request_id = f"restart-{policy.session}"
    experiment.publish_applied(request_id=request_id, ack=True)
    experiment.spin(lambda: request_id in experiment.results and
                    experiment.latest_applied is not None and
                    experiment.latest_applied["session"] == policy.session,
                    "fresh receiver state and all-family output readiness")
    return previous_session


def transient_state(experiment, processes, start, make_frame):
    experiment.mission("CANCELLED")
    assert experiment.request("User", [])["accepted"]
    experiment.settle()
    experiment.refresh_enabled = False
    identities = ("retained-contact-one", "retained-contact-two", "retained-other-team")
    for ordinal, identity in enumerate(identities):
        for update in (1, 2):
            report = dds.DynamicData(experiment.provider.type("Report"))
            report["sequence"] = 91000 + 10 * ordinal + update
            report["values"] = [ordinal + 0.25, update + 0.5]
            experiment.expected[identity] = dds.DynamicData(report)
            experiment.probes["A"].writers["State"].write(make_frame(
                experiment.provider, "A", "ContactReport", "report", report, identity,
                "TEAM_B" if ordinal == 2 else "TEAM_A"))
            experiment.settle(0.15)
    assert not any(identity in experiment.received for identity in identities)
    assert experiment.request("User", ["ContactReport"])["accepted"]
    experiment.settle(1.5)
    widening = {identity: experiment.received.get(identity, 0) for identity in identities}
    assert not widening[identities[2]], "team filter bypass during widening"
    assert experiment.request("User", [])["accepted"]
    experiment.settle(0.3)
    before = dict(experiment.received)
    assert experiment.request("User", ["ContactReport"])["accepted"]
    experiment.settle(1.5)
    rewidening = {identity: experiment.received.get(identity, 0) - before.get(identity, 0)
                 for identity in identities}
    inventory_revision = experiment.cached_inventory["revision"]
    replay_started = time.monotonic()
    restart_receiver(experiment, processes, start, "-stress-durable")
    experiment.spin(lambda: experiment.cached_inventory is not None,
                    "transient-local WAN inventory replay without refresh")
    replay_ms = (time.monotonic() - replay_started) * 1000
    assert experiment.cached_inventory["revision"] == inventory_revision
    assert experiment.policy.mode == "Auto" and not experiment.policy.selected()
    before = dict(experiment.received)
    assert experiment.request("User", ["ContactReport"])["accepted"]
    experiment.settle(1.5)
    after_restart_widening = {
        identity: experiment.received.get(identity, 0) - before.get(identity, 0)
        for identity in identities}
    before = dict(experiment.received)
    restart_receiver(experiment, processes, start, "-stress-selected", selected=True)
    experiment.spin(lambda: all(experiment.received.get(identity, 0) > before.get(identity, 0)
                               for identity in identities[:2]),
                    "new WAN reader latest value per selected key")
    experiment.settle(0.3)
    selected_replay = {identity: experiment.received.get(identity, 0) - before.get(identity, 0)
                       for identity in identities}
    assert list(selected_replay.values()) == [1, 1, 0], selected_replay
    import runner as rig
    rig.stop(processes["A"])
    start("A", "-stress-cache-reset")
    rig.wait(lambda: experiment.probes["A"].matched(), "empty restarted writer LAN matches")
    before = dict(experiment.received)
    restart_receiver(experiment, processes, start, "-stress-empty-cache", selected=True)
    experiment.settle(1.5)
    assert experiment.cached_inventory is None, "writer cache survived source restart"
    assert all(experiment.received.get(identity, 0) == before.get(identity, 0)
               for identity in identities), "pre-restart State replayed from new writer"
    experiment.refresh_enabled = True
    experiment.publish_inventory(force=True)
    experiment.publish_applied()
    experiment.spin(lambda: experiment.cached_inventory is not None and
                    experiment.latest_applied is not None, "source republish after cache reset")
    return dict(inventory_replayed_without_refresh=True,
                receiver_restart_replay_ms=replay_ms,
                retained_keys=list(identities), updates_per_key=2,
                in_place_widening_deliveries=widening,
                narrow_then_widen_deliveries=rewidening,
                post_restart_widening_deliveries=after_restart_widening,
                new_selected_wan_reader_deliveries=selected_replay,
                source_writer_restart_clears_history=True,
                limitation="WAN endpoints transient-local; LAN endpoints remain volatile")


def restart_and_late_join(experiment, processes, start):
    # The fixture authority remains alive; these are real Routing Service restarts.
    import runner as rig
    rig.stop(processes["A"])
    start("A", "-stress-source")
    rig.wait(lambda: experiment.probes["A"].matched(), "restarted source LAN matches")
    early = experiment.offer("ContactReport", 80000)
    experiment.settle(0.3)
    early_received = early in experiment.received
    target_inventory = experiment.inventory_revision + 1
    experiment.inventory_revision = target_inventory
    experiment.publish_inventory(force=True)
    readiness_started = time.monotonic()
    experiment.spin(lambda: experiment.cached_inventory["revision"] >= target_inventory,
                    "fresh source WAN inventory after restart")
    readiness_ms = (time.monotonic() - readiness_started) * 1000
    experiment.measure("source_restart_preserves_receiver_selection")
    experiment.refresh_enabled = False
    experiment.settle(0.3)
    previous_session = restart_receiver(experiment, processes, start, "-stress-late")
    if experiment.transient_local:
        experiment.spin(lambda: experiment.cached_inventory is not None,
                        "transient-local late WAN inventory replay")
        assert experiment.cached_inventory["revision"] == target_inventory
    else:
        experiment.settle(0.6)
        assert experiment.cached_inventory is None, "unexpected volatile WAN replay"
    refreshed = time.monotonic()
    experiment.publish_inventory(force=True)
    experiment.publish_applied()
    experiment.refresh_enabled = True
    experiment.spin(lambda: experiment.cached_inventory is not None and
                    experiment.latest_applied is not None, "late WAN metadata refresh")
    recovered = time.monotonic()
    assert not experiment.request("User", ["ContactReport"], session=previous_session)["accepted"]
    experiment.measure("late_wan_auto_init")
    assert experiment.request("User", ["ContactReport"])["accepted"]
    experiment.measure("late_wan_user_selection")
    return dict(source_restarted=True, receiver_wan_participant_recreated=True,
                early_report_after_lan_match_received=early_received,
                fresh_wan_readiness_ms=readiness_ms,
                volatile_inventory_requires_refresh=not experiment.transient_local,
                old_session_rejected=True,
                metadata_refresh_ms=(recovered - refreshed) * 1000,
                limitation="Python authority explicitly resets receiver session; not persistent override recovery")


def run_extended(experiment, processes, start, make_frame, record):
    saved = experiment.policy
    result = dict(result="RUNNING")
    stages = [
            ("continuous", lambda: transitions(experiment)),
            ("outage", lambda: outage(experiment)),
            ("concurrency", lambda: concurrency(experiment)),
            ("family_failures", lambda: failures(experiment)),
            ("capacity", lambda: capacity(experiment, make_frame)),
            ("lifecycle", lambda: lifecycle(experiment, make_frame)),
            ("restart_late_join", lambda: restart_and_late_join(experiment, processes, start))]
    if experiment.transient_local:
        stages.insert(0, ("transient_state",
                         lambda: transient_state(experiment, processes, start, make_frame)))
    for name, execute in stages:
        try:
            result[name] = execute()
        except Exception as error:
            result.update(result="FAIL", failed_stage=name, error=str(error))
            record(result)
            raise
        record(result)
        print("PASS: extended " + name, flush=True)
    experiment.commit(replace(saved, session=experiment.policy.session,
                              revision=experiment.policy.revision + 1))
    experiment.publish_applied()
    experiment.measure("restore_after_extended_tests")
    result["result"] = "NATIVE_STAGES_PASS"
    record(result)
    return result
