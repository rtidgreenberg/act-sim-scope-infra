"""In-process mission contract checks; no live DDS participants are created."""
import importlib.util
import json
import sys
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT, set_wan_qos_env


@pytest.fixture(scope="module")
def mission_modules():
    set_wan_qos_env()
    sim_dir = REPO_ROOT / "harness_v2" / "sims"
    server_dir = REPO_ROOT / "gui/mesh_dashboard/server"
    sys.path.insert(0, str(sim_dir))
    sys.path.insert(0, str(server_dir))
    try:
        modules = []
        for name, path in [
            ("mission_bridge_test", REPO_ROOT / "gui/mesh_dashboard/server/mesh_bridge.py"),
            ("mission_sim_test", sim_dir / "platform_sim.py"),
        ]:
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules.append(module)
        yield modules
    finally:
        sys.path.remove(str(sim_dir))
        sys.path.remove(str(server_dir))


def confirm_shared_dispatch(bridge, result):
    for command in result["commands"]:
        bridge.record_mission_ack({
            "source": command["target"], "command_id": command["command_id"],
            "run_id": command["run_id"], "accepted": True,
        })
        bridge.platform_cache[command["target"]]["mission"]["PlatformMissionStatus"] = {
            "mission_id": command["command_id"], "mission_phase": "TRANSIT",
        }
    bridge.reconcile_team_missions()


def test_shared_goal_expands_to_whole_team_and_commits_only_after_acks(backend, mission_modules):
    bridge = backend
    _, simulator = mission_modules
    bridge.reconcile_team_missions()
    plan = bridge.validate_mission_plan({
        "team": "TEAM_A", "targets": ["Platform_30"], "waypoints": [[32.74, -117.38]],
    })
    assert len(plan["plans"]) == 5
    result = bridge.write_mission_command({"operation": "route", "plan_id": plan["plan_id"]})
    assert not bridge.team_missions
    for sample in bridge.samples:
        command = simulator.parse_mission_payload(sample["payload"])
        assert command["goal"] == {"kind": "waypoints", "waypoints": [[32.74, -117.38]]}
        assert len(command["fleet"]) == 5
        assert not command["waypoints"]
    confirm_shared_dispatch(bridge, result)
    assert set(bridge.team_missions) == {"TEAM_A"}


def test_late_join_inherits_goal_without_restarting_other_members(backend):
    bridge = backend
    bridge.cache["Control_20"]["peers"][-1]["health"]["team_partition"] = ["Platform_34", "TEAM_B"]
    bridge.reconcile_team_missions()
    plan = bridge.validate_mission_plan({
        "team": "TEAM_A", "targets": ["Platform_30"], "waypoints": [[32.74, -117.38]],
    })
    result = bridge.write_mission_command({"operation": "route", "plan_id": plan["plan_id"]})
    confirm_shared_dispatch(bridge, result)
    original = {node: follower["command_id"] for node, follower in bridge.team_followers.items()}
    bridge.cache["Control_20"]["peers"][-1]["health"]["team_partition"] = ["Platform_34", "TEAM_A"]
    bridge.reconcile_team_missions()
    assert all(bridge.team_followers[node]["command_id"] == cid for node, cid in original.items())
    assert bridge.samples[-1]["destination"] == "Platform_34"
    assert json.loads(bytes(bridge.samples[-1]["payload"]))["g"] == bridge.team_missions["TEAM_A"]["goal"]
    count = len(bridge.samples)
    bridge.reconcile_team_missions()
    assert len(bridge.samples) == count


def test_reassign_to_idle_team_stops_old_goal_and_preserves_remaining_team(backend):
    bridge = backend
    bridge.reconcile_team_missions()
    plan = bridge.validate_mission_plan({
        "team": "TEAM_A", "targets": ["Platform_30"], "waypoints": [[32.74, -117.38]],
    })
    result = bridge.write_mission_command({"operation": "route", "plan_id": plan["plan_id"]})
    confirm_shared_dispatch(bridge, result)
    version = bridge.team_missions["TEAM_A"]["version"]
    bridge.cache["Control_20"]["peers"][-1]["health"]["team_partition"] = ["Platform_34"]
    bridge.reconcile_team_missions()
    assert bridge.samples[-1]["destination"] == "Platform_34"
    assert json.loads(bytes(bridge.samples[-1]["payload"]))["op"] == "cancel"
    assert bridge.team_missions["TEAM_A"]["version"] == version
    count = len(bridge.samples)
    bridge.reconcile_team_missions()
    assert len(bridge.samples) == count


@pytest.mark.parametrize("team", ["TEAM_B", ""])
@pytest.mark.parametrize("late_acks", [False, True])
def test_reassignment_stops_shared_goal_before_mission_telemetry(backend, team, late_acks):
    backend.reconcile_team_missions()
    plan = backend.validate_mission_plan({
        "team": "TEAM_A", "targets": ["Platform_30"], "waypoints": [[32.74, -117.38]],
    })
    result = backend.write_mission_command({"operation": "route", "plan_id": plan["plan_id"]})

    def acknowledge():
        for command in result["commands"]:
            backend.record_mission_ack(ack_for(command))
        backend.reconcile_team_missions()

    if not late_acks:
        acknowledge()
    backend.cache["Control_20"]["peers"][-1]["health"]["team_partition"] = ["Platform_34", *([team] if team else [])]
    backend.reconcile_team_missions()
    cancel = backend.samples[-1]
    assert cancel["destination"] == "Platform_34"
    assert json.loads(bytes(cancel["payload"]))["op"] == "cancel"
    if late_acks:
        acknowledge()
    backend.platform_cache["Platform_34"]["mission"]["PlatformMissionStatus"] = {
        "mission_id": result["commands"][-1]["command_id"], "mission_phase": "TRANSIT",
    }
    backend.reconcile_team_missions()
    assert backend.team_followers["Platform_34"]["command_id"] == cancel["command_id"]
    assert len(backend.samples) == 6
    assert backend.team_missions["TEAM_A"]["goal"]["kind"] == "waypoints"


def test_returning_to_team_during_pending_dispatch_reinherits_cancelled_goal(backend):
    backend.reconcile_team_missions()
    plan = backend.validate_mission_plan({
        "team": "TEAM_A", "targets": ["Platform_30"], "waypoints": [[32.74, -117.38]],
    })
    result = backend.write_mission_command({"operation": "route", "plan_id": plan["plan_id"]})
    peer = backend.cache["Control_20"]["peers"][-1]
    peer["health"]["team_partition"] = ["Platform_34", "TEAM_B"]
    backend.reconcile_team_missions()
    peer["health"]["team_partition"] = ["Platform_34", "TEAM_A"]
    backend.reconcile_team_missions()
    confirm_shared_dispatch(backend, result)
    inherited = backend.samples[-1]
    assert inherited["destination"] == "Platform_34"
    assert inherited["command_id"] != result["commands"][-1]["command_id"]
    assert json.loads(bytes(inherited["payload"]))["g"] == backend.team_missions["TEAM_A"]["goal"]
    assert len(backend.samples) == 7


def test_membership_change_during_publication_retains_departing_recipient(backend):
    backend.reconcile_team_missions()
    plan = backend.validate_mission_plan({
        "team": "TEAM_A", "targets": ["Platform_30"], "waypoints": [[32.74, -117.38]],
    })

    def publish(sample):
        backend.samples.append(dict(sample))
        backend.cache["Control_20"]["peers"][-1]["health"]["team_partition"] = ["Platform_34", "TEAM_B"]

    backend.command_writer.write = publish
    backend.write_mission_command({"operation": "route", "plan_id": plan["plan_id"]})
    assert "Platform_34" in backend.team_dispatches["TEAM_A"]["commands"]
    backend.reconcile_team_missions()
    assert backend.samples[-1]["destination"] == "Platform_34"
    assert json.loads(bytes(backend.samples[-1]["payload"]))["op"] == "cancel"


def test_joining_pending_team_stops_previous_goal_until_new_goal_commits(backend):
    peers = backend.cache["Control_20"]["peers"]
    peers[-1]["health"]["team_partition"] = ["Platform_34", "TEAM_B"]
    backend.reconcile_team_missions()
    plan_a = backend.validate_mission_plan({
        "team": "TEAM_A", "targets": ["Platform_30"], "waypoints": [[32.74, -117.38]],
    })
    confirm_shared_dispatch(backend, backend.write_mission_command({"operation": "route", "plan_id": plan_a["plan_id"]}))
    plan_b = backend.validate_mission_plan({
        "team": "TEAM_B", "targets": ["Platform_34"], "waypoints": [[32.74, -117.44]],
    })
    pending = backend.write_mission_command({"operation": "route", "plan_id": plan_b["plan_id"]})
    peers[1]["health"]["team_partition"] = ["Platform_31", "TEAM_B"]
    backend.reconcile_team_missions()
    assert backend.samples[-1]["destination"] == "Platform_31"
    assert json.loads(bytes(backend.samples[-1]["payload"]))["op"] == "cancel"
    confirm_shared_dispatch(backend, pending)
    inherited = backend.samples[-1]
    assert inherited["destination"] == "Platform_31"
    assert json.loads(bytes(inherited["payload"]))["g"] == backend.team_missions["TEAM_B"]["goal"]


def test_shared_validation_rejects_roster_changes_and_unconfirmed_membership(backend, mission_modules):
    plan = backend.validate_mission_plan({
        "team": "TEAM_A", "targets": ["Platform_30"], "waypoints": [[32.74, -117.38]],
    })
    backend.cache["Control_20"]["peers"][-1]["health"]["team_partition"] = ["Platform_34", "TEAM_B"]
    with pytest.raises(mission_modules[0].MissionConflict, match="roster changed"):
        backend.write_mission_command({"operation": "route", "plan_id": plan["plan_id"]})


def test_timed_out_shared_dispatch_recovers_after_real_late_acks(backend):
    plan = backend.validate_mission_plan({
        "team": "TEAM_A", "targets": ["Platform_30"], "waypoints": [[32.74, -117.38]],
    })
    result = backend.write_mission_command({"operation": "route", "plan_id": plan["plan_id"]})
    backend.mission_commands[result["commands"][0]["command_id"]]["state"] = "timed_out"
    backend.reconcile_team_missions()
    assert not backend.team_missions
    assert backend.mission_inventory_snapshot()["platforms"][0]["inheritance_error"]
    confirm_shared_dispatch(backend, result)
    assert "TEAM_A" in backend.team_missions
    assert not backend.mission_inheritance_errors


def test_individual_cancel_preserves_team_goal_and_whole_team_cancel_clears_it(backend):
    plan = backend.validate_mission_plan({
        "team": "TEAM_A", "targets": ["Platform_30"], "waypoints": [[32.74, -117.38]],
    })
    confirm_shared_dispatch(backend, backend.write_mission_command({"operation": "route", "plan_id": plan["plan_id"]}))
    version = backend.team_missions["TEAM_A"]["version"]
    single = backend.write_mission_command({"operation": "cancel", "targets": ["Platform_30"]})
    confirm_shared_dispatch(backend, single)
    assert backend.team_missions["TEAM_A"]["version"] == version
    whole = backend.write_mission_command({"operation": "cancel", "targets": [f"Platform_{i}" for i in range(30, 35)]})
    confirm_shared_dispatch(backend, whole)
    assert not backend.team_missions


def test_active_team_to_active_team_reassignment_only_changes_joiner(backend):
    peers = backend.cache["Control_20"]["peers"]
    for peer in peers[2:]:
        peer["health"]["team_partition"][-1] = "TEAM_B"
    backend.reconcile_team_missions()
    for team, node, destination in (
            ("TEAM_A", "Platform_30", [32.74, -117.38]),
            ("TEAM_B", "Platform_32", [32.74, -117.44])):
        plan = backend.validate_mission_plan({"team": team, "targets": [node], "waypoints": [destination]})
        confirm_shared_dispatch(backend, backend.write_mission_command({"operation": "route", "plan_id": plan["plan_id"]}))
    original = {node: follower["command_id"] for node, follower in backend.team_followers.items()}
    peers[1]["health"]["team_partition"][-1] = "TEAM_B"
    backend.reconcile_team_missions()
    assert backend.samples[-1]["destination"] == "Platform_31"
    assert json.loads(bytes(backend.samples[-1]["payload"]))["g"] == backend.team_missions["TEAM_B"]["goal"]
    assert all(backend.team_followers[node]["command_id"] == cid for node, cid in original.items()
               if node != "Platform_31")


def test_shared_location_delivery_includes_other_teams_but_not_self(backend, monkeypatch):
    monkeypatch.setattr(backend, "_audit_nodes_for_run",
                        lambda run: ["Control_20", "Platform_30", "Platform_31", "Platform_32"])
    assert backend._expected_recipients({
        "topic": "PlatformInitStatus", "source_node": "Platform_30", "run_id": "mission-test-run",
    }) == ["Control_20", "Platform_31", "Platform_32"]


def test_64_waypoints_round_trip_through_dds_type(mission_modules):
    bridge, simulator = mission_modules
    waypoints = [[32.69 + i * 0.001, -117.32 - i * 0.001] for i in range(64)]
    request = bridge.validate_mission_request({
        "operation": "route", "targets": ["Platform_30"], "waypoints": waypoints,
    })
    assert 100 < len(request["payload"]) <= 8192
    import rti.connextdds as dds
    provider = dds.QosProvider(str(REPO_ROOT / "harness_v2/datamodel/gen/ActTypes.xml"))
    sample = dds.DynamicData(provider.type("control_command"))
    sample["payload"] = list(request["payload"])
    result = simulator.parse_mission_payload(sample["payload"])
    assert len(result["waypoints"]) == 64
    assert result["waypoints"] == [tuple(point) for point in request["waypoints"]]
    sample["payload"] = [0] * 8192
    assert len(sample["payload"]) == 8192


@pytest.mark.parametrize("operation", ["cancel", "return_to_base"])
def test_non_route_commands_omit_waypoints(mission_modules, operation):
    bridge, simulator = mission_modules
    request = bridge.validate_mission_request({"operation": operation, "targets": ["Platform_30"]})
    assert simulator.parse_mission_payload(request["payload"])["operation"] == operation
    with pytest.raises(ValueError, match="does not accept waypoints"):
        bridge.validate_mission_request({"operation": operation, "targets": ["Platform_30"], "waypoints": []})


def test_simulator_accepts_bridge_planned_return_to_base(mission_modules):
    _, simulator = mission_modules
    planned_return = {
        "v": 1,
        "op": "return_to_base",
        "w": [[32.700000, -117.310000], [32.690000, -117.320000]],
    }
    result = simulator.parse_mission_payload(json.dumps(planned_return).encode())
    assert result == {
        "operation": "return_to_base",
        "waypoints": [(32.7, -117.31), (32.69, -117.32)],
    }


def test_waypoint_limit_is_enforced_on_both_sides(mission_modules):
    bridge, simulator = mission_modules
    waypoints = [[32.7, -117.3]] * 65
    with pytest.raises(ValueError, match="64"):
        bridge.validate_mission_request({"operation": "route", "targets": ["Platform_30"], "waypoints": waypoints})
    with pytest.raises(ValueError, match="64"):
        simulator.parse_mission_payload(json.dumps({"v": 1, "op": "route", "w": waypoints}).encode())


def test_invalid_target_shape_is_rejected(mission_modules):
    bridge, _ = mission_modules
    with pytest.raises(ValueError, match="targets"):
        bridge.validate_mission_request({"operation": "cancel", "targets": [{}]})

@pytest.mark.parametrize("operation", ["cancel", "return_to_base", "set_time_scale", "route"])
def test_six_platform_command_limit(mission_modules, operation):
    bridge, simulator = mission_modules
    body = {"operation": operation, "targets": [f"Platform_{i}" for i in range(30, 36)]}
    if operation == "set_time_scale":
        body["time_scale"] = 20
    elif operation == "route":
        body["waypoints"] = [[32.74, -117.38]]
    request = bridge.validate_mission_request(body)
    assert request["targets"] == body["targets"]
    assert simulator.parse_mission_payload(request["payload"])["operation"] == operation
    with pytest.raises(ValueError, match="1 to 6"):
        bridge.validate_mission_request(dict(body, targets=body["targets"] + ["Platform_36"]))


def test_payload_byte_limit(mission_modules):
    _, simulator = mission_modules
    with pytest.raises(ValueError, match="8192"):
        simulator.parse_mission_payload(b" " * 8193)


@pytest.fixture
def backend(mission_modules, monkeypatch, tmp_path):
    module, _ = mission_modules
    bridge = module.DdsBridge.__new__(module.DdsBridge)
    bridge.cache_lock = threading.Lock()
    bridge.mission_lock = threading.Lock()
    bridge.cache = {"Control_20": {"peers": [
        {"presence": "PRESENCE_ALIVE", "last_seen_delta_ms": 1,
         "health": {"router": f"Platform_{number}/router", "overall_state": "ROUTER_OK",
                    "team_partition": [f"Platform_{number}", "TEAM_A"]}}
        for number in range(30, 35)
    ]}}
    now_ms = int(time.time() * 1000)
    bridge.mesh_updated_at = {"Control_20": now_ms}
    bridge.platform_cache = {
        f"Platform_{number}": {
            "init": {"PlatformInitStatus": {
                "latitude": module.launch_position_for(f"Platform_{number}")[0],
                "longitude": module.launch_position_for(f"Platform_{number}")[1],
            }}, "mission": {}, "init_updated_at": now_ms, "updated_at": now_ms,
        } for number in range(30, 35)
    }
    bridge.platform_status_modes = {"Platform_34": "debug"}
    bridge.platform_status_mode_changed_ns = {}
    bridge.mission_commands = {}
    bridge.mission_active_commands = {}
    bridge.mission_acks = {}
    bridge.mission_plans = {}
    bridge.mission_command_sequence = 0
    bridge.mission_planner = module.WaterPlanner()
    bridge.team_missions = {}
    bridge.team_dispatches = {}
    bridge.team_memberships = {}
    bridge.team_followers = {}
    bridge.mission_inheritance_errors = {}
    bridge.scenario_reports = {}
    bridge.scenario_dispatched_targets = set()
    bridge.scenario_samples = []
    bridge.scenario_type = None
    bridge.scenario_writer = SimpleNamespace(write=lambda sample: bridge.scenario_samples.append(dict(sample)))
    bridge.audit_run_id = "mission-test-run"
    bridge.audit_offsets = {}
    bridge.audit_sent = {}
    bridge.audit_received = {}
    bridge.audit_versions = {}
    bridge.wire_delivery = module.WireDelivery()
    bridge.traffic_cache = {}
    bridge.traffic_observers = set()
    bridge.control_command_type = None
    bridge.events = []
    bridge.samples = []
    bridge.status_requests = []
    bridge.command_writer = SimpleNamespace(write=lambda sample: bridge.samples.append(dict(sample)))
    bridge._broadcast = lambda event: bridge.events.append(event)
    bridge.write_status_mode = lambda target, mode: bridge.status_requests.append((target, mode))
    monkeypatch.setattr(module.dds, "DynamicData", lambda dtype: {})
    directory = tmp_path / "mission-audit"
    directory.mkdir(parents=True)
    (directory / "run_id").write_text(bridge.audit_run_id, encoding="utf-8")
    monkeypatch.setattr(bridge, "_mission_audit_path", lambda: directory / "events.jsonl")
    monkeypatch.setattr(bridge, "_active_audit_event_paths",
                        lambda filename="events.jsonl": [directory / filename]
                        if (directory / filename).exists() else [])
    yield bridge


def route_request(targets=None):
    return {"operation": "route", "targets": targets or ["Platform_30"],
            "waypoints": [[32.68, -117.33]]}


def ack_for(command, **changes):
    return dict({"command_id": command["command_id"], "source": command["target"],
                 "run_id": command["run_id"], "accepted": True, "message": "route accepted"}, **changes)


def test_inventory_uses_confirmed_squads_homes_and_presence(backend, mission_modules):
    module, simulator = mission_modules
    backend.team_assignments = {"Platform_30": "TEAM_C"}  # Unconfirmed DDS request must not win.
    rows = backend.mission_inventory_snapshot()["platforms"]
    assert len(rows) == 5
    assert all(row["available"] and row["connected"] and row["squad"] == "TEAM_A" for row in rows)
    assert [choice["label"] for choice in backend.mission_inventory_snapshot()["squads"]] == [
        "Alpha", "Bravo", "Reserve", "Unassigned"]
    for row in rows:
        assert tuple(row["home"].values()) == simulator.launch_position_for(row["platform"])
        assert module.launch_position_for(row["platform"]) == simulator.launch_position_for(row["platform"])
    peer = backend.cache["Control_20"]["peers"][0]
    peer["presence"] = "PRESENCE_DEAD"
    row = backend.mission_inventory_snapshot()["platforms"][0]
    assert row["health"] == "PRESENCE_DEAD" and not row["available"]
    peer["presence"] = "PRESENCE_ALIVE"
    peer["health"]["team_partition"] = ["Platform_30", "TEAM_B"]
    assert backend.mission_inventory_snapshot()["platforms"][0]["squad"] == "TEAM_B"
    peer["health"]["team_partition"] = ["Platform_30"]
    assert backend.mission_inventory_snapshot()["platforms"][0]["squad"] == ""
    backend.platform_cache["Platform_30"]["init_updated_at"] -= module.MISSION_FRESHNESS_MS + 1
    assert not backend.mission_inventory_snapshot()["platforms"][0]["available"]


@pytest.mark.parametrize("missing", ["stale", "position", "presence", "mesh", "unknown"])
def test_unavailable_positions_prevent_validation_and_publication(backend, mission_modules, missing):
    module, _ = mission_modules
    target = "Platform_30"
    if missing == "stale":
        backend.platform_cache[target]["init_updated_at"] = 1
    elif missing == "position":
        backend.platform_cache[target]["init"] = {}
    elif missing == "presence":
        backend.cache["Control_20"]["peers"][0]["presence"] = "PRESENCE_STALE"
    elif missing == "mesh":
        backend.mesh_updated_at["Control_20"] = 1
    else:
        target = "Platform_99"
    with pytest.raises(module.MissionConflict, match=target):
        backend.validate_mission_plan({"targets": [target], "waypoints": [[32.68, -117.33]]})
    with pytest.raises(module.MissionConflict, match=target):
        backend.write_mission_command(route_request([target]))
    assert not backend.samples


def test_validated_plan_has_no_writes_expires_and_is_bounded(backend, mission_modules, monkeypatch):
    module, _ = mission_modules
    request = route_request(["Platform_30", "Platform_31"])
    validated = backend.validate_mission_plan({key: request[key] for key in ("targets", "waypoints")})
    assert [plan["target"] for plan in validated["plans"]] == request["targets"]
    assert not backend.samples and not backend.status_requests and not backend.mission_commands
    result = backend.write_mission_command({"operation": "replace_route", "plan_id": validated["plan_id"]})
    assert len(result["commands"]) == 2 and not result["errors"]
    backend.mission_plans[validated["plan_id"]]["created_at"] -= module.MISSION_PLAN_TTL_MS
    with pytest.raises(module.MissionConflict, match="expired"):
        backend.write_mission_command({"operation": "route", "plan_id": validated["plan_id"]})
    monkeypatch.setattr(module, "MISSION_PLAN_LIMIT", 2)
    for _ in range(3):
        backend.validate_mission_plan({"targets": ["Platform_30"], "waypoints": [[32.68, -117.33]]})
    assert len(backend.mission_plans) == 2


def test_go_rechecks_position_and_confirmed_squad(backend, mission_modules):
    module, _ = mission_modules
    validated = backend.validate_mission_plan({"targets": ["Platform_30"],
                                               "waypoints": [[32.68, -117.33]]})
    calls = []
    original = backend.mission_planner.plan
    def plan(start, waypoints):
        calls.append(start)
        return original(start, waypoints)
    backend.mission_planner.plan = plan
    backend.platform_cache["Platform_30"]["init"]["PlatformInitStatus"]["latitude"] = 32.695
    backend.write_mission_command({"operation": "route", "plan_id": validated["plan_id"]})
    assert calls == [[32.695, -117.32]]
    backend.cache["Control_20"]["peers"][0]["health"]["team_partition"] = ["Platform_30", "TEAM_B"]
    before = len(backend.samples)
    with pytest.raises(module.MissionConflict, match="squad changed"):
        backend.write_mission_command({"operation": "route", "plan_id": validated["plan_id"]})
    assert len(backend.samples) == before


def test_active_run_identity_reloaded_and_missing_run_blocks_publication(backend, mission_modules, monkeypatch):
    module, _ = mission_modules
    backend.write_mission_command(route_request())
    backend._mission_audit_path().with_name("run_id").write_text("next-run", encoding="utf-8")
    command = backend.write_mission_command(route_request())["commands"][0]
    assert command["run_id"] == "next-run" and len(backend.mission_commands) == 1
    backend._mission_audit_path().with_name("run_id").unlink()
    backend.audit_run_id = None
    monkeypatch.delenv("ACT_RUN_ID", raising=False)
    before = len(backend.samples)
    with pytest.raises(module.MissionConflict, match="run_id"):
        backend.write_mission_command(route_request())
    assert len(backend.samples) == before


@pytest.mark.parametrize("waypoints", [[[32.7, -117.1]], [[32.4, -117.33]],
                                      [[float("nan"), -117.33]], [[True, -117.33]]])
def test_land_bounds_and_malformed_waypoints_rejected_before_writes(backend, waypoints):
    with pytest.raises(ValueError):
        backend.validate_mission_plan({"targets": ["Platform_30"], "waypoints": waypoints})
    with pytest.raises(ValueError):
        backend.write_mission_command(dict(route_request(), waypoints=waypoints))
    assert not backend.samples


def test_real_coastline_detour_and_rtb_use_water_route(backend, mission_modules):
    _, simulator = mission_modules
    start, finish = [32.69, -117.32], [32.65, -117.12]
    planner = backend.mission_planner
    from mission_planning import project
    assert not planner.water_segment(project(start), project(finish))
    route = planner.plan(start, [finish])
    assert len(route) > 1 and route[-1] == finish
    previous = start
    for point in route:
        assert planner.water_segment(project(previous), project(point))
        previous = point
    backend.platform_cache["Platform_30"]["init"]["PlatformInitStatus"].update(
        latitude=finish[0], longitude=finish[1])
    result = backend.write_mission_command({"operation": "return_to_base", "targets": ["Platform_30"]})
    assert not result["errors"]
    payload = simulator.parse_mission_payload(backend.samples[-1]["payload"])
    assert payload["operation"] == "return_to_base"
    assert payload["waypoints"] == [tuple(start)]
    assert len(result["commands"][0]["waypoints"]) > 1
    assert payload["waypoints"][-1] == simulator.launch_position_for("Platform_30")
    previous = finish
    for point in planner.plan(finish, payload["waypoints"]):
        assert planner.water_segment(project(previous), project(point))
        previous = point


def test_register_before_write_ack_correlates_but_lan_audit_is_not_wan_delivery(backend):
    def write(sample):
        command = backend.mission_commands[sample["command_id"]]
        assert command["state"] == "pending"
        assert not backend.record_mission_ack(ack_for(command, source="Platform_31"))
        assert not backend.record_mission_ack(ack_for(command, run_id="old-run"))
        assert not backend.record_mission_ack(ack_for(command, command_id="routine"))
        assert backend.record_mission_ack(ack_for(command))
        backend.samples.append(dict(sample))
    backend.command_writer.write = write
    result = backend.write_mission_command(route_request())
    command = result["commands"][0]
    assert command["state"] == "accepted" and command["published"]
    assert 0 <= command["updated_at"] - command["created_at"] < 1000
    assert command["created_at"] < 10**14  # milliseconds, not simulator microseconds
    assert backend.poll_audit_events()
    sent = json.loads(backend._mission_audit_path().read_text().splitlines()[0])
    assert sent["event"] == "sent" and sent["run_id"] == "mission-test-run"
    assert sent["sequence"] == backend.samples[0]["audit_sequence"]
    received = dict(sent, event="received", node="Platform_30")
    with backend._mission_audit_path().open("a") as events:
        events.write(json.dumps(received) + "\n")
    backend.poll_audit_events()
    assert backend.delivery_snapshot()["scope"] == "wan_wire"
    assert not backend.delivery_snapshot()["rows"]
    row = backend.application_delivery_snapshot()["rows"][0]
    assert (row["topic"], row["sent"], row["received"], row["lost"]) == ("ControlCommand", 1, 1, 0)
    ack_sent = dict(sent, topic="PlatformCommandAck", source_node="Platform_30",
                    source="Platform_30", destination="Control_20", node="Platform_30")
    with backend._mission_audit_path().open("a") as events:
        events.write(json.dumps(ack_sent) + "\n")
        events.write(json.dumps(dict(ack_sent, event="received", node="Control_20")) + "\n")
    backend.poll_audit_events()
    rows = backend.application_delivery_snapshot()["rows"]
    assert [(row["topic"], row["sent"], row["received"]) for row in rows] == [
        ("ControlCommand", 1, 1), ("PlatformCommandAck", 1, 1)]
    assert len(backend.mission_ack_snapshot()["acks"]) == 1


def test_timeout_late_rejection_and_bounded_histories(backend, mission_modules):
    module, _ = mission_modules
    command = backend.write_mission_command(route_request())["commands"][0]
    stored = backend.mission_commands[command["command_id"]]
    stored["created_at"] -= module.MISSION_ACK_TIMEOUT_MS
    assert backend.mission_command_snapshot()["commands"][0]["state"] == "timed_out"
    assert backend.record_mission_ack(ack_for(command, accepted=False, message="bad route"))
    current = backend.mission_command_snapshot()["commands"][0]
    assert current["state"] == "rejected" and current["late_ack"]
    assert current["ack"]["message"] == "bad route"
    for index in range(205):
        item = dict(command, command_id=f"bounded-{index}", state="pending")
        backend.mission_commands[item["command_id"]] = item
        backend._bound_history(backend.mission_commands)
        backend.record_mission_ack(ack_for(item))
    assert len(backend.mission_commands) == len(backend.mission_acks) == 200


def test_partial_success_failed_record_and_debug_mode_preserved(backend):
    def write(sample):
        if sample["destination"] == "Platform_31":
            raise RuntimeError("publication failed")
        backend.samples.append(dict(sample))
    backend.command_writer.write = write
    result = backend.write_mission_command(route_request(["Platform_30", "Platform_31", "Platform_34"]))
    assert [command["state"] for command in result["commands"]] == ["pending", "failed", "pending"]
    assert result["errors"] == [{"target": "Platform_31", "message": "publication failed"}]
    assert len(backend.samples) == 2
    assert ("Platform_34", "mission") not in backend.status_requests
    assert not backend.record_mission_ack(ack_for(result["commands"][1]))
    assert len(backend._mission_audit_path().read_text().splitlines()) == 2


@pytest.mark.parametrize("exception_type", [RuntimeError, ValueError, OSError, "dds"])
def test_expected_publication_failures_are_logged_and_reported(
        backend, mission_modules, caplog, exception_type):
    module, _ = mission_modules
    error = (module.dds.TimeoutError("DDS timeout") if exception_type == "dds"
             else exception_type("expected publication failure"))
    def write(sample):
        if sample["destination"] == "Platform_30":
            raise error
        backend.samples.append(dict(sample))
    backend.command_writer.write = write
    result = backend.write_mission_command(route_request(["Platform_30", "Platform_31"]))
    assert result["errors"] == [{"target": "Platform_30", "message": str(error)}]
    assert [command["state"] for command in result["commands"]] == ["failed", "pending"]
    assert [command["published"] for command in result["commands"]] == [False, True]
    assert any(record.exc_info and "Platform_30" in record.getMessage() for record in caplog.records)


def test_audit_io_failure_preserves_published_state_and_logs_traceback(backend, caplog):
    log_sent = backend._log_mission_sent
    def audit(sample):
        if sample["destination"] == "Platform_30":
            raise OSError("audit append failed")
        log_sent(sample)
    backend._log_mission_sent = audit
    result = backend.write_mission_command(route_request(["Platform_30", "Platform_31"]))
    assert result["errors"] == [{"target": "Platform_30", "message": "audit append failed"}]
    assert all(command["published"] and command["state"] == "pending" for command in result["commands"])
    assert len(backend.samples) == 2
    assert any(record.exc_info and "published=True" in record.getMessage() for record in caplog.records)


def test_unexpected_programming_failure_is_not_swallowed(backend):
    def write(sample):
        raise KeyError("unexpected implementation error")
    backend.command_writer.write = write
    with pytest.raises(KeyError, match="unexpected implementation error"):
        backend.write_mission_command(route_request())


def test_parallel_ids_unique_per_target_and_duplicate_id_preflight(backend, mission_modules):
    module, _ = mission_modules
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: backend.write_mission_command(
            {"operation": "cancel", "targets": ["Platform_30", "Platform_31"]}), range(8)))
    commands = [command for result in results for command in result["commands"]]
    assert len({command["command_id"] for command in commands}) == 16
    assert len({sample["audit_sequence"] for sample in backend.samples}) == 16
    body = dict(route_request(), command_id="operator-unique")
    backend.write_mission_command(body)
    before = len(backend.samples)
    with pytest.raises(module.MissionConflict, match="already exists"):
        backend.write_mission_command(body)
    assert len(backend.samples) == before


@pytest.fixture
def simulator_state(mission_modules):
    _, module = mission_modules
    simulator = module.PlatformSim.__new__(module.PlatformSim)
    simulator.home_latitude, simulator.home_longitude = module.launch_position_for("Platform_30")
    simulator.latitude, simulator.longitude = simulator.home_latitude, simulator.home_longitude
    simulator.waypoints = []
    simulator.waypoint_index = 0
    simulator.mission_id = ""
    simulator.mission_phase = "IDLE"
    simulator.mission_started_at = None
    simulator.last_motion_at = 1
    simulator.time_scale = 1.0
    simulator.mission_elapsed_s = 0.0
    simulator.loop_start = None
    simulator.navigation_speed_knots = 0.0
    simulator.navigation_state = "IDLE"
    simulator.mission_command_results = {}
    return simulator


@pytest.mark.parametrize("scale", [1, 20])
def test_demo_clock_payload_and_publication(backend, mission_modules, scale):
    bridge, simulator = mission_modules
    request = bridge.validate_mission_request({
        "operation": "set_time_scale", "targets": ["Platform_30"], "time_scale": scale,
    })
    assert simulator.parse_mission_payload(request["payload"]) == {
        "operation": "set_time_scale", "waypoints": [], "time_scale": scale,
    }
    result = backend.write_mission_command({
        "operation": "set_time_scale", "targets": ["Platform_30"], "time_scale": scale,
    })
    command = result["commands"][0]
    assert json.loads(bytes(backend.samples[0]["payload"]))["t"] == scale
    assert not backend.status_requests
    assert backend.mission_inventory_snapshot()["platforms"][0]["time_scale"] is None
    backend.record_mission_ack(ack_for(command))
    assert backend.mission_inventory_snapshot()["platforms"][0]["time_scale"] == scale


@pytest.mark.parametrize("scale", [None, True, 0, -1, 2, "20", float("nan"), float("inf")])
def test_demo_clock_invalid_rates_are_rejected(mission_modules, scale):
    bridge, simulator = mission_modules
    with pytest.raises(ValueError, match="time_scale"):
        bridge.validate_mission_request({
            "operation": "set_time_scale", "targets": ["Platform_30"], "time_scale": scale,
        })
    with pytest.raises(ValueError, match="time_scale"):
        simulator.parse_mission_payload(json.dumps({"v": 1, "op": "set_time_scale", "t": scale}).encode())


def test_demo_clock_scales_motion_without_replacing_mission(simulator_state, mission_modules, monkeypatch):
    _, module = mission_modules
    now = [10.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    sim = simulator_state
    route = b'{"v":1,"op":"route","w":[[32.71,-117.32]]}'
    assert sim.handle_mission_command(route, "route", "Control_20", "run")[0]
    sim.advance_mission()
    assert sim.latitude == sim.home_latitude
    assert sim.handle_mission_command(b'{"v":1,"op":"set_time_scale","t":20}', "clock", "Control_20", "run")[0]
    assert sim.mission_id == "route" and sim.mission_phase == "TRANSIT"
    now[0] += 2
    sim.advance_mission()
    assert sim._distance_m(sim.home_latitude, sim.home_longitude, sim.latitude, sim.longitude) == pytest.approx(
        10 * 1852 / 3600 * 20 * 2)
    assert sim.mission_elapsed_s == pytest.approx(40)
    assert sim.handle_mission_command(b'{"v":1,"op":"set_time_scale","t":1}', "normal", "Control_20", "run")[0]
    before = sim.latitude
    now[0] += 2
    sim.advance_mission()
    assert sim._distance_m(before, sim.longitude, sim.latitude, sim.longitude) == pytest.approx(10 * 1852 / 3600 * 2)
    assert sim.mission_elapsed_s == pytest.approx(42)


def test_scenario_targets_require_dds_reception_but_are_independent(backend, mission_modules):
    assert not any(target["revealed"] for target in backend.mission_inventory_snapshot()["scenario_targets"])
    backend.reveal_scenario_target({"target_id": "target_2"})
    assert backend.record_scenario_report(backend.scenario_samples[-1])
    backend.reveal_scenario_target({"target_id": "target_1"})
    assert not backend.mission_inventory_snapshot()["scenario_targets"][0]["revealed"]
    assert backend.record_scenario_report(backend.scenario_samples[-1])
    assert backend.mission_inventory_snapshot()["scenario_targets"][0]["revealed"]
    plan = backend.validate_mission_plan({"targets": ["Platform_30", "Platform_31"], "target_id": "target_1"})
    assert backend.samples == []
    for route in plan["plans"]:
        assert route["loop_start"] >= 1 and route["target_id"] == "target_1"
        assert route["loiter_radius_m"] == 1000
    commands = backend.write_mission_command({"operation": "route", "plan_id": plan["plan_id"]})["commands"]
    backend.record_mission_ack(ack_for(commands[0]))
    backend.reveal_scenario_target({"target_id": "target_2"})
    assert backend.record_scenario_report(backend.scenario_samples[-1])
    assert all(target["revealed"] for target in backend.mission_inventory_snapshot()["scenario_targets"])


def test_target_off_preserves_mission_and_rejects_late_reveal(backend, mission_modules):
    bridge, _ = mission_modules
    backend.reveal_scenario_target({"target_id": "target_1", "visible": True})
    reveal = backend.scenario_samples[-1]
    backend.record_scenario_report(reveal)
    plan = backend.validate_mission_plan({"targets": ["Platform_30"], "target_id": "target_1"})
    backend.write_mission_command({"operation": "route", "plan_id": plan["plan_id"]})
    commands = dict(backend.mission_commands)
    backend.reveal_scenario_target({"target_id": "target_1", "visible": False})
    assert backend.record_scenario_report(backend.scenario_samples[-1])
    assert not backend.mission_inventory_snapshot()["scenario_targets"][0]["revealed"]
    assert backend.mission_commands == commands
    assert not backend.record_scenario_report(reveal)
    with pytest.raises(bridge.MissionConflict, match="Reveal"):
        backend.validate_mission_plan({"targets": ["Platform_30"], "target_id": "target_1"})
    backend.reveal_scenario_target({"target_id": "target_1", "visible": True})
    assert backend.record_scenario_report(backend.scenario_samples[-1])
    assert backend.mission_inventory_snapshot()["scenario_targets"][0]["revealed"]


@pytest.mark.parametrize("visible", [None, "false", 0])
def test_target_visibility_requires_boolean(backend, visible):
    with pytest.raises(ValueError, match="boolean"):
        backend.reveal_scenario_target({"target_id": "target_1", "visible": visible})


def test_command_publication_does_not_preempt_platform_reporting(backend):
    backend.write_mission_command(route_request())
    assert backend.samples
    assert not backend.status_requests


@pytest.mark.parametrize("detail,debug,expected", [
    ("ROUTE_DISABLED", "ROUTE_DISABLED", "init"),
    ("ROUTE_ENABLED", "ROUTE_DISABLED", "mission"),
    ("ROUTE_ENABLED", "ROUTE_ENABLED", "debug"),
])
def test_reported_route_state_overrides_requested_mode(backend, detail, debug, expected):
    backend.platform_status_modes["Platform_30"] = "debug"
    backend.cache["Control_20"]["peers"][0]["health"]["routes"] = [
        {"route_name": "platform_detail_status", "state": detail},
        {"route_name": "platform_debug_status", "state": debug},
    ]
    assert backend._reported_status_mode("Platform_30") == expected


def test_missing_wan_capture_does_not_claim_network_loss(backend):
    from test_wire_delivery import observation
    backend.wire_delivery.ingest(observation(timestamp=time.time_ns() - 20_000_000_000))
    backend._aggregate_traffic_locked = lambda: [{"domain_id": 200, "contributors": ["Control_20"]}]
    row = backend.delivery_snapshot()["rows"][0]
    assert row["observed_missing"] == 1
    assert row["lost"] is None
    assert not row["percentage_ready"]
    assert row["loss_percentage"] is None
    for node in ("Control_20", "Platform_30"):
        backend.traffic_cache[node, 200] = {
            "wire_delivery_enabled": True, "capture_status": "live",
        }
    backend._aggregate_traffic_locked = lambda: [{
        "domain_id": 200, "contributors": ["Control_20", "Platform_30"],
    }]
    row = backend.delivery_snapshot()["rows"][0]
    assert not row["coverage_complete"]
    assert row["lost"] is None
    for node in ("Control_20", "Platform_30"):
        backend.traffic_cache[node, 200]["wire_coverage_since_ms"] = time.time() * 1000 - 60_000
    row = backend.delivery_snapshot()["rows"][0]
    assert row["coverage_complete"]
    assert row["lost"] == 1


def test_fresh_init_recovers_terminal_mission_and_clears_completed_team_goal(backend):
    now = time.time_ns()
    backend.team_missions = {"TEAM_A": {"version": "v1", "goal": {"kind": "waypoints"}}}
    for node, entry in backend.platform_cache.items():
        mission_id = "mission-" + node
        entry["mission"]["PlatformMissionStatus"] = {
            "mission_id": mission_id, "mission_phase": "TRANSIT", "sent_at_ns": now - 20_000_000_000,
        }
        entry["init"]["PlatformInitStatus"].update(
            mission_id=mission_id, mission_phase="COMPLETE", sent_at_ns=now)
        backend.team_followers[node] = {"team": "TEAM_A", "version": "v1", "command_id": mission_id}
        backend.team_memberships[node] = "TEAM_A"
    assert all(boat["mission"]["phase"] == "COMPLETE"
               for boat in backend.mission_inventory_snapshot()["platforms"])
    backend.reconcile_team_missions()
    assert not backend.team_missions
    assert not backend.samples


def test_new_init_mission_does_not_inherit_previous_mission_progress(backend):
    entry = backend.platform_cache["Platform_30"]
    entry["mission"]["PlatformMissionStatus"] = {
        "mission_id": "previous", "mission_phase": "COMPLETE", "waypoints_total": 12,
        "waypoint_index": 12, "sent_at_ns": 10,
    }
    entry["init"]["PlatformInitStatus"].update(
        mission_id="current", mission_phase="TRANSIT", sent_at_ns=20)
    boat = next(boat for boat in backend.mission_inventory_snapshot()["platforms"]
                if boat["platform"] == "Platform_30")
    assert boat["mission"]["mission_id"] == "current"
    assert boat["mission"]["phase"] == "TRANSIT"
    assert boat["mission"]["waypoints_total"] == 0


def test_active_mission_and_scenario_progress_survive_command_history_eviction(backend):
    backend.reveal_scenario_target({"target_id": "target_1"})
    backend.record_scenario_report(backend.scenario_samples[0])
    plan = backend.validate_mission_plan({
        "team": "TEAM_A", "targets": ["Platform_30"], "target_id": "target_1", "loiter_radius_m": 1000,
    })
    result = backend.write_mission_command({"operation": "route", "plan_id": plan["plan_id"]})
    confirm_shared_dispatch(backend, result)
    original = backend.mission_inventory_snapshot()["platforms"]
    for index in range(40):
        backend.write_mission_command({
            "operation": "set_time_scale", "targets": [f"Platform_{i}" for i in range(30, 35)],
            "time_scale": 20 if index % 2 else 1,
        })
    assert len(backend.mission_commands) == 200
    assert not any(command["command_id"] in backend.mission_commands for command in result["commands"])
    snapshot = backend.mission_inventory_snapshot()
    assert snapshot["scenario_targets"][0]["dispatched"]
    for before, after in zip(original, snapshot["platforms"]):
        assert after["mission"]["command"] == before["mission"]["command"]
        assert after["mission"]["target_id"] == "target_1"
        assert after["mission"]["loiter_radius_m"] == 1000
    assert len(backend.mission_active_commands) == 5
    assert backend.reveal_scenario_target({"target_id": "target_2"})["published"]
    backend.platform_cache["Platform_30"]["mission"]["PlatformMissionStatus"]["mission_id"] = "external-goal"
    boat = backend.mission_inventory_snapshot()["platforms"][0]
    assert boat["mission"]["command"] is None and boat["mission"]["target_id"] is None


@pytest.mark.parametrize("radius", [500, 1000, 2000, 3000])
def test_loiter_distance_is_preserved_in_validated_dds_route(backend, simulator_state, radius):
    backend.reveal_scenario_target({"target_id": "target_1"})
    backend.record_scenario_report(backend.scenario_samples[0])
    plan = backend.validate_mission_plan({
        "targets": ["Platform_30"], "target_id": "target_1", "loiter_radius_m": radius})
    route = plan["plans"][0]
    assert route["loiter_radius_m"] == radius
    ring = route["waypoints"][route["loop_start"] - 1:]
    for first, last in zip(ring, ring[1:]):
        mid = [(first[0] + last[0]) / 2, (first[1] + last[1]) / 2]
        assert simulator_state._distance_m(32.755, -117.36, *mid) >= radius
    command = backend.write_mission_command({"operation": "route", "plan_id": plan["plan_id"]})["commands"][0]
    assert command["loiter_radius_m"] == radius
    payload = json.loads(bytes(backend.samples[-1]["payload"]))
    assert payload["l"] == command["loop_start"]
    assert payload["w"] == command["waypoints"]


@pytest.mark.parametrize("radius", [True, None, "1000", 0, 999, float("nan"), float("inf")])
def test_invalid_loiter_distance_is_rejected_without_writes(backend, radius):
    with pytest.raises(ValueError, match="loiter_radius_m"):
        backend.validate_mission_plan({
            "targets": ["Platform_30"], "target_id": "target_1", "loiter_radius_m": radius})
    assert backend.samples == []


def test_continuous_orbit_survives_multiple_laps_and_can_cancel(simulator_state, mission_modules, monkeypatch):
    _, module = mission_modules
    now = [10.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    sim = simulator_state
    points = [[sim.home_latitude, sim.home_longitude], [sim.home_latitude + .0001, sim.home_longitude],
              [sim.home_latitude + .0001, sim.home_longitude + .0001], [sim.home_latitude, sim.home_longitude]]
    payload = json.dumps({"v": 1, "op": "route", "w": points, "l": 1}).encode()
    assert sim.handle_mission_command(payload, "orbit", "Control_20", "run")[0]
    now[0] += 120
    sim.advance_mission()
    assert sim.mission_phase == "ORBIT" and sim.mission_id == "orbit"
    assert 1 <= sim.waypoint_index < len(points)
    before = sim.latitude, sim.longitude
    now[0] += 2
    sim.advance_mission()
    assert (sim.latitude, sim.longitude) != before and sim.mission_phase == "ORBIT"
    sim.handle_mission_command(b'{"v":1,"op":"cancel"}', "cancel", "Control_20", "run")
    assert sim.mission_phase == "CANCELLED" and sim.loop_start is None


@pytest.mark.parametrize("index", [-1, True, 2, None])
def test_invalid_orbit_loop_is_rejected(mission_modules, index):
    bridge, simulator = mission_modules
    body = {"operation": "route", "targets": ["Platform_30"],
            "waypoints": [[32.69, -117.32], [32.70, -117.32]], "loop_start": index}
    with pytest.raises(ValueError, match="loop_start"):
        bridge.validate_mission_request(body)
    with pytest.raises(ValueError, match="loop_start"):
        simulator.parse_mission_payload(json.dumps({"v": 1, "op": "route", "w": body["waypoints"], "l": index}).encode())


@pytest.mark.parametrize("operation", ["route", "replace_route", "return_to_base"])
def test_motion_clock_reset_and_reliable_redelivery_idempotent(
        simulator_state, mission_modules, monkeypatch, operation):
    _, module = mission_modules
    now = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    sim = simulator_state
    points = ([[32.6901, -117.32], [sim.home_latitude, sim.home_longitude]]
              if operation == "return_to_base" else [[32.6901, -117.32]])
    payload = json.dumps({"v": 1, "op": operation, "w": points}).encode()
    assert sim.handle_mission_command(payload, "test-command", "Control_20", "run")[0]
    assert sim.last_motion_at == now[0]
    sim.advance_mission()
    assert sim.latitude == sim.home_latitude  # No pre-command elapsed travel.
    now[0] += 0.5
    sim.advance_mission()
    assert 0 < sim.latitude - sim.home_latitude < 0.0001
    before = (sim.waypoint_index, sim.latitude, sim.last_motion_at, sim.mission_started_at)
    assert sim.handle_mission_command(payload, "test-command", "Control_20", "run")[0]
    assert (sim.waypoint_index, sim.latitude, sim.last_motion_at, sim.mission_started_at) == before
    now[0] += 10
    sim.advance_mission()
    assert sim.mission_phase == "COMPLETE"
    assert sim.navigation_speed_knots == 0
    assert sim.navigation_state == "COMPLETE"
    assert sim.handle_mission_command(payload, "test-command", "Control_20", "run")[0]
    assert sim.mission_phase == "COMPLETE"


@pytest.mark.parametrize("with_navigator", [False, True])
def test_terminal_speed_is_zero_on_the_completion_tick(
        simulator_state, mission_modules, monkeypatch, with_navigator):
    _, module = mission_modules
    monkeypatch.setattr(module.time, "monotonic", lambda: 1000.0)
    sim = simulator_state
    sim.args = SimpleNamespace(source="Platform_30")
    sim.heading_deg = 0.0
    sim.navigation_speed_knots = 10.0
    sim.navigation_state = "DIRECT"
    sim.mission_phase = "RTB"
    sim.last_motion_at = 999.0
    sim.waypoints = [(sim.latitude + 0.000001, sim.longitude)]
    if with_navigator:
        sim.navigator = SimpleNamespace(step=lambda start, destination, *_args: (
            destination, "DIRECT"))
    sim.advance_mission()
    assert sim.mission_phase == "COMPLETE"
    assert sim.navigation_speed_knots == 0
    assert sim.navigation_state == "COMPLETE"


def test_simulator_rejected_duplicates_and_cancel_are_idempotent(simulator_state):
    sim = simulator_state
    result = sim.handle_mission_command(b'{"v":2}', "bad", "Control_20", "run")
    assert not result[0]
    assert sim.handle_mission_command(b'{"v":1,"op":"cancel"}', "bad", "Control_20", "run") == result
    assert sim.mission_phase == "IDLE"
    sim.navigation_speed_knots = 10.0
    sim.navigation_state = "DIRECT"
    assert sim.handle_mission_command(b'{"v":1,"op":"cancel"}', "cancel", "Control_20", "run")[0]
    assert sim.mission_phase == "CANCELLED"
    assert sim.navigation_speed_knots == 0
    assert sim.navigation_state == "CANCELLED"
    for index in range(205):
        sim.handle_mission_command(b'{"v":1,"op":"cancel"}', str(index), "Control_20", "run")
    assert len(sim.mission_command_results) == 200


@pytest.mark.parametrize("planner_failure", [False, True])
def test_rejected_return_to_base_preserves_active_navigation(
        simulator_state, monkeypatch, planner_failure):
    from mission_navigation import Navigator
    sim = simulator_state
    sim.args = SimpleNamespace(source="Platform_30")
    sim.navigator = Navigator("Platform_30", "mission-test-run")
    sim.apply_mission_command({
        "operation": "route", "goal": {
            "kind": "loiter", "center": [32.755, -117.36], "radius_m": 1000,
        }, "fleet": ["Platform_30", "Platform_31"],
    }, "old-goal")
    nav = sim.navigator
    nav.detour = {"position": (1, 2), "goal": (3, 4)}
    before = (list(sim.waypoints), sim.last_motion_at, set(nav.expected_peers),
              list(nav.exclusions), dict(nav.detour))
    if planner_failure:
        def fail_plan(*_args):
            raise ValueError("No water connection to the planning grid.")
        monkeypatch.setattr(nav.planner, "plan", fail_plan)
    payload = json.dumps({
        "v": 1, "op": "return_to_base", "f": ["Platform_30"],
        "w": [[sim.home_latitude, sim.home_longitude]] if planner_failure else [[32.74, -117.4]],
    }).encode()
    accepted, message = sim.handle_mission_command(payload, "bad-rtb", "Control_20", "mission-test-run")
    assert not accepted
    assert ("No water connection" if planner_failure else "end at the platform home") in message
    assert sim.mission_id == "old-goal"
    assert before == (sim.waypoints, sim.last_motion_at, nav.expected_peers, nav.exclusions, nav.detour)
    assert nav.step((sim.latitude, sim.longitude), sim.waypoints[0], 10, .1, 1000)[1] == "WAITING_PEERS"


def test_http_validation_and_partial_publication_contract(backend, mission_modules):
    module, _ = mission_modules
    from aiohttp.test_utils import TestClient, TestServer
    backend.start_poll_thread = lambda: None
    async def check():
        async with TestClient(TestServer(module.build_app(
                backend, REPO_ROOT / "gui/mesh_dashboard/static"))) as client:
            response = await client.post("/api/mission_validate", json={
                "targets": ["Platform_30"], "waypoints": [[32.68, -117.33]]})
            assert response.status == 200
            validated = await response.json()
            assert not backend.samples
            response = await client.post("/api/mission_commands", json={
                "operation": "route", "plan_id": validated["plan_id"]})
            assert response.status == 200 and not (await response.json())["errors"]
            response = await client.post("/api/mission_commands", json={
                "operation": "route", "plan_id": "expired"})
            assert response.status == 409
            response = await client.post("/api/mission_validate", json={
                "targets": ["Platform_30"], "waypoints": [[32.7, -117.1]]})
            assert response.status == 400 and "water" in (await response.json())["error"]
            response = await client.post("/api/mission_commands", json={
                "operation": "cancel", "targets": ["Platform_30"], "waypoints": []})
            assert response.status == 400
            backend.platform_cache["Platform_30"]["init_updated_at"] = 1
            response = await client.post("/api/mission_validate", json={
                "targets": ["Platform_30"], "waypoints": [[32.68, -117.33]]})
            assert response.status == 409 and "stale" in (await response.json())["error"]
            backend.platform_cache["Platform_30"]["init_updated_at"] = int(time.time() * 1000)
            def write(sample):
                if sample["destination"] == "Platform_31":
                    raise RuntimeError("DDS write failed")
                backend.samples.append(dict(sample))
            backend.command_writer.write = write
            response = await client.post("/api/mission_commands", json={
                "operation": "cancel", "targets": ["Platform_30", "Platform_31"]})
            result = await response.json()
            assert response.status == 207
            assert result["errors"] == [{"target": "Platform_31", "message": "DDS write failed"}]
            response = await client.post("/api/mission_commands", json={
                "operation": "cancel", "targets": ["Platform_31"]})
            assert response.status == 503 and (await response.json())["commands"][0]["state"] == "failed"
    asyncio.run(check())
