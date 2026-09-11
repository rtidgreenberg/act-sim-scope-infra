"""End-to-end control-topic coverage for platform_mesh_control.py.

Drives the production control-platform configuration through the same DDS topics used by
the dashboard. The helper process translates the delivered samples into local router admin
commands, which this test verifies through target-router acknowledgements and route state.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import rti.connextdds as dds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import REPO_ROOT, set_wan_qos_env  # noqa: E402
from util.dds_probe import AdminChannel, Probe, wait_for_route  # noqa: E402

CONFIG = "control-platform.yaml"
PLATFORM_NODE = "Platform_30"
PLATFORM_ROUTER = "platform-30-control-platform"
DETAIL_ROUTE = "platform_detail_status"
DEBUG_ROUTE = "platform_debug_status"
MISSION_TOPICS = (
    ("PlatformDetailStatus", "platform_detail_status"),
    ("PlatformMissionStatus", "platform_mission_status"),
    ("PlatformWaypointStatus", "platform_waypoint_status"),
)
DEBUG_TOPICS = (
    ("PlatformDebugStatus", "platform_debug_status"),
    ("PlatformThrusterStatus", "platform_thruster_status"),
    ("PlatformPowerStatus", "platform_power_status"),
)


def _start_platform_mesh_control(domains, e2e_tmp_dir):
    environment = os.environ.copy()
    environment["NDDS_QOS_PROFILES"] = ";".join((
        str(REPO_ROOT / "harness_v2" / "qos" / "act_qos_profiles.xml"),
        str(REPO_ROOT / "harness_v2" / "datamodel" / "gen" / "ActTypes.xml"),
    ))
    log_path = e2e_tmp_dir / "platform_mesh_control.log"
    log_file = open(log_path, "w")
    process = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "harness_v2" / "scripts" /
                             "platform_mesh_control.py"),
         "--domain", str(domains["platform_lan"]), "--node", PLATFORM_NODE,
         "--router-name", PLATFORM_ROUTER],
        cwd=REPO_ROOT, env=environment, stdout=log_file, stderr=subprocess.STDOUT)
    return process, log_file, log_path


def _write_until_route(writer, sample, status_reader, route_name, predicate, alive):
    deadline = time.monotonic() + 20.0
    facts = None
    while time.monotonic() < deadline:
        assert alive(), "router or platform mesh control process exited"
        writer.write(sample)
        facts = wait_for_route(status_reader, route_name, predicate,
                               timeout_s=0.3, poll_s=0.05, check_alive=alive)
        if facts is not None and predicate(facts):
            return facts
    return facts


def test_dashboard_control_topics_reconfigure_target_platform(
        router_pair, admin_types_xml, unique_domains, e2e_tmp_dir):
    set_wan_qos_env()
    control_proc, platform_proc, _ = router_pair(CONFIG, unique_domains)
    control = Probe(unique_domains["control_lan"])
    platform = Probe(unique_domains["platform_lan"])
    mesh_control = log_file = None
    try:
        provider = dds.QosProvider(str(admin_types_xml))
        platform_admin = AdminChannel(platform, provider)
        team_type = provider.type("TeamAssignment")
        mode_type = provider.type("PlatformStatusMode")
        team_writer = control.writer("ActTeamAssignment", "TeamAssignment", dtype=team_type)
        mode_writer = control.writer("ActPlatformStatusMode", "PlatformStatusMode", dtype=mode_type)
        status_writers = [
            platform.writer(topic, type_name)
            for topic, type_name in MISSION_TOPICS + DEBUG_TOPICS
        ]

        mesh_control, log_file, log_path = _start_platform_mesh_control(
            unique_domains, e2e_tmp_dir)
        alive = lambda: (control_proc.is_alive() and platform_proc.is_alive() and
                         mesh_control.poll() is None)  # noqa: E731

        team = dds.DynamicData(team_type)
        team["platform_node"] = PLATFORM_NODE
        team["team_name"] = "alpha"
        deadline = time.monotonic() + 20.0
        ack = None
        while time.monotonic() < deadline and ack is None:
            assert alive(), f"platform mesh control exited; log={log_path}"
            team_writer.write(team)
            ack = platform_admin.acks.wait("team-ctrl-1", timeout_s=0.3,
                                           poll_s=0.05, check_alive=alive)
        assert ack is not None and ack["accepted"], (
            f"TeamAssignment did not add the target WAN partition: {ack}; log={log_path}")

        mission = dds.DynamicData(mode_type)
        mission["platform_node"] = PLATFORM_NODE
        mission["resolution_mode"] = 1  # STATUS_MISSION
        mission["request_id"] = "e2e-mission"
        facts = _write_until_route(
            mode_writer, mission, platform_admin.status, DETAIL_ROUTE,
            lambda state: state["state"] == "ROUTE_ENABLED", alive)
        assert facts is not None and facts["state"] == "ROUTE_ENABLED", (
            f"MISSION did not enable {DETAIL_ROUTE}: {facts}; log={log_path}")

        debug = dds.DynamicData(mode_type)
        debug["platform_node"] = PLATFORM_NODE
        debug["resolution_mode"] = 2  # STATUS_DEBUG
        debug["request_id"] = "e2e-debug"
        facts = _write_until_route(
            mode_writer, debug, platform_admin.status, DEBUG_ROUTE,
            lambda state: state["state"] == "ROUTE_ENABLED", alive)
        assert facts is not None and facts["state"] == "ROUTE_ENABLED", (
            f"DEBUG did not enable {DEBUG_ROUTE}: {facts}; log={log_path}")

        init = dds.DynamicData(mode_type)
        init["platform_node"] = PLATFORM_NODE
        init["resolution_mode"] = 0  # STATUS_INIT
        init["request_id"] = "e2e-init"
        facts = _write_until_route(
            mode_writer, init, platform_admin.status, DETAIL_ROUTE,
            lambda state: state["state"] == "ROUTE_DISABLED", alive)
        assert facts is not None and facts["state"] == "ROUTE_DISABLED", (
            f"INIT did not disable {DETAIL_ROUTE}: {facts}; log={log_path}")
    finally:
        if mesh_control is not None and mesh_control.poll() is None:
            mesh_control.terminate()
            mesh_control.wait(timeout=5.0)
        if log_file is not None:
            log_file.close()
        control.close()
        platform.close()