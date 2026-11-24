"""Reliable RouterConfig events carry writer-observed filters into the LAN mesh view."""

import json
import os
import subprocess
import time

import pytest
import rti.connextdds as dds

from conftest import start_router
from test_presence_roster import mesh_reader
from test_two_topic_controller import alive, command, mesh, ready, wan_contract
from util.dds_probe import read_status_revision, reader_qos, writer_qos


def observations(health):
    if not isinstance(health, dict):
        health = json.loads(health.to_json())
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
        for writer in health.get("wan_writer_filters") or []
    }


def wait_config(reader, nodes, predicate, timeout=40, latest=None):
    deadline = time.monotonic() + timeout
    latest = {} if latest is None else latest
    while time.monotonic() < deadline:
        assert alive(nodes)
        for sample in reader.take():
            if sample.info.valid and sample.data["original_topic"] == "RouterConfig":
                health = sample.data["payload.router_config"]
                name = str(health["router"])
                data = json.loads(health.to_json())
                latest[name] = {
                    "sequence": int(health["snapshot_seq"]),
                    "revision": int(health["state_revision"]),
                    "session": int(health["session_id"]),
                    "sent_at_ns": int(health["send_timestamp"]),
                    "filters": observations(data),
                }
        if predicate(latest):
            return latest
        time.sleep(0.1)
    raise AssertionError(f"filter observations timed out: {latest}")


def remote_filter(latest, reporter, topic, peer):
    return latest.get(reporter, {}).get("filters", {}).get(topic, {}).get("readers", {}).get(peer)


def test_observer_health_matches_transmitted_acknowledgments(mesh):
    nodes, wan, provider = mesh
    health_reader = wan.reader("Status", "WanStatus", qos=reader_qos("wan_union_periodic"))
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
                local = dict(cached[key])
                if health.get("wan_writer_filters") is None:
                    local.pop("wan_writer_filters", None)
                    health.pop("wan_writer_filters", None)
                assert local == health, "local health differs from the transmitted heartbeat"
                return
        time.sleep(0.1)
    raise AssertionError("no matching local/WAN heartbeat with a valid acknowledgment")


def test_reliable_config_snapshots_leave_every_heartbeat_compact(mesh, wan_contract):
    nodes, wan, provider = mesh
    health_family = "Telemetry" if wan_contract == "three_topic_union" else "Status"
    reader = wan.reader(health_family, "WanStatus", qos=reader_qos("wan_union_periodic"))
    config_reader = wan.reader("Event", "WanEvent", qos=reader_qos("wan_event"))
    assert config_reader.qos.reliability.kind == dds.ReliabilityKind.RELIABLE
    assert config_reader.qos.history.kind == dds.HistoryKind.KEEP_ALL
    mesh_view = mesh_reader(nodes["Control_20"][1], provider)
    reporter = "Boat30/phase4"
    wait_config(config_reader, nodes, lambda latest: bool(latest.get(reporter, {}).get("filters")))
    # Allow startup discovery callbacks to settle before measuring stable cadence.
    time.sleep(2)
    reader.take()
    config_reader.take()
    compact = snapshots = 0
    first_age = last_age = None
    last_observation = last_sequence = None
    max_age = 0
    compact_sizes, snapshot_sizes = [], []
    deadline = time.monotonic() + 34
    while time.monotonic() < deadline:
        assert alive(nodes)
        for sample in reader.take():
            if not sample.info.valid or sample.data["payload"].discriminator_value != 10:
                continue
            health = json.loads(sample.data["payload.health"].to_json())
            if health["router"] != reporter:
                continue
            assert health.get("wan_writer_filters") is None, "WAN health repeated config details"
            compact += 1
            compact_sizes.append(len(sample.data.to_cdr_buffer()))
        for sample in config_reader.take():
            if sample.info.valid and sample.data["original_topic"] == "RouterConfig":
                config = sample.data["payload.router_config"]
                if config["router"] != reporter:
                    continue
                assert sample.data["instance_id"] == config["router"]
                assert sample.data["audit_sequence"] == config["snapshot_seq"]
                assert sample.data["session_id"] == config["session_id"]
                assert sample.data["sent_at_ns"] == config["send_timestamp"]
                snapshots += 1
                snapshot_sizes.append(len(sample.data.to_cdr_buffer()))
        for sample in mesh_view.take():
            if not sample.info.valid:
                continue
            for peer in sample.data["peers"]:
                if str(peer["health"]["router"]) != reporter:
                    continue
                assert observations(peer["health"]), "compact heartbeat erased cached filters"
                age = int(peer["wan_writer_filters_age_ms"])
                assert age >= 0
                observation = observations(peer["health"])[health_family]["observed_at_ns"]
                sequence = int(peer["health"]["heartbeat_seq"])
                if last_observation == observation and sequence != last_sequence:
                    assert age >= last_age, "compact health falsely refreshed snapshot age"
                last_observation, last_sequence = observation, sequence
                max_age = max(max_age, age)
                if first_age is None:
                    first_age = age
                last_age = age
        time.sleep(0.1)
    assert compact >= 25, (compact, snapshots)
    assert 1 <= snapshots <= 2, (compact, snapshots)
    assert first_age is not None and last_age is not None
    assert max_age >= 20000
    assert snapshot_sizes and compact_sizes
    print(f"Compact {health_family} health CDR bytes={sorted(set(compact_sizes))}, "
          f"reliable Event config bytes={sorted(set(snapshot_sizes))}; counts={compact}/{snapshots}")


@pytest.mark.parametrize("cache_pressure", ["none", "other_sessions", "unseen_routers"])
def test_config_before_health_replay_session_change_and_empty_snapshot(mesh, cache_pressure):
    nodes, wan, provider = mesh
    reader = wan.reader("Status", "WanStatus", qos=reader_qos("wan_union_periodic"))
    configs = wan.reader("Event", "WanEvent", qos=reader_qos("wan_event"))
    template = None
    deadline = time.monotonic() + 35
    while template is None and time.monotonic() < deadline:
        for sample in reader.take():
            if sample.info.valid and sample.data["payload"].discriminator_value == 10:
                health = json.loads(sample.data["payload.health"].to_json())
                template = dds.DynamicData(sample.data)
                break
        time.sleep(0.1)
    assert template is not None
    writer = wan.writer("Status", "WanStatus", qos=writer_qos("wan_union_periodic"))
    config_writer = wan.writer("Event", "WanEvent", qos=writer_qos("wan_event"))
    config_template = None
    deadline = time.monotonic() + 40
    while config_template is None and time.monotonic() < deadline:
        for sample in configs.take():
            if sample.info.valid and sample.data["original_topic"] == "RouterConfig":
                config_template = dds.DynamicData(sample.data)
                break
        time.sleep(0.1)
    assert config_template is not None
    assert config_writer.qos.reliability.kind == dds.ReliabilityKind.RELIABLE
    assert config_writer.qos.history.kind == dds.HistoryKind.KEEP_ALL
    mesh_view = mesh_reader(nodes["Control_20"][1], provider)
    template["source_id"] = "Sparse"
    template["instance_id"] = "Sparse/producer"
    template["payload.health.router"] = "Sparse/producer"
    config_template["source_id"] = "Sparse"
    config_template["instance_id"] = "Sparse/producer"
    config_template["payload.router_config.router"] = "Sparse/producer"
    details = list(config_template["payload.router_config.wan_writer_filters"])

    def send_config(session, sequence, filters):
        config_template["session_id"] = session
        config_template["audit_sequence"] = sequence
        config_template["sent_at_ns"] = time.time_ns()
        config_template["payload.router_config.session_id"] = session
        config_template["payload.router_config.snapshot_seq"] = sequence
        config_template["payload.router_config.send_timestamp"] = config_template["sent_at_ns"]
        config_template["payload.router_config.wan_writer_filters"] = filters
        config_writer.write(config_template)

    def send_and_observe(session, sequence, predicate):
        template["session_id"] = session
        template["payload.health.session_id"] = session
        template["audit_sequence"] = sequence
        template["payload.health.heartbeat_seq"] = sequence
        template["sent_at_ns"] = time.time_ns()
        template["payload.health.send_timestamp"] = template["sent_at_ns"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            assert alive(nodes)
            writer.write(template)
            for sample in mesh_view.take():
                if not sample.info.valid:
                    continue
                for peer in sample.data["peers"]:
                    health = json.loads(peer["health"].to_json())
                    if (health["router"] == str(template["payload.health.router"])
                            and int(health["session_id"]) == session
                            and int(health["heartbeat_seq"]) == sequence):
                        age = int(peer["wan_writer_filters_age_ms"])
                        if predicate(health, age):
                            return health, age
            time.sleep(0.1)
        raise AssertionError("sparse diagnostic test heartbeat not observed")

    # A config can beat its first heartbeat because Event and Status order independently.
    send_config(123, 1, details)
    full, age = send_and_observe(123, 1, lambda health, age: bool(observations(health)))
    assert observations(full) and age >= 0
    compact, age = send_and_observe(123, 2, lambda health, age: bool(observations(health)))
    assert observations(compact) == observations(full)
    restarted, age = send_and_observe(124, 1, lambda health, age: age == -1)
    assert restarted.get("wan_writer_filters") is None
    assert age == -1
    send_config(124, 2, details)
    recovered, age = send_and_observe(124, 2, lambda health, age: bool(observations(health)))
    assert observations(recovered) and age >= 0
    send_config(124, 3, [])
    empty, age = send_and_observe(124, 3, lambda health, age: health.get("wan_writer_filters") == [])
    assert empty["wan_writer_filters"] == [] and age >= 0
    send_config(124, 2, details)
    send_config(123, 99, details)
    time.sleep(1)
    replayed, _ = send_and_observe(124, 4, lambda health, age: True)
    assert replayed["wan_writer_filters"] == [], "older sequence/session restored stale config"
    config_template["session_id"] = 124
    config_template["payload.router_config.session_id"] = 999
    config_template["audit_sequence"] = 4
    config_template["payload.router_config.snapshot_seq"] = 4
    config_writer.write(config_template)
    time.sleep(1)
    unchanged, unchanged_age = send_and_observe(124, 5, lambda health, age: True)
    assert unchanged["wan_writer_filters"] == [], "invalid config overwrote the reliable snapshot"
    assert "router_config_invalid_sample" in nodes["Control_20"][0].log_path.read_text()

    if cache_pressure == "other_sessions":
        send_config(125, 1, details)
        send_config(126, 1, details)
        time.sleep(1)
        send_config(124, 2, details)
        time.sleep(1)
        replayed, age = send_and_observe(124, 6, lambda health, age: True)
        assert replayed["wan_writer_filters"] == [], "session eviction forgot the active replay watermark"
        assert age >= unchanged_age, "rejected replay refreshed the active snapshot age"
        next_session, age = send_and_observe(126, 1, lambda health, age: bool(observations(health)))
        assert observations(next_session) and age >= 0
        send_config(126, 2, [])
        cleared, cleared_age = send_and_observe(
            126, 2, lambda health, age: health.get("wan_writer_filters") == [])
        assert cleared["wan_writer_filters"] == []
        send_config(127, 1, details)
        send_config(128, 1, details)
        time.sleep(1)
        send_config(126, 1, details)
        time.sleep(1)
        replayed, age = send_and_observe(126, 3, lambda health, age: True)
        assert replayed["wan_writer_filters"] == [] and age >= cleared_age
    elif cache_pressure == "unseen_routers":
        for index in range(256):
            source = f"ConfigOnly{index}"
            config_template["source_id"] = source
            config_template["instance_id"] = source + "/producer"
            config_template["payload.router_config.router"] = source + "/producer"
            send_config(1, 1, [])
        deadline = time.monotonic() + 10
        log_path = nodes["Control_20"][0].log_path
        while time.monotonic() < deadline:
            text = log_path.read_text()
            if "router_config_cache_evicted" in text or "router_config_cache_full" in text:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("config cache did not reach its capacity")
        config_template["source_id"] = "Sparse"
        config_template["instance_id"] = "Sparse/producer"
        config_template["payload.router_config.router"] = "Sparse/producer"
        send_config(124, 2, details)
        time.sleep(1)
        protected, age = send_and_observe(124, 6, lambda health, age: True)
        assert protected["wan_writer_filters"] == [] and age >= unchanged_age
        source = "Fresh"
        config_template["source_id"] = source
        config_template["instance_id"] = source + "/producer"
        config_template["payload.router_config.router"] = source + "/producer"
        send_config(200, 1, details)
        template["source_id"] = source
        template["instance_id"] = source + "/producer"
        template["payload.health.router"] = source + "/producer"
        fresh, age = send_and_observe(200, 1, lambda health, age: bool(observations(health)))
        assert observations(fresh) and age >= 0


def test_config_reports_team_filter_changes_and_reader_departure(mesh, router_binary):
    nodes, wan, provider = mesh
    control, boat, other = (nodes[name][1] for name in ("Control_20", "Boat30", "Boat31"))
    command_writer = control.writer("Mission", "control_command")
    command_reader = boat.reader("Mission", "control_command")
    data_writer = boat.writer("PlatformData", "platform_data")
    data_reader = other.reader("PlatformData", "platform_data")
    health_reader = wan.reader("Event", "WanEvent", qos=reader_qos("wan_event"))
    mesh_view = mesh_reader(control, provider)
    for node in ("Boat30", "Boat31"):
        assert command(nodes, provider, node, "ADD_PARTICIPANT_PARTITION", f"assign-{node}",
                       participant="platform_wan", partition="TEAM_A")["accepted"]

    for node, route, matched in (
            ("Control_20", "control_command", 3), ("Boat30", "control_command", 1),
            ("Boat30", "platform_team_to_wan", 2), ("Boat31", "wan_team_to_platform", 1)):
        ready(nodes, node, route, minimum_output_matched=matched)

    def selected(latest, team):
        event = remote_filter(latest, "Control_20/phase4", "Event", "Boat30/phase4")
        status = remote_filter(latest, "Boat30/phase4", "Status", "Boat31/phase4")
        return (event and status and not event["last_error"] and not status["last_error"]
                and event["has_filter"] and status["has_filter"]
                and f"'{team}'" in status["parameters"])

    initial = wait_config(health_reader, nodes, lambda latest:
        selected(latest, "TEAM_A") and latest["Boat30/phase4"]["revision"]
        == read_status_revision(nodes["Boat30"][2].status))
    previous = remote_filter(initial, "Boat30/phase4", "Status", "Boat31/phase4")
    assert "audience.destination_ids" in previous["expression"]
    assert "original_topic = 'PlatformData'" in previous["expression"]
    assert "original_topic = 'RouterHealth'" in previous["expression"]
    assert "payload._d" not in previous["expression"]
    event = remote_filter(initial, "Control_20/phase4", "Event", "Boat30/phase4")
    assert "original_topic = 'Mission'" in event["expression"]
    assert "payload._d" not in event["expression"]
    assert initial["Boat30/phase4"]["filters"]["Status"]["observed_at_ns"] > 0
    assert False in initial["Boat30/phase4"]["filters"]["Event"]["unnamed_readers"]
    assert remote_filter(initial, "Boat30/phase4", "Status", "Boat30/phase4") is None
    assert remote_filter(initial, "Boat30/phase4", "Event", "Boat30/phase4") is None

    assert command(nodes, provider, "Boat31", "REMOVE_PARTICIPANT_PARTITION", "leave-alpha",
                   participant="platform_wan", partition="TEAM_A")["accepted"]
    assert command(nodes, provider, "Boat31", "ADD_PARTICIPANT_PARTITION", "join-bravo",
                   participant="platform_wan", partition="TEAM_B")["accepted"]
    previous_revision = initial["Boat30/phase4"]["revision"]
    changed = wait_config(
        health_reader, nodes,
        lambda latest: selected(latest, "TEAM_B") and "'TEAM_A'" not in remote_filter(
            latest, "Boat30/phase4", "Status", "Boat31/phase4")["parameters"], latest=initial)
    current = remote_filter(changed, "Boat30/phase4", "Status", "Boat31/phase4")
    assert current["reader_id"] == previous["reader_id"], "filter update recreated the reader"
    assert changed["Boat30/phase4"]["revision"] == previous_revision

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
    wait_config(health_reader, nodes, lambda latest:
        "Status" in latest.get("Boat30/phase4", {}).get("filters", {})
        and remote_filter(latest, "Boat30/phase4", "Status", "Boat31/phase4") is None)

    work = departed.log_path.parent / "filter-reader-restart"
    work.mkdir()
    restarted = start_router(
        router_binary, departed.log_path.parent / "control-platform.yaml", "platform", work,
        node_name="Boat31", name="phase4", admin_participant="platform_lan")
    nodes["Boat31"] = restarted, probe, admin
    try:
        rejoined = wait_config(health_reader, nodes, lambda latest:
            remote_filter(latest, "Boat30/phase4", "Status", "Boat31/phase4"))
        fresh = remote_filter(rejoined, "Boat30/phase4", "Status", "Boat31/phase4")
        assert fresh["reader_id"] != previous["reader_id"]
        assert "'TEAM_A'" not in fresh["parameters"] and "'TEAM_B'" not in fresh["parameters"]
        restarted_mesh = mesh_reader(probe, provider)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if any(observations(peer["health"]) for sample in restarted_mesh.take()
                   if sample.info.valid for peer in sample.data["peers"]):
                break
            time.sleep(0.1)
        else:
            raise AssertionError("restarted router did not recover remote configuration")
    finally:
        restarted.stop()


@pytest.mark.skipif(os.environ.get("ROUTER_CONFIG_LOSS_TEST") != "1",
                    reason="requires an explicitly owned isolated network with NET_ADMIN")
def test_router_config_published_during_packet_loss_is_repaired(mesh):
    nodes, wan, _ = mesh
    reader = wan.reader("Event", "WanEvent", qos=reader_qos("wan_event"))
    reporter = "Boat30/phase4"
    initial = wait_config(reader, nodes, lambda latest: reporter in latest)
    assert initial[reporter]["filters"]
    for handle in reader.matched_publications:
        assert reader.matched_publication_data(handle).reliability.kind == dds.ReliabilityKind.RELIABLE
    reader.take()
    subprocess.run(["tc", "qdisc", "add", "dev", "lo", "root", "netem", "loss", "100%"],
                   check=True, capture_output=True, text=True)
    started = time.time_ns()
    try:
        # The unchanged full-snapshot refresh is due within 30 seconds.
        time.sleep(32)
        counters = subprocess.run(["tc", "-s", "qdisc", "show", "dev", "lo"],
                                  check=True, capture_output=True, text=True).stdout
        assert "loss 100%" in counters
        assert alive(nodes)
        for sample in reader.take():
            if sample.info.valid and sample.data["original_topic"] == "RouterConfig":
                assert int(sample.data["sent_at_ns"]) < started, "packet-loss injection did not isolate DDS"
    finally:
        subprocess.run(["tc", "qdisc", "del", "dev", "lo", "root"], check=True,
                       capture_output=True, text=True)
    restored = time.time_ns()
    recovered = wait_config(reader, nodes, lambda latest:
        reporter in latest and started <= latest[reporter]["sent_at_ns"] <= restored, timeout=12)
    assert recovered[reporter]["sequence"] > initial[reporter]["sequence"]
    print(f"Recovered RouterConfig {recovered[reporter]['sequence']} published during 100% loss")
