"""Event/State/Telemetry use unchanged payloads, QoS and LAN routing checks."""

import signal
import time

import rti.connextdds as dds

from util.dds_probe import reader_qos, writer_qos
from test_two_topic_controller import (
    mesh,
    wan_contract,
    alive,
    ready,
    test_all_application_cases_and_only_two_wan_topic_names as test_all_cases_and_three_topics,
    test_multiple_contacts_preserve_system_origin_and_instance_identity as test_contact_identity,
    test_application_instance_lifecycle_through_route as test_application_lifecycle,
    test_contact_producer_aliases_share_canonical_lifecycle_key as test_contact_alias_lifecycle,
    test_team_updates_exact_interests_and_source_alias_gates as test_team_and_alias_gates,
    test_addressed_and_broadcast_control_survive_team_changes as test_addressed_and_broadcast,
    test_wire_readiness_and_wrong_original_type_are_explicit as test_wire_readiness,
    test_status_health_presence_rtt_stale_dead_and_restart as test_shared_health_and_restart,
)
from test_wan_filter_observations import (
    test_reliable_config_snapshots_leave_every_heartbeat_compact as test_reliable_config_transport,
)

WAN_CONTRACT = "three_topic_union"


def test_selected_production_qos():
    event = writer_qos("wan_three_topic_event")
    contacts = writer_qos("wan_union_contacts")
    reader = reader_qos("wan_union_contacts")
    assert event.history.kind == dds.HistoryKind.KEEP_ALL
    assert contacts.history.kind == dds.HistoryKind.KEEP_LAST and contacts.history.depth == 1
    assert reader.history.kind == dds.HistoryKind.KEEP_ALL
    assert float(reader.protocol.rtps_reliable_reader.heartbeat_suppression_duration) == 0.0625
    for qos in (event, contacts):
        assert qos.reliability.kind == dds.ReliabilityKind.RELIABLE
        assert qos.publish_mode.flow_controller_name == "dds.flow_controller.token_bucket.wan_priority"
        assert qos.protocol.rtps_reliable_writer.max_bytes_per_nack_response == 524288
        assert qos.multi_channel.filter_name == "DDSPRIFLTR"
        assert [channel.filter_expression for channel in qos.multi_channel.channels] == [
            "@priority >= 100", "@priority < 100"]


def test_event_retention_and_latest_contact_deletion_after_receiver_pause(mesh):
    nodes, _, _ = mesh
    source = nodes["Boat30"][1]
    destination = nodes["Control_20"][1]
    contacts = source.writer("ContactReport", "contact_report", qos=writer_qos("lan_event"))
    events = source.writer("PlatformCommandAck", "control_command_ack", qos=writer_qos("lan_event"))
    contact_reader = destination.reader("ContactReport", "contact_report", qos=reader_qos("lan_event"))
    event_reader = destination.reader("PlatformCommandAck", "control_command_ack", qos=reader_qos("lan_event"))
    ready(nodes, "Boat30", "platform_events")

    samples = [source.sample("contact_report", source="", contact_id=key, run_id="pause",
                             audit_sequence=1) for key in ("latest", "deleted")]
    deadline = time.monotonic() + 20
    seen = set()
    while time.monotonic() < deadline and len(seen) != 2:
        for sample in samples:
            contacts.write(sample)
        for item in contact_reader.take():
            if item.info.valid:
                seen.add(item.data["contact_id"])
        time.sleep(0.05)
    assert seen == {"latest", "deleted"}
    deleted = contact_reader.lookup_instance(destination.sample(
        "contact_report", source="Boat30", contact_id="deleted"))
    event_reader.take()
    process = nodes["Control_20"][0]
    process._popen.send_signal(signal.SIGSTOP)
    try:
        for sequence in range(1, 181):
            events.write(source.sample("control_command_ack", source="Boat30",
                                       run_id="pause", audit_sequence=sequence))
            for sample in samples:
                sample["audit_sequence"] = sequence + 1
                contacts.write(sample)
        contacts.dispose_instance(contacts.lookup_instance(samples[1]))
        time.sleep(3)
    finally:
        process._popen.send_signal(signal.SIGCONT)

    sequences = set()
    latest = None
    disposed = False
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        assert alive(nodes)
        for item in event_reader.take():
            if item.info.valid and item.data["run_id"] == "pause":
                sequences.add(int(item.data["audit_sequence"]))
        for item in contact_reader.take():
            if item.info.valid and item.data["contact_id"] == "latest":
                latest = int(item.data["audit_sequence"])
            if item.info.instance_handle == deleted:
                disposed = item.info.state.instance_state == dds.InstanceState.NOT_ALIVE_DISPOSED
        if sequences == set(range(1, 181)) and latest == 181 and disposed:
            return
        time.sleep(0.05)
    assert sequences == set(range(1, 181)), sorted(set(range(1, 181)) - sequences)
    assert latest == 181
    assert disposed, "The receiver retained a contact ghost after the latest dispose"


def test_delivery_profiles_follow_physical_contact_transport(mesh):
    from gui.mesh_dashboard.server.delivery_config import load_delivery_config

    nodes, _, _ = mesh
    arguments = nodes["Control_20"][0]._popen.args
    path = arguments[arguments.index("--config") + 1]
    config = load_delivery_config(path, dds.QosProvider, dds.Duration.infinite)
    contacts = config["topics"]["ContactReport"]
    assert {entry["profile"] for entry in contacts} == {"ACT_QOS_LIB::wan_union_contacts"}
    for entry in contacts:
        settings = entry["settings"]
        assert settings["reliability"] == "RELIABLE"
        assert settings["history"] == ("KEEP_LAST" if entry["endpoint"] == "writer" else "KEEP_ALL")
        if entry["endpoint"] == "writer":
            assert settings["history_depth"] == 1
    assert {entry["settings"]["reliability"] for entry in config["topics"]["RouterHealth"]} == {"BEST_EFFORT"}
