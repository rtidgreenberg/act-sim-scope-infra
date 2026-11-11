"""Writer-observed CFT state reaches the WAN health branch and LAN mesh view."""

import json
import time

from conftest import start_router
from test_presence_roster import mesh_reader
from test_two_topic_controller import alive, command, mesh, ready
from util.dds_probe import reader_qos


def observations(health):
    return {
        str(writer["topic_name"]): {
            "observed_at_ns": int(writer["observed_at_ns"]),
            "unnamed_readers": [
                bool(reader["has_filter"])
                for reader in writer["reader_filters"]
                if not reader["peer_router"] and not reader["last_error"]
            ],
            "readers": {
                str(reader["peer_router"]): {
                    "reader_id": str(reader["reader_id"]),
                    "has_filter": bool(reader["has_filter"]),
                    "expression": str(reader["expression"]),
                    "parameters": list(reader["parameters"]),
                    "last_error": str(reader["last_error"]),
                }
                for reader in writer["reader_filters"] if reader["peer_router"]
            },
        }
        for writer in health["wan_writer_filters"]
    }


def wait_health(reader, nodes, predicate, timeout=25):
    deadline = time.monotonic() + timeout
    latest = {}
    while time.monotonic() < deadline:
        assert alive(nodes)
        for sample in reader.take():
            if sample.info.valid and sample.data["payload"].discriminator_value == 10:
                health = sample.data["payload.health"]
                latest[str(health["router"])] = {
                    "sequence": int(health["heartbeat_seq"]),
                    "revision": int(health["state_revision"]),
                    "filters": observations(health),
                }
        if predicate(latest):
            return latest
        time.sleep(0.1)
    raise AssertionError(f"filter observations timed out: {latest}")


def remote_filter(latest, reporter, topic, peer):
    return latest.get(reporter, {}).get("filters", {}).get(topic, {}).get("readers", {}).get(peer)


def test_observer_health_matches_transmitted_acknowledgments(mesh):
    nodes, wan, provider = mesh
    health_reader = wan.reader("Status", "WanStatus", qos=reader_qos("wan_union_status"))
    mesh_view = mesh_reader(nodes["Control_20"][1], provider)
    transmitted = {}
    cached = {}
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        assert alive(nodes)
        for sample in health_reader.take():
            if not sample.info.valid or sample.data["payload"].discriminator_value != 10:
                continue
            health = json.loads(sample.data["payload.health"].to_json())
            if health["router"] == "Control_20/phase4":
                key = (int(health["session_id"]), int(health["heartbeat_seq"]))
                transmitted[key] = health
        for sample in mesh_view.take():
            if sample.info.valid:
                health = json.loads(sample.data["observer_health"].to_json())
                key = (int(health["session_id"]), int(health["heartbeat_seq"]))
                cached[key] = health
        for key in transmitted.keys() & cached.keys():
            health = transmitted[key]
            if any(int(peer["ack_session_id"]) and int(peer["ack_heartbeat_seq"])
                   for peer in health["peers_seen"]):
                assert cached[key] == health, "local health differs from the transmitted heartbeat"
                return
        time.sleep(0.1)
    raise AssertionError("no matching local/WAN heartbeat with a valid acknowledgment")


def test_health_reports_team_filter_changes_and_reader_departure(mesh, router_binary):
    nodes, wan, provider = mesh
    control, boat, other = (nodes[name][1] for name in ("Control_20", "Boat30", "Boat31"))
    command_writer = control.writer("ControlCommand", "control_command")
    command_reader = boat.reader("ControlCommand", "control_command")
    data_writer = boat.writer("PlatformData", "platform_data")
    data_reader = other.reader("PlatformData", "platform_data")
    health_reader = wan.reader("Status", "WanStatus", qos=reader_qos("wan_union_status"))
    mesh_view = mesh_reader(control, provider)
    for node in ("Boat30", "Boat31"):
        assert command(nodes, provider, node, "ADD_PARTICIPANT_PARTITION", f"assign-{node}",
                       participant="platform_wan", partition="TEAM_A")["accepted"]

    for node, route in (("Control_20", "control_command"), ("Boat30", "control_command"),
                        ("Boat30", "platform_team_to_wan"), ("Boat31", "wan_team_to_platform")):
        ready(nodes, node, route)

    def selected(latest, team):
        event = remote_filter(latest, "Control_20/phase4", "Event", "Boat30/phase4")
        status = remote_filter(latest, "Boat30/phase4", "Status", "Boat31/phase4")
        return (event and status and not event["last_error"] and not status["last_error"]
                and event["has_filter"] and status["has_filter"]
                and f"'{team}'" in status["parameters"])

    previous_health = None

    def settled(latest):
        nonlocal previous_health
        if not selected(latest, "TEAM_A"):
            return False
        health = latest["Boat30/phase4"]
        current = health["sequence"], health["revision"]
        if previous_health is not None and current[0] <= previous_health[0]:
            return False
        stable = previous_health is not None and current[1] == previous_health[1]
        previous_health = current
        return stable

    # Matched-count callbacks can arrive after route readiness; wait for distinct stable heartbeats.
    initial = wait_health(health_reader, nodes, settled)
    previous = remote_filter(initial, "Boat30/phase4", "Status", "Boat31/phase4")
    assert "audience.destination_ids" in previous["expression"]
    assert "original_topic = 'PlatformData'" in previous["expression"]
    assert "original_topic = 'RouterHealth'" in previous["expression"]
    assert "payload._d" not in previous["expression"]
    event = remote_filter(initial, "Control_20/phase4", "Event", "Boat30/phase4")
    assert "original_topic = 'ControlCommand'" in event["expression"]
    assert "payload._d" not in event["expression"]
    assert initial["Boat30/phase4"]["filters"]["Status"]["observed_at_ns"] > 0
    assert False in initial["Boat30/phase4"]["filters"]["Status"]["unnamed_readers"]
    assert remote_filter(initial, "Boat30/phase4", "Status", "Boat30/phase4")

    assert command(nodes, provider, "Boat31", "REMOVE_PARTICIPANT_PARTITION", "leave-alpha",
                   participant="platform_wan", partition="TEAM_A")["accepted"]
    assert command(nodes, provider, "Boat31", "ADD_PARTICIPANT_PARTITION", "join-bravo",
                   participant="platform_wan", partition="TEAM_B")["accepted"]
    changed = wait_health(
        health_reader, nodes,
        lambda latest: selected(latest, "TEAM_B") and "'TEAM_A'" not in remote_filter(
            latest, "Boat30/phase4", "Status", "Boat31/phase4")["parameters"])
    current = remote_filter(changed, "Boat30/phase4", "Status", "Boat31/phase4")
    assert current["reader_id"] == previous["reader_id"], "filter update recreated the reader"
    assert changed["Boat30/phase4"]["revision"] == initial["Boat30/phase4"]["revision"]
    assert remote_filter(changed, "Control_20/phase4", "Event", "Boat30/phase4") == remote_filter(
        initial, "Control_20/phase4", "Event", "Boat30/phase4")
    revision = changed["Boat30/phase4"]["revision"]
    advanced = wait_health(health_reader, nodes, lambda latest:
        latest.get("Boat30/phase4", {}).get("sequence", 0)
        > changed["Boat30/phase4"]["sequence"] + 1)
    assert advanced["Boat30/phase4"]["revision"] == revision, "telemetry changed config revision"

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        samples = [sample.data for sample in mesh_view.take() if sample.info.valid]
        if any("Event" in observations(sample["observer_health"])
               and int(sample["observer_health_age_ms"]) >= 0
               and all(int(peer["health_age_ms"]) >= 0 for peer in sample["peers"])
               for sample in samples):
            break
        time.sleep(0.1)
    else:
        raise AssertionError("local writer observations missing from the LAN mesh view")

    departed, probe, admin = nodes.pop("Boat31")
    departed.stop()
    wait_health(health_reader, nodes, lambda latest:
        "Status" in latest.get("Boat30/phase4", {}).get("filters", {})
        and remote_filter(latest, "Boat30/phase4", "Status", "Boat31/phase4") is None)

    work = departed.log_path.parent / "filter-reader-restart"
    work.mkdir()
    restarted = start_router(
        router_binary, departed.log_path.parent / "control-platform.yaml", "platform", work,
        node_name="Boat31", name="phase4", admin_participant="platform_lan")
    nodes["Boat31"] = restarted, probe, admin
    try:
        rejoined = wait_health(health_reader, nodes, lambda latest:
            remote_filter(latest, "Boat30/phase4", "Status", "Boat31/phase4"))
        fresh = remote_filter(rejoined, "Boat30/phase4", "Status", "Boat31/phase4")
        assert fresh["reader_id"] != previous["reader_id"]
        assert "'TEAM_A'" not in fresh["parameters"] and "'TEAM_B'" not in fresh["parameters"]
    finally:
        restarted.stop()
