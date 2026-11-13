#!/usr/bin/env python3
"""Observe priority scheduling with a known unsent backlog on one Event writer."""

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import time

import rti.connextdds as dds

from priorities import DEFAULT_PRIORITIES
from spike import NativeEndpoints, frame, providers, wait_for


def observe(mode, command_priority, contact_priority=10):
    qos, types = providers()
    participant = dds.DomainParticipant(
        202, qos.participant_qos_from_profile("ChurnSpike::priority_on_demand"))
    controller = participant.find_flow_controller("dds.flow_controller.token_bucket.churn")
    writer = None
    try:
        topic = dds.DynamicData.Topic(participant, "Event", types.type("WanEvent"))
        reader = dds.DynamicData.DataReader(
            dds.Subscriber(participant), topic,
            qos.datareader_qos_from_profile("ChurnSpike::event_reader"))
        writer = dds.DynamicData.DataWriter(
            dds.Publisher(participant), topic,
            qos.datawriter_qos_from_profile(
                "ChurnSpike::" + mode if mode in (
                    "priority_channels", "priority_bands", "priority_bands_batch",
                    "priority_bands_repair", "priority_bands_last1")
                else "ChurnSpike::priority_event"))
        wait_for(lambda: writer.publication_matched_status.current_count == 1)
        run_id = f"priority-order-{mode}-{command_priority}-{time.time_ns()}"
        params = dds.WriteParams()
        params.priority = contact_priority
        total = 400
        for index in range(total):
            writer.write(frame(types, "Event", index, index + 1, run_id), params)
        params.priority = command_priority
        writer.write(frame(types, "Event", 0, total + 1, run_id, "ControlCommand"), params)
        if writer.qos.batch.enable:
            writer.flush()
        before_trigger = [s for s in reader.take() if s.info.valid]
        if before_trigger:
            raise AssertionError("On-demand controller leaked samples before its trigger")
        received = []
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and len(received) < total + 1:
            controller.trigger_flow()
            time.sleep(0.01)
            received.extend((str(s.data["original_topic"]), int(s.data["audit_sequence"]))
                            for s in reader.take() if s.info.valid)
        if len(received) != total + 1 or len(set(received)) != total + 1:
            raise AssertionError(f"Missing/duplicate data: {len(received)} of {total + 1}")
        if [sequence for topic, sequence in received if topic == "ContactReport"] != list(
                range(1, total + 1)):
            raise AssertionError("Contact sample ordering changed")
        command_index = received.index(("ControlCommand", total + 1))
        overtakes = ((mode == "priority_channels" and command_priority > contact_priority)
                     or (mode in ("priority_bands", "priority_bands_batch", "priority_bands_repair",
                                  "priority_bands_last1")
                         and command_priority >= 100 > contact_priority))
        expected = 0 if overtakes else total
        if command_index != expected:
            raise AssertionError(f"Command observed at {command_index}, expected {expected}")
        return {"mode": mode, "contact_priority": contact_priority, "command_priority": command_priority,
                "contact_count": total, "received_count": len(received),
                "command_receive_index_zero_based": command_index,
                "first_ten": received[:10],
                "scope": "one local participant/writer/reader, unsent queue, not reliable repair"}
    finally:
        if writer is not None:
            writer.close()
        if controller is not None:
            controller.close()
        participant.close()


def observe_band_lifecycle(priority, mode="priority_bands", endpoint_class=NativeEndpoints):
    priorities = dict(DEFAULT_PRIORITIES, ContactReport=priority)
    endpoints = endpoint_class("Event", True, priority_mode=mode, priorities=priorities)
    states = []
    try:
        run_id = f"priority-lifecycle-{priority}-{time.time_ns()}"
        data = frame(endpoints.types, "Event", 9000, 1, run_id)

        def received(state, sequence=None):
            return any(sample.info.state.instance_state == state
                       and (sequence is None or (
                           sample.info.valid and int(sample.data["audit_sequence"]) == sequence))
                       for sample in endpoints.reader.take())

        endpoints.write(endpoints.writer, data)
        wait_for(lambda: received(dds.InstanceState.ALIVE, 1))
        states.append("alive:1")
        handle = endpoints.writer.lookup_instance(data)
        if handle == dds.InstanceHandle.nil:
            raise AssertionError("Lifecycle probe instance was not registered")
        endpoints.write(endpoints.writer, data, "dispose", handle)
        wait_for(lambda: received(dds.InstanceState.NOT_ALIVE_DISPOSED))
        states.append("disposed")
        endpoints.write(endpoints.writer, frame(endpoints.types, "Event", 9000, 2, run_id))
        wait_for(lambda: received(dds.InstanceState.ALIVE, 2))
        states.append("alive:2")
        return {"mode": mode, "kind": "lifecycle", "contact_priority": priority,
                "observed_states": states, "effective_qos": endpoints.effective_qos(),
                "scope": "remote CFT reader, serial write/dispose/revive, no outage"}
    finally:
        endpoints.close()


def observe_self_ignore(mode, endpoint_class=NativeEndpoints):
    endpoints = endpoint_class("Event", True, priority_mode=mode)
    try:
        run_id = f"self-ignore-{mode}-{time.time_ns()}"
        endpoints.write(endpoints.writer, frame(endpoints.types, "Event", 1, 1, run_id))
        wait_for(lambda: any(
            sample.info.valid and int(sample.data["audit_sequence"]) == 1
            for sample in endpoints.reader.take()))
        command = frame(endpoints.types, "Event", 1, 2, run_id, "ControlCommand")
        endpoints.acknowledge(command)
        wait_for(lambda: any(
            sample.info.valid and int(sample.data["audit_sequence"]) == 2
            for sample in endpoints.ack_reader.take()))
        wait_for(lambda: endpoints.metrics()["unacked"] == 0)
        writers = dict(endpoints.writers, acknowledgment=endpoints.ack_writer)
        matched = {name: writer.publication_matched_status.current_count
                   for name, writer in writers.items()}
        if any(count != 1 for count in matched.values()):
            raise AssertionError(f"Local readers remain writer destinations: {matched}")
        return {"mode": mode, "kind": "self_ignore", "matched_remote_readers": matched,
                "remote_contact_and_ack_received": True, "unacked": 0}
    finally:
        endpoints.close()


def observe_last_state(case, *, qos_files=(), topic_name="Event",
                       writer_profile="ChurnSpike::priority_bands_last1",
                       reader_profile="ChurnSpike::event_reader"):
    qos, types = providers(qos_files)
    with ExitStack() as cleanup:
        participant = dds.DomainParticipant(
            202, qos.participant_qos_from_profile("ChurnSpike::priority_on_demand"))
        cleanup.callback(participant.close)
        controller = participant.find_flow_controller("dds.flow_controller.token_bucket.churn")
        if controller is None:
            raise RuntimeError("On-demand flow controller was not created")
        cleanup.callback(controller.close)
        topic = dds.DynamicData.Topic(participant, topic_name, types.type("WanEvent"))
        reader = dds.DynamicData.DataReader(
            dds.Subscriber(participant), topic,
            qos.datareader_qos_from_profile(reader_profile))
        cleanup.callback(reader.close)
        writer = dds.DynamicData.DataWriter(
            dds.Publisher(participant), topic,
            qos.datawriter_qos_from_profile(writer_profile))
        cleanup.callback(writer.close)
        if writer.qos.history.kind != dds.HistoryKind.KEEP_LAST or writer.qos.history.depth != 1:
            raise AssertionError("Native writer is not KEEP_LAST(1)")
        wait_for(lambda: writer.publication_matched_status.current_count == 1)
        run_id = f"last-state-{case}-{time.time_ns()}"
        params = dds.WriteParams()
        params.priority = 10
        data = frame(types, "Event", 9000, 0, run_id)
        observed = []

        def collect():
            for sample in reader.take():
                if sample.info.valid and str(sample.data["instance_id"]) != "contact_9000":
                    continue
                observed.append({
                    "valid": sample.info.valid,
                    "sequence": int(sample.data["audit_sequence"]) if sample.info.valid else None,
                    "disposed": sample.info.state.instance_state == dds.InstanceState.NOT_ALIVE_DISPOSED,
                })

        def pump():
            controller.trigger_flow()
            time.sleep(0.01)
            collect()
            return bool(observed) and writer.reliable_writer_cache_changed_status.unacknowledged_sample_count == 0

        writer.write(data, params)
        wait_for(pump)
        observed.clear()
        # Consume any tokens left by the seed's final trigger before queuing replacements.
        for identity in range(9001, 9033):
            writer.write(frame(types, "Event", identity, 0, run_id), params)
        time.sleep(0.05)
        collect()
        steps = {"updates": (1, 2, 3), "dispose": (1, 2, None),
                 "revive": (None, 3)}[case]
        for sequence in steps:
            if sequence is None:
                params.handle = writer.lookup_instance(data)
                if params.handle == dds.InstanceHandle.nil:
                    raise AssertionError("Seeded instance is not registered")
                writer.dispose_instance(params)
            else:
                writer.write(frame(types, "Event", 9000, sequence, run_id), params)
        collect()
        if observed:
            raise AssertionError("On-demand writer sent before its trigger")
        wait_for(pump)
        expected = [{"valid": case != "dispose", "sequence": None if case == "dispose" else 3,
                     "disposed": case == "dispose"}]
        if observed != expected:
            raise AssertionError(f"KEEP_LAST(1) {case}: {observed}, expected {expected}")
        return {"mode": writer_profile.split("::")[-1], "kind": "last_state", "case": case,
                "topic": topic_name, "writer_profile": writer_profile, "reader_profile": reader_profile,
                "observed": observed, "history_depth": writer.qos.history.depth,
                "scope": "one seeded instance, queued operations, on-demand flow controller"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (not Path("/.dockerenv").exists()
            or {name for _, name in socket.if_nameindex()} != {"lo"}):
        raise RuntimeError("Run only in an owned --network none container")
    output = args.output
    results = []
    for mode in ("production", "priority_channels", "priority_bands", "priority_bands_repair",
                 "priority_bands_last1"):
        result = observe_self_ignore(mode)
        results.append(result)
        output.write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    for mode in ("sample_priority", "priority_channels"):
        for command_priority in (1, 100):
            result = observe(mode, command_priority)
            results.append(result)
            output.write_text(json.dumps(results, indent=2) + "\n")
            print(json.dumps(result), flush=True)
    for contact_priority, command_priority in (
            (10, 0), (10, 99), (10, 100), (0, 100), (100, 99), (100, 101)):
        result = observe("priority_bands", command_priority, contact_priority)
        results.append(result)
        output.write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    for priority in (0, 100):
        result = observe_band_lifecycle(priority)
        results.append(result)
        output.write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    for command_priority in (99, 100):
        result = observe("priority_bands_batch", command_priority)
        results.append(result)
        output.write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    for priority in (0, 100):
        result = observe_band_lifecycle(priority, "priority_bands_batch")
        results.append(result)
        output.write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    for command_priority in (99, 100):
        result = observe("priority_bands_repair", command_priority)
        results.append(result)
        output.write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    for priority in (0, 100):
        result = observe_band_lifecycle(priority, "priority_bands_repair")
        results.append(result)
        output.write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    for command_priority in (99, 100):
        result = observe("priority_bands_last1", command_priority)
        results.append(result)
        output.write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    for priority in (0, 100):
        result = observe_band_lifecycle(priority, "priority_bands_last1")
        results.append(result)
        output.write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    for case in ("updates", "dispose", "revive"):
        result = observe_last_state(case)
        results.append(result)
        output.write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
