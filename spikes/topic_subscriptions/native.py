#!/usr/bin/env python3
"""Real RS transport with a deliberately separate, LAN-only fixture controller."""

from contextlib import ExitStack
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import rti.connextdds as dds

from policy import ACTIVE, EXPRESSION, Policy, TOPICS


HERE = Path("/workspace/spikes/topic_subscriptions")
WORK = Path("/work")
sys.path.insert(0, "/workspace/spikes/routing_service_lbed")
import runner as rig


rig.TYPE_FILE = HERE / "types.xml"
rig.TYPE = "SubscriptionFrame"
rig.REPORT = WORK
rig.CAPTURE = WORK


def remove_unused_lbed(root):
    library = root.find("qos_library")
    for profile in list(library):
        if profile.get("name") in {"lbed", "classic_lbed"}:
            library.remove(profile)
    root.append(ET.parse(HERE / "qos.xml").getroot().find("qos_library"))


def write_json(path, value):
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def decoded(value):
    return json.loads(value.to_json())


def frame(provider, node, topic, branch, value, instance, team=""):
    result = dds.DynamicData(provider.type(rig.TYPE))
    for name, content in dict(source=node, destination="B" if node == "A" else "A",
                              original_topic=topic, instance_id=instance, team=team).items():
        result[name] = content
    result.set_complex("payload." + branch, value)
    assert result.get_complex("payload." + branch) == value
    return result


def service(path, node, transient_local=False, initial_policy=None):
    rig.service(path, node, "sedp")
    root = ET.parse(path)
    remove_unused_lbed(root.getroot())
    root.find(".//participant[@name='wan']/domain_participant_qos").set(
        "base_name", "TopicSubscriptions::wan_participant")
    if transient_local:
        for direction, kind, qos in (("tx", "output", "datawriter_qos"),
                                     ("rx", "input", "datareader_qos")):
            root.find(f".//topic_route[@name='{direction}State']/{kind}/{qos}").set(
                "base_name", "TopicSubscriptions::state")
    if node == "B":
        routing = root.find("routing_service")
        library = rig.child(root.getroot(), "plugin_library", name="Fixture")
        plugin = rig.child(library, "processor_plugin", name="Selection")
        rig.child(plugin, "dll", str(WORK / "build/libsubscription_processor.so"))
        rig.child(plugin, "create_function", "SubscriptionPlugin_create_processor_plugin")
        for family in rig.FAMILIES:
            port = routing.find(f".//topic_route[@name='rx{family}']/input")
            element = rig.child(port, "content_filter")
            rig.child(element, "expression", EXPRESSION)
            parameters = rig.child(element, "expression_parameters")
            for value in (initial_policy or Policy()).parameters():
                rig.child(parameters, "element", "'" + value + "'")
            route = routing.find(f".//topic_route[@name='rx{family}']")
            duration = rig.child(route, "periodic_action")
            rig.child(duration, "sec", 0)
            rig.child(duration, "nanosec", 20000000)
            route.remove(duration)
            route.insert(1, duration)
            processor = rig.child(route, "processor", plugin_name="Fixture::Selection")
            entry = rig.child(rig.child(rig.child(processor, "property"), "value"), "element")
            rig.child(entry, "name", "fixture.control_path")
            rig.child(entry, "value", str(WORK / f"control-{family}"))
            route.remove(processor)
            route.insert(2, processor)
    assert root.find(".//administration") is None
    rig.save(root.getroot(), path)


class NativeControl:
    def __init__(self):
        self.sequence = 0

    def call(self, family, parameters=None):
        self.sequence += 1
        identity = str(self.sequence)
        path = WORK / f"control-{family}"
        body = [identity, "get" if parameters is None else "set"]
        if parameters is not None:
            body.extend("'" + value + "'" for value in parameters)
        temporary = path.with_suffix(".new-request")
        temporary.write_text("\n".join(body) + "\n")
        temporary.replace(path.with_suffix(".request"))
        reply = path.with_suffix(".reply")
        content = []

        def ready():
            nonlocal content
            if not reply.exists():
                return False
            content = reply.read_text().splitlines()
            return bool(content) and content[0] == identity
        rig.wait(ready, "direct native filter callback/readback", timeout=5)
        assert len(content) >= 4 and content[1] == "0", content
        assert content[3] == EXPRESSION, content
        return tuple(content[4:])

    def read(self, family):
        return self.call(family)

    def apply(self, family, parameters):
        assert self.call(family, parameters) == tuple("'" + value + "'" for value in parameters)

    def discard_pending(self):
        for family in rig.FAMILIES:
            for suffix in (".request", ".reply"):
                (WORK / f"control-{family}{suffix}").unlink(missing_ok=True)


class Experiment:
    def __init__(self, provider, control, probes, cleanup):
        self.provider, self.control, self.probes = provider, control, probes
        self.policy = Policy()
        self.inventory_revision = 0
        self.inventory = None
        self.cached_inventory = None
        self.latest_applied = None
        self.results = {}
        self.seen_requests = {}
        self.measurements = []
        self.checks = []
        self.expected = {}
        self.received = {}
        self.inject = None
        self.degraded = False
        self.degraded_error = ""
        self.applied_sequence = 0
        self.applied_rejections = 0
        self.received_at = {}
        self.offered_at = {}
        self.lifecycle = []
        self.mission_sequence = 0
        self.processed_mission = 0
        self.relay_in_background = False
        self.requests = 0
        self.cache_rejections = 0
        self.producer = dds.DomainParticipant(
            110, provider.participant_qos_from_profile("LBEDSpike::participant"))
        cleanup.callback(self.producer.close)
        self.publisher = dds.Publisher(self.producer, provider.publisher_qos_from_profile(
            "LBEDSpike::partition_B"))
        self.original_subscriber = dds.Subscriber(probes["A"].participant,
            provider.subscriber_qos_from_profile("LBEDSpike::partition_B"))
        self.original_topics, self.original_readers, self.original_writers = {}, {}, {}
        for name in TOPICS:
            topic = dds.DynamicData.Topic(self.producer, "Original_" + name, provider.type("Report"))
            self.original_topics[name] = topic
            self.original_writers[name] = dds.DynamicData.DataWriter(
                self.publisher, topic, provider.datawriter_qos_from_profile("LBEDSpike::event"))
            receiver = dds.DynamicData.Topic(probes["A"].participant, "Original_" + name,
                                             provider.type("Report"))
            self.original_readers[name] = dds.DynamicData.DataReader(
                self.original_subscriber, receiver,
                provider.datareader_qos_from_profile("LBEDSpike::event"))
        self.mission_topic = dds.DynamicData.Topic(probes["B"].participant, "LocalMission",
                                                  provider.type("LocalMission"))
        self.mission_writer = dds.DynamicData.DataWriter(
            dds.Publisher(probes["B"].participant, provider.publisher_qos_from_profile(
                "LBEDSpike::partition_B")),
            self.mission_topic, provider.datawriter_qos_from_profile("LBEDSpike::event"))
        self.mission_reader = dds.DynamicData.DataReader(
            probes["B"].subscriber, self.mission_topic,
            provider.datareader_qos_from_profile("LBEDSpike::event"))
        self.refresh_enabled = True
        self.last_inventory = 0
        self.last_applied = 0
        rig.wait(lambda: all(reader.subscription_matched_status.current_count == 1
                            for reader in self.original_readers.values()),
                 "original LAN application matches")

    def inventory_value(self):
        value = dds.DynamicData(self.provider.type("Inventory"))
        entries = []
        for topic, family in TOPICS.items():
            entry = dds.DynamicData(self.provider.type("TopicEntry"))
            for key, content in dict(topic=topic, type_name="Report", family=family,
                    matched=self.original_readers[topic].subscription_matched_status.current_count > 0).items():
                entry[key] = content
            entries.append(entry)
        value["entries"] = entries
        value["session"] = 1
        value["revision"] = self.inventory_revision
        return value

    def publish_inventory(self, force=False):
        value = self.inventory_value()
        current = decoded(value)["entries"]
        if self.inventory is None or self.inventory["entries"] != current:
            self.inventory_revision += 1
            value["revision"] = self.inventory_revision
            self.inventory = decoded(value)
            force = True
        if force or (self.refresh_enabled and time.monotonic() - self.last_inventory > 1):
            self.probes["A"].writers["State"].write(
                frame(self.provider, "A", "Inventory", "inventory", value, "inventory"))
            self.last_inventory = time.monotonic()

    def publish_applied(self, request_id="", accepted=True, error="", ack=False):
        self.applied_sequence += 1
        value = dds.DynamicData(self.provider.type("Applied"))
        fields = dict(session=self.policy.session, revision=self.policy.revision,
            snapshot_sequence=self.applied_sequence,
            mode=self.policy.mode, mission=self.policy.mission, team=self.policy.team,
            topics=self.policy.selected(), request_id=request_id, accepted=accepted,
            user_topics=list(self.policy.user_topics),
            protected_topics=["PlatformStatus"] if self.policy.mission in ACTIVE else [],
            confirmed=not self.degraded, error=error or self.degraded_error)
        for key, content in fields.items():
            value[key] = content
        self.probes["B"].writers["State"].write(
            frame(self.provider, "B", "Applied", "applied", value, "applied"))
        heartbeat = dds.DynamicData(self.provider.type("Report"))
        heartbeat["timestamp"] = time.time_ns()
        self.probes["B"].writers["Periodic"].write(
            frame(self.provider, "B", "Heartbeat", "report", heartbeat, "heartbeat"))
        if ack:
            self.probes["B"].writers["Event"].write(frame(
                self.provider, "B", "SubscriptionAck", "applied", value, request_id))
        self.last_applied = time.monotonic()
        return fields

    def commit(self, candidate):
        if self.degraded:
            raise RuntimeError("router degraded: reconciliation required")
        old = {family: self.control.read(family) for family in rig.FAMILIES}
        try:
            for family in rig.FAMILIES:
                self.control.apply(family, candidate.parameters())
                if family == getattr(self, "inject_family", "State") and self.inject:
                    raise RuntimeError("injected failure after native State mutation")
        except Exception as error:
            failures = []
            for family in rig.FAMILIES:
                try:
                    if family == getattr(self, "inject_family", "State") and self.inject == "rollback":
                        raise RuntimeError(f"injected {family} rollback refusal")
                    self.control.apply(family, tuple(value[1:-1] for value in old[family]))
                except Exception as rollback:
                    failures.append(f"{family}: {rollback}")
            if failures:
                self.degraded = True
                self.degraded_error = f"{error}; ROLLBACK FAILED: {'; '.join(failures)}"
                raise RuntimeError(self.degraded_error) from error
            assert all(self.control.read(family) == old[family] for family in rig.FAMILIES)
            raise
        self.policy = candidate

    def relay_originals(self):
        for topic, reader in self.original_readers.items():
            for report, info in reader.take():
                if info.valid:
                    identity = f"{report['sequence']}:{topic}"
                    self.expected[identity] = dds.DynamicData(report)
                    self.probes["A"].writers[TOPICS[topic]].write(frame(
                        self.provider, "A", topic, "report", report, identity,
                        "" if topic == "PlatformStatus" else "TEAM_A"))

    def tick(self):
        self.publish_inventory()
        if self.refresh_enabled and time.monotonic() - self.last_applied > 1:
            self.publish_applied()
        for data, info in self.mission_reader.take():
            if info.valid:
                self.commit(self.policy.transition(data["phase"]))
                self.processed_mission = data["sequence"]
                self.publish_applied()
        if not self.relay_in_background:
            self.relay_originals()
        for node, probe in self.probes.items():
            for family, reader in probe.readers.items():
                for value, info in reader.take():
                    if not info.valid:
                        if node == "B":
                            self.lifecycle.append((family, info.state.instance_state))
                        continue
                    topic = value["original_topic"]
                    if node == "B" and topic == "Inventory":
                        inventory = decoded(value.get_complex("payload.inventory"))
                        if self.cached_inventory is None or inventory["revision"] >= (
                                self.cached_inventory["revision"]):
                            self.cached_inventory = inventory
                        else:
                            self.cache_rejections += 1
                    elif node == "B" and topic == "SubscriptionRequest" and family == "Event":
                        request = decoded(value.get_complex("payload.selection"))
                        request_id = request["request_id"]
                        if request_id in self.seen_requests:
                            previous = self.seen_requests[request_id]
                            if previous["request"] != request:
                                self.publish_applied(request_id, False,
                                                     "request ID reused with different contents", True)
                            else:
                                self.publish_applied(request_id, **previous["result"], ack=True)
                            continue
                        try:
                            self.commit(self.policy.select(request))
                            result = dict(accepted=True, error="")
                        except (ValueError, RuntimeError) as error:
                            result = dict(accepted=False, error=str(error))
                        if len(self.seen_requests) == 16:
                            del self.seen_requests[next(iter(self.seen_requests))]
                        self.seen_requests[request_id] = dict(request=request, result=result)
                        self.publish_applied(request_id, **result, ack=True)
                    elif node == "A" and topic in {"Applied", "SubscriptionAck"}:
                        applied = decoded(value.get_complex("payload.applied"))
                        if topic == "SubscriptionAck":
                            self.results[applied["request_id"]] = applied
                        if self.latest_applied is None or (
                                applied["session"], applied["snapshot_sequence"]) > (
                                self.latest_applied["session"], self.latest_applied["snapshot_sequence"]):
                            self.latest_applied = applied
                        else:
                            self.applied_rejections += 1
                    elif node == "B" and topic in TOPICS:
                        identity = value["instance_id"]
                        assert value.get_complex("payload.report") == self.expected[identity], identity
                        self.received[identity] = self.received.get(identity, 0) + 1
                        self.received_at.setdefault(identity, time.monotonic())

    def spin(self, predicate, label, timeout=20):
        def ready():
            self.tick()
            return predicate()
        rig.wait(ready, label, timeout=timeout)

    def settle(self, seconds=1):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.tick()
            time.sleep(0.01)

    def request(self, mode, topics, session=None, revision=None, request_id=None):
        request_id = self.send_request(mode, topics, session, revision, request_id)
        self.spin(lambda: request_id in self.results, "reliable subscription result")
        return self.results[request_id]

    def send_request(self, mode, topics, session=None, revision=None, request_id=None):
        self.requests += 1
        request_id = request_id or f"r{self.requests}"
        request = dict(request_id=request_id, session=self.policy.session if session is None else session,
            revision=self.policy.revision if revision is None else revision, mode=mode, topics=topics)
        value = dds.DynamicData(self.provider.type("Selection"))
        for key, content in request.items():
            value[key] = content
        self.results.pop(request_id, None)
        self.probes["A"].writers["Event"].write(frame(
            self.provider, "A", "SubscriptionRequest", "selection", value, request_id))
        return request_id

    def mission(self, phase, wait_remote=True):
        self.mission_sequence += 1
        sequence = self.mission_sequence
        value = dds.DynamicData(self.provider.type("LocalMission"))
        value["phase"] = phase
        value["sequence"] = sequence
        self.mission_writer.write(value)
        self.spin(lambda: self.processed_mission == sequence and self.policy.mission == phase and
                  (not wait_remote or (self.latest_applied["mission"] == phase and
                                       self.latest_applied["revision"] >= self.policy.revision)),
                  "local DDS mission transition")

    def offer(self, topic, sequence):
        index = list(TOPICS).index(topic) + 1
        report = dds.DynamicData(self.provider.type("Report"))
        report["sequence"] = sequence
        report["timestamp"] = time.time_ns()
        report["values"] = [index + 0.25, sequence * 0.5, -index]
        identity = f"{sequence}:{topic}"
        self.offered_at[identity] = time.monotonic()
        self.original_writers[topic].write(report)
        return identity

    def measure(self, label):
        self.settle()
        self.refresh_enabled = False
        self.settle(0.25)
        start = time.time()
        offered = {}
        for index, topic in enumerate(TOPICS, 1):
            sequence = 10000 + 10 * len(self.measurements) + index
            identity = self.offer(topic, sequence)
            offered[identity] = topic
        self.settle(1.5)
        selected = [identity for identity, topic in offered.items()
                    if topic in self.policy.selected() and (
                        topic == "PlatformStatus" or self.policy.team == "TEAM_A")]
        actual = [identity for identity in offered if identity in self.received]
        assert sorted(actual) == sorted(selected), (label, actual, selected)
        assert all(self.received[identity] == 1 for identity in actual)
        assert set(offered) <= self.expected.keys(), (label, "missing original LAN ingestion")
        self.measurements.append(dict(label=label, start=start, end=time.time(),
            offered=offered, expected=selected, received=actual,
            mode=self.policy.mode, mission=self.policy.mission, team=self.policy.team))
        write_json(WORK / "progress.json", self.measurements)
        print(f"PASS: {label}: {len(selected)}/{len(offered)} original reports", flush=True)
        self.refresh_enabled = True

    def snapshot(self):
        return dict(applied=self.latest_applied, inventory=self.cached_inventory)


def main(extended=False, transient_local=False):
    assert {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}
    os.environ["LD_LIBRARY_PATH"] = ":".join(str(rig.SDK / suffix) for suffix in (
        "resource/app/lib/x64Linux4gcc8.5.0", "lib/x64Linux4gcc8.5.0"))
    subprocess.run(["cmake", "-S", str(HERE), "-B", str(WORK / "build"),
                    "-DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0"], check=True)
    subprocess.run(["cmake", "--build", str(WORK / "build"), "-j2"], check=True)
    commands = WORK / "commands"
    commands.mkdir()
    commands.chmod(0o777)
    provider_path = WORK / "provider.xml"
    document = rig.document()
    remove_unused_lbed(document)
    rig.save(document, provider_path)
    provider = dds.QosProvider(str(provider_path))
    for kind in ("datawriter", "datareader"):
        actual = getattr(provider, kind + "_qos_from_profile")("TopicSubscriptions::state")
        assert actual.durability.kind == dds.DurabilityKind.TRANSIENT_LOCAL
    assert provider.datawriter_qos_from_profile("TopicSubscriptions::state").history.depth == 1
    os.chdir(WORK)
    with ExitStack() as cleanup:
        capture = WORK / "wan.pcapng"
        capture.touch()
        capture.chmod(0o666)
        log = cleanup.enter_context((WORK / "capture.log").open("w"))
        recorder = subprocess.Popen(["dumpcap", "-i", "lo", "-f",
            "udp dst portrange 35150-35399", "-a", "duration:240", "-w", str(capture)],
            stdout=log, stderr=subprocess.STDOUT)
        cleanup.callback(rig.stop, recorder)
        rig.wait(lambda: capture.stat().st_size > 0, "capture ready", timeout=5)
        processes = {}
        starts = {node: 0 for node in rig.DOMAINS}

        def start(node, suffix="", initial_policy=None):
            path = WORK / f"{node}.xml"
            service(path, node, transient_local, initial_policy)
            output = cleanup.enter_context((WORK / f"{node}{suffix}.log").open("w"))
            process = subprocess.Popen([
                str(rig.SDK / "resource/app/bin/x64Linux4gcc8.5.0/rtiroutingserviceapp"),
                "-cfgFile", str(path), "-cfgName", "Gateway" + node, "-verbosity", "3"],
                stdout=output, stderr=subprocess.STDOUT)
            cleanup.callback(rig.stop, process)
            processes[node] = process
            starts[node] += 1
        for node in rig.DOMAINS:
            start(node)
        source_configuration_hash = hashlib.sha256((WORK / "A.xml").read_bytes()).hexdigest()
        probes = {node: rig.Probe(node, provider) for node in rig.DOMAINS}
        for probe in probes.values():
            cleanup.callback(probe.close)
        rig.wait(lambda: all(probe.matched() for probe in probes.values()),
                 "three native WAN families and LAN endpoints", processes.values())
        control = NativeControl()
        assert all(control.read(family) == tuple("'" + value + "'" for value in Policy().parameters())
                   for family in rig.FAMILIES)
        experiment = Experiment(provider, control, probes, cleanup)
        experiment.transient_local = transient_local
        experiment.spin(lambda: experiment.cached_inventory is not None and
                        experiment.latest_applied is not None, "State inventory and applied snapshot")
        assert len(experiment.cached_inventory["entries"]) == 3
        assert all(entry["matched"] for entry in experiment.cached_inventory["entries"])
        experiment.checks.append("automatic prepared-topic inventory from native LAN matches")
        experiment.measure("auto_init")
        experiment.mission("ORBIT")
        experiment.measure("auto_active")
        assert experiment.request("User", ["ContactReport"])["accepted"]
        experiment.measure("user_override")
        experiment.mission("CANCELLED")
        experiment.measure("user_survives_mission_change")
        assert experiment.request("Auto", [])["accepted"]
        experiment.measure("return_auto_current_idle")
        assert experiment.request("User", [])["accepted"]
        experiment.measure("empty_user_inventory_remains")
        assert experiment.cached_inventory is not None
        experiment.mission("TRANSIT")
        experiment.measure("active_navigation_protected")
        assert experiment.request("User", ["ContactReport"])["accepted"]
        experiment.commit(experiment.policy.change_team("TEAM_B"))
        experiment.publish_applied()
        experiment.measure("user_cannot_bypass_team")
        experiment.commit(experiment.policy.change_team("TEAM_A"))
        experiment.publish_applied()
        experiment.measure("team_restoration")
        revision = experiment.policy.revision
        for kwargs, error in ((dict(session=0), "session"), (dict(revision=0), "revision"),
                              (dict(topics=["Unknown"]), "unsupported")):
            result = experiment.request("User", kwargs.pop("topics", ["ContactReport"]), **kwargs)
            assert not result["accepted"] and error in result["error"], result
            assert experiment.policy.revision == revision
        result = experiment.request("User", ["ContactReport"], request_id="duplicate")
        revision = experiment.policy.revision
        result = experiment.request("User", ["ContactReport"], revision=revision - 1,
                                    request_id="duplicate")
        assert result["accepted"] and experiment.policy.revision == revision
        result = experiment.request("User", [], request_id="duplicate")
        assert not result["accepted"] and "reused" in result["error"]
        experiment.checks.append("stale/conflict/unknown requests and duplicate-content validation")
        experiment.inject = "apply"
        previous = experiment.policy
        result = experiment.request("User", ["PlatformMissionStatus"])
        assert not result["accepted"] and "injected" in result["error"]
        assert experiment.policy == previous and not experiment.degraded
        experiment.inject = None
        experiment.measure("native_multi_family_rollback")
        experiment.checks.append("every-family native rollback and parameter readback")
        extended_report = None
        if extended:
            from stress import run_extended
            extended_report = run_extended(experiment, processes, start, frame,
                lambda result: write_json(WORK / "extended_report.json", result))
            write_json(WORK / "extended_report.json", extended_report)
        experiment.original_writers["ContactReport"].close()
        experiment.spin(lambda: not next(entry for entry in experiment.cached_inventory["entries"]
            if entry["topic"] == "ContactReport")["matched"], "producer disappearance")
        assert "ContactReport" in experiment.policy.selected()
        experiment.original_writers["ContactReport"] = dds.DynamicData.DataWriter(
            experiment.publisher, experiment.original_topics["ContactReport"],
            provider.datawriter_qos_from_profile("LBEDSpike::event"))
        experiment.spin(lambda: next(entry for entry in experiment.cached_inventory["entries"]
            if entry["topic"] == "ContactReport")["matched"], "producer reappearance")
        experiment.checks.append("native producer churn does not silently clear selection")
        stale = experiment.inventory_value()
        stale["revision"] = 0
        probes["A"].writers["State"].write(frame(
            provider, "A", "Inventory", "inventory", stale, "inventory"))
        experiment.spin(lambda: experiment.cache_rejections > 0, "stale inventory rejection")
        experiment.refresh_enabled = False
        experiment.settle(0.3)
        late = dds.DynamicData.DataReader(probes["B"].subscriber,
            probes["B"].readers["State"].topic_description,
            provider.datareader_qos_from_profile("LBEDSpike::state"))
        late_cleanup = cleanup.enter_context(ExitStack())
        late_cleanup.callback(late.close)
        experiment.settle(0.3)
        assert not [data for data, info in late.take() if info.valid]
        experiment.publish_inventory(force=True)
        recovered = []

        def late_recovered():
            recovered.extend(data for data, info in late.take() if info.valid)
            return bool(recovered)
        experiment.spin(late_recovered, "volatile late-reader recovery on refresh")
        experiment.refresh_enabled = True
        experiment.checks.append("volatile late reader has no replay until explicit refresh")
        experiment.settle()
        late_cleanup.close()
        for family in rig.FAMILIES:
            control.apply(family, experiment.policy.parameters())
            assert not (WORK / f"control-{family}.request").exists()
        rig.stop(processes["B"])
        control.discard_pending()
        start("B", "-restart")
        rig.wait(lambda: probes["B"].matched(), "restarted native router LAN matches")
        previous_session = experiment.policy.session
        experiment.policy = Policy(session=previous_session + 1)
        assert all(control.read(family) == tuple("'" + value + "'" for value in experiment.policy.parameters())
                   for family in rig.FAMILIES)
        experiment.seen_requests.clear()
        experiment.latest_applied = None
        experiment.publish_applied()
        experiment.spin(lambda: experiment.latest_applied is not None, "restart applied state")
        assert not experiment.request("User", ["ContactReport"], session=previous_session)["accepted"]
        experiment.measure("restart_resets_auto_and_rejects_old_session")
        experiment.checks.append("actual native router restart, Auto reset and stale-session rejection")
        write_json(WORK / "browser_state.json", experiment.snapshot())
        (WORK / "browser_ready").write_text("ready\n")
        deadline = time.monotonic() + 75
        handled = set()
        while not (WORK / "browser_done").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("host browser did not finish")
            experiment.tick()
            for phase in ("ORBIT", "CANCELLED"):
                marker = f"browser_mission_{phase}"
                if (WORK / marker).exists() and marker not in handled:
                    handled.add(marker)
                    experiment.mission(phase)
            if (WORK / "producer_absent").exists() and "producer_absent" not in handled:
                experiment.original_writers["ContactReport"].close()
                handled.add("producer_absent")
            if (WORK / "producer_present").exists() and "producer_present" not in handled:
                experiment.original_writers["ContactReport"] = dds.DynamicData.DataWriter(
                    experiment.publisher, experiment.original_topics["ContactReport"],
                    provider.datawriter_qos_from_profile("LBEDSpike::event"))
                handled.add("producer_present")
            for path in sorted(commands.glob("*.json")):
                if path.name in handled:
                    continue
                request = json.loads(path.read_text())
                result = experiment.request(request["mode"], request["topics"],
                    session=request["session"], revision=request["revision"],
                    request_id=path.stem)
                handled.add(path.name)
                write_json(WORK / f"result-{path.stem}.json", result)
            write_json(WORK / "browser_state.json", experiment.snapshot())
            time.sleep(0.02)
        assert experiment.policy.mode == "Auto"
        assert {"producer_absent", "producer_present"} <= handled
        experiment.checks.append("graph preserves unsubmitted selections through native producer churn")
        experiment.measure("browser_return_to_auto")
        stale_applied = dict(experiment.latest_applied)
        experiment.inject = "rollback"
        result = experiment.request("User", ["ContactReport"])
        assert not result["accepted"] and "ROLLBACK FAILED" in result["error"]
        assert experiment.degraded and experiment.policy.mode == "Auto"
        assert control.read("State") != tuple("'" + value + "'" for value in experiment.policy.parameters())
        result = experiment.request("User", [])
        assert not result["accepted"] and "reconciliation" in result["error"]
        assert not experiment.latest_applied["confirmed"]
        stale_value = dds.DynamicData(provider.type("Applied"))
        for key, value in stale_applied.items():
            stale_value[key] = value
        probes["B"].writers["State"].write(frame(
            provider, "B", "Applied", "applied", stale_value, "applied"))
        probes["B"].writers["Event"].write(frame(
            provider, "B", "SubscriptionAck", "applied", stale_value, "stale-ack"))
        experiment.settle(1.2)
        assert not experiment.latest_applied["confirmed"]
        assert experiment.latest_applied["error"] == experiment.degraded_error
        assert experiment.applied_rejections > 0
        experiment.checks.append("stale cross-family snapshots cannot hide persistent native divergence")
        write_json(WORK / "degraded_state.json", experiment.snapshot())
        write_json(WORK / "browser_state.json", experiment.snapshot())
        rig.wait(lambda: (WORK / "browser_fault_done").exists(),
                 "browser presentation of divergent native filters", timeout=20)
        experiment.checks.append("rollback failure exposes native/model divergence and blocks further changes")
        for process in processes.values():
            rig.stop(process)
        rig.stop(recorder)
        packets = subprocess.check_output(["tshark", "-r", str(capture), "-Y", "rtps",
            "-T", "fields", "-e", "frame.time_epoch", "-e", "udp.payload"], text=True)
        timed_packets = [(float(timestamp), bytes.fromhex(payload.replace(":", "")))
                         for timestamp, payload in (line.split("\t") for line in packets.splitlines())
                         if payload]
        for measurement in experiment.measurements:
            window = [packet for timestamp, packet in timed_packets
                      if measurement["start"] <= timestamp <= measurement["end"]]
            sent = [identity for identity in measurement["offered"]
                    if any(identity.encode() + b"\0" in packet for packet in window)]
            assert sorted(sent) == sorted(measurement["expected"]), measurement
            measurement["wan_unique_samples"] = sent
            measurement["later_wan_samples"] = [
                identity for identity in measurement["offered"] if any(
                    timestamp > measurement["end"] and identity.encode() + b"\0" in packet
                    for timestamp, packet in timed_packets)]
            measurement["source_writer_prefixes"] = sorted({
                packet[8:20].hex() for packet in window if any(
                    identity.encode() + b"\0" in packet for identity in sent)})
        source_prefixes = {prefix for measurement in experiment.measurements
                           for prefix in measurement["source_writer_prefixes"]}
        assert len(source_prefixes) == starts["A"], source_prefixes
        assert source_configuration_hash == hashlib.sha256((WORK / "A.xml").read_bytes()).hexdigest()
        if extended_report:
            for phase in extended_report["continuous"]["phases"]:
                window = [packet for timestamp, packet in timed_packets
                          if phase["operation_end_epoch"] + 0.35 <= timestamp <=
                          phase["settled_end_epoch"]]
                sent = [identity for identity in phase["offered"]
                        if any(identity.encode() + b"\0" in packet for packet in window)]
                assert set(sent) <= set(phase["expected"]), (phase["label"], "unselected WAN stream")
                assert all(identity in sent for identity in phase["expected"]
                           if identity.endswith(":ContactReport"))
                phase["wan_unique_samples"] = sent
                in_update = [identity for identity in phase["in_update_offers"] if any(
                    phase["operation_start_epoch"] <= timestamp <= phase["operation_end_epoch"]
                    and identity.encode() + b"\0" in packet for timestamp, packet in timed_packets)]
                assert in_update, (phase["label"], "WAN producer paused during filter operation")
                phase["wan_samples_during_operation"] = len(in_update)
            extended_report["result"] = "PASS"
            write_json(WORK / "extended_report.json", extended_report)
        rows = rig.capture_rows(capture)
        writer_ids = {"0003e902", "0003ea02", "0003eb02"}
        writers = {row["prefix"] + row["writer"] for row in rows
                   if row["kind"] == "DATA" and row["writer"] in writer_ids}
        assert len(writers) == 3 * sum(starts.values()), writers
        drops = re.search(r"Packets received/dropped on interface .*?: (\d+)/(\d+)",
                          (WORK / "capture.log").read_text())
        assert drops and int(drops[2]) == 0
        errors = [line for path in WORK.glob("*.log") if path.name != "console.log"
                  for line in path.read_text().splitlines() if re.match(r"^(ERROR|FATAL)\b", line)]
        assert not errors, errors
        assert not any("REMOTE GET" in line or "REMOTE UPDATE" in line
                       for path in WORK.glob("*.log") if path.name != "console.log"
                       for line in path.read_text().splitlines())
        write_json(WORK / "report.json", dict(result="PASS", checks=experiment.checks,
            measurements=experiment.measurements, capture_drops=int(drops[2]),
            native_error_lines=len(errors), three_pairs_per_router=True,
            wan_writers_including_restarted_gateway=len(writers),
            prototype_only="LAN fixture controller; not production processor or six-topic integration",
            administration_configured=False, administration_rpc_calls=0,
            wan_state_durability="TRANSIENT_LOCAL" if transient_local else "VOLATILE",
            lan_state_durability="VOLATILE",
            filter_changes="receiver-owned native CFT API; DDS propagates to source writers",
            source_configuration_unchanged=True, source_writer_prefixes=sorted(source_prefixes),
            explicit_router_starts=starts, extended=extended_report,
            rollback_failure_detected=True, browser=json.loads((WORK / "browser_report.json").read_text())))
    assert not [path.name for path in Path("/dev/shm").iterdir()
                if path.name.startswith(("RTI", "dds"))]
    print("PASS: native inventory, user/mission selection, wire suppression, browser, and fault cases",
          flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--extended", action="store_true")
    parser.add_argument("--transient-local", action="store_true")
    options = parser.parse_args()
    main(options.extended, options.transient_local)
