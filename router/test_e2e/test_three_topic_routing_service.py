"""Event/State/Periodic processor checks with the existing schema-3 union types."""

from contextlib import ExitStack
import time
import xml.etree.ElementTree as ET

import rti.connextdds as dds

from conftest import REPO_ROOT
from test_routing_service import (
    rs_build,
    rs_contract,
    rs_mesh,
    priority_config,
    test_rs_all_existing_application_cases_and_shared_endpoint_counts,
    test_rs_native_lifecycle,
    test_rs_addressed_filtering_and_malformed_frame_recovery,
    test_rs_priority_defaults_and_overrides,
    test_rs_rejects_invalid_priority_configuration,
    test_rs_periodic_lan_input_is_one_hz_per_instance,
)
from util.dds_probe import reader_qos, writer_qos, write_until_seen
import yaml


RS_WAN_CONTRACT = "three_topic_union"


def test_three_topic_writer_policies():
    for profile, threshold, reliable, history in (
        ("rs_three_topic_event", 500, True, dds.HistoryKind.KEEP_ALL),
        ("rs_three_topic_state", 300, True, dds.HistoryKind.KEEP_LAST),
        ("rs_three_topic_periodic", 400, False, dds.HistoryKind.KEEP_LAST),
    ):
        qos = writer_qos(profile)
        assert qos.publish_mode.kind == dds.PublishModeKind.ASYNCHRONOUS
        assert qos.publish_mode.flow_controller_name == "dds.flow_controller.token_bucket.act_rs"
        assert qos.reliability.kind == (
            dds.ReliabilityKind.RELIABLE if reliable else dds.ReliabilityKind.BEST_EFFORT)
        assert qos.history.kind == history
        if history == dds.HistoryKind.KEEP_LAST:
            assert qos.history.depth == 1
        assert [channel.filter_expression for channel in qos.multi_channel.channels] == [
            f"@priority >= {threshold}", f"@priority < {threshold}"]
    assert reader_qos("wan_union_contacts").history.kind == dds.HistoryKind.KEEP_ALL


def test_six_priority_classes_overtake_pending_lower_classes(priority_config, unique_domains,
                                                           e2e_tmp_dir):
    result = priority_config({"topic_priorities": {"PlatformCommandAck": "normal"}})
    assert result.returncode == 0, result.stderr
    priorities = yaml.safe_load(result.stdout)["publication_priorities"]
    result = priority_config({"topic_priorities": {"ContactReport": "high"}})
    assert result.returncode == 0, result.stderr
    state_high = yaml.safe_load(result.stdout)["publication_priorities"]["ContactReport"]
    queued = [
        ("Periodic", "PlatformStatus", "navigation", "source", priorities["PlatformStatus"]),
        ("State", "ContactReport", "contact_report", "contact_id", priorities["ContactReport"]),
        ("Event", "PlatformCommandAck", "command_ack", "source", priorities["PlatformCommandAck"]),
        ("State", "ContactReport", "contact_report", "contact_id", state_high),
        ("Periodic", "RouterHealth", "health", "router", priorities["RouterHealth"]),
        ("Event", "Mission", "command", "source", priorities["Mission"]),
    ]
    assert [entry[4] for entry in queued] == [100, 200, 300, 400, 500, 600]
    root = ET.parse(REPO_ROOT / "harness_v2/qos/act_qos_profiles.xml").getroot()
    fixture = ET.parse(REPO_ROOT / "router/routing_service/fixtures/wan_priority.xml").getroot()
    root.extend(fixture.findall("qos_library"))
    path = e2e_tmp_dir / "six-priorities.xml"
    ET.ElementTree(root).write(path)
    provider = dds.QosProvider(str(path))
    types = dds.QosProvider(str(REPO_ROOT / "harness_v2/datamodel/gen/ActTypes.xml"))
    with ExitStack() as cleanup:
        participant = dds.DomainParticipant(
            unique_domains["wan"],
            provider.participant_qos_from_profile("ACT_RS_PRIORITY_TEST::on_demand"))
        cleanup.callback(participant.close)
        publisher = dds.Publisher(participant)
        subscriber = dds.Subscriber(participant)
        controller = participant.find_flow_controller("dds.flow_controller.token_bucket.act_rs")
        cleanup.callback(controller.close)
        endpoints = {}
        for family, type_name, profile in (
            ("Event", "WanEvent", "wan_three_topic_event"),
            ("State", "WanEvent", "wan_union_contacts"),
            ("Periodic", "WanStatus", "wan_union_periodic"),
        ):
            topic = dds.DynamicData.Topic(participant, family, types.type(type_name))
            reader = dds.DynamicData.DataReader(
                subscriber, topic, provider.datareader_qos_from_profile("ACT_QOS_LIB::" + profile))
            writer = dds.DynamicData.DataWriter(
                publisher, topic, provider.datawriter_qos_from_profile(
                    "ACT_QOS_LIB::rs_three_topic_" + family.lower()))
            cleanup.callback(reader.close)
            cleanup.callback(writer.close)
            endpoints[family] = (writer, reader)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not all(
                writer.publication_matched_status.current_count == 1
                for writer, _ in endpoints.values()):
            time.sleep(0.01)
        assert all(w.publication_matched_status.current_count == 1 for w, _ in endpoints.values())
        for family, original, branch, key, priority in queued:
            for sequence in (1, 2):
                frame = dds.DynamicData(types.type("WanStatus" if family == "Periodic" else "WanEvent"))
                frame["source_id"] = "Boat30"
                frame["original_topic"] = original
                frame["instance_id"] = f"{priority}-{sequence}"
                frame["schema_version"] = 3
                frame["audit_sequence"] = sequence
                frame["run_id"] = str(priority)
                with frame.loan_value("payload") as payload:
                    with payload.data.loan_value(branch) as value:
                        value.data[key] = f"{priority}-{sequence}"
                params = dds.WriteParams()
                params.priority = priority
                endpoints[family][0].write(frame, params)
        assert all(not reader.take() for _, reader in endpoints.values())
        received = []
        deadline = time.monotonic() + 10
        while len(received) < 12 and time.monotonic() < deadline:
            controller.trigger_flow()
            time.sleep(0.03)
            for _, reader in endpoints.values():
                received.extend(
                    (info.reception_timestamp.sec * 1000000000 + info.reception_timestamp.nanosec,
                     int(data["run_id"]), int(data["audit_sequence"]))
                    for data, info in reader.take() if info.valid)
        assert [(priority, sequence) for _, priority, sequence in sorted(received)] == [
            (priority, sequence) for priority in (600, 500, 400, 300, 200, 100)
            for sequence in (1, 2)]


def test_contact_frames_only_use_state(rs_mesh):
    nodes, observer = rs_mesh
    source = nodes["Boat30"][1]
    destination = nodes["Control_20"][1]
    source_writer = source.writer("ContactReport", "contact_report", writer_qos("lan_event"))
    wan_reader = observer.reader("State", "WanEvent", reader_qos("wan_union_contacts"))
    destination_reader = destination.reader("ContactReport", "contact_report",
                                             reader_qos("lan_event"))
    sample = source.sample("contact_report", source="Boat30", contact_id="family",
                           run_id="family", audit_sequence=1)
    seen = write_until_seen(lambda: source_writer.write(sample), wan_reader,
                            lambda data: data["instance_id"], "family",
                            check_alive=lambda: all(p.is_alive() for p, _ in nodes.values()))
    assert seen.get("family")
    frame = seen["family"][0]
    frame["source_id"] = "Injected"
    frame["instance_id"] = "wrong-family"
    with frame.loan_value("payload") as payload:
        with payload.data.loan_value("contact_report") as contact:
            contact.data["contact_id"] = "wrong-family"
    wrong = observer.writer("Event", "WanEvent", writer_qos("wan_three_topic_event"))
    correct = observer.writer("State", "WanEvent", writer_qos("wan_union_contacts"))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and any(
            writer.publication_matched_status.current_count == 0 for writer in (wrong, correct)):
        time.sleep(0.02)
    assert all(w.publication_matched_status.current_count > 0 for w in (wrong, correct))
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        wrong.write(frame)
        assert not any(info.valid and data["contact_id"] == "wrong-family"
                       for data, info in destination_reader.take())
        time.sleep(0.02)
    seen = write_until_seen(lambda: correct.write(frame), destination_reader,
                            lambda data: data["contact_id"], "wrong-family",
                            check_alive=lambda: all(p.is_alive() for p, _ in nodes.values()))
    assert seen.get("wrong-family")
    assert seen["wrong-family"][0]["source"] == "Injected"
