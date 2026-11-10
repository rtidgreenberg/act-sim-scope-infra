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


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for adapter pilot")


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
        reader = dds.DynamicData.DataReader(
            self.participants[index], self.topic(index, name, type_name),
            self.qos.datareader_qos_from_profile("ACT_QOS_LIB::" + profile),
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

    wait_until(complete)
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
        wait_until(lambda: pilot.stats()["lifecycle_pending"] >= 1)
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
    frame["schema_version"] = 2
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
