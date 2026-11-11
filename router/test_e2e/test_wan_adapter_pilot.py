"""Real DDS tests of inactive production adapters, before controller/config wiring."""

import json
import os
import select
import subprocess
import threading
import time
from pathlib import Path

import pytest
import rti.connextdds as dds

from conftest import REPO_ROOT, set_wan_qos_env


def wait_until(predicate, timeout=10, context=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    detail = f": {context()}" if context else ""
    raise AssertionError("Timed out waiting for adapter pilot" + detail)


class Pilot:
    def __init__(self, process, log_path):
        self.process = process
        self.log_path = log_path

    def response(self):
        ready, _, _ = select.select([self.process.stdout], [], [], 15)
        assert ready, f"Pilot did not respond; log: {self.log_path}"
        line = self.process.stdout.readline()
        assert line, f"Pilot exited {self.process.poll()}; log: {self.log_path}"
        return json.loads(line)

    def command(self, command, ok=True):
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()
        result = self.response()
        assert result["ok"] is ok, f"{command}: {result}; log: {self.log_path}"

    def stats(self):
        self.process.stdin.write("stats\n")
        self.process.stdin.flush()
        return self.response()


@pytest.fixture
def pilot(unique_domains, admin_types_xml, tmp_path):
    set_wan_qos_env()
    binary = Path(os.environ.get("WAN_ADAPTER_PILOT", REPO_ROOT / "router/build/wan_adapter_pilot"))
    assert binary.is_file(), "Build wan_adapter_pilot before running these tests"
    log_path = tmp_path / "wan_adapter_pilot.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [str(binary), str(admin_types_xml),
             str(REPO_ROOT / "harness_v2/qos/act_qos_profiles.xml"),
             str(unique_domains["control_lan"]), str(unique_domains["wan"]),
             str(unique_domains["platform_lan"])],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
            text=True, bufsize=1,
        )
        case = Pilot(process, log_path)
        ready = False
        try:
            assert case.response() == {"ready": True}
            ready = True
            yield case
        finally:
            if process.poll() is None:
                try:
                    if ready:
                        case.command("shutdown")
                finally:
                    process.stdin.close()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            assert process.returncode == 0, f"Pilot exited {process.returncode}; log: {log_path}"
            process.stdout.close()


class Rig:
    def __init__(self, participants, types, qos):
        self.participants = participants
        self.types = types
        self.qos = qos
        self.topics = []
        self.topic_bindings = {}
        self.readers = []
        self.writers = []
        self.command_writer = self.writer(0, "ControlCommand", "control_command", "lan_event")
        self.command_reader = self.reader(1, "ControlCommand", "control_command", "lan_event")
        self.nav_writer = self.writer(1, "PlatformInitStatus", "platform_init_status", "lan_status_1s")
        self.nav_reader = self.reader(0, "PlatformInitStatus", "platform_init_status", "lan_status_1s")
        self.event_reader = self.reader(2, "Event", "WanEvent", "wan_event")
        self.status_reader = self.reader(2, "Status", "WanStatus", "wan_status")
        wait_until(lambda: self.command_writer.publication_matched_status.current_count == 1)
        wait_until(lambda: self.nav_writer.publication_matched_status.current_count == 1)
        for reader in (self.command_reader, self.nav_reader, self.event_reader, self.status_reader):
            wait_until(lambda: reader.subscription_matched_status.current_count == 1)

    def topic(self, index, name, type_name):
        if (index, name) in self.topic_bindings:
            return self.topic_bindings[index, name]
        topic = dds.DynamicData.Topic(self.participants[index], name, self.types.type(type_name))
        self.topics.append(topic)
        self.topic_bindings[index, name] = topic
        return topic

    def writer(self, index, name, type_name, profile):
        writer = dds.DynamicData.DataWriter(
            self.participants[index], self.topic(index, name, type_name),
            self.qos.datawriter_qos_from_profile("ACT_QOS_LIB::" + profile),
        )
        self.writers.append(writer)
        return writer

    def reader(self, index, name, type_name, profile):
        qos = self.qos.datareader_qos_from_profile("ACT_QOS_LIB::" + profile)
        lifecycle = qos.reader_data_lifecycle
        retention = self.qos.datareader_qos_from_profile(
            "ACT_QOS_LIB::router_lifecycle_reader").reader_data_lifecycle
        lifecycle.autopurge_nowriter_instances_delay = retention.autopurge_nowriter_instances_delay
        lifecycle.autopurge_disposed_instances_delay = retention.autopurge_disposed_instances_delay
        qos.reader_data_lifecycle = lifecycle
        reader = dds.DynamicData.DataReader(
            self.participants[index], self.topic(index, name, type_name),
            qos,
        )
        self.readers.append(reader)
        return reader

    def sample(self, type_name, sequence):
        sample = dds.DynamicData(self.types.type(type_name))
        sample["source"] = "Control20" if type_name == "control_command" else "Boat30"
        sample["source_node"] = sample["source"]
        sample["run_id"] = "adapter-pilot"
        sample["audit_sequence"] = sequence
        sample["sent_at_ns"] = 9876543210123 + sequence
        sample["timestamp"] = 123456 + sequence
        if type_name == "control_command":
            sample["destination"] = "Boat30"
            sample["command_id"] = f"command-{sequence}"
            sample["command_type"] = "opaque-command"
            sample["payload"] = [index % 256 for index in range(8192)]
        elif type_name == "platform_init_status":
            sample["latitude"] = 32.5
            sample["longitude"] = -117.25
            sample["speed_knots"] = 7.5
            sample["mission_id"] = "pilot-mission"
        else:
            sample["bus_voltage"] = 48.5
            sample["charging"] = True
        return sample

    def close(self):
        for group in (self.readers, self.writers, self.topics):
            for entity in reversed(group):
                entity.close()
        for participant in reversed(self.participants):
            participant.close()


@pytest.fixture
def rig(pilot, unique_domains, admin_types_xml):
    qos = dds.QosProvider(str(REPO_ROOT / "harness_v2/qos/act_qos_profiles.xml"))
    types = dds.QosProvider(str(admin_types_xml))
    participants = [
        dds.DomainParticipant(unique_domains["control_lan"],
                              qos.participant_qos_from_profile("ACT_QOS_LIB::lan_transport_loopback")),
        dds.DomainParticipant(unique_domains["platform_lan"],
                              qos.participant_qos_from_profile("ACT_QOS_LIB::lan_transport_loopback")),
        dds.DomainParticipant(unique_domains["wan"],
                              qos.participant_qos_from_profile("ACT_QOS_LIB::wan_two_topic_participant")),
    ]
    case = Rig(participants, types, qos)
    try:
        yield case
    finally:
        pilot.command("shutdown")
        case.close()


def take_valid(reader):
    return [sample.data for sample in reader.take() if sample.info.valid]


def receive(reader, sequence):
    samples = []

    def complete():
        samples.extend(take_valid(reader))
        return any(sample["audit_sequence"] == sequence for sample in samples)

    wait_until(complete, context=lambda: f"wanted {sequence}; received "
               f"{[sample['audit_sequence'] for sample in samples]}")
    matches = [sample for sample in samples if sample["audit_sequence"] == sequence]
    assert len(matches) == 1
    time.sleep(0.1)
    assert not [sample for sample in take_valid(reader) if sample["audit_sequence"] == sequence]
    return matches[0]


def test_production_codec_wire_codec_and_single_publication(pilot, rig):
    command = rig.sample("control_command", 1)
    rig.command_writer.write(command)
    assert receive(rig.command_reader, 1) == command
    event = receive(rig.event_reader, 1)
    assert event["original_topic"] == "ControlCommand"
    assert event["source_id"] == "Control20"
    assert event["session_id"] == 42
    with event.loan_value("payload") as payload:
        with payload.data.loan_value("command") as local:
            assert local.data == command

    nav = rig.sample("platform_init_status", 2)
    rig.nav_writer.write(nav)
    assert receive(rig.nav_reader, 2) == nav
    status = receive(rig.status_reader, 2)
    with status.loan_value("payload") as payload:
        with payload.data.loan_value("navigation") as local:
            assert local.data == nav
    with status.loan_value("audience") as audience:
        assert audience.data["control"] is True
        assert audience.data["global"] is True
    time.sleep(0.3)
    stats = pilot.stats()
    assert stats["counts"] == {
        "command_source": 1, "command_destination": 1,
        "nav_c2": 1, "nav_global": 1, "nav_destination": 1, "nav_destination_alias": 1,
    }
    assert stats["control"]["writers"] == stats["platform"]["writers"] == 1
    assert stats["control"]["readers"] == stats["platform"]["readers"] == 1
    assert stats["control"]["destination_writers"] == 1
    assert stats["platform"]["source_readers"] == 1
    assert stats["stats_sources"] == 4
    assert stats["errors"] == 0
    assert take_valid(rig.nav_reader) == []
    assert take_valid(rig.command_reader) == []


def test_shared_references_failures_and_reenable(pilot, rig):
    initial = pilot.stats()
    for command in ("bad_source", "bad_destination", "bad_filter",
                    "bind nav_c2 99", "unbind nav_c2 99"):
        pilot.command(command, ok=False)
        after = pilot.stats()
        for name in ("control", "platform", "stats_sources", "counts"):
            assert after[name] == initial[name]
    pilot.command("bind power_source 10")
    pilot.command("bind power_destination 11")
    power_writer = rig.writer(1, "PlatformPowerStatus", "platform_power_status", "lan_status_1s")
    power_reader = rig.reader(0, "PlatformPowerStatus", "platform_power_status", "lan_status_1s")
    wait_until(lambda: power_writer.publication_matched_status.current_count == 1)
    wait_until(lambda: power_reader.subscription_matched_status.current_count == 1)
    assert pilot.stats()["platform"]["writers"] == 1
    assert pilot.stats()["control"]["readers"] == 1
    assert pilot.stats()["stats_sources"] == 4

    pilot.command("unbind nav_c2 3")
    pilot.command("unbind nav_destination 5")
    rig.nav_writer.write(rig.sample("platform_init_status", 20))
    receive(rig.nav_reader, 20)
    status = receive(rig.status_reader, 20)
    with status.loan_value("audience") as audience:
        assert audience.data["control"] is False
        assert audience.data["global"] is True
    pilot.command("unbind nav_global 4")
    pilot.command("unbind nav_destination_alias 6")
    assert pilot.stats()["platform"]["writers"] == 1
    assert pilot.stats()["control"]["readers"] == 1
    assert pilot.stats()["stats_sources"] == 4
    power_writer.write(rig.sample("platform_power_status", 21))
    assert receive(power_reader, 21) == rig.sample("platform_power_status", 21)
    receive(rig.status_reader, 21)

    pilot.command("unbind power_source 10")
    pilot.command("unbind power_destination 11")
    assert pilot.stats()["platform"]["writers"] == 0
    assert pilot.stats()["control"]["readers"] == 0
    assert pilot.stats()["stats_sources"] == 2
    pilot.command("bind nav_c2 30")
    pilot.command("bind nav_destination 31")
    wait_until(lambda: rig.nav_writer.publication_matched_status.current_count == 1)
    wait_until(lambda: rig.nav_reader.subscription_matched_status.current_count == 1)
    rig.nav_writer.write(rig.sample("platform_init_status", 22))
    receive(rig.nav_reader, 22)
    receive(rig.status_reader, 22)
    pilot.command("shutdown")
    stats = pilot.stats()
    assert stats["counts"] == {}
    assert stats["stats_sources"] == 0
    for name in ("control", "platform"):
        assert all(value == 0 for value in stats[name].values())


def test_detach_under_active_traffic(pilot, rig):
    stopped = threading.Event()
    errors = []

    def send():
        try:
            sequence = 100
            while not stopped.is_set():
                rig.nav_writer.write(rig.sample("platform_init_status", sequence))
                sequence += 1
                stopped.wait(0.01)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=send)
    thread.start()
    try:
        wait_until(lambda: pilot.stats()["counts"]["nav_global"] >= 3)
        pilot.command("unbind nav_c2 3")
        before = pilot.stats()["counts"]["nav_global"]
        wait_until(lambda: pilot.stats()["counts"]["nav_global"] > before)
        pilot.command("unbind nav_global 4")
        wait_until(lambda: rig.nav_writer.publication_matched_status.current_count == 0)
        take_valid(rig.status_reader)
        time.sleep(0.3)
        assert take_valid(rig.status_reader) == []
        assert pilot.stats()["platform"]["writers"] == 0
        assert pilot.stats()["errors"] == 0
        wait_until(lambda: any(item.info.state.instance_state == dds.InstanceState.NOT_ALIVE_NO_WRITERS
                               for item in rig.nav_reader.take()))
    finally:
        stopped.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert errors == []


def test_restored_lan_writer_does_not_loop_back_to_wan(pilot, rig):
    pilot.command("bind nav_local_destination 40")
    local_reader = rig.reader(1, "PlatformInitStatus", "platform_init_status", "lan_status_1s")
    remote_writer = rig.writer(2, "Status", "WanStatus", "wan_union_status")
    wait_until(lambda: remote_writer.publication_matched_status.current_count == 3)
    local = rig.sample("platform_init_status", 50)
    local["source"] = "OtherBoat"
    local["source_node"] = "OtherBoat"
    frame = dds.DynamicData(rig.types.type("WanStatus"))
    frame["source_id"] = "OtherBoat"
    frame["instance_id"] = "OtherBoat"
    frame["schema_version"] = 3
    frame["original_topic"] = "PlatformInitStatus"
    frame["audit_sequence"] = 50
    with frame.loan_value("payload") as payload:
        with payload.data.loan_value("navigation") as navigation:
            for field in ("source", "source_node", "run_id", "audit_sequence", "sent_at_ns",
                          "timestamp", "latitude", "longitude", "speed_knots", "mission_id"):
                navigation.data[field] = local[field]
    remote_writer.write(frame)
    assert receive(local_reader, 50) == local
    receive(rig.nav_reader, 50)
    frames = []

    def complete():
        frames.extend(take_valid(rig.status_reader))
        return any(sample["audit_sequence"] == 50 for sample in frames)

    wait_until(complete)
    time.sleep(0.3)
    frames.extend(take_valid(rig.status_reader))
    assert len([sample for sample in frames if sample["audit_sequence"] == 50]) == 1
    counts = pilot.stats()["counts"]
    assert counts["nav_c2"] == counts["nav_global"] == 0
    assert counts["nav_local_destination"] == 1
    assert pilot.stats()["errors"] == 0


def take_states(reader, field):
    states = {}
    for item in reader.take():
        key = item.data if item.info.valid else reader.key_value(item.info.instance_handle)
        states[key[field]] = item.info.state.instance_state
    return states


def wait_state(reader, field, key, state):
    seen = {}

    def complete():
        seen.update(take_states(reader, field))
        return seen.get(key) == state

    wait_until(complete, context=lambda: f"key {key}; wanted {state}; observed {seen}")


def test_native_lifecycle_shared_routes_and_multiple_input_writers(pilot, rig):
    first = rig.sample("platform_init_status", 301)
    second = rig.sample("platform_init_status", 302)
    other_writer = rig.writer(1, "PlatformInitStatus", "platform_init_status", "lan_status_1s")
    wait_until(lambda: other_writer.publication_matched_status.current_count == 1)
    for writer, sample in ((rig.nav_writer, first), (other_writer, second)):
        time.sleep(1.05)
        writer.write(sample)
        receive(rig.nav_reader, sample["audit_sequence"])
        receive(rig.status_reader, sample["audit_sequence"])

    before = pilot.stats()["counts"]
    rig.nav_writer.dispose_instance(rig.nav_writer.lookup_instance(first))
    wait_state(rig.status_reader, "instance_id", "Boat30", dds.InstanceState.NOT_ALIVE_DISPOSED)
    wait_state(rig.nav_reader, "source", "Boat30", dds.InstanceState.NOT_ALIVE_DISPOSED)
    assert pilot.stats()["counts"] == before

    second["audit_sequence"] = 303
    time.sleep(1.05)
    other_writer.write(second)
    receive(rig.nav_reader, 303)
    receive(rig.status_reader, 303)
    before = pilot.stats()["counts"]
    rig.nav_writer.unregister_instance(rig.nav_writer.lookup_instance(first))
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        for reader, field in ((rig.nav_reader, "source"), (rig.status_reader, "instance_id")):
            assert take_states(reader, field).get("Boat30") != dds.InstanceState.NOT_ALIVE_NO_WRITERS
        time.sleep(0.02)
    assert pilot.stats()["counts"] == before

    pilot.command("unbind nav_c2 3")
    pilot.command("unbind nav_destination 5")
    assert rig.nav_reader.subscription_matched_status.current_count == 1
    other_writer.unregister_instance(other_writer.lookup_instance(second))
    wait_state(rig.status_reader, "instance_id", "Boat30", dds.InstanceState.NOT_ALIVE_NO_WRITERS)
    wait_state(rig.nav_reader, "source", "Boat30", dds.InstanceState.NOT_ALIVE_NO_WRITERS)
    after = pilot.stats()
    assert after["counts"]["nav_global"] == before["nav_global"]
    assert after["counts"]["nav_destination_alias"] == before["nav_destination_alias"]
    assert after["errors"] == after["warnings"] == 0


@pytest.mark.parametrize("operation", ["dispose", "unregister"])
def test_native_lifecycle_after_filter_change_and_unknown_output(pilot, rig, operation):
    original = rig.sample("control_command", 401)
    rig.command_writer.write(original)
    receive(rig.command_reader, 401)
    frame = receive(rig.event_reader, 401)
    remote = rig.writer(2, "Event", "WanEvent", "wan_event")
    wait_until(lambda: remote.publication_matched_status.current_count >= 2)

    def injected(source, destination, sequence):
        result = dds.DynamicData(frame)
        result["source_id"] = source
        result["instance_id"] = source
        result["audit_sequence"] = sequence
        result["audience.destination_ids"] = [destination]
        result["payload.command.source"] = source
        result["payload.command.destination"] = destination
        result["payload.command.audit_sequence"] = sequence
        return result

    known = injected("KnownRemote", "Boat30", 402)
    remote.write(known)
    receive(rig.command_reader, 402)
    take_valid(rig.event_reader)
    pilot.command("filter_command OtherBoat")
    handle = remote.lookup_instance(known)
    if operation == "dispose":
        remote.dispose_instance(handle)
        state = dds.InstanceState.NOT_ALIVE_DISPOSED
    else:
        remote.unregister_instance(handle)
        state = dds.InstanceState.NOT_ALIVE_NO_WRITERS
    wait_state(rig.command_reader, "source", "KnownRemote", state)

    pilot.command("filter_command Boat30")
    excluded = injected("NeverMaterialized", "OtherBoat", 403)
    remote.write(excluded)
    wait_until(lambda: any(item["audit_sequence"] == 403 for item in take_valid(rig.event_reader)))
    handle = remote.lookup_instance(excluded)
    if operation == "dispose":
        remote.dispose_instance(handle)
    else:
        remote.unregister_instance(handle)
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        assert "NeverMaterialized" not in take_states(rig.command_reader, "source")
        time.sleep(0.02)
    known["audit_sequence"] = 404
    known["payload.command.audit_sequence"] = 404
    remote.write(known)
    receive(rig.command_reader, 404)
    assert pilot.stats()["errors"] == 0


@pytest.mark.parametrize("operation", ["dispose", "unregister"])
def test_native_lifecycle_with_queued_valid_data(pilot, rig, operation):
    expected = {f"Burst-{index}" for index in range(8)}
    for index, key in enumerate(sorted(expected)):
        sample = rig.sample("control_command", 500 + index)
        sample["source"] = key
        rig.command_writer.write(sample)
        handle = rig.command_writer.lookup_instance(sample)
        if operation == "dispose":
            rig.command_writer.dispose_instance(handle)
        else:
            rig.command_writer.unregister_instance(handle)
    state = (dds.InstanceState.NOT_ALIVE_DISPOSED if operation == "dispose"
             else dds.InstanceState.NOT_ALIVE_NO_WRITERS)
    seen = {}

    def complete():
        seen.update(take_states(rig.command_reader, "source"))
        return all(seen.get(key) == state for key in expected)

    wait_until(complete, context=lambda: f"wanted {state}; observed {seen}; stats={pilot.stats()}")
    assert pilot.stats()["errors"] == 0


def test_malformed_lifecycle_does_not_stop_later_samples(pilot, rig):
    sample = rig.sample("control_command", 601)
    rig.command_writer.write(sample)
    receive(rig.command_reader, 601)
    frame = receive(rig.event_reader, 601)
    remote = rig.writer(2, "Event", "WanEvent", "wan_event")
    wait_until(lambda: remote.publication_matched_status.current_count >= 2)
    frame["source_id"] = "MalformedRemote"
    frame["original_topic"] = "UnsupportedTopic"
    remote.write(frame)
    time.sleep(0.3)
    assert pilot.stats()["errors"] == 0, "Unsupported topic should be filtered before decoding"
    assert not any(s.info.valid for s in rig.command_reader.take())
    frame["original_topic"] = "ControlCommand"
    frame["source_id"] = ""
    remote.write(frame)
    wait_until(lambda: pilot.stats()["errors"] >= 1)
    before = pilot.stats()["errors"]
    remote.dispose_instance(remote.lookup_instance(frame))
    wait_until(lambda: pilot.stats()["errors"] > before)
    sample["audit_sequence"] = 602
    rig.command_writer.write(sample)
    receive(rig.command_reader, 602)
    receive(rig.event_reader, 602)


def test_dispose_then_unregister_uses_native_reader_transition_policy(pilot, rig):
    sample = rig.sample("control_command", 801)
    source_observer = rig.reader(0, "ControlCommand", "control_command", "lan_event")
    wait_until(lambda: source_observer.subscription_matched_status.current_count == 1)
    rig.command_writer.write(sample)
    receive(source_observer, 801)
    receive(rig.command_reader, 801)
    receive(rig.event_reader, 801)
    handle = rig.command_writer.lookup_instance(sample)
    rig.command_writer.dispose_instance(handle)
    for reader, field in ((source_observer, "source"), (rig.event_reader, "instance_id"),
                          (rig.command_reader, "source")):
        wait_state(reader, field, "Control20", dds.InstanceState.NOT_ALIVE_DISPOSED)
    rig.command_writer.unregister_instance(handle)
    states = {"input": [], "wan": [], "destination": []}
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        for name, reader, field in (
                ("input", source_observer, "source"),
                ("wan", rig.event_reader, "instance_id"),
                ("destination", rig.command_reader, "source")):
            found = take_states(reader, field)
            if "Control20" in found:
                states[name].append(found["Control20"])
        time.sleep(0.02)
    if dds.InstanceState.NOT_ALIVE_NO_WRITERS in states["input"]:
        assert dds.InstanceState.NOT_ALIVE_NO_WRITERS in states["wan"]
        assert dds.InstanceState.NOT_ALIVE_NO_WRITERS in states["destination"]
    else:
        assert all(dds.InstanceState.NOT_ALIVE_NO_WRITERS not in values
                   for values in states.values()), states
    sample["audit_sequence"] = 802
    rig.command_writer.write(sample)
    receive(rig.command_reader, 802)
    receive(rig.event_reader, 802)
    assert pilot.stats()["errors"] == 0
