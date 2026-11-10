"""Opt-in controller routing over Event/Status, including router-owned contact origins."""

import re
import time
from collections import Counter
from contextlib import ExitStack

import pytest
import rti.connextdds as dds

from conftest import render_config, set_wan_qos_env, start_router, wait_for_mutual_discovery
from util.dds_probe import (
    AdminChannel, COMMAND_KIND, Probe, reader_qos, wait_for_route, write_until_seen,
)

CASES = [
    ("ControlCommand", "control_command", "source", "control"),
    ("ActTeamAssignment", "TeamAssignment", "platform_node", "control"),
    ("ActPlatformStatusMode", "PlatformStatusMode", "platform_node", "control"),
    ("PlatformCommandAck", "control_command_ack", "source", "platform"),
    ("ContactReport", "contact_report", "contact_id", "platform"),
    ("PlatformInitStatus", "platform_init_status", "source", "platform"),
    ("PlatformData", "platform_data", "source", "platform"),
    ("PlatformMissionStatus", "platform_mission_status", "source", "platform"),
    ("PlatformWaypointStatus", "platform_waypoint_status", "source", "platform"),
    ("PlatformDetailStatus", "platform_detail_status", "source", "platform"),
    ("PlatformDebugStatus", "platform_debug_status", "source", "platform"),
    ("PlatformThrusterStatus", "platform_thruster_status", "source", "platform"),
    ("PlatformPowerStatus", "platform_power_status", "source", "platform"),
    ("ActRouterDiagnostics", "RouterDiagnostics", "router", "platform"),
]


@pytest.fixture
def mesh(router_binary, admin_types_xml, e2e_tmp_dir, unique_domains, extra_domains, monkeypatch):
    set_wan_qos_env()
    for index in range(1, 12):
        monkeypatch.setenv("WAN_PEER" + (str(index) if index > 1 else ""),
                           "[0-15]@builtin.udpv4://127.0.0.1")
    processes = []
    cleanup = ExitStack()
    provider = dds.QosProvider(str(admin_types_xml))
    nodes = {}
    try:
        wan = Probe(unique_domains["wan"], participant_profile="wan_two_topic_participant",
                    participant_partition=["SHARED"])
        cleanup.callback(wan.close)
        publication_reader = wan.participant.publication_reader
        subscription_reader = wan.participant.subscription_reader
        for node, role, domains, admin_participant in (
            ("Control_20", "control", unique_domains, "control_lan"),
            ("Boat30", "platform", unique_domains, "platform_lan"),
            ("Boat31", "platform", dict(unique_domains, platform_lan=extra_domains["platform_lan"]),
             "platform_lan"),
        ):
            work = e2e_tmp_dir / node
            work.mkdir()
            path = render_config("control-platform.yaml", domains, work)
            text = path.read_text().replace("router:\n", "router:\n  wan_contract: two_topic_union\n", 1)
            text = text.replace("ACT_QOS_LIB::wan_router_participant\n",
                                "ACT_QOS_LIB::wan_two_topic_participant\n")
            text = text.replace("ACT_QOS_LIB::wan_status\n",
                                "ACT_QOS_LIB::wan_union_status\n")
            path.write_text(text)
            process = start_router(router_binary, path, role, work, node_name=node, name="phase4",
                                   admin_participant=admin_participant)
            processes.append(process)
            cleanup.callback(process.stop)
            probe = Probe(domains[admin_participant])
            cleanup.callback(probe.close)
            nodes[node] = (process, probe, AdminChannel(probe, provider))
        assert all(wait_for_mutual_discovery(processes[0], process)
                   == (True, True) for process in processes[1:])
        yield nodes, wan, provider
    finally:
        cleanup.close()


def alive(nodes):
    return all(process.is_alive() for process, _, _ in nodes.values())


def command(nodes, provider, node, kind, command_id, route="", participant="", partition="",
            subscriber="", publisher=""):
    process, _, admin = nodes[node]
    sample = dds.DynamicData(provider.type("RouterCommand"))
    for name, value in {
        "target_node": node, "target_router": "phase4", "command_id": command_id,
        "kind": COMMAND_KIND[kind], "route_name": route, "participant_name": participant,
        "partition_name": partition, "route.input.subscriber_partition": subscriber,
        "route.output.publisher_partition": publisher,
    }.items():
        sample[name] = value
    deadline = time.monotonic() + 15
    ack = None
    while ack is None and time.monotonic() < deadline:
        admin.cmd_writer.write(sample)
        ack = admin.acks.wait(command_id, timeout_s=0.5, check_alive=lambda: alive(nodes))
    assert ack is not None, f"no ack for {command_id}: {process.log_path}"
    return ack


def ready(nodes, node, route):
    process, _, admin = nodes[node]
    facts = wait_for_route(
        admin.status, route, lambda value: value["state"] == "ROUTE_ENABLED"
        and value["input_matched"] > 0 and value["output_matched"] > 0,
        timeout_s=25, check_alive=lambda: alive(nodes))
    assert facts and facts["state"] == "ROUTE_ENABLED", (facts, process.log_path)
    assert facts["input_matched"] > 0 and facts["output_matched"] > 0, facts


def receive(nodes, writer, reader, sample, key="audit_sequence"):
    wanted = sample[key]
    buckets = write_until_seen(
        lambda: writer.write(sample), reader, lambda data: data[key], wanted,
        check_alive=lambda: alive(nodes))
    assert buckets.get(wanted), f"missing {key}={wanted}; logs: {[p.log_path for p, _, _ in nodes.values()]}"
    assert buckets[wanted][0] == sample
    return buckets[wanted][0]


def absent(nodes, writer, reader, sample, window=2.0):
    deadline = time.monotonic() + window
    while time.monotonic() < deadline:
        assert alive(nodes)
        writer.write(sample)
        for received in reader.take():
            if received.info.valid:
                if received.data["audit_sequence"] == sample["audit_sequence"]:
                    for node, (process, _, _) in nodes.items():
                        print(node, "\n".join(
                            line for line in process.log_path.read_text().splitlines()
                            if "participant_created" in line or
                            "wan_first_sample" in line or "wan_filter_" in line or
                            ("wan_interest_applied" in line and "topic=PlatformData" in line)))
                assert received.data["audit_sequence"] != sample["audit_sequence"]
        time.sleep(0.1)


def test_all_application_cases_and_only_two_wan_topic_names(mesh):
    nodes, wan, provider = mesh
    control = nodes["Control_20"][1]
    boat = nodes["Boat30"][1]
    other = nodes["Boat31"][1]
    endpoints = []
    for topic, type_name, key, role in CASES:
        source = control if role == "control" else boat
        destination = other if topic == "PlatformData" else boat if role == "control" else control
        sample = source.sample(type_name, **{key: "Boat30" if key == "platform_node" else topic})
        if key == "source" or topic == "ContactReport":
            sample["run_id"] = "phase4"
            sample["audit_sequence"] = 42
            sample["sent_at_ns"] = 123456789
        if topic == "ContactReport":
            sample["source"] = "Boat30"
            sample["source_node"] = "Boat30"
        if topic == "ControlCommand":
            sample["destination"] = "Boat30"
            sample["payload"] = list(range(256)) * 32
        endpoints.append((source.writer(topic, type_name),
                          destination.reader(topic, type_name), sample, key))
    for node in ("Boat30", "Boat31"):
        assert command(nodes, provider, node, "ADD_PARTICIPANT_PARTITION", f"team-{node}",
                       participant="platform_wan", partition="TEAM_A")["accepted"]
    for route in ("platform_detail_status", "platform_debug_status"):
        assert command(nodes, provider, "Boat30", "ENABLE_ROUTE", f"enable-{route}",
                       route=route)["accepted"]
    for writer, reader, sample, key in endpoints:
        receive(nodes, writer, reader, sample, key)
    deadline = time.monotonic() + 10
    publications = []
    while time.monotonic() < deadline:
        publications = wan.discovered_publications()
        if {item["topic"] for item in publications} == {"Event", "Status"}:
            break
        time.sleep(0.1)
    assert {item["topic"] for item in publications} == {"Event", "Status"}, publications
    for node in nodes:
        assert "wan_route_error" not in nodes[node][0].log_path.read_text()


def test_multiple_contacts_preserve_system_origin_and_instance_identity(mesh):
    nodes, wan, provider = mesh
    contacts = nodes["Control_20"][1].reader("ContactReport", "contact_report")
    events = wan.reader("Event", "WanEvent", qos=reader_qos("wan_event"))
    expected = {}
    handles = []
    endpoints = []

    def receive_contact(writer, sent, received):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert alive(nodes)
            writer.write(sent)
            for item in contacts.take():
                if item.info.valid and item.data["audit_sequence"] == received["audit_sequence"]:
                    assert item.data == received
                    return item.info.instance_handle
            time.sleep(0.1)
        pytest.fail(f"missing contact {received['source']}/{received['contact_id']}")

    for sequence, (node, contact_id) in enumerate(
            (("Boat30", "contact-101"), ("Boat30", "contact-102"), ("Boat31", "contact-101")), 1):
        source = nodes[node][1]
        fields = dict(source_node=node, contact_id=contact_id,
            run_id="contact-instances", audit_sequence=sequence, sent_at_ns=123456789 + sequence)
        sample = source.sample("contact_report", source="", **fields)
        received = source.sample("contact_report", source=node, **fields)
        writer = source.writer("ContactReport", "contact_report")
        handle = receive_contact(writer, sample, received)
        assert all(handle != previous for previous in handles)
        handles.append(handle)
        endpoints.append((writer, sample, received))
        expected[(node, "ContactReport", contact_id)] = sample

    found = {}
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and found.keys() != expected.keys():
        assert alive(nodes)
        for sample in events.take():
            if not sample.info.valid or sample.data["original_topic"] != "ContactReport":
                continue
            frame = sample.data
            key = (frame["source_id"], frame["original_topic"], frame["instance_id"])
            assert key in expected, key
            assert frame["schema_version"] == 2
            assert frame["payload.contact_report"] == expected[key]
            assert frame["run_id"] == expected[key]["run_id"]
            assert frame["audit_sequence"] == expected[key]["audit_sequence"]
            assert frame["sent_at_ns"] == expected[key]["sent_at_ns"]
            found[key] = frame
        time.sleep(0.1)
    assert found.keys() == expected.keys(), found

    for handle, (writer, sample, received) in zip(handles, endpoints):
        sample["source"] = "forged-producer-origin"
        sample["audit_sequence"] += 100
        received["audit_sequence"] += 100
        assert receive_contact(writer, sample, received) == handle


def test_team_updates_exact_interests_and_source_alias_gates(mesh):
    nodes, wan, provider = mesh
    boat, other, control = (nodes[name][1] for name in ("Boat30", "Boat31", "Control_20"))
    data_writer = boat.writer("PlatformData", "platform_data")
    data_reader = other.reader("PlatformData", "platform_data")
    nav_writer = boat.writer("PlatformInitStatus", "platform_init_status")
    nav_other = other.reader("PlatformInitStatus", "platform_init_status",
                             qos=reader_qos("lan_status_1s"))
    nav_control = control.reader("PlatformInitStatus", "platform_init_status")
    sample = boat.sample("platform_data", source="Boat30", audit_sequence=1)
    ready(nodes, "Boat30", "platform_team_to_wan")
    ready(nodes, "Boat31", "wan_team_to_platform")
    absent(nodes, data_writer, data_reader, sample)
    for node, team in (("Boat30", "TEAM_A"), ("Boat31", "TEAM_AB")):
        assert command(nodes, provider, node, "ADD_PARTICIPANT_PARTITION", f"add-{node}-{team}",
                       participant="platform_wan", partition=team)["accepted"]
    sample["audit_sequence"] = 2
    absent(nodes, data_writer, data_reader, sample)
    assert command(nodes, provider, "Boat31", "ADD_PARTICIPANT_PARTITION", "join-A",
                   participant="platform_wan", partition="TEAM_A")["accepted"]
    sample["audit_sequence"] = 3
    receive(nodes, data_writer, data_reader, sample)
    assert command(nodes, provider, "Boat31", "REMOVE_PARTICIPANT_PARTITION", "leave-A",
                   participant="platform_wan", partition="TEAM_A")["accepted"]
    time.sleep(1)
    sample["audit_sequence"] = 4
    absent(nodes, data_writer, data_reader, sample)
    # Different topics may select different exact teams within the shared Status CFT.
    assert command(nodes, provider, "Boat31", "SET_ROUTE_PARTITION", "route-team",
                   route="wan_team_to_platform", subscriber="TEAM_A")["accepted"]
    sample["audit_sequence"] = 5
    receive(nodes, data_writer, data_reader, sample)
    rejected = command(nodes, provider, "Boat31", "SET_ROUTE_PARTITION", "bad-lan",
                       route="wan_team_to_platform", subscriber="TEAM_A", publisher="LAN_X")
    assert not rejected["accepted"]
    sample["audit_sequence"] = 6
    receive(nodes, data_writer, data_reader, sample)
    ready(nodes, "Control_20", "platform_init_status")
    ready(nodes, "Boat31", "wan_locations_to_platform")
    nav = boat.sample("platform_init_status", source="Boat30", audit_sequence=10)
    receive(nodes, nav_writer, nav_control, nav)
    receive(nodes, nav_writer, nav_other, nav)
    assert command(nodes, provider, "Boat30", "DISABLE_ROUTE", "c2-off",
                   route="platform_init_status")["accepted"]
    nav["audit_sequence"] = 11
    absent(nodes, nav_writer, nav_control, nav)
    receive(nodes, nav_writer, nav_other, nav)
    assert command(nodes, provider, "Boat30", "DISABLE_ROUTE", "global-off",
                   route="platform_locations_to_wan")["accepted"]
    nav["audit_sequence"] = 12
    absent(nodes, nav_writer, nav_other, nav)
    assert command(nodes, provider, "Boat30", "ENABLE_ROUTE", "c2-on",
                   route="platform_init_status")["accepted"]
    nav["audit_sequence"] = 13
    receive(nodes, nav_writer, nav_control, nav)
    absent(nodes, nav_writer, nav_other, nav)
    # Unsupported names reject without disturbing the current working audience.
    assert not command(nodes, provider, "Boat30", "ADD_PARTICIPANT_PARTITION", "quoted-team",
                       participant="platform_wan", partition="O'BRIEN")["accepted"]
    assert not command(nodes, provider, "Boat31", "SET_ROUTE_PARTITION", "quoted-interest",
                       route="wan_team_to_platform", subscriber="O'BRIEN")["accepted"]
    sample["audit_sequence"] = 7
    receive(nodes, data_writer, data_reader, sample)
    rejected = command(nodes, provider, "Boat30", "ADD_PARTICIPANT_PARTITION", "pattern",
                       participant="platform_wan", partition="TEAM_*")
    assert not rejected["accepted"]


def test_addressed_and_broadcast_control_survive_team_changes(mesh):
    nodes, _, provider = mesh
    control = nodes["Control_20"][1]
    boat, other = nodes["Boat30"][1], nodes["Boat31"][1]
    writer = control.writer("ControlCommand", "control_command")
    local = boat.reader("ControlCommand", "control_command")
    remote = other.reader("ControlCommand", "control_command")
    sample = control.sample("control_command", source="Control_20", destination="Boat30",
                            audit_sequence=1)
    receive(nodes, writer, local, sample)
    absent(nodes, writer, remote, sample)
    for node in ("Boat30", "Boat31"):
        assert command(nodes, provider, node, "ADD_PARTICIPANT_PARTITION", f"assign-{node}",
                       participant="platform_wan", partition="TEAM_A")["accepted"]
    sample["audit_sequence"] = 2
    receive(nodes, writer, local, sample)
    absent(nodes, writer, remote, sample)
    assignment_writer = control.writer("ActTeamAssignment", "TeamAssignment")
    assignment_local = boat.reader("ActTeamAssignment", "TeamAssignment")
    assignment_other = other.reader("ActTeamAssignment", "TeamAssignment")
    assignment = control.sample("TeamAssignment", platform_node="Boat30")
    receive(nodes, assignment_writer, assignment_local, assignment, "platform_node")
    receive(nodes, assignment_writer, assignment_other, assignment, "platform_node")
    mode_writer = control.writer("ActPlatformStatusMode", "PlatformStatusMode")
    mode_local = boat.reader("ActPlatformStatusMode", "PlatformStatusMode")
    mode_other = other.reader("ActPlatformStatusMode", "PlatformStatusMode")
    mode = control.sample("PlatformStatusMode", platform_node="Boat30")
    receive(nodes, mode_writer, mode_local, mode, "platform_node")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        mode_writer.write(mode)
        assert not any(sample.info.valid for sample in mode_other.take())
        assert alive(nodes)
        time.sleep(0.1)


def test_wire_readiness_and_wrong_original_type_are_explicit(mesh):
    nodes, _, _ = mesh
    control_proc, control, admin = nodes["Control_20"]
    waiting = wait_for_route(
        admin.status, "control_command", lambda value: value["topic_state"] == "TOPIC_IDLE",
        check_alive=lambda: alive(nodes))
    assert waiting and waiting["topic_state"] == "TOPIC_IDLE"
    wrong = control.writer("ControlCommand", "platform_init_status")
    failed = wait_for_route(
        admin.status, "control_command", lambda value: value["topic_state"] == "TOPIC_ERROR",
        check_alive=lambda: alive(nodes))
    assert failed and failed["topic_state"] == "TOPIC_ERROR", failed
    assert "wire-learned original type differs" in control_proc.log_path.read_text()
    assert wrong is not None and alive(nodes)
    boat = nodes["Boat30"][1]
    writer = boat.writer("PlatformInitStatus", "platform_init_status")
    reader = control.reader("PlatformInitStatus", "platform_init_status")
    receive(nodes, writer, reader, boat.sample("platform_init_status", source="Boat30",
                                             audit_sequence=99))


def test_status_health_presence_rtt_stale_dead_and_restart(mesh, router_binary):
    from test_presence_roster import mesh_reader, wait_mesh, ALIVE, STALE, DEAD
    from test_link_stats import link_stats_reader
    from util.dds_probe import writer_qos

    nodes, wan, provider = mesh
    control = nodes["Control_20"][1]
    mesh_view = mesh_reader(control, provider)
    stats_view = link_stats_reader(control, provider)
    health_view = wan.reader("Status", "WanStatus", qos=reader_qos("wan_union_status"))
    identities = {node + "/phase4" for node in nodes}

    def wait_health(predicate, timeout=20):
        deadline = time.monotonic() + timeout
        frames = {}
        while time.monotonic() < deadline:
            assert alive(nodes)
            for sample in health_view.take():
                if sample.info.valid:
                    frame = sample.data
                    if frame["payload"].discriminator_value != 10:
                        continue
                    health = frame["payload.health"]
                    assert frame["source_id"] == health["router"].split("/")[0]
                    assert frame["session_id"] == health["session_id"] != 0
                    assert frame["audit_sequence"] == health["heartbeat_seq"]
                    assert frame["sent_at_ns"] == health["send_timestamp"]
                    assert frame["original_topic"] == "RouterHealth"
                    assert frame["schema_version"] == 2
                    assert frame["instance_id"] == health["router"]
                    frames[health["router"]] = frame
            if predicate(frames):
                return frames
            time.sleep(0.1)
        raise AssertionError(f"health predicate timed out: {list(frames)}")

    frames = wait_health(lambda frames: identities <= frames.keys() and any(
        int(ref["presence"]) == ALIVE
        for ref in frames["Boat31/phase4"]["payload.health.peers_seen"]))
    old_session = frames["Boat31/phase4"]["session_id"]
    peers = wait_mesh(mesh_view, lambda peers: all(
        peers.get(node + "/phase4", {}).get("presence") == ALIVE
        for node in ("Boat30", "Boat31")), 20, alive=lambda: alive(nodes))
    assert all(peers[node + "/phase4"]["presence"] == ALIVE for node in ("Boat30", "Boat31"))
    publications = [p for p in wan.discovered_publications() if p["topic"] == "Status"]
    counts = Counter(p["participant_key"] for p in publications)
    assert len(counts) == 3 and set(counts.values()) == {1}, publications
    subscriptions = Counter(
        str(data.participant_key.value) for data, info in wan.participant.subscription_reader.read()
        if info.valid and data.topic_name == "Status"
        and str(data.participant_key.value) in counts)
    assert subscriptions == counts, (subscriptions, counts)

    measured, received, pushed = set(), set(), set()
    expected = {"Boat30/phase4", "Boat31/phase4"}
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not (measured == received == pushed == expected):
        assert alive(nodes)
        for sample in stats_view.take():
            if not sample.info.valid:
                continue
            peer = sample.data["peer_router"]
            if sample.data["rtt_valid"] and sample.data["rtt_count"] > 0:
                measured.add(peer)
            if sample.data["samples_received"] > 0:
                received.add(peer)
            if sample.data["pushed_samples"] > 0:
                pushed.add(peer)
        time.sleep(0.1)
    assert measured == received == pushed == expected, (measured, received, pushed)

    probe_writer = wan.writer("Status", "WanStatus", qos=writer_qos("wan_union_status"))
    navigation_reader = control.reader("PlatformInitStatus", "platform_init_status")
    navigation_discovery = control.writer("PlatformInitStatus", "platform_init_status")
    ready(nodes, "Control_20", "platform_init_status")
    frame = dds.DynamicData(frames["Boat31/phase4"])
    frame["source_id"] = "ProbeNode"
    frame["instance_id"] = "ProbeNode/phase4"
    frame["payload.health.router"] = "ProbeNode/phase4"
    reported_ages = {
        str(ref["router"]): int(ref["last_seen_delta_ms"])
        for ref in frame["payload.health.peers_seen"] if int(ref["presence"]) == ALIVE
    }

    def probe_snapshot():
        snapshot = None
        for sample in mesh_view.read():
            if sample.info.valid:
                for peer in sample.data["peers"]:
                    if peer["health.router"] == "ProbeNode/phase4":
                        snapshot = {
                            "activity_age": int(peer["last_seen_delta_ms"]),
                            "edges": {
                                str(ref["router"]): int(ref["last_seen_delta_ms"])
                                for ref in peer["health.peers_seen"]
                            },
                        }
        return snapshot

    for _ in range(3):
        probe_writer.write(frame)
        time.sleep(0.5)
    status_writer = probe_writer
    navigation = wan.sample(
        "WanStatus", source_id="ProbeNode",
        instance_id="ProbeNode",
        schema_version=2, original_topic="PlatformInitStatus")
    navigation["payload.navigation.source"] = "ProbeNode"
    navigation["audience.global"] = True
    navigation["audience.control"] = True

    status_writer.write(navigation)
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        probe_writer.write(frame)
        time.sleep(0.5)
        for sample in mesh_view.take():
            if sample.info.valid:
                for peer in sample.data["peers"]:
                    if peer["health.router"] == "ProbeNode/phase4":
                        assert peer["presence"] == ALIVE, "Nonhealth deadline changed presence"

    peers = wait_mesh(mesh_view, lambda p: p.get("ProbeNode/phase4", {}).get("presence") == STALE,
                      12, alive=lambda: alive(nodes))
    assert peers["ProbeNode/phase4"]["presence"] == STALE
    status_writer.write(navigation)
    peers = wait_mesh(mesh_view, lambda p: p.get("ProbeNode/phase4", {}).get("presence") == ALIVE,
                      3, alive=lambda: alive(nodes))
    assert peers["ProbeNode/phase4"]["presence"] == ALIVE
    # Application updates refresh source activity, but not cached health topology.
    previous_ages = None
    deadline = time.monotonic() + 32
    while time.monotonic() < deadline:
        assert alive(nodes)
        status_writer.write(navigation)
        time.sleep(0.1)
        snapshot = probe_snapshot()
        assert snapshot is not None
        assert reported_ages.keys() <= snapshot["edges"].keys(), snapshot
        if previous_ages is not None:
            assert all(snapshot["edges"][peer] >= previous_ages[peer]
                       for peer in reported_ages), (previous_ages, snapshot)
        previous_ages = snapshot["edges"]
    assert snapshot["activity_age"] < 1500, snapshot
    assert all(snapshot["edges"][peer] >= age + 30_000
               for peer, age in reported_ages.items()), snapshot
    control_health = wait_health(lambda frames: "Control_20/phase4" in frames)[
        "Control_20/phase4"]["payload.health"]
    direct_refs = {str(ref["router"]): ref for ref in control_health["peers_seen"]}
    assert int(direct_refs["ProbeNode/phase4"]["last_seen_delta_ms"]) < 1500, direct_refs

    # Refresh health, then silence all Status while the DDS participant remains alive.
    probe_writer.write(frame)
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        snapshot = probe_snapshot()
        if snapshot is not None and all(
                snapshot["edges"].get(peer, age + 2000) < age + 1500
                for peer, age in reported_ages.items()):
            break
        time.sleep(0.1)
    assert snapshot is not None and all(
        peer in snapshot["edges"] and snapshot["edges"][peer] < age + 1500
        for peer, age in reported_ages.items()), snapshot
    peers = wait_mesh(mesh_view, lambda p: p.get("ProbeNode/phase4", {}).get("presence") == ALIVE,
                      3, alive=lambda: alive(nodes))
    peers = wait_mesh(mesh_view, lambda p: p.get("ProbeNode/phase4", {}).get("presence") == STALE,
                      8, alive=lambda: alive(nodes))
    assert peers["ProbeNode/phase4"]["presence"] == STALE
    stale_at = time.monotonic()
    # Repeated deadline callbacks must not restart the 30-second countdown.
    time.sleep(27)
    peers = wait_mesh(mesh_view, lambda p: p.get("ProbeNode/phase4", {}).get("presence") == STALE,
                      1, alive=lambda: alive(nodes))
    assert peers["ProbeNode/phase4"]["presence"] == STALE
    peers = wait_mesh(mesh_view, lambda p: p.get("ProbeNode/phase4", {}).get("presence") == DEAD,
                      8, alive=lambda: alive(nodes))
    assert peers["ProbeNode/phase4"]["presence"] == DEAD
    assert 29 <= time.monotonic() - stale_at <= 35
    probe_writer.write(frame)
    peers = wait_mesh(mesh_view, lambda p: p.get("ProbeNode/phase4", {}).get("presence") == ALIVE,
                      3, alive=lambda: alive(nodes))
    assert peers["ProbeNode/phase4"]["presence"] == ALIVE
    probe_writer.dispose_instance(probe_writer.register_instance(frame))
    peers = wait_mesh(mesh_view, lambda p: p.get("ProbeNode/phase4", {}).get("presence") == STALE,
                      3, alive=lambda: alive(nodes))
    assert peers["ProbeNode/phase4"]["presence"] == STALE
    probe_writer.write(navigation)
    peers = wait_mesh(mesh_view, lambda p: p.get("ProbeNode/phase4", {}).get("presence") == ALIVE,
                      3, alive=lambda: alive(nodes))
    assert peers["ProbeNode/phase4"]["presence"] == ALIVE
    probe_writer.write(frame)
    time.sleep(0.5)
    probe_writer.close()
    peers = wait_mesh(mesh_view, lambda p: p.get("ProbeNode/phase4", {}).get("presence") == STALE,
                      3, alive=lambda: alive(nodes))
    assert peers["ProbeNode/phase4"]["presence"] == STALE

    process, probe, admin = nodes["Boat31"]
    process.kill()
    peers = wait_mesh(mesh_view, lambda p: p.get("Boat31/phase4", {}).get("presence") == DEAD,
                      40, alive=lambda: nodes["Control_20"][0].is_alive())
    assert peers["Boat31/phase4"]["presence"] == DEAD
    assert peers["ProbeNode/phase4"]["presence"] == DEAD
    assert peers["Boat30/phase4"]["presence"] == ALIVE
    restart_dir = process.log_path.parent / "restart"
    restart_dir.mkdir()
    restarted = start_router(
        router_binary, process.log_path.parent / "control-platform.yaml", "platform",
        restart_dir, node_name="Boat31", name="phase4", admin_participant="platform_lan")
    nodes["Boat31"] = (restarted, probe, admin)
    try:
        peers = wait_mesh(mesh_view, lambda p: p.get("Boat31/phase4", {}).get("presence") == ALIVE,
                          20, alive=lambda: alive(nodes))
        assert peers["Boat31/phase4"]["presence"] == ALIVE
        frames = wait_health(lambda f: "Boat31/phase4" in f
                             and f["Boat31/phase4"]["session_id"] != old_session)
        assert frames["Boat31/phase4"]["session_id"] != old_session
        # With no active application sources, health alone creates only Status.
        assert {item["topic"] for item in wan.discovered_publications()} == {"Status"}
    finally:
        restarted.stop()
