"""Actual router state convergence, separate from native protocol instrumentation."""

from collections import Counter
import json
import os
from pathlib import Path
import time

import pytest
import rti.connextdds as dds

from test_two_topic_controller import alive, mesh, ready
from test_presence_roster import ALIVE, DEAD, latest_mesh, mesh_reader, wait_mesh
from util.dds_probe import writer_qos
from spike import Blackout, frame, providers
from workload import (
    STATIONARY_CONTACT_ID, Operation, converged, discrepancies, initial_identities, operations,
    positive_seconds,
)


@pytest.mark.parametrize("family", ["Event", "Status"])
@pytest.mark.parametrize("outage", [False, True])
def test_routed_churn(mesh, family, outage, e2e_tmp_dir):
    nodes, wan, provider = mesh
    source = nodes["Boat30"][1]
    receiver = nodes["Control_20"][1]
    topic, type_name = (("ContactReport", "contact_report") if family == "Event"
                        else ("PlatformInitStatus", "platform_init_status"))
    writer = (source.writer(topic, type_name) if family == "Event"
              else wan.writer("Status", "WanStatus", qos=writer_qos("wan_union_status")))
    reader = receiver.reader(topic, type_name)
    _, types = providers()
    run_id = f"routed-churn-{family}-{time.time_ns()}"
    truth, actual, observations = {}, {}, []
    counts, outage_counts = Counter(), Counter()
    sequence = 0
    lag_max = 0.0
    health_reader = mesh_reader(receiver, provider)
    presence_trace = []
    next_presence = 0.0
    outage_seconds = positive_seconds(os.environ.get("CHURN_OUTAGE_SECONDS", "2"))
    stationary_key = f"contact_{STATIONARY_CONTACT_ID}" if family == "Event" else None
    # The fixture allocated the WAN domain; inspect its actual participant, never a live domain.
    drop = Blackout(wan.participant.domain_id)

    def collect():
        nonlocal next_presence
        assert alive(nodes), "Router process exited"
        now = time.monotonic()
        if now >= next_presence:
            peers = latest_mesh(health_reader)
            if peers is not None:
                states = {name: peer["presence"] for name, peer in peers.items()}
                if not presence_trace or presence_trace[-1]["peers"] != states:
                    presence_trace.append({"at": now, "peers": states})
            next_presence = now + 0.1
        for item in reader.take():
            if item.info.valid and item.data["run_id"] != run_id:
                continue
            data = item.data if item.info.valid else reader.key_value(item.info.instance_handle)
            if family == "Event":
                assert data["source"] == "Boat30", "Contact source was not restored by adapter"
            identity = str(data["contact_id"] if family == "Event" else data["source"])
            state = ("alive" if item.info.state.instance_state == dds.InstanceState.ALIVE else
                     "disposed" if item.info.state.instance_state == dds.InstanceState.NOT_ALIVE_DISPOSED
                     else "no_writers")
            value_sequence = (int(data["audit_sequence"]) if item.info.valid
                              else actual.get(identity, {}).get("sequence", 0))
            actual[identity] = {"state": state, "sequence": value_sequence}
            observations.append({"at": time.monotonic(), "key": identity,
                                 "state": state, "valid": item.info.valid,
                                 "sequence": value_sequence})

    def send(operation, measured=False):
        nonlocal sequence
        sequence += 1
        if family == "Event":
            identity = f"contact_{operation.identity}"
            data = source.sample(type_name, source="", contact_id=identity,
                                 run_id=run_id, audit_sequence=sequence)
        else:
            data = frame(types, family, operation.identity, sequence, run_id)
            identity = str(data["source_id"])
        if operation.kind == "dispose":
            handle = writer.lookup_instance(data)
            assert handle != dds.InstanceHandle.nil, f"Missing source registration: {identity}"
            writer.dispose_instance(handle)
        else:
            writer.write(data)
        truth[identity] = {"state": "disposed" if operation.kind == "dispose" else "alive",
                           "sequence": sequence}
        if measured:
            counts[operation.kind] += 1
            if drop.active:
                outage_counts[operation.kind] += 1

    errors = []
    event_discovery = {"writers": [], "readers": []}
    try:
        if family == "Event" and os.environ.get("CHURN_EVENT_DURABILITY") == "transient_local":
            assert writer_qos("wan_event").durability.kind == dds.DurabilityKind.TRANSIENT_LOCAL
        if family == "Status":
            # Dynamic route discovery needs a genuine original-type LAN producer.
            primer = source.writer(topic, type_name)
            data = source.sample(type_name, source="Boat30", run_id="churn-route-setup",
                                 audit_sequence=0)
            until = time.monotonic() + 25
            primed = False
            while time.monotonic() < until and not primed:
                primer.write(data)
                time.sleep(0.1)
                primed = any(item.info.valid and item.data["source"] == "Boat30"
                             for item in reader.take())
            assert primed, "Genuine source route did not prime Status receive adapter"
        # Discovery/setup retries are outside the measured workload.
        for identity in initial_identities("steady"):
            send(Operation("create", identity))
        if stationary_key:
            send(Operation("create", STATIONARY_CONTACT_ID))
        until = time.monotonic() + 25
        retry = time.monotonic() + 1
        while time.monotonic() < until:
            collect()
            if converged(truth, actual):
                break
            if time.monotonic() >= retry:
                for identity in initial_identities("steady"):
                    send(Operation("create", identity))
                if stationary_key:
                    send(Operation("create", STATIONARY_CONTACT_ID))
                retry = time.monotonic() + 1
            time.sleep(0.1)
        assert converged(truth, actual), f"Initial route did not converge: {discrepancies(truth, actual)}"
        if family == "Event":
            ready(nodes, "Boat30", "platform_events")
            expected_durability = (
                dds.DurabilityKind.TRANSIENT_LOCAL
                if os.environ.get("CHURN_EVENT_DURABILITY") == "transient_local"
                else dds.DurabilityKind.VOLATILE)
            for label, discovery_reader in (
                    ("writers", wan.participant.publication_reader),
                    ("readers", wan.participant.subscription_reader)):
                for item in discovery_reader.take():
                    if item.info.valid and item.data.topic_name == "Event":
                        assert item.data.durability.kind == expected_durability
                        event_discovery[label].append(str(item.data.durability.kind))
                assert event_discovery[label], f"No actual Event {label} discovered"
        peers = wait_mesh(
            health_reader, lambda peers: peers.get("Boat30/phase4", {}).get("presence") == ALIVE,
            timeout_s=10, alive=lambda: alive(nodes))
        assert peers and peers.get("Boat30/phase4", {}).get("presence") == ALIVE
        start = time.monotonic()
        baseline = float(os.environ.get("CHURN_BASELINE", "20"))
        recovery = float(os.environ.get("CHURN_RECOVERY", "20"))
        stop = start + baseline + (outage_seconds if outage else 0) + recovery
        tick = 0
        while time.monotonic() < stop:
            now = time.monotonic()
            if outage and drop.started is None and now >= start + baseline:
                collect()
                drop.start()
            if drop.active and now >= drop.started + outage_seconds:
                drop.stop()
            due = start + tick * 0.05
            if now >= due:
                lag_max = max(lag_max, now - due)
                for operation in operations(tick):
                    send(operation, measured=True)
                tick += 1
            collect()
            time.sleep(0.001)
        drop.stop()
        until = time.monotonic() + 10
        while time.monotonic() < until:
            collect()
            if converged(truth, actual):
                break
            time.sleep(0.05)
        final = discrepancies(truth, actual)
        if any(final.values()):
            errors.append({name: len(values) for name, values in final.items()})
        if lag_max > 0.05:
            errors.append(f"Scheduler exceeded 50-ms lag: {lag_max}")
        stationary_observations = [
            item for item in observations if item["key"] == stationary_key]
        if any(item["state"] != "alive" for item in stationary_observations):
            errors.append("Never-disposed stationary contact changed instance state")
        if outage:
            if not drop.counters or "DROP" not in drop.counters:
                errors.append("No impairment counter evidence")
            if (drop.finished is None or drop.started is None
                    or not outage_seconds - 0.01 <= drop.finished - drop.started <= outage_seconds + 0.05):
                errors.append("Outage duration outside requested tolerance")
        if outage and outage_seconds >= 40:
            if not any(item["peers"].get("Boat30/phase4") == DEAD for item in presence_trace):
                errors.append("Two-topic health did not report DEAD during long outage")
            if not presence_trace or presence_trace[-1]["peers"].get("Boat30/phase4") != ALIVE:
                errors.append("Two-topic health did not recover to ALIVE")
        report = {
            "family": family, "outage": outage, "operations": dict(counts),
            "blackout_operations": dict(outage_counts), "scheduler_lag_max_s": lag_max,
            "outage_started": drop.started, "outage_finished": drop.finished,
            "requested_outage_s": outage_seconds, "presence_trace": presence_trace,
            "stationary_contact": {
                "key": stationary_key, "observations": stationary_observations,
                "final": actual.get(stationary_key),
            },
            "drop_counters": drop.counters, "source_truth": truth,
            "destination_state": actual, "final_discrepancies": final,
            "observations": observations, "errors": errors,
            "event_durability_experiment": os.environ.get("CHURN_EVENT_DURABILITY", "production"),
            "event_discovery_durability": event_discovery,
            "scope": ("full LAN-WAN-LAN Event route" if family == "Event" else
                      "multi-source WAN injection through production receive adapter to LAN"),
        }
        directory = Path(os.environ["CHURN_RESULTS"])
        (directory / f"routed_{family}_{outage}.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({key: report[key] for key in
                          ("family", "outage", "operations", "scope", "errors")}))
        assert not errors, errors
    finally:
        drop.stop()
        directory = Path(os.environ["CHURN_RESULTS"])
        for node, (process, _, _) in nodes.items():
            (directory / f"router_tail_{family}_{outage}_{node}.txt").write_text(
                process.log_path.read_text()[-16000:])
