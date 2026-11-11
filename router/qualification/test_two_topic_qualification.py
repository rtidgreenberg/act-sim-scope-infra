"""SDK qualification only: real UDP peers, not production router migration."""

import itertools
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import rti.connextdds as dds


ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "harness_v2/datamodel/ActTypes.idl"
FIXTURE = Path(__file__).parent / "fixtures/two_topic_qualification.idl"
QOS = ROOT / "harness_v2/qos/act_qos_profiles.xml"
PROFILE = "ACT_QOS_LIB::"
DOMAINS = itertools.count(201)


@pytest.fixture
def unique_domains():
    domain = next(DOMAINS)
    assert domain <= 232
    return {"wan": domain}


def wait_until(predicate, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"Condition did not become true within {timeout}s")


@pytest.fixture(scope="session")
def qualification_types(tmp_path_factory):
    directory = tmp_path_factory.mktemp("two_topic_types")
    generator = Path(os.environ["NDDSHOME"]) / "bin/rtiddsgen"
    for source in (MODEL, FIXTURE):
        subprocess.run(
            [str(generator), "-convertToXml", "-I", str(MODEL.parent),
             "-d", str(directory), str(source)],
            check=True, text=True,
        )
    return dds.QosProvider(str(directory / "two_topic_qualification.xml"))


@pytest.fixture
def peers(unique_domains):
    provider = dds.QosProvider(str(QOS))
    participants = []
    for name in ("qualification/source", "qualification/receiver"):
        qos = provider.participant_qos_from_profile(PROFILE + "wan_two_topic_participant")
        qos.participant_name.name = name
        participants.append(dds.DomainParticipant(unique_domains["wan"], qos))
    try:
        yield participants, provider
    finally:
        for participant in reversed(participants):
            participant.close()


def status(types, branch, sequence=1, instance=None, speed=10):
    data = dds.DynamicData(types.type("QualificationStatus"))
    data["source_node"] = "Boat30"
    data["source_router"] = "router"
    data["instance_id"] = instance or f"{branch}/primary"
    data["session_id"] = 42
    data["audit_sequence"] = sequence
    data["destination"] = "Control20"
    data["team_id"] = "TEAM_A"
    with data.loan_value("payload") as payload:
        with payload.data.loan_value(branch) as active:
            if branch == "navigation":
                active.data["source"] = "Boat30"
                active.data["speed_knots"] = speed
            elif branch == "health":
                active.data["router"] = "Boat30/router"
                active.data["session_id"] = 42
                active.data["heartbeat_seq"] = sequence
    return data


def event(types, branch, sequence):
    data = dds.DynamicData(types.type("QualificationEvent"))
    data["source_node"] = "Control20"
    data["source_router"] = "router"
    data["instance_id"] = "commands"
    data["session_id"] = 42
    data["audit_sequence"] = sequence
    data["destination"] = "Boat30"
    with data.loan_value("payload") as payload:
        with payload.data.loan_value(branch) as active:
            active.data["source"] = "Control20"
            active.data["command_id"] = f"command-{sequence}"
    return data


def sequences(reader):
    return [int(sample.data["audit_sequence"]) for sample in reader.take()
            if sample.info.valid]


def collect_sequences(reader, expected, timeout=10):
    found = []

    def complete():
        found.extend(sequences(reader))
        return len(found) >= expected

    wait_until(complete, timeout)
    return found


def routed_parent(participant, publisher, partition):
    if publisher:
        qos = participant.default_publisher_qos
        if partition is not None:
            qos.partition = dds.Partition([partition])
        return dds.Publisher(participant, qos)
    qos = participant.default_subscriber_qos
    if partition is not None:
        qos.partition = dds.Partition([partition])
    return dds.Subscriber(participant, qos)


@contextmanager
def endpoints(peers, types, family="Status", profile="wan_status",
              expression=None, parameters=(), partition=None, type_name=None):
    participants, qos = peers
    if type_name == "WanStatus" and profile == "wan_status":
        profile = "wan_union_status"
    type_ = types.type(type_name or "Qualification" + family)
    topics = [dds.DynamicData.Topic(p, family, type_) for p in participants]
    publisher = routed_parent(participants[0], True, partition)
    subscriber = routed_parent(participants[1], False, partition)
    cft = None
    if expression is not None:
        cft = dds.DynamicData.ContentFilteredTopic(
            topics[1], family + "_qualification_filter",
            dds.Filter(expression, list(parameters)),
        )
    writer = dds.DynamicData.DataWriter(
        publisher, topics[0], qos.datawriter_qos_from_profile(PROFILE + profile)
    )
    reader = dds.DynamicData.DataReader(
        subscriber, cft if cft is not None else topics[1],
        qos.datareader_qos_from_profile(PROFILE + profile),
    )
    try:
        wait_until(lambda: writer.publication_matched_status.current_count == 1)
        wait_until(lambda: reader.subscription_matched_status.current_count == 1)
        yield writer, reader, cft, publisher, topics
    finally:
        reader.close()
        writer.close()
        if cft is not None:
            cft.close()
        subscriber.close()
        publisher.close()
        for topic in reversed(topics):
            topic.close()


def pushed(writer):
    return int(writer.datawriter_protocol_status.pushed_sample_count.total)


@pytest.mark.parametrize("family,topic,branch,type_name,key,profile", [
    ("Event", "ControlCommand", "command", "control_command", "source", "wan_event"),
    ("Event", "ActTeamAssignment", "team_assignment", "TeamAssignment", "platform_node", "wan_event"),
    ("Event", "ActPlatformStatusMode", "status_mode", "PlatformStatusMode", "platform_node", "wan_event"),
    ("Event", "PlatformCommandAck", "command_ack", "control_command_ack", "source", "wan_event"),
    ("Event", "ContactReport", "contact_report", "contact_report", "contact_id", "wan_event"),
    ("Status", "PlatformInitStatus", "navigation", "platform_init_status", "source", "wan_status"),
    ("Status", "PlatformData", "data", "platform_data", "source", "wan_status"),
    ("Status", "PlatformMissionStatus", "mission", "platform_mission_status", "source", "wan_status"),
    ("Status", "PlatformWaypointStatus", "waypoint", "platform_waypoint_status", "source", "wan_status"),
    ("Status", "PlatformDetailStatus", "detail", "platform_detail_status", "source", "wan_status"),
    ("Status", "PlatformDebugStatus", "debug", "platform_debug_status", "source", "wan_status"),
    ("Status", "PlatformThrusterStatus", "thruster", "platform_thruster_status", "source", "wan_status"),
    ("Status", "PlatformPowerStatus", "power", "platform_power_status", "source", "wan_status"),
    ("Status", "ActRouterDiagnostics", "diagnostics", "RouterDiagnostics", "router", "wan_status"),
    ("Status", "RouterHealth", "health", "RouterHealth", "router", "router_health"),
])
def test_production_cases_round_trip_and_stable_keys(
    peers, qualification_types, family, topic, branch, type_name, key, profile,
):
    if family == "Status":
        profile = "wan_union_status"
    frame = dds.DynamicData(qualification_types.type("Wan" + family))
    frame["source_id"] = "Boat30"
    frame["instance_id"] = "Boat30"
    frame["schema_version"] = 3
    frame["original_topic"] = topic
    frame["audit_sequence"] = 1
    with frame.loan_value("payload") as payload:
        with payload.data.loan_value(branch) as active:
            active.data[key] = "Boat30"
            if type_name == "control_command":
                active.data["payload"] = [0, 1, 127, 255]
                active.data["command_id"] = "command-1"
    with endpoints(
        peers, qualification_types, family=family, profile=profile,
        type_name="Wan" + family, expression=f"original_topic = '{topic}'",
    ) as (writer, reader, _, _, _):
        baseline = pushed(writer)
        frame["original_topic"] = "UnmappedTopic"
        writer.write(frame)
        time.sleep(0.3)
        assert sequences(reader) == []
        assert pushed(writer) == baseline, "Wrong-topic DATA was pushed"
        frame["original_topic"] = topic
        handle = writer.register_instance(frame)
        writer.write(frame)
        received = []

        def delivered():
            received.extend(s.data for s in reader.take() if s.info.valid)
            return bool(received)

        wait_until(delivered)
        assert received[0] == frame, "Production envelope/payload changed on the wire"
        frame["audit_sequence"] = 2
        frame["session_id"] = 99
        frame["membership_revision"] = 10
        with frame.loan_value("audience") as audience:
            audience.data["destination_ids"] = ["TEAM_B"]
        assert writer.register_instance(frame) == handle
        recovered = writer.key_value(handle)
        assert recovered["source_id"] == "Boat30"
        assert recovered["original_topic"] == topic
        assert recovered["instance_id"] == frame["instance_id"]


def test_production_three_key_contact_identity(peers, qualification_types):
    with endpoints(peers, qualification_types, family="Event", profile="wan_event",
                   type_name="WanEvent") as (writer, reader, _, _, _):
        handles = []
        for sequence, (source, contact_id) in enumerate(
                (("Boat30", "contact-101"), ("Boat30", "contact-102"),
                 ("Boat31", "contact-101")), 1):
            frame = dds.DynamicData(qualification_types.type("WanEvent"))
            frame["source_id"] = source
            frame["original_topic"] = "ContactReport"
            frame["instance_id"] = contact_id
            frame["schema_version"] = 3
            frame["audit_sequence"] = sequence
            frame["payload.contact_report.source"] = source
            frame["payload.contact_report.contact_id"] = contact_id
            handle = writer.register_instance(frame)
            assert all(handle != previous for previous in handles)
            handles.append(handle)
            writer.write(frame)
            frame["session_id"] = 99
            frame["audit_sequence"] += 10
            assert writer.register_instance(frame) == handle
            key = writer.key_value(handle)
            assert (key["source_id"], key["original_topic"], key["instance_id"]) == (
                source, "ContactReport", contact_id)
        assert sorted(collect_sequences(reader, 3)) == [1, 2, 3]
        other_topic = dds.DynamicData(qualification_types.type("WanEvent"))
        other_topic["source_id"] = "Boat30"
        other_topic["original_topic"] = "ControlCommand"
        other_topic["instance_id"] = "contact-101"
        assert all(writer.register_instance(other_topic) != handle for handle in handles)


@pytest.mark.parametrize("selector", ["control", "global"])
def test_production_boolean_audience_filter(peers, qualification_types, selector):
    frame = dds.DynamicData(qualification_types.type("WanStatus"))
    frame["source_id"] = "Boat30"
    frame["instance_id"] = "Boat30"
    frame["original_topic"] = "PlatformInitStatus"
    frame["schema_version"] = 3
    frame["audit_sequence"] = 1
    with frame.loan_value("payload") as payload:
        with payload.data.loan_value("navigation") as active:
            active.data["source"] = "Boat30"
    with endpoints(peers, qualification_types, type_name="WanStatus",
                   expression=f"original_topic = 'PlatformInitStatus' AND audience.{selector} = TRUE") as (
            writer, reader, _, _, _):
        baseline = pushed(writer)
        writer.write(frame)
        time.sleep(0.3)
        assert sequences(reader) == []
        assert pushed(writer) == baseline
        frame[f"audience.{selector}"] = True
        frame["audit_sequence"] = 2
        writer.write(frame)
        assert collect_sequences(reader, 1) == [2]


def test_production_mixed_audience_filter(peers, qualification_types):
    frame = dds.DynamicData(qualification_types.type("WanStatus"))
    frame["source_id"] = "Boat30"
    frame["instance_id"] = "Boat30"
    frame["original_topic"] = "PlatformData"
    frame["schema_version"] = 3
    frame["audit_sequence"] = 1
    frame["audience.destination_ids"] = ["Boat30"]
    with frame.loan_value("payload") as payload:
        with payload.data.loan_value("data") as active:
            active.data["source"] = "Boat30"
    team_expression = " OR ".join(f"audience.destination_ids[{index}] = %1" for index in range(16))
    expression = f"source_id <> %0 AND (original_topic = 'PlatformData' AND ({team_expression}))"
    parameters = ["'Boat31'", "'Boat31'"]
    with endpoints(peers, qualification_types, type_name="WanStatus",
                   expression=expression,
                   parameters=parameters) as (writer, reader, cft, _, _):
        baseline = pushed(writer)
        writer.write(frame)
        time.sleep(0.3)
        assert sequences(reader) == []
        assert pushed(writer) == baseline
        cft.set_filter(dds.Filter(
            "source_id <> %0 AND ((original_topic = 'PlatformInitStatus' AND audience.global = TRUE) OR "
            f"(original_topic = 'PlatformData' AND ({team_expression})))", parameters))
        frame["audit_sequence"] = 2
        writer.write(frame)
        time.sleep(0.3)
        assert sequences(reader) == []
        assert pushed(writer) == baseline
        frame["audience.destination_ids"] = ["Boat31"]
        frame["audit_sequence"] = 3
        writer.write(frame)
        assert collect_sequences(reader, 1) == [3]


def test_full_team_set_exact_membership_sender_filter(peers, qualification_types):
    expression = " OR ".join(
        f"audience.destination_ids[{index}] = %{interest}"
        for index in range(16) for interest in range(16)
    )
    parameters = ["'TEAM_A'"] + [f"'INTEREST_{index}'" for index in range(1, 16)]

    def sample(sequence, teams, branch="data"):
        frame = dds.DynamicData(qualification_types.type("WanStatus"))
        frame["source_id"] = "Boat30"
        frame["instance_id"] = "Boat30"
        frame["schema_version"] = 3
        frame["original_topic"] = "PlatformData" if branch == "data" else "RouterHealth"
        frame["audit_sequence"] = sequence
        with frame.loan_value("audience") as audience:
            audience.data["destination_ids"] = teams
        with frame.loan_value("payload") as payload:
            with payload.data.loan_value(branch) as active:
                active.data["source" if branch == "data" else "router"] = "Boat30"
        return frame

    with endpoints(
        peers, qualification_types, type_name="WanStatus",
        expression=f"original_topic = 'PlatformData' AND ({expression})", parameters=parameters,
    ) as (writer, reader, cft, _, _):
        baseline = pushed(writer)
        for sequence, teams in enumerate(([], ["TEAM_B"], ["TEAM_A_extra"]), 1):
            writer.write(sample(sequence, teams))
        writer.write(sample(4, ["TEAM_A"], "health"))
        time.sleep(0.4)
        assert sequences(reader) == []
        assert pushed(writer) == baseline, "Nonmember DATA was pushed"
        for sequence, teams in ((10, ["TEAM_A"]), (11, ["TEAM_B"] * 15 + ["TEAM_A"])):
            writer.write(sample(sequence, teams))
            assert collect_sequences(reader, 1) == [sequence]
        for interest in range(16):
            teams = ["NONMEMBER"] * 16
            teams[interest] = parameters[interest][1:-1]
            writer.write(sample(100 + interest, teams))
            assert collect_sequences(reader, 1) == [100 + interest]
        assert pushed(writer) == baseline + 18
        parameters[0] = "'TEAM_B'"
        cft.filter_parameters = parameters
        received = []

        def updated():
            writer.write(sample(12, ["TEAM_B"]))
            received.extend(sequences(reader))
            return 12 in received

        wait_until(updated)
        reader.take()
        time.sleep(0.2)
        baseline = pushed(writer)
        writer.write(sample(13, ["TEAM_A"]))
        time.sleep(0.4)
        assert sequences(reader) == []
        assert pushed(writer) == baseline, "Old-team DATA was pushed after convergence"


def test_keys_keep_variants_and_sensors_independent(peers, qualification_types):
    with endpoints(peers, qualification_types) as (writer, reader, _, _, _):
        first = status(qualification_types, "navigation", 1)
        changed = status(qualification_types, "navigation", 2)
        health = status(qualification_types, "health", 3)
        sensor = status(qualification_types, "navigation", 4, "navigation/sensor-2")
        handles = [writer.register_instance(s) for s in (first, changed, health, sensor)]
        assert handles[0] == handles[1]
        assert len({str(handles[i]) for i in (0, 2, 3)}) == 3
        key = writer.key_value(handles[2])
        assert key["instance_id"] == "health/primary"
        for sample in (first, changed, health, sensor):
            writer.write(sample)
        assert sorted(collect_sequences(reader, 4)) == [1, 2, 3, 4]
        assert writer.datawriter_cache_status.alive_instance_count == 3


def test_sender_suppresses_union_and_audience_then_updates_filter(peers, qualification_types):
    with endpoints(
        peers, qualification_types,
        expression="payload._d = %0 AND destination = %1 AND team_id = %2",
        parameters=("1", "'Control20'", "'TEAM_A'"),
    ) as (writer, reader, cft, _, _):
        baseline = pushed(writer)
        for sequence in range(1, 21):
            sample = status(qualification_types, "health", sequence)
            writer.write(sample)
            sample = status(qualification_types, "navigation", sequence)
            sample["destination"] = "OtherControl"
            writer.write(sample)
            sample["destination"] = "Control20"
            sample["team_id"] = "TEAM_B"
            writer.write(sample)
        time.sleep(0.4)
        assert pushed(writer) == baseline, "Unselected application DATA was pushed"
        assert sequences(reader) == []
        writer.write(status(qualification_types, "navigation", 100))
        assert collect_sequences(reader, 1) == [100]
        assert pushed(writer) == baseline + 1
        cft.filter_parameters = ["2", "'Control20'", "'TEAM_A'"]

        def updated():
            writer.write(status(qualification_types, "health", 200))
            return 200 in sequences(reader)

        wait_until(updated)
        before_rejected = pushed(writer)
        for sequence in range(300, 320):
            writer.write(status(qualification_types, "navigation", sequence))
        time.sleep(0.4)
        assert pushed(writer) == before_rejected
        assert set(sequences(reader)) <= {200}


def test_filter_inside_production_union_branch(peers, qualification_types):
    with endpoints(
        peers, qualification_types,
        expression="payload._d = %0 AND payload.navigation.speed_knots > %1",
        parameters=("1", "5"),
    ) as (writer, reader, _, _, _):
        baseline = pushed(writer)
        writer.write(status(qualification_types, "navigation", 1, speed=3))
        writer.write(status(qualification_types, "health", 2))
        time.sleep(0.3)
        assert pushed(writer) == baseline
        writer.write(status(qualification_types, "navigation", 3, speed=8))
        assert collect_sequences(reader, 1) == [3]
        assert pushed(writer) == baseline + 1


def test_event_commands_and_acks_survive_pending_backlog(peers, qualification_types):
    with endpoints(
        peers, qualification_types, family="Event", profile="wan_event"
    ) as (writer, reader, _, publisher, _):
        assert writer.qos.history.kind == dds.HistoryKind.KEEP_ALL
        assert reader.qos.history.kind == dds.HistoryKind.KEEP_ALL
        assert os.environ.get("ACT_TWO_TOPIC_ISOLATED") == "1" and Path("/.dockerenv").exists(), (
            "Run through router/qualification/run_checks.sh: packet impairment "
            "must never run in the host network namespace"
        )
        subprocess.run(
            ["tc", "qdisc", "add", "dev", "lo", "root", "netem", "loss", "100%"],
            check=True,
        )
        try:
            for sequence in range(1, 21):
                branch = "command" if sequence % 2 else "ack"
                writer.write(event(qualification_types, branch, sequence))
            time.sleep(0.1)
            assert writer.datawriter_cache_status.sample_count == 20
            assert writer.datawriter_cache_status.alive_instance_count == 1
            assert sequences(reader) == [], "The impairment did not block delivery"
        finally:
            subprocess.run(["tc", "qdisc", "del", "dev", "lo", "root"], check=True)
        assert sorted(collect_sequences(reader, 20)) == list(range(1, 21))
        writer.wait_for_acknowledgments(dds.Duration(10))


@pytest.mark.parametrize("partition", [None, "qualification-health"])
@pytest.mark.parametrize("application_profile", ["wan_status", "router_health"])
def test_health_deadline_not_masked_by_other_status(
    peers, qualification_types, partition, application_profile
):
    with endpoints(
        peers, qualification_types, profile="router_health",
        expression="payload._d = 2", partition=partition,
    ) as (health_writer, reader, _, _, topics):
        participants, qos = peers
        application = dds.DynamicData.DataWriter(
            dds.Publisher(participants[0]), topics[0],
            qos.datawriter_qos_from_profile(PROFILE + application_profile),
        )
        try:
            health = status(qualification_types, "health", 1)
            health_writer.write(health)
            assert collect_sequences(reader, 1) == [1]
            handle = reader.lookup_instance(health)
            reader.requested_deadline_missed_status
            deadline = None
            until = time.monotonic() + 5
            sequence = 2
            while time.monotonic() < until:
                application.write(status(qualification_types, "navigation", sequence))
                sequence += 1
                current = reader.requested_deadline_missed_status
                if current.total_count_change:
                    deadline = current
                time.sleep(0.05)
            assert sequence > 50
            expected_matches = 2 if application_profile == "router_health" and partition is None else 1
            assert reader.subscription_matched_status.current_count == expected_matches
            assert deadline is not None, "Health deadline was masked by other Status traffic"
            assert deadline.last_instance_handle == handle
            assert sequences(reader) == []
            assert reader.liveliness_changed_status.alive_count == expected_matches
            health_writer.write(status(qualification_types, "health", 500))
            assert collect_sequences(reader, 1) == [500]
        finally:
            application.close()


def test_health_instance_disposal_reaches_filtered_reader(peers, qualification_types):
    with endpoints(
        peers, qualification_types, profile="router_health",
        expression="payload._d = 2",
    ) as (writer, reader, _, _, _):
        health = status(qualification_types, "health", 1)
        writer.write(health)
        assert collect_sequences(reader, 1) == [1]
        writer_handle = writer.lookup_instance(health)
        reader_handle = reader.lookup_instance(health)
        key = writer.key_value(writer_handle)
        writer.dispose_instance(writer.lookup_instance(key))
        found = []

        def disposed():
            for sample in reader.take():
                if not sample.info.valid:
                    found.append(sample.info)
            return any(
                info.instance_handle == reader_handle
                and info.state.instance_state == dds.InstanceState.NOT_ALIVE_DISPOSED
                for info in found
            )

        wait_until(disposed)
