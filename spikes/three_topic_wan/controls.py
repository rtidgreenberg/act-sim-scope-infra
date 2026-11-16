#!/usr/bin/env python3
"""Native policy, endpoint isolation, replacement and cross-writer priority controls."""

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import time
import traceback

from three_topic import HERE, ThreeTopicEndpoints
import rti.connextdds as dds
from priority_order import observe_band_lifecycle, observe_last_state, observe_self_ignore
from spike import frame, providers, run_case, wait_for, write_junit
from workload import reception_order


def inventory():
    endpoints = ThreeTopicEndpoints()
    try:
        result = endpoints.effective_qos()
        inventory = result["endpoint_inventory"]
        expected = {node + "/" + topic for node in ("Source", "Receiver")
                    for topic in ("Event", "Contacts", "Status")}
        for kind in ("writers", "readers"):
            if set(inventory[kind]) != expected:
                raise AssertionError(f"Unexpected {kind}: {inventory[kind]}")
            for name, endpoint in inventory[kind].items():
                topic = name.split("/")[1]
                if endpoint["topic"] != topic:
                    raise AssertionError(f"Incorrect physical topic: {name}: {endpoint}")
                history = dds.HistoryKind.KEEP_ALL if topic == "Event" else dds.HistoryKind.KEEP_LAST
                reliability = dds.ReliabilityKind.BEST_EFFORT if topic == "Status" else dds.ReliabilityKind.RELIABLE
                if endpoint["history"] != str(history) or endpoint["reliability"] != str(reliability):
                    raise AssertionError(f"Incorrect policy: {name}: {endpoint}")
                if topic != "Event" and endpoint["depth"] != 1:
                    raise AssertionError(f"Incorrect latest-state depth: {name}: {endpoint}")
                if kind == "readers" and endpoint["matched_writers"] != 1:
                    raise AssertionError(f"Reader has local or missing destinations: {name}: {endpoint}")
                if kind == "writers":
                    if endpoint["matched_readers"] != 1 or endpoint["channel_count"] != 0:
                        raise AssertionError(f"Local destination or unexpected MultiChannel: {name}: {endpoint}")
                    if endpoint["durability"] != str(dds.DurabilityKind.VOLATILE):
                        raise AssertionError(f"Unexpected durability: {name}: {endpoint}")
                    if topic != "Status" and endpoint["flow_controller"] != result["flow_controller"]:
                        raise AssertionError(f"Reliable classes do not share one budget: {name}: {endpoint}")
        for original_topic, writer in (
                ("ContactReport", endpoints.command_writer), ("Mission", endpoints.writer)):
            try:
                endpoints.write(writer, frame(
                    endpoints.types, "Event", 1, 1, "wrong-class", original_topic))
            except ValueError:
                pass
            else:
                raise AssertionError(f"Misrouted {original_topic} accepted on {writer.topic_name}")
        return {"kind": "endpoint_inventory", "effective_qos": result, "misrouting_rejected": True}
    finally:
        endpoints.close()


def queued_priority(command_priority, contact_priority):
    qos, types = providers((HERE / "qos.xml",))
    with ExitStack() as cleanup:
        participant = dds.DomainParticipant(
            202, qos.participant_qos_from_profile("ChurnSpike::priority_on_demand"))
        cleanup.callback(participant.close)
        controller = participant.find_flow_controller("dds.flow_controller.token_bucket.churn")
        if controller is None:
            raise RuntimeError("Shared on-demand controller not created")
        cleanup.callback(controller.close)
        writers, readers = {}, {}
        for name, writer_profile, reader_profile in (
                ("Contacts", "ThreeTopic::contacts", "ThreeTopic::contact_reader"),
                ("Event", "ThreeTopic::events", "ChurnSpike::event_reader")):
            topic = dds.DynamicData.Topic(participant, name, types.type("WanEvent"))
            reader = dds.DynamicData.DataReader(
                dds.Subscriber(participant), topic, qos.datareader_qos_from_profile(reader_profile))
            cleanup.callback(reader.close)
            writer = dds.DynamicData.DataWriter(
                dds.Publisher(participant), topic, qos.datawriter_qos_from_profile(writer_profile))
            cleanup.callback(writer.close)
            if len(writer.qos.multi_channel.channels) != 0:
                raise AssertionError("This test must not use MultiChannel")
            writers[name], readers[name] = writer, reader
        wait_for(lambda: all(
            writer.publication_matched_status.current_count == 1 for writer in writers.values()))
        run_id = f"three-topic-priority-{time.time_ns()}"
        params = dds.WriteParams()
        params.priority = contact_priority
        total = 400
        for identity in range(total):
            for sequence in (2 * identity + 1, 2 * identity + 2):
                writers["Contacts"].write(frame(types, "Event", identity, sequence, run_id), params)
        params.priority = command_priority
        commands = list(range(10001, 10005))
        for sequence in commands:
            writers["Event"].write(frame(
                types, "Event", 0, sequence, run_id, "Mission"), params)
        if any(list(reader.take()) for reader in readers.values()):
            raise AssertionError("On-demand controller leaked before its trigger")
        received = []

        def pump():
            controller.trigger_flow()
            time.sleep(0.01)
            for topic, reader in readers.items():
                received.extend((sample.info.reception_timestamp.sec * 1_000_000_000
                                 + sample.info.reception_timestamp.nanosec,
                                 topic, int(sample.data["audit_sequence"]))
                                for sample in reader.take() if sample.info.valid)
            return (len(received) >= total + len(commands)
                    and all(writer.reliable_writer_cache_changed_status.unacknowledged_sample_count == 0
                            for writer in writers.values()))

        wait_for(pump, seconds=20)
        received = reception_order(received)
        contacts = [sequence for topic, sequence in received if topic == "Contacts"]
        events = [sequence for topic, sequence in received if topic == "Event"]
        if sorted(contacts) != list(range(2, 2 * total + 1, 2)) or events != commands:
            raise AssertionError(f"Wrong latest-state or required-event coverage: {contacts}, {events}")
        expected_first = "Event" if command_priority > contact_priority else "Contacts"
        if received[0][0] != expected_first:
            raise AssertionError(f"Priority did not select {expected_first}: {received[:10]}")
        if command_priority > contact_priority and received[:len(commands)] != [
                ("Event", sequence) for sequence in commands]:
            raise AssertionError(f"Required Event queue did not overtake Contacts: {received[:10]}")
        return {"kind": "cross_writer_priority", "command_priority": command_priority,
                "contact_priority": contact_priority, "channel_count": 0,
                "contact_writes": 2 * total, "contact_latest_states_received": len(contacts),
                "commands_sent": len(commands), "commands_received": len(events),
                "first_ten": received[:10],
                "ordering_basis": "DDS reception timestamps; cross-reader ties rejected",
                "scope": "one local participant, two independent writers, shared on-demand HPF; "
                         "middleware commit order, not wire arrival or reliable repair"}


def required_event_negative():
    endpoints = ThreeTopicEndpoints()
    suppressed = []
    original_acknowledge = endpoints.acknowledge

    def suppress_one_ack(command):
        if int(command["audit_sequence"]) == 2:
            suppressed.append(2)
        else:
            original_acknowledge(command)

    endpoints.acknowledge = suppress_one_ack
    try:
        result = run_case(endpoints, "steady", False, 2, 2, drain_seconds=5)
        if (result["passed"] or result["missing_command_acks"] != [2] or suppressed != [2]
                or result["write_history"]["required"] or any(result["final_discrepancies"].values())
                or result["errors"] != ["Missing command ACKs: [2]"]):
            raise AssertionError(f"Latest-state acceptance hid required-event loss: {result['errors']}")
        return {"kind": "required_event_negative", "suppressed_ack": 2,
                "expected_failure": result["errors"], "final_contact_state_correct": True,
                "event_delivery": result["event_delivery"],
                "scope": "deliberately suppress one application ACK; contact coalescing must not mask it"}
    finally:
        endpoints.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (not Path("/.dockerenv").exists()
            or {name for _, name in socket.if_nameindex()} != {"lo"}):
        raise RuntimeError("Run only in an owned --network none container")
    cases = [
        ("inventory", inventory),
        ("self_ignore", lambda: observe_self_ignore("sample_priority", ThreeTopicEndpoints)),
        ("lifecycle", lambda: observe_band_lifecycle(10, "sample_priority", ThreeTopicEndpoints)),
        ("priority_events_first", lambda: queued_priority(100, 10)),
        ("priority_contacts_first", lambda: queued_priority(10, 100)),
        ("required_event_negative", required_event_negative),
    ]
    for case in ("updates", "dispose", "revive"):
        cases.append(("queued_" + case, lambda case=case: observe_last_state(
            case, qos_files=(HERE / "qos.xml",), topic_name="Contacts",
            writer_profile="ThreeTopic::contacts", reader_profile="ThreeTopic::contact_reader")))
    results = []
    for name, check in cases:
        try:
            result = {"run_id": name, "passed": True, "errors": [], **check()}
        except Exception:
            result = {"run_id": name, "passed": False, "errors": [traceback.format_exc()]}
        results.append(result)
        args.output.write_text(json.dumps(results, indent=2) + "\n")
        write_junit(results, args.output.with_name("junit.xml"))
        print(json.dumps({key: result[key] for key in ("run_id", "passed", "errors")}), flush=True)
    raise SystemExit(0 if all(result["passed"] for result in results) else 1)


if __name__ == "__main__":
    main()
