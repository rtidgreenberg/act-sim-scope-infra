#!/usr/bin/env python3
"""Minimal DDS<->HTTP/WebSocket bridge for gui/mesh_dashboard/, replacing RTI Web
Integration Service (WIS). Design/rationale:
docs/cpp_router/mesh-dashboard-bridge-implementation-plan.md.

Reads ActRouterMeshStatus (control_lan, domain 20 by default) and serves it as:
  GET  /api/mesh_status        -> [{"data": {...}}, ...]  (current cached samples)
  GET  /ws                     -> WebSocket; pushes {"data": {...}} per new sample
  POST /api/team_assignment    -> body {"platform_node": ..., "team_name": ...};
                                   writes one ActTeamAssignment sample, 204 on success
  POST /api/mission_validate   -> {targets, waypoints}; returns a five-minute plan_id
  POST /api/mission_commands   -> {operation, plan_id} for validated routes, or legacy
                                   {operation, targets, waypoints}; cancel/RTB omit
                                   waypoints. Returns {commands, errors}, 200/207.
Also serves the static dashboard page (gui/mesh_dashboard/static/) at "/".

The "data" envelope on the REST/WebSocket responses matches what mesh_graph.js's
ingestSampleArray() already expects (it was written against WIS's own REST1/WS
envelope shape), so the front-end's sample-parsing code needed no changes -- only
the URL/connection plumbing did.
"""
import argparse
import asyncio
import json
import logging
import functools
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from aiohttp import web
try:
    from emane_config import EmaneConfiguration
    from system_config import load_system_config, duration as config_duration
except ModuleNotFoundError as exc:
    if exc.name not in ("emane_config", "system_config"):
        raise
    from gui.mesh_dashboard.server.emane_config import EmaneConfiguration
    from gui.mesh_dashboard.server.system_config import load_system_config, duration as config_duration
try:
    from wire_delivery import WireDelivery
    from delivery_config import load_delivery_config
except ModuleNotFoundError as exc:
    if exc.name != "wire_delivery":
        raise
    from gui.mesh_dashboard.server.wire_delivery import WireDelivery
    from gui.mesh_dashboard.server.delivery_config import load_delivery_config
try:
    from traffic_metrics import aggregate as aggregate_metrics, coherent_samples, validate_sample
except ModuleNotFoundError as exc:
    if exc.name != "traffic_metrics":
        raise
    from gui.mesh_dashboard.server.traffic_metrics import aggregate as aggregate_metrics, coherent_samples, validate_sample
import rti.connextdds as dds
try:
    from mission_planning import WaterPlanner, SQUADS, launch_position_for, SCENARIO_TARGETS, orbit_points, LOITER_RADII_M, DEFAULT_LOITER_RADIUS_M
except ModuleNotFoundError:
    from gui.mesh_dashboard.server.mission_planning import WaterPlanner, SQUADS, launch_position_for, SCENARIO_TARGETS, orbit_points, LOITER_RADII_M, DEFAULT_LOITER_RADIUS_M

logger = logging.getLogger(__name__)

_THIS_FILE = Path(__file__).resolve()
DEFAULT_TYPES_XML = _THIS_FILE.parents[3] / "harness_v2" / "datamodel" / "gen" / "ActTypes.xml"
DEFAULT_QOS_XML = _THIS_FILE.parents[3] / "harness_v2" / "qos" / "act_qos_profiles.xml"
DEFAULT_STATIC_DIR = _THIS_FILE.parents[1] / "static"

MESH_STATUS_TOPIC = "ActRouterMeshStatus"
MESH_STATUS_TYPE = "RouterMeshStatus"
LINK_STATS_TOPIC = "ActRouterLinkStats"
LINK_STATS_TYPE = "RouterLinkStats"
TEAM_ASSIGNMENT_TOPIC = "ActTeamAssignment"
TEAM_ASSIGNMENT_TYPE = "TeamAssignment"
STATUS_MODE_TOPIC = "ActPlatformStatusMode"
STATUS_MODE_TYPE = "PlatformStatusMode"
CONTROL_COMMAND_TOPIC = "ControlCommand"
CONTROL_COMMAND_TYPE = "control_command"
CONTROL_COMMAND_ACK_TOPIC = "PlatformCommandAck"
CONTROL_COMMAND_ACK_TYPE = "control_command_ack"
MISSION_PAYLOAD_VERSION = 1
MISSION_OPERATIONS = {"route", "replace_route", "cancel", "return_to_base", "set_time_scale"}
MAX_MISSION_TARGETS = 6
MAX_MISSION_WAYPOINTS = 64
MAX_MISSION_PAYLOAD_BYTES = 8192
MISSION_COMMAND_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
PLATFORM_NODE_RE = re.compile(r"^Platform_([3-9]\d)$")
MISSION_HISTORY_LIMIT = 200
MISSION_ACK_TIMEOUT_MS = 15_000
MISSION_FRESHNESS_MS = 12_000
MISSION_PLAN_TTL_MS = 300_000
MISSION_PLAN_LIMIT = 200


class MissionConflict(ValueError):
    """Valid request that cannot be executed against current mission state."""


INIT_STATUS_TOPICS = {
    "PlatformInitStatus": "platform_init_status",
}
MISSION_STATUS_TOPICS = {
    "PlatformDetailStatus": "platform_detail_status",
    "PlatformMissionStatus": "platform_mission_status",
    "PlatformWaypointStatus": "platform_waypoint_status",
}
DEBUG_STATUS_TOPICS = {
    "PlatformDebugStatus": "platform_debug_status",
    "PlatformThrusterStatus": "platform_thruster_status",
    "PlatformPowerStatus": "platform_power_status",
}
MISSION_MODE_TOPICS = set(MISSION_STATUS_TOPICS)
DEBUG_MODE_TOPICS = set(DEBUG_STATUS_TOPICS)
FIXED_CONTROL_RECIPIENT_TOPICS = {
    "PlatformInitStatus", "PlatformDetailStatus", "PlatformMissionStatus",
    "PlatformWaypointStatus", "PlatformDebugStatus", "PlatformThrusterStatus",
    "PlatformPowerStatus", "PlatformCommandAck", "ContactReport",
}
PERCENTAGE_MIN_SAMPLES = 30
DELIVERY_SETTLE_MS = 2_000
RELIABLE_DELIVERY_SETTLE_MS = 15_000
RELIABLE_DELIVERY_TOPICS = {"ControlCommand", "PlatformCommandAck", "RouterHealth"}
EMANE_EVENT_GROUP = "224.1.2.8"
EMANE_EVENT_PORT = "45702"
EMANE_PATHLOSS_MAX_DB = 200
EMANE_CONTROLLER_ENABLED = True
EMANE_COMMEFFECT_MAX_LATENCY_MS = 60_000
EMANE_COMMEFFECT_MAX_BANDWIDTH_BPS = 1_000_000_000


def validate_mission_request(body):
    """Return a normalized command request and compact DDS payload bytes."""
    if not isinstance(body, dict):
        raise ValueError("request must be a JSON object")
    if set(body) - {"operation", "targets", "waypoints", "command_id", "time_scale", "loop_start"}:
        raise ValueError("request has unsupported fields")
    operation = body.get("operation")
    if operation not in MISSION_OPERATIONS:
        raise ValueError("operation must be route, replace_route, cancel, return_to_base, or set_time_scale")
    scale = body.get("time_scale")
    if operation == "set_time_scale":
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or scale not in (1, 20):
            raise ValueError("time_scale must be 1 or 20")
    elif "time_scale" in body:
        raise ValueError(f"{operation} does not accept time_scale")
    targets = body.get("targets")
    if (not isinstance(targets, list) or not 1 <= len(targets) <= MAX_MISSION_TARGETS or
            any(not isinstance(target, str) or not PLATFORM_NODE_RE.fullmatch(target)
                for target in targets) or len(set(targets)) != len(targets)):
        raise ValueError(f"targets must contain 1 to {MAX_MISSION_TARGETS} unique Platform_30..Platform_99 names")
    requested_id = body.get("command_id")
    if requested_id is not None and (not isinstance(requested_id, str) or
                                     not MISSION_COMMAND_ID_RE.fullmatch(requested_id)):
        raise ValueError("command_id must be 1 to 64 URL-safe characters")
    waypoints = body.get("waypoints", [])
    if operation in {"route", "replace_route"}:
        if not isinstance(waypoints, list) or not 1 <= len(waypoints) <= MAX_MISSION_WAYPOINTS:
            raise ValueError(f"{operation} requires 1 to {MAX_MISSION_WAYPOINTS} waypoints")
        compact_waypoints = []
        for waypoint in waypoints:
            if (not isinstance(waypoint, (list, tuple)) or len(waypoint) != 2 or
                    any(isinstance(value, bool) or not isinstance(value, (int, float))
                        for value in waypoint)):
                raise ValueError("each waypoint must be [latitude, longitude]")
            latitude, longitude = float(waypoint[0]), float(waypoint[1])
            if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
                raise ValueError("waypoint coordinate is out of range")
            compact_waypoints.append([round(latitude, 6), round(longitude, 6)])
    elif "waypoints" in body:
        raise ValueError(f"{operation} does not accept waypoints")
    else:
        compact_waypoints = []
    loop_start = body.get("loop_start")
    if "loop_start" in body:
        if (operation not in {"route", "replace_route"} or isinstance(loop_start, bool) or
                not isinstance(loop_start, int) or not 0 <= loop_start < len(compact_waypoints) or
                len({tuple(point) for point in compact_waypoints[loop_start:]}) < 2):
            raise ValueError("loop_start must identify a non-degenerate route circuit")
    payload_object = {"v": MISSION_PAYLOAD_VERSION, "op": operation}
    if compact_waypoints:
        payload_object["w"] = compact_waypoints
    if operation == "set_time_scale":
        payload_object["t"] = scale
    if "loop_start" in body:
        payload_object["l"] = loop_start
    payload = json.dumps(payload_object, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(payload) > MAX_MISSION_PAYLOAD_BYTES:
        raise ValueError("mission payload exceeds the 8192-byte ControlCommand limit")
    return {
        "operation": operation,
        "targets": targets,
        "waypoints": compact_waypoints,
        "command_id": requested_id,
        "payload": payload,
        **({"time_scale": scale} if operation == "set_time_scale" else {}),
        **({"loop_start": loop_start} if "loop_start" in body else {}),
    }


def _qos_provider_for(types_xml: Path):
    """Avoid double-parsing types_xml's XML DOM in this process.

    run_mesh.sh exports NDDS_QOS_PROFILES including this exact file for
    platform_mesh_control.py's benefit; Connext auto-loads that at process init, so if this
    process inherited that env var, loading the file again explicitly hits a double-parse
    error. Reuse QosProvider.default (which already has it loaded) instead, in that case only.
    """
    resolved = types_xml.resolve()
    for entry in os.environ.get("NDDS_QOS_PROFILES", "").split(";"):
        entry = entry.strip()
        if entry and Path(entry).resolve() == resolved:
            return dds.QosProvider.default
    return dds.QosProvider(f"{DEFAULT_QOS_XML};{types_xml}")


class DdsBridge:
    """Owns the one DomainParticipant + reader + writer this service needs."""

    def __init__(self, domain_id, types_xml, poll_interval, participant_qos_profile=None,
                 traffic_observers=None, router_config=None):
        self.poll_interval = poll_interval
        self.cache = {}          # observer_node -> latest sample dict
        self.mesh_received_at = {}
        self.platform_cache = {} # platform_node -> latest per-resolution topic samples
        self.cache_lock = threading.Lock()
        self.ws_clients = set()  # set[web.WebSocketResponse]
        self.loop = None         # set once the aiohttp event loop is running
        self._stop = threading.Event()

        qp = _qos_provider_for(types_xml)
        self.delivery_config = load_delivery_config(router_config, dds.QosProvider, dds.Duration.infinite)
        self.system_config = load_system_config(router_config, dds.QosProvider, dds.Duration.infinite)
        self.system_config["sections"].append({
            "id": "dashboard", "title": "Dashboard and mission limits",
            "description": "Values used by this running dashboard process, not router reachability timers.",
            "columns": ["Setting", "Current value", "Meaning"],
            "rows": [
                ["Observer LAN domain", domain_id, "DDS domain used by the dashboard bridge."],
                ["DDS polling interval", config_duration(poll_interval), "Local reader polling cadence."],
                ["Mission position / presence freshness", config_duration(MISSION_FRESHNESS_MS / 1000),
                 "Maximum telemetry age for mission availability; does not mark router presence STALE."],
                ["Mission acknowledgement timeout", config_duration(MISSION_ACK_TIMEOUT_MS / 1000),
                 "How long a pending mission command waits for an acknowledgement."],
                ["Validated mission plan lifetime", config_duration(MISSION_PLAN_TTL_MS / 1000),
                 "A validated plan must be dispatched before it expires."],
                ["Maximum mission targets", MAX_MISSION_TARGETS, "Maximum boats per mission command."],
                ["Maximum mission waypoints", MAX_MISSION_WAYPOINTS, "Maximum waypoints per route command."],
                ["Reliable delivery observation grace", config_duration(WireDelivery.RELIABLE_GRACE_MS / 1000),
                 "WAN wire delivery waits for repair before counting missing reliable samples."],
                ["Other delivery observation grace", config_duration(WireDelivery.OTHER_GRACE_MS / 1000),
                 "WAN wire delivery grace for non-reliable samples."],
            ],
        })
        mesh_type = qp.type(MESH_STATUS_TYPE)
        link_stats_type = qp.type(LINK_STATS_TYPE)
        diagnostics_type = qp.type("RouterDiagnostics")
        team_type = qp.type(TEAM_ASSIGNMENT_TYPE)
        status_mode_type = qp.type(STATUS_MODE_TYPE)
        control_command_type = qp.type(CONTROL_COMMAND_TYPE)
        control_command_ack_type = qp.type(CONTROL_COMMAND_ACK_TYPE)

        pqos = qp.participant_qos_from_profile(
            participant_qos_profile or "ACT_QOS_LIB::lan_participant_base")
        self.participant = dds.DomainParticipant(domain_id, pqos)

        mesh_topic = dds.DynamicData.Topic(self.participant, MESH_STATUS_TOPIC, mesh_type)
        subscriber = dds.Subscriber(self.participant)
        self.reader = dds.DynamicData.DataReader(
            subscriber, mesh_topic, qp.datareader_qos_from_profile("ACT_QOS_LIB::router_mesh_status"))
        diagnostics_topic = dds.DynamicData.Topic(self.participant, "ActRouterDiagnostics", diagnostics_type)
        self.diagnostics_reader = dds.DynamicData.DataReader(
            subscriber, diagnostics_topic, qp.datareader_qos_from_profile("ACT_QOS_LIB::lan_event"))
        self.router_diagnostics_cache = {}
        link_stats_topic = dds.DynamicData.Topic(self.participant, LINK_STATS_TOPIC,
                                                 link_stats_type)
        link_stats_qos = qp.datareader_qos_from_profile("ACT_QOS_LIB::router_link_stats")
        self.link_stats_reader = dds.DynamicData.DataReader(
            subscriber, link_stats_topic, link_stats_qos)
        self.link_stats_cache = {}
        self.link_stats_received_at = {}
        self.team_topic = dds.DynamicData.Topic(self.participant, TEAM_ASSIGNMENT_TOPIC, team_type)
        ack_topic = dds.DynamicData.Topic(
            self.participant, CONTROL_COMMAND_ACK_TOPIC, control_command_ack_type)
        self.command_ack_reader = dds.DynamicData.DataReader(
            subscriber, ack_topic, qp.datareader_qos_from_profile("ACT_QOS_LIB::lan_event"))
        self.scenario_type = qp.type("contact_report")
        self.scenario_topic = dds.DynamicData.Topic(self.participant, "ContactReport", self.scenario_type)
        self.scenario_reader = dds.DynamicData.DataReader(
            subscriber, self.scenario_topic, qp.datareader_qos_from_profile("ACT_QOS_LIB::lan_event"))
        self.scenario_reports = {}

        # Platform status readers (all best-effort + volatile on control_lan).
        self.platform_readers = []
        topic_specs = []
        for topic, tname in INIT_STATUS_TOPICS.items():
            topic_specs.append(("init", topic, tname))
        for topic, tname in MISSION_STATUS_TOPICS.items():
            topic_specs.append(("mission", topic, tname))
        for topic, tname in DEBUG_STATUS_TOPICS.items():
            topic_specs.append(("debug", topic, tname))

        for level, topic_name, type_name in topic_specs:
            dtype = qp.type(type_name)
            topic = dds.DynamicData.Topic(self.participant, topic_name, dtype)
            reader = dds.DynamicData.DataReader(subscriber, topic)
            self.platform_readers.append((level, topic_name, reader))

        self.traffic_cache = {}  # (observer, domain_id) -> latest sample dict
        self.traffic_history = {}  # (observer, domain_id) -> bounded capture-window history
        self.traffic_observers = set(traffic_observers or [])
        self.traffic_published_at = {}  # (observer, domain_id) -> timestamp in last aggregate
        self.latest_traffic_aggregate = []
        self.emane_cache = {}  # observer -> latest MAC transmit delta
        self.emane_published_at = {}  # observer -> timestamp in last aggregate
        self.latest_emane_aggregate = None
        self.audit_offsets = {}  # events.jsonl path -> consumed byte offset
        self.audit_run_id = None
        self.audit_sent = {}  # (run_id, topic, source, sequence) -> sent event
        self.audit_received = {}  # (run_id, topic, source, sequence, receiver) -> receive event
        self.audit_versions = {}  # (topic, source, recipient) -> [sent, received]
        self.wire_delivery = WireDelivery()
        self.platform_status_modes = {}  # platform_node -> init|mission|debug
        self.platform_status_mode_changed_ns = {}  # platform_node -> monotonic mode-change cutoff
        self.emane_impairments = {}
        self.mesh_updated_at = {}
        self.mission_commands = {}
        self.mission_active_commands = {}
        self.mission_acks = {}
        self.mission_command_sequence = 0
        self.mission_lock = threading.Lock()
        self.mission_plans = {}
        self.mission_planner = WaterPlanner()
        self.team_missions = {}
        self.team_dispatches = {}
        self.team_memberships = {}
        self.team_followers = {}
        self.mission_inheritance_errors = {}
        self.scenario_dispatched_targets = set()

        # TeamAssignmentWriterQos equivalent: VOLATILE + RELIABLE.
        publisher = dds.Publisher(self.participant)
        self.scenario_writer = dds.DynamicData.DataWriter(
            publisher, self.scenario_topic, qp.datawriter_qos_from_profile("ACT_QOS_LIB::lan_event"))
        self.writer = dds.DynamicData.DataWriter(publisher, self.team_topic)
        command_topic = dds.DynamicData.Topic(
            self.participant, CONTROL_COMMAND_TOPIC, control_command_type)
        self.command_writer = dds.DynamicData.DataWriter(
            publisher, command_topic, qp.datawriter_qos_from_profile("ACT_QOS_LIB::lan_event"))

        # StatusResolution writer (RELIABLE + VOLATILE).
        status_topic = dds.DynamicData.Topic(self.participant, STATUS_MODE_TOPIC,
                             status_mode_type)
        self.status_mode_writer = dds.DynamicData.DataWriter(publisher, status_topic)

        self.team_type = team_type
        self.status_mode_type = status_mode_type
        self.control_command_type = control_command_type

    def start_poll_thread(self):
        threading.Thread(target=self._poll_loop, daemon=True, name="dds-poll").start()

    def _poll_loop(self):
        while not self._stop.is_set():
            for data, info in self.reader.take():
                if not info.valid:
                    continue
                sample = json.loads(data.to_json())
                key = sample.get("observer_node", "?")
                with self.cache_lock:
                    self.cache[key] = sample
                    self.mesh_received_at[key] = time.monotonic()
                    self.mesh_updated_at[key] = int(time.time() * 1000)

                self._broadcast({"type": "mesh_status", "data": sample})
                self._broadcast({"type": "mission_inventory", "data": self.mission_inventory_snapshot()})

            for data, info in self.command_ack_reader.take():
                if not info.valid:
                    continue
                sample = json.loads(data.to_json())
                self.record_mission_ack(sample)
            for data, info in self.diagnostics_reader.take():
                if info.valid:
                    self.record_router_diagnostics(json.loads(data.to_json()))
            for data, info in self.scenario_reader.take():
                if info.valid:
                    self.record_scenario_report(json.loads(data.to_json()))

            for level, topic_name, reader in self.platform_readers:
                for data, info in reader.take():
                    if not info.valid:
                        continue
                    sample = json.loads(data.to_json())
                    # New types have source at top level; legacy types nested in msg
                    platform_node = sample.get("source") or sample.get("msg", {}).get("source")
                    if not platform_node:
                        continue

                    now_ms = int(time.time() * 1000)
                    with self.cache_lock:
                        if platform_node not in self.platform_cache:
                            self.platform_cache[platform_node] = {
                                "init": {},
                                "mission": {},
                                "debug": {},
                                "updated_at": 0,
                                "init_updated_at": 0,
                                "mission_updated_at": 0,
                                "debug_updated_at": 0,
                            }
                        entry = self.platform_cache[platform_node]
                        entry[level][topic_name] = sample
                        entry["updated_at"] = now_ms
                        entry[f"{level}_updated_at"] = now_ms
                        payload = {
                            "type": "platform_status",
                            "platform": platform_node,
                            "data": json.loads(json.dumps(entry)),
                        }

                    self._broadcast(payload)
                    if topic_name in {"PlatformInitStatus", "PlatformMissionStatus",
                                      "PlatformWaypointStatus"}:
                        self._broadcast({
                            "type": "mission_inventory",
                            "data": self.mission_inventory_snapshot(),
                        })

                for data, info in self.link_stats_reader.take():
                    if not info.valid:
                        continue
                    sample = json.loads(data.to_json())
                    key = (
                        sample.get("observer_router", ""),
                        sample.get("peer_router", ""),
                        sample.get("network", ""),
                    )
                    sample["telemetry_sample_id"] = uuid.uuid4().hex
                    with self.cache_lock:
                        self.link_stats_cache[key] = sample
                        self.link_stats_received_at[key] = time.monotonic()
                    self._broadcast({"type": "link_stats", "data": self.link_stats_snapshot()})

            self.poll_audit_events()
            if self.poll_wire_events():
                self._broadcast({"type": "delivery_stats", "data": self.delivery_snapshot()})

            self.expire_mission_commands()
            self.reconcile_team_missions()
            self._stop.wait(self.poll_interval)

    def _broadcast(self, payload):
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._broadcast_async(payload)))

    async def _broadcast_async(self, payload):
        dead = []
        for ws in list(self.ws_clients):
            try:
                await ws.send_str(json.dumps(payload))
            except (ConnectionResetError, RuntimeError):
                dead.append(ws)
        for ws in dead:
            self.ws_clients.discard(ws)

    def snapshot(self):
        with self.cache_lock:
            now = time.monotonic()
            return [{"data": sample, "telemetry_age_ms": max(
                0, (now - self.mesh_received_at[key]) * 1000)}
                for key, sample in self.cache.items()]

    def traffic_snapshot(self):
        with self.cache_lock:
            return self._aggregate_traffic_locked()

    def record_router_diagnostics(self, sample):
        router = sample.get("router")
        if not router:
            raise ValueError("router diagnostics missing identity")
        with self.cache_lock:
            previous = self.router_diagnostics_cache.get(router, {}).get("data")
            if previous and sample["send_timestamp"] <= previous["send_timestamp"]:
                return
            self.router_diagnostics_cache[router] = {
                "data": sample, "received_at": int(time.time() * 1000)}
        self._broadcast({"type": "router_diagnostics", "data": self.router_diagnostics_snapshot()})

    def router_diagnostics_snapshot(self):
        now = int(time.time() * 1000)
        with self.cache_lock:
            health = {peer["health"]["router"]: peer["health"] for status in self.cache.values()
                      for peer in status.get("peers", [])}
            rows = []
            for router, entry in self.router_diagnostics_cache.items():
                node = router.split("/", 1)[0]
                summary = health.get(router)
                enabled = not node.startswith("Platform_") or any(
                    route["route_name"] == "platform_debug_status"
                    and route["state"] != "ROUTE_DISABLED"
                    for route in (summary or {}).get("routes", []))
                state = "live"
                if node.startswith("Platform_") and not summary:
                    state = "awaiting health"
                elif not enabled:
                    state = "debug off / historical"
                elif summary and entry["data"]["config_hash"] != summary["config_hash"]:
                    state = "configuration mismatch"
                elif summary and entry["data"]["state_revision"] != summary["state_revision"]:
                    state = "awaiting matching health revision"
                elif entry["data"]["send_timestamp"] < self.platform_status_mode_changed_ns.get(node, 0):
                    state = "awaiting fresh debug snapshot"
                elif now - entry["received_at"] > 5000 or now - entry["data"]["send_timestamp"] / 1e6 > 5000:
                    state = "stale"
                rows.append(dict(entry, router=router, state=state))
            return {"rows": rows}

    def emane_snapshot(self):
        with self.cache_lock:
            return self._aggregate_emane_locked()

    def link_stats_snapshot(self):
        with self.cache_lock:
            now = time.monotonic()
            rows = [
                dict(sample, telemetry_age_ms=max(
                    0, (now - self.link_stats_received_at[key]) * 1000))
                for key, sample in self.link_stats_cache.items()
            ]
        rows.sort(key=lambda row: (
            row.get("observer_router", ""),
            row.get("peer_router", ""),
            row.get("network", ""),
        ))
        return {"rows": rows}

    def _reported_status_mode(self, source_node):
        for status in self.cache.values():
            for peer in status.get("peers", []):
                health = peer.get("health", {})
                if health.get("router", "").split("/", 1)[0] != source_node:
                    continue
                routes = {route["route_name"]: route["state"]
                          for route in health.get("routes", [])}
                if "platform_debug_status" in routes:
                    if routes["platform_debug_status"] != "ROUTE_DISABLED":
                        return "debug"
                    if routes.get("platform_detail_status", "ROUTE_DISABLED") != "ROUTE_DISABLED":
                        return "mission"
                    return "init"
        return self.platform_status_modes.get(source_node, "init")

    def _platform_topic_expected(self, source_node, topic, sent_at_ns):
        if not source_node.startswith("Platform_"):
            return True
        mode = self._reported_status_mode(source_node)
        cutoff_ns = self.platform_status_mode_changed_ns.get(source_node, 0)
        if topic in MISSION_MODE_TOPICS | DEBUG_MODE_TOPICS and sent_at_ns < cutoff_ns:
            return False
        if topic in DEBUG_MODE_TOPICS:
            return mode == "debug"
        if topic in MISSION_MODE_TOPICS:
            return mode in ("mission", "debug")
        return True

    def _audit_nodes_for_run(self, run_id):
        nodes = []
        debug_root = _THIS_FILE.parents[3] / "debug"
        for path in debug_root.glob("*_debug/run_id"):
            try:
                if path.read_text(encoding="utf-8").strip() != run_id:
                    continue
            except OSError:
                continue
            match = re.fullmatch(r"(control|platform)_(\d+)_debug", path.parent.name)
            if match:
                nodes.append(f"{match.group(1).title()}_{match.group(2)}")
        return sorted(set(nodes))

    def _active_audit_event_paths(self, filename="events.jsonl"):
        if self.audit_run_id is None:
            run_id_path = _THIS_FILE.parents[3] / "debug" / "control_20_debug" / "run_id"
            try:
                self.audit_run_id = run_id_path.read_text(encoding="utf-8").strip()
            except OSError:
                return []
            if not self.audit_run_id:
                return []

        debug_root = _THIS_FILE.parents[3] / "debug"
        paths = []
        for run_id_path in debug_root.glob("*_debug/run_id"):
            try:
                if run_id_path.read_text(encoding="utf-8").strip() != self.audit_run_id:
                    continue
            except OSError:
                continue
            event_path = run_id_path.with_name(filename)
            if event_path.is_file():
                paths.append(event_path)
        return paths

    def _team_names_for_node(self, node_name):
        names = set()
        for sample in self.cache.values():
            for peer in sample.get("peers", []):
                health = peer.get("health", {})
                if str(health.get("router", "")).split("/", 1)[0] != node_name:
                    continue
                names.update(
                    partition for partition in health.get("team_partition", [])
                    if partition != node_name
                )
        return names

    def _expected_recipients(self, event):
        if event["topic"] == "ControlCommand":
            return [event["destination"]] if event.get("destination") else []
        if event["topic"] == "PlatformData" and event["source_node"].startswith("Platform_"):
            run_nodes = self._audit_nodes_for_run(event.get("run_id", ""))
            source_teams = self._team_names_for_node(event["source_node"]) | {event["source_node"]}
            return [node for node in run_nodes
                    if node.startswith("Platform_")
                    and node != event["source_node"]
                    and source_teams.intersection(self._team_names_for_node(node) | {node})]
        if event["topic"] == "RouterHealth":
            run_nodes = self._audit_nodes_for_run(event.get("run_id", ""))
            if self._health_discovery_is_global(event.get("run_id", "")):
                return [node for node in run_nodes if node != event["source_node"]]
            if event["source_node"] == "Control_20":
                return [node for node in run_nodes if node.startswith("Platform_")]
            if event["source_node"].startswith("Platform_"):
                return ["Control_20"] if "Control_20" in run_nodes else []
            return []
        if event["topic"] == "PlatformInitStatus" and event["source_node"].startswith("Platform_"):
            return [node for node in self._audit_nodes_for_run(event.get("run_id", ""))
                    if node == "Control_20" or node.startswith("Platform_") and node != event["source_node"]]
        if event["topic"] in FIXED_CONTROL_RECIPIENT_TOPICS \
                and event["source_node"].startswith("Platform_"):
            sent_at_ns = int(event.get("sent_at_ns", 0))
            if not self._platform_topic_expected(event["source_node"], event["topic"], sent_at_ns):
                return []
            return ["Control_20"]
        return []

    @functools.lru_cache(maxsize=16)
    def _health_discovery_is_global(self, run_id):
        path = _THIS_FILE.parents[3] / "debug/test_controller_debug/manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.debug("No manifest for legacy health audience: %s", run_id)
            return False
        except (OSError, json.JSONDecodeError):
            logger.exception("Cannot resolve RouterHealth discovery scope: %s", path)
            raise
        if manifest.get("run_id") != run_id:
            return False
        scope = manifest.get("router_health_scope", "control_platform")
        if scope not in ("full_mesh", "control_platform"):
            raise ValueError(f"Unknown RouterHealth scope: {scope}")
        return scope == "full_mesh"

    def _audit_version(self, flow):
        return self.audit_versions.setdefault(flow, [0, 0])

    def _prune_audit_events(self, earliest_ns):
        self.audit_sent = {
            key: event for key, event in self.audit_sent.items()
            if event["sent_at_ns"] >= earliest_ns
        }
        self.audit_received = {
            key: event for key, event in self.audit_received.items()
            if event["sent_at_ns"] >= earliest_ns
        }

    def _required_samples_for_full_set(self, rate_hz, window_ms):
        if rate_hz is None or rate_hz <= 0:
            return PERCENTAGE_MIN_SAMPLES
        return max(1, int(round(rate_hz * (window_ms / 1000.0))))

    def _delivery_settle_ms(self, topic):
        if topic in RELIABLE_DELIVERY_TOPICS:
            return RELIABLE_DELIVERY_SETTLE_MS
        return DELIVERY_SETTLE_MS

    def poll_audit_events(self):
        changed = False
        for path in self._active_audit_event_paths():
            offset = self.audit_offsets.get(path, 0)
            if path.stat().st_size < offset:
                offset = 0
            with path.open(encoding="utf-8") as event_file:
                event_file.seek(offset)
                while True:
                    start = event_file.tell()
                    line = event_file.readline()
                    if not line:
                        break
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        if not line.endswith("\n"):
                            event_file.seek(start)
                            break
                        self.audit_offsets[path] = event_file.tell()
                        continue
                    self.audit_offsets[path] = event_file.tell()
                    required = {"event", "run_id", "node", "topic", "source_node", "sequence", "sent_at_ns"}
                    if not required.issubset(event):
                        continue
                    key = (event["run_id"], event["topic"], event["source_node"], event["sequence"])
                    with self.cache_lock:
                        if event["event"] == "sent":
                            if key in self.audit_sent:
                                continue
                            self.audit_sent[key] = event
                            for recipient in self._expected_recipients(event):
                                version = self._audit_version((event["topic"], event["source_node"], recipient))
                                version[0] += 1
                                if key + (recipient,) in self.audit_received:
                                    version[1] += 1
                            changed = True
                        elif event["event"] == "received":
                            received_key = key + (event["node"],)
                            if received_key in self.audit_received:
                                continue
                            self.audit_received[received_key] = event
                            sent = self.audit_sent.get(key)
                            if sent and event["node"] in self._expected_recipients(sent):
                                self._audit_version((sent["topic"], sent["source_node"], event["node"]))[1] += 1
                            changed = True
                with self.cache_lock:
                    self._prune_audit_events(time.time_ns() - 60_000_000_000)
        return changed

    def poll_wire_events(self):
        paths = self._active_audit_event_paths("wan_events.jsonl")
        with self.cache_lock:
            return self.wire_delivery.poll(paths, self.audit_run_id)

    @staticmethod
    def _wire_capture_healthy(sample):
        return (sample.get("wire_delivery_enabled", False)
                and sample.get("capture_status") == "live"
                and not sample.get("capture_invalid_rows_interval",
                                   sample.get("capture_invalid_rows", 0))
                and not sample.get("capture_gap", False)
                and sample.get("capture_timing_valid", True)
                and not sample.get("late_capture_packets_interval", 0))

    def delivery_snapshot(self, window_ms=30_000):
        with self.cache_lock:
            now_ns = time.time_ns()
            snapshot = self.wire_delivery.snapshot(window_ms, now_ns=now_ns)
            window_start_ms = now_ns / 1_000_000 - window_ms
            snapshot["node_modes"] = {
                peer["health"]["router"].split("/", 1)[0]: self._reported_status_mode(
                    peer["health"]["router"].split("/", 1)[0])
                for status in self.cache.values() for peer in status.get("peers", [])}
            capture = self._aggregate_traffic_locked()
            snapshot["capture"] = next((sample for sample in capture if sample["domain_id"] == 200), None)
            fresh = set((snapshot["capture"] or {}).get("contributors", []))
            for row in snapshot["rows"]:
                row["coverage_complete"] = all(
                    node in fresh
                    and self._wire_capture_healthy(self.traffic_cache.get((node, 200), {}))
                    and self.traffic_cache[(node, 200)].get("wire_coverage_since_ms") is not None
                    and self.traffic_cache[(node, 200)]["wire_coverage_since_ms"] <= window_start_ms
                    for node in (row["source"], row["recipient"]))
                if not row["coverage_complete"]:
                    row["observed_missing"] = row["lost"]
                    row["lost"] = row["loss_percentage"] = row["delivery_percentage"] = None
                    row["observed_recovered_samples"] = row["recovered_samples"]
                    row["recovered_samples"] = row["avg_recovery_ms"] = None
                    row["percentage_ready"] = False
            return snapshot

    def application_delivery_snapshot(self, window_ms=30_000):
        now_ns = time.time_ns()
        earliest_ns = now_ns - window_ms * 1_000_000
        with self.cache_lock:
            flows = {}
            node_modes = {}
            for key, sent in self.audit_sent.items():
                if sent["sent_at_ns"] < earliest_ns:
                    continue
                for recipient in self._expected_recipients(sent):
                    flows.setdefault((sent["topic"], sent["source_node"], recipient), []).append((key, sent))
            rows = []
            for (topic, source, recipient), events in flows.items():
                if source.startswith("Platform_"):
                    node_modes[source] = self._reported_status_mode(source)
                settle_ms = self._delivery_settle_ms(topic)
                settled_cutoff_ns = now_ns - settle_ms * 1_000_000
                events.sort(key=lambda item: item[1]["sent_at_ns"])
                settled_events = [(key, event) for key, event in events
                                  if event["sent_at_ns"] <= settled_cutoff_ns]
                settled_keys = {key for key, _ in settled_events}
                pending = sum((key + (recipient,)) not in self.audit_received
                              for key, _ in events if key not in settled_keys)
                received = sum((key + (recipient,)) in self.audit_received for key, _ in events)
                settled_received = sum((key + (recipient,)) in self.audit_received for key, _ in settled_events)
                lost = len(settled_events) - settled_received
                rate_hz = None
                if len(events) > 1:
                    elapsed_ns = events[-1][1]["sent_at_ns"] - events[0][1]["sent_at_ns"]
                    if elapsed_ns > 0:
                        rate_hz = (len(events) - 1) * 1_000_000_000 / elapsed_ns
                sent_version, received_version = self._audit_version((topic, source, recipient))
                settled_window_ms = max(1, window_ms - settle_ms)
                required_samples = self._required_samples_for_full_set(rate_hz, settled_window_ms)
                percentage_ready = len(settled_events) >= required_samples
                delivery_percentage = None
                loss_percentage = None
                if percentage_ready:
                    delivery_percentage = 100 * settled_received / len(settled_events)
                    loss_percentage = 100 * lost / len(settled_events)
                rows.append({
                    "topic": topic,
                    "source": source,
                    "recipient": recipient,
                    "expected": len(settled_events),
                    "settled": len(settled_events),
                    "sent": len(events),
                    "received": received,
                    "settled_received": settled_received,
                    "missing": lost,
                    "lost": lost,
                    "pending": pending,
                    "percentage": delivery_percentage,
                    "delivery_percentage": delivery_percentage,
                    "loss_percentage": loss_percentage,
                    "percentage_ready": percentage_ready,
                    "percentage_min_samples": PERCENTAGE_MIN_SAMPLES,
                    "percentage_required_samples": required_samples,
                    "settle_ms": settle_ms,
                    "settled_window_ms": settled_window_ms,
                    "send_rate_hz": rate_hz,
                    "sent_event_version": sent_version,
                    "received_event_version": received_version,
                    "last_updated_ms": events[-1][1]["sent_at_ns"] // 1_000_000,
                })
            return {
                "window_ms": window_ms,
                "node_modes": node_modes,
                "rows": sorted(rows, key=lambda row: (row["topic"], row["source"], row["recipient"])),
            }

    def _aggregate_traffic_locked(self):
        domains = {}
        for sample in self.traffic_cache.values():
            domains.setdefault(sample["domain_id"], {})[sample["observer"]] = sample
        result = []
        for domain, samples in sorted(domains.items()):
            if all(sample.get("schema_version", 1) >= 3 for sample in samples.values()):
                samples = coherent_samples(samples, self.traffic_observers, {
                    name: self.traffic_history.get((name, domain), []) for name in samples})
            result.append(dict(aggregate_metrics(samples, self.traffic_observers), domain_id=domain))
        return result

    def _aggregate_emane_locked(self):
        return aggregate_metrics(self.emane_cache, self.traffic_observers, mac=True)

    def update_traffic(self, sample):
        """Store one node interval and return a complete aggregate when available."""
        validate_sample(sample)
        domain_id, observer = sample["domain_id"], sample["observer"]
        with self.cache_lock:
            previous = self.traffic_cache.get((observer, domain_id), {})
            if sample["capture_timestamp"] <= previous.get("capture_timestamp", 0):
                return None
            invalid_rows = sample.get("capture_invalid_rows", 0)
            previous_invalid_rows = previous.get("capture_invalid_rows", 0)
            sample = dict(sample, capture_invalid_rows_interval=sample.get("capture_invalid_rows_interval", (
                invalid_rows - previous_invalid_rows
                if invalid_rows >= previous_invalid_rows else invalid_rows)))
            coverage_since = None
            if self._wire_capture_healthy(sample):
                coverage_since = sample["capture_timestamp"]
                interval_start = sample["capture_timestamp"] - sample["interval_ms"]
                # One millisecond accommodates independently rounded interval/end times.
                if (self._wire_capture_healthy(previous)
                        and previous.get("wire_coverage_since_ms") is not None
                        and interval_start <= previous["capture_timestamp"] + 1
                        and invalid_rows >= previous_invalid_rows
                        and sample.get("capture_restarts", 0) == previous.get("capture_restarts", 0)):
                    coverage_since = previous["wire_coverage_since_ms"]
            sample = dict(sample, wire_coverage_since_ms=coverage_since)
            self.traffic_cache[(observer, domain_id)] = sample
            if sample.get("schema_version", 1) >= 3:
                history = self.traffic_history.setdefault((observer, domain_id), [])
                history.append(sample)
                del history[:-8]
            current = next(row for row in self._aggregate_traffic_locked() if row["domain_id"] == domain_id)
            observers = current["contributors"]
            keys = {(name, domain_id) for name in observers}
            if not keys:
                return None
            if any(self.traffic_cache[key].get("capture_timestamp", 0) <=
                   self.traffic_published_at.get(key, 0) for key in keys):
                return None
            self.latest_traffic_aggregate = self._aggregate_traffic_locked()
            for key in keys:
                self.traffic_published_at[key] = (
                    current["capture_timestamp"] if current["schema_version"] >= 3
                    else self.traffic_cache[key].get("capture_timestamp", 0))
            return list(self.latest_traffic_aggregate)

    def update_emane(self, sample):
        """Store one MAC TX delta and return an all-node aggregate when complete."""
        validate_sample(sample, mac=True)
        observer = sample["observer"]
        with self.cache_lock:
            if sample["capture_timestamp"] <= self.emane_cache.get(observer, {}).get("capture_timestamp", 0):
                return None
            self.emane_cache[observer] = sample
            aggregate = self._aggregate_emane_locked()
            observers = aggregate["contributors"]
            if not observers:
                return None
            if any(self.emane_cache[name].get("capture_timestamp", 0) <=
                   self.emane_published_at.get(name, 0) for name in observers):
                return None

            self.latest_emane_aggregate = aggregate
            for name in observers:
                self.emane_published_at[name] = self.emane_cache[name]["capture_timestamp"]
            return dict(aggregate)

    def platform_snapshot(self, platform_node=None):
        with self.cache_lock:
            if platform_node:
                return self.platform_cache.get(platform_node, {
                    "init": {}, "mission": {}, "debug": {}, "updated_at": 0,
                })
            return json.loads(json.dumps(self.platform_cache))

    def mission_inventory_snapshot(self):
        now_ms = int(time.time() * 1000)
        with self.cache_lock:
            nodes = set(self.platform_cache)
            peers = {}
            # A control-observer view is authoritative; other observers are fallback only.
            statuses = sorted(self.cache.items(), key=lambda item: (
                item[0] == "Control_20", self.mesh_updated_at.get(item[0], 0)))
            for observer, status in statuses:
                for peer in status.get("peers", []):
                    router = peer.get("health", {}).get("router", "")
                    node = router.split("/", 1)[0]
                    if PLATFORM_NODE_RE.fullmatch(node):
                        nodes.add(node)
                        peers[node] = (peer, self.mesh_updated_at.get(observer, 0))
            inventory = []
            for node in sorted(nodes):
                entry = self.platform_cache.get(node, {})
                init = entry.get("init", {}).get("PlatformInitStatus", {})
                mission = entry.get("mission", {}).get("PlatformMissionStatus", {})
                # The compact always-on summary can recover terminal state after WAN loss.
                if init.get("mission_phase"):
                    detailed_at = mission.get("sent_at_ns", mission.get("timestamp", 0) * 1000)
                    init_at = init.get("sent_at_ns", init.get("timestamp", 0) * 1000)
                    if not mission or init_at >= detailed_at:
                        if init["mission_id"] != mission.get("mission_id"):
                            mission = {}
                        mission = dict(mission, mission_id=init["mission_id"],
                                       mission_phase=init["mission_phase"])
                mission_id = mission.get("mission_id", "")
                mission_command = self.mission_commands.get(mission_id)
                if mission_command is not None:
                    self.mission_active_commands[node] = dict(mission_command)
                else:
                    mission_command = self.mission_active_commands.get(node)
                if mission_command and mission_command["command_id"] != mission_id:
                    mission_command = None
                    self.mission_active_commands.pop(node, None)
                waypoint = entry.get("mission", {}).get("PlatformWaypointStatus", {})
                detail = entry.get("mission", {}).get("PlatformDetailStatus", {})
                peer, mesh_at = peers.get(node, ({}, 0))
                presence = peer.get("presence", "UNKNOWN")
                connected = (presence == "PRESENCE_ALIVE" and
                             0 <= now_ms - mesh_at <= MISSION_FRESHNESS_MS and
                             0 <= peer.get("last_seen_delta_ms", MISSION_FRESHNESS_MS + 1)
                             <= MISSION_FRESHNESS_MS)
                position_at = entry.get("init_updated_at", 0)
                age_ms = max(0, now_ms - position_at) if position_at else None
                coordinates = [init.get("latitude"), init.get("longitude")]
                has_position = all(
                    isinstance(value, (int, float)) and not isinstance(value, bool) and
                    math.isfinite(value) for value in coordinates)
                fresh = has_position and age_ms is not None and age_ms <= MISSION_FRESHNESS_MS
                teams = peer.get("health", {}).get("team_partition", [])
                squad = next((choice["value"] for choice in SQUADS
                              if choice["value"] and choice["value"] in teams), "")
                home_lat, home_lon = launch_position_for(node)
                inventory.append({
                    "platform": node,
                    "display_name": node.replace("_", " "),
                    "available": bool(connected and fresh),
                    "connected": bool(connected),
                    "stale": not fresh or not connected,
                    "presence": presence,
                    "health": presence,
                    "position_age_ms": age_ms,
                    "squad": squad,
                    "team_confirmed": bool(connected and node in teams),
                    "inheritance_error": self.mission_inheritance_errors.get(node),
                    "squad_label": next(choice["label"] for choice in SQUADS if choice["value"] == squad),
                    "home": {"latitude": home_lat, "longitude": home_lon},
                    "position": {
                        "latitude": init.get("latitude"),
                        "longitude": init.get("longitude"),
                        "heading_deg": init.get("heading_deg"),
                        "speed_knots": init.get("speed_knots"),
                    },
                    "battery_pct": detail.get("battery_pct"),
                    "time_scale": init.get("time_scale") or next((command["time_scale"]
                                        for command in reversed(list(self.mission_commands.values()))
                                        if command["target"] == node and
                                        command["operation"] == "set_time_scale" and
                                        command["state"] == "accepted"), None),
                    "mission": {
                        "mission_id": mission_id,
                        "phase": mission.get("mission_phase", "IDLE"),
                        "waypoint_index": mission.get("waypoint_index", 0),
                        "waypoints_total": mission.get("waypoints_total", 0),
                        "distance_to_waypoint_m": mission.get("distance_to_waypoint_m"),
                        "elapsed_s": mission.get("mission_elapsed_s"),
                        "navigation_state": init.get("navigation_state"),
                        "minimum_separation_m": init.get("minimum_separation_m"),
                        "target_id": (mission_command or {}).get("target_id"),
                        "loiter_radius_m": (mission_command or {}).get("loiter_radius_m"),
                        "command": json.loads(json.dumps(mission_command)),
                        "waypoint": waypoint,
                    },
                    "updated_at": entry.get("updated_at", 0),
                })
            return {"platforms": inventory, "squads": SQUADS,
                    "team_missions": json.loads(json.dumps(self.team_missions)),
                    "scenario_targets": [dict(target, revealed=target["id"] in self.scenario_reports
                        and not self.scenario_reports[target["id"]].get("lost", False),
                        dispatched=target["id"] in self.scenario_dispatched_targets)
                        for target in SCENARIO_TARGETS]}

    @staticmethod
    def _scenario_target(target_id):
        target = next((target for target in SCENARIO_TARGETS if target["id"] == target_id), None)
        if target is None:
            raise ValueError("target_id must be target_1 or target_2")
        return target

    def _log_scenario_event(self, kind, sample):
        event = {"event": kind, "topic": "ContactReport", "node": "Control_20",
                 "source_node": "Control_20", "source": sample["source"], "run_id": sample["run_id"],
                 "sequence": sample["audit_sequence"], "sent_at_ns": sample["sent_at_ns"],
                 "recorded_at_ns": time.time_ns(), "destination": "Control_20"}
        with self._mission_audit_path().open("a", encoding="utf-8") as events:
            events.write(json.dumps(event) + "\n")

    def record_scenario_report(self, sample):
        if sample.get("source") != "Control_20/Scenario" or sample.get("run_id") != self.audit_run_id:
            return False
        target = next((target for target in SCENARIO_TARGETS if target["id"] == sample.get("contact_id")), None)
        if target is None or any(not math.isfinite(sample[key]) or abs(sample[key] - target[key]) > .00001
                                 for key in ("latitude", "longitude")):
            logger.warning("Ignoring invalid scenario ContactReport: %s", sample.get("contact_id"))
            return False
        with self.cache_lock:
            previous = self.scenario_reports.get(target["id"])
            if previous and sample["audit_sequence"] <= previous["audit_sequence"]:
                logger.debug("Ignoring older scenario ContactReport: %s", target["id"])
                return False
            self.scenario_reports[target["id"]] = sample
        try:
            self._log_scenario_event("received", sample)
        except OSError:
            logger.exception("Scenario ContactReport received but audit write failed")
        self._broadcast({"type": "mission_inventory", "data": self.mission_inventory_snapshot()})
        return True

    def reveal_scenario_target(self, body):
        if (not isinstance(body, dict) or "target_id" not in body
                or set(body) - {"target_id", "visible"}
                or not isinstance(body.get("visible", True), bool)):
            raise ValueError("expected JSON {target_id, visible?: boolean}")
        target = self._scenario_target(body["target_id"])
        with self.mission_lock:
            sample = dds.DynamicData(self.scenario_type)
            for key, value in {
                "source": "Control_20/Scenario", "source_node": "Control_20",
                "run_id": self._mission_run_id(), "contact_id": target["id"],
                "classification": "DEMO_TARGET", "sensor_type": "SCENARIO",
                "latitude": target["latitude"], "longitude": target["longitude"],
                "confidence_pct": 100.0, "lost": not body.get("visible", True),
                "audit_sequence": time.time_ns(), "sent_at_ns": time.time_ns(),
                "timestamp": int(time.time() * 1_000_000),
            }.items():
                sample[key] = value
            self.scenario_writer.write(sample)
            self._log_scenario_event("sent", sample)
            return {"target_id": target["id"], "published": True}

    @staticmethod
    def _bound_history(history, limit=MISSION_HISTORY_LIMIT):
        while len(history) > limit:
            history.pop(next(iter(history)))

    def expire_mission_commands(self):
        now_ms = int(time.time() * 1000)
        expired = []
        with self.cache_lock:
            for command in self.mission_commands.values():
                if command["state"] == "pending" and now_ms - command["created_at"] >= MISSION_ACK_TIMEOUT_MS:
                    command.update(state="timed_out", updated_at=now_ms, timed_out_at=now_ms)
                    expired.append(dict(command))
        for command in expired:
            self._broadcast({"type": "mission_command", "data": command})

    def record_mission_ack(self, sample):
        self.expire_mission_commands()
        now_ms = int(time.time() * 1000)
        with self.cache_lock:
            command = self.mission_commands.get(sample.get("command_id"))
            if (not command or sample.get("source") != command["target"] or
                    sample.get("run_id") != command["run_id"] or command["state"] == "failed"):
                return False
            ack = dict(sample, received_at=now_ms, late_ack="timed_out_at" in command)
            self.mission_acks[(sample["source"], sample["command_id"])] = ack
            self._bound_history(self.mission_acks)
            command.update(ack=ack, state="accepted" if sample.get("accepted") else "rejected",
                           late_ack=ack["late_ack"], updated_at=now_ms)
            if command["state"] == "accepted" and command.get("target_id"):
                self.scenario_dispatched_targets.add(command["target_id"])
            command = dict(command)
        self._broadcast({"type": "mission_ack", "data": ack})
        self._broadcast({"type": "mission_command", "data": command})
        self._broadcast({"type": "mission_inventory", "data": self.mission_inventory_snapshot()})
        return True

    def mission_ack_snapshot(self):
        self.expire_mission_commands()
        with self.cache_lock:
            acks = [dict(ack) for ack in self.mission_acks.values()]
        acks.sort(key=lambda ack: ack["received_at"], reverse=True)
        return {"acks": acks}

    def mission_command_snapshot(self):
        self.expire_mission_commands()
        with self.cache_lock:
            commands = json.loads(json.dumps(list(self.mission_commands.values())))
        commands.sort(key=lambda command: command["created_at"], reverse=True)
        return {"commands": commands}

    def _prune_mission_plans(self):
        now_ms = int(time.time() * 1000)
        self.mission_plans = {key: plan for key, plan in self.mission_plans.items()
                              if now_ms - plan["created_at"] < MISSION_PLAN_TTL_MS}
        self._bound_history(self.mission_plans, MISSION_PLAN_LIMIT)

    def _plan_targets(self, targets, waypoints, operation="route"):
        inventory = {row["platform"]: row for row in self.mission_inventory_snapshot()["platforms"]}
        plans = []
        for target in targets:
            boat = inventory.get(target)
            if boat is None:
                raise MissionConflict(f"{target}: platform is missing from the current mesh inventory.")
            if not boat["available"]:
                raise MissionConflict(
                    f"{target}: unavailable, stale or missing position/presence "
                    f"(presence={boat['presence']}, position age={boat['position_age_ms']} ms).")
            if operation in {"cancel", "set_time_scale"}:
                route = []
            else:
                destination = ([list(launch_position_for(target))]
                               if operation == "return_to_base" else waypoints)
                try:
                    route = self.mission_planner.plan(
                        [boat["position"]["latitude"], boat["position"]["longitude"]], destination)
                except ValueError as exc:
                    raise ValueError(f"{target}: {exc}") from exc
            plans.append({"target": target, "waypoints": route})
        return plans

    def validate_mission_plan(self, body):
        if not isinstance(body, dict) or set(body) - {"team"} not in (
                {"targets", "waypoints"}, {"targets", "target_id"}, {"targets", "target_id", "loiter_radius_m"}):
            raise ValueError("expected JSON {targets, waypoints} or {targets, target_id, loiter_radius_m?}")
        target = self._scenario_target(body["target_id"]) if "target_id" in body else None
        radius = body.get("loiter_radius_m", DEFAULT_LOITER_RADIUS_M)
        if isinstance(radius, bool) or not isinstance(radius, (int, float)) or radius not in LOITER_RADII_M:
            raise ValueError("loiter_radius_m must be 500, 1000, 2000 or 3000")
        if target and (target["id"] not in self.scenario_reports
                       or self.scenario_reports[target["id"]].get("lost", False)):
            raise MissionConflict("Reveal this scenario target first.")
        request = validate_mission_request({
            "operation": "route", "targets": body["targets"],
            "waypoints": [[target["latitude"], target["longitude"]]] if target else body["waypoints"],
        })
        with self.mission_lock:
            self._prune_mission_plans()
            team = body.get("team")
            goal = None
            if team is not None:
                inventory = {row["platform"]: row for row in self.mission_inventory_snapshot()["platforms"]}
                if team not in {"TEAM_A", "TEAM_B", "TEAM_C"}:
                    raise ValueError("team must be Alpha, Bravo or Reserve")
                if any(inventory.get(node, {}).get("squad") != team for node in request["targets"]):
                    raise MissionConflict("Select boats belonging to the mission team.")
                request["targets"] = sorted(node for node, boat in inventory.items() if boat["squad"] == team)
                if any(not inventory[node]["team_confirmed"] for node in request["targets"]):
                    raise MissionConflict("Wait for confirmed team membership.")
                goal = ({"kind": "loiter", "center": [target["latitude"], target["longitude"]], "radius_m": radius}
                        if target else {"kind": "waypoints", "waypoints": request["waypoints"]})
                plans = self._goal_plans(request["targets"], goal)
                if target:
                    for plan in plans:
                        plan.update(target_id=target["id"], loiter_radius_m=radius)
            elif target:
                plans = []
                inventory = {row["platform"]: row for row in self.mission_inventory_snapshot()["platforms"]}
                for node in request["targets"]:
                    boat = inventory.get(node)
                    if not boat or not boat["available"]:
                        raise MissionConflict(f"{node}: unavailable or missing from the current inventory.")
                    position = boat["position"]
                    angle = math.atan2(
                        (position["longitude"] - target["longitude"]) * math.cos(math.radians(target["latitude"])),
                        position["latitude"] - target["latitude"])
                    ring = orbit_points(target, angle, radius)
                    approach = self._plan_targets([node], [ring[0]])[0]["waypoints"]
                    circuit = self.mission_planner.plan(approach[-1], ring[1:])
                    if len(approach) + len(circuit) > MAX_MISSION_WAYPOINTS:
                        raise ValueError("Orbit detour exceeds the 64-waypoint limit.")
                    plans.append({"target": node, "waypoints": approach + circuit,
                                  "loop_start": len(approach), "target_id": target["id"],
                                  "loiter_radius_m": radius})
            else:
                plans = self._plan_targets(request["targets"], request["waypoints"])
            plan_id = uuid.uuid4().hex
            inventory = {row["platform"]: row for row in self.mission_inventory_snapshot()["platforms"]}
            self.mission_plans[plan_id] = {
                "plans": plans, "created_at": int(time.time() * 1000),
                "squads": {target: inventory[target]["squad"] for target in request["targets"]},
                "team": team, "goal": goal,
            }
            self._bound_history(self.mission_plans, MISSION_PLAN_LIMIT)
            return {"plan_id": plan_id, "plans": json.loads(json.dumps(plans))}

    def _mission_request(self, body):
        if isinstance(body, dict) and "plan_id" in body:
            if set(body) != {"operation", "plan_id"} or body.get("operation") not in {"route", "replace_route"}:
                raise ValueError("validated commands require {operation: route|replace_route, plan_id}")
            self._prune_mission_plans()
            plan_id = body["plan_id"]
            if not isinstance(plan_id, str) or plan_id not in self.mission_plans:
                raise MissionConflict("Unknown or expired mission plan; validate the route again.")
            stored_plan = self.mission_plans[plan_id]
            stored = stored_plan["plans"]
            inventory = {row["platform"]: row for row in self.mission_inventory_snapshot()["platforms"]}
            if stored_plan.get("team"):
                team = stored_plan["team"]
                members = sorted(node for node, boat in inventory.items() if boat["squad"] == team)
                if members != sorted(stored_plan["squads"]) or any(
                        not inventory[node]["team_confirmed"] for node in members):
                    raise MissionConflict("Team roster changed; validate the shared mission again.")
                plans = self._goal_plans(members, stored_plan["goal"])
                for plan, original in zip(plans, stored):
                    for key in ("target_id", "loiter_radius_m"):
                        if key in original:
                            plan[key] = original[key]
                return {"operation": body["operation"], "command_id": None,
                        "targets": members, "plans": plans, "team": team,
                        "goal": stored_plan["goal"], "team_version": uuid.uuid4().hex}
            for target, squad in stored_plan["squads"].items():
                if target in inventory and inventory[target]["squad"] != squad:
                    raise MissionConflict(f"{target}: confirmed squad changed; validate the route again.")
            # Recheck from current positions: boats may move between validation and publication.
            plans = []
            for plan in stored:
                if "loop_start" in plan:
                    approach = self._plan_targets([plan["target"]], plan["waypoints"][:plan["loop_start"]])[0]["waypoints"]
                    circuit = self.mission_planner.plan(approach[-1], plan["waypoints"][plan["loop_start"]:])
                    if len(approach) + len(circuit) > MAX_MISSION_WAYPOINTS:
                        raise ValueError("Orbit detour exceeds the 64-waypoint limit.")
                    plans.append(dict(plan, waypoints=approach + circuit, loop_start=len(approach)))
                else:
                    plans.append(self._plan_targets([plan["target"]], plan["waypoints"])[0])
            return {"operation": body["operation"], "command_id": None,
                    "targets": [plan["target"] for plan in plans], "plans": plans}
        request = validate_mission_request(body)
        request["plans"] = self._plan_targets(
            request["targets"], request["waypoints"], request["operation"])
        if "loop_start" in request:
            for plan in request["plans"]:
                index = request["loop_start"]
                entry = request["waypoints"][max(0, index - 1)]
                approach = self._plan_targets([plan["target"]], request["waypoints"][:index] or [entry])[0]["waypoints"]
                points = request["waypoints"][index:] if index else request["waypoints"][1:]
                if not points or points[-1] != entry:
                    points = [*points, entry]
                circuit = self.mission_planner.plan(approach[-1], points)
                if len(approach) + len(circuit) > MAX_MISSION_WAYPOINTS:
                    raise ValueError("Orbit detour exceeds the 64-waypoint limit.")
                plan.update(waypoints=approach + circuit, loop_start=len(approach))
        return request

    def _goal_plans(self, targets, goal):
        from mission_planning import goal_plan
        inventory = {boat["platform"]: boat for boat in self.mission_inventory_snapshot()["platforms"]}
        plans = []
        for node in targets:
            boat = inventory.get(node)
            if not boat or not boat["available"]:
                raise MissionConflict(f"{node}: unavailable or missing from the current inventory.")
            start = [boat["position"]["latitude"], boat["position"]["longitude"]]
            planned = ({"waypoints": self.mission_planner.plan(start, [launch_position_for(node)])}
                       if goal["kind"] == "return_to_base" else goal_plan(self.mission_planner, start, node, goal))
            plans.append(dict(planned, target=node))
        return plans

    def reconcile_team_missions(self):
        with self.mission_lock:
            inventory = {boat["platform"]: boat for boat in self.mission_inventory_snapshot()["platforms"]}
            with self.cache_lock:
                for team, dispatch in list(self.team_dispatches.items()):
                    states = [self.mission_commands.get(cid, {}).get("state") for cid in dispatch["commands"].values()]
                    if any(state in {"failed", "rejected", "timed_out"} for state in states):
                        if not dispatch.get("failed"):
                            for node in dispatch["commands"]:
                                self.mission_inheritance_errors[node] = "Shared team mission failed; replace or cancel it."
                            logger.error("Shared mission dispatch failed for %s: %s", team, states)
                        dispatch["failed"] = True
                        continue
                    if not states or not all(state == "accepted" for state in states):
                        continue
                    if dispatch["goal"] is None:
                        self.team_missions.pop(team, None)
                    else:
                        self.team_missions[team] = dispatch
                    for node in dispatch["commands"]:
                        self.mission_inheritance_errors.pop(node, None)
                    del self.team_dispatches[team]
                missions = dict(self.team_missions)
            for team, mission in missions.items():
                members = [boat for boat in inventory.values() if boat["squad"] == team]
                if members and all(
                        boat["available"] and boat["team_confirmed"] and
                        self.team_followers.get(boat["platform"], {}).get("version") == mission["version"] and
                        boat["mission"]["mission_id"] == self.team_followers[boat["platform"]]["command_id"] and
                        boat["mission"]["phase"] == "COMPLETE" for boat in members):
                    with self.cache_lock:
                        self.team_missions.pop(team, None)
            for node, boat in inventory.items():
                if not boat["available"] or not boat["team_confirmed"]:
                    continue
                team = boat["squad"]
                previous = self.team_memberships.get(node)
                self.team_memberships[node] = team
                follower = self.team_followers.get(node, {})
                pending = team in self.team_dispatches
                if pending and follower.get("team") == team:
                    continue
                mission = None if pending else self.team_missions.get(team)
                followed_command = self.mission_commands.get(follower.get("command_id"), {})
                if followed_command.get("state") in {"failed", "rejected", "timed_out"}:
                    with self.cache_lock:
                        self.mission_inheritance_errors.setdefault(node, "Inherited mission failed; reassign or replace it.")
                changed = previous is not None and previous != team
                if mission and (changed or follower.get("team") != team or
                                follower.get("version") != mission["version"]):
                    if follower.get("team") == team and follower.get("version") == mission["version"]:
                        cid = follower.get("command_id")
                        if self.mission_commands.get(cid, {}).get("state") == "pending":
                            continue
                    goal = mission["goal"]
                    operation = "return_to_base" if goal["kind"] == "return_to_base" else "replace_route"
                    try:
                        plans = self._goal_plans([node], goal)
                        if goal["kind"] == "loiter":
                            plans[0].update(target_id=mission.get("target_id"), loiter_radius_m=goal["radius_m"])
                        result = self._publish_mission_commands({
                            "operation": operation, "targets": [node], "plans": plans, "command_id": None,
                            "goal": goal, "team": team, "team_version": mission["version"],
                        }, self._mission_run_id())
                    except (MissionConflict, ValueError, OSError) as exc:
                        logger.exception("Mission inheritance failed for %s", node)
                        with self.cache_lock:
                            self.mission_inheritance_errors[node] = str(exc)
                        continue
                    command = result["commands"][0]
                    with self.cache_lock:
                        self.team_followers[node] = {"team": team, "version": mission["version"],
                                                     "command_id": command["command_id"]}
                        if result["errors"]:
                            self.mission_inheritance_errors[node] = result["errors"][0]["message"]
                        else:
                            self.mission_inheritance_errors.pop(node, None)
                elif not mission and (
                        (changed and boat["mission"]["phase"] in {"TRANSIT", "HOLDING", "RTB", "ORBIT"}) or
                        (follower.get("team") not in (None, team) and follower.get("version") is not None)):
                    result = self._publish_mission_commands({
                        "operation": "cancel", "targets": [node], "plans": [{"target": node, "waypoints": []}],
                        "command_id": None,
                    }, self._mission_run_id())
                    with self.cache_lock:
                        self.team_followers[node] = {"team": team, "version": None,
                                                     "command_id": result["commands"][0]["command_id"]}
                        if result["errors"]:
                            self.mission_inheritance_errors[node] = result["errors"][0]["message"]
                        else:
                            self.mission_inheritance_errors.pop(node, None)

    def _mission_audit_path(self):
        return Path(os.environ.get(
            "NODE_DEBUG_DIR", str(_THIS_FILE.parents[3] / "debug/control_20_debug"))) / "events.jsonl"

    def _mission_run_id(self):
        try:
            run_id = self._mission_audit_path().with_name("run_id").read_text(encoding="utf-8").strip()
        except OSError:
            run_id = os.environ.get("ACT_RUN_ID") or self.audit_run_id
        if not run_id:
            raise MissionConflict("Active delivery audit run_id is unavailable; mission was not published.")
        if self.audit_run_id != run_id:
            with self.cache_lock:
                self.audit_run_id = run_id
                self.audit_offsets.clear()
                self.audit_sent.clear()
                self.audit_received.clear()
                self.audit_versions.clear()
                self.mission_commands.clear()
                self.mission_active_commands.clear()
                self.mission_acks.clear()
                self.scenario_reports.clear()
                self.scenario_dispatched_targets.clear()
        return run_id

    def _log_mission_sent(self, sample):
        event = {
            "event": "sent", "run_id": sample["run_id"], "node": "Control_20",
            "topic": CONTROL_COMMAND_TOPIC, "source_node": sample["source_node"],
            "sequence": int(sample["audit_sequence"]), "sent_at_ns": int(sample["sent_at_ns"]),
            "recorded_at_ns": time.time_ns(), "source": sample["source"],
            "destination": sample["destination"], "command_id": sample["command_id"],
        }
        with self._mission_audit_path().open("a", encoding="utf-8") as events:
            events.write(json.dumps(event, sort_keys=True) + "\n")

    def write_mission_command(self, body):
        with self.mission_lock:
            request = self._mission_request(body)
            run_id = self._mission_run_id()
            result = self._publish_mission_commands(request, run_id)
            inventory = self.mission_inventory_snapshot()["platforms"]
            with self.cache_lock:
                teams = ([request["team"]] if request.get("team") else [
                    team for team in self.team_missions.keys() | self.team_dispatches.keys()
                    if request["operation"] in {"cancel", "return_to_base"} and
                    any(boat["squad"] == team for boat in inventory) and
                    all(boat["platform"] in request["targets"] for boat in inventory if boat["squad"] == team)])
                for team in teams:
                    dispatch = {
                        "version": request.get("team_version", uuid.uuid4().hex),
                        "goal": request.get("goal", {"kind": "return_to_base"}
                                            if request["operation"] == "return_to_base" else None),
                        "target_id": next((plan.get("target_id") for plan in request["plans"]), None),
                        "commands": {command["target"]: command["command_id"] for command in result["commands"]
                                     if request.get("team") or next(boat["squad"] for boat in inventory
                                             if boat["platform"] == command["target"]) == team},
                    }
                    self.team_dispatches[team] = dispatch
                    for node, cid in dispatch["commands"].items():
                        self.team_followers[node] = {
                            "team": team, "version": dispatch["version"] if dispatch["goal"] else None,
                            "command_id": cid,
                        }
            return result

    def _publish_mission_commands(self, request, run_id):
        prefix = request["command_id"] or f"mission-{uuid.uuid4().hex}"
        ids = [prefix if len(request["targets"]) == 1 else f"{prefix[:62]}-{index}"
               for index in range(1, len(request["targets"]) + 1)]
        with self.cache_lock:
            if any(command_id in self.mission_commands for command_id in ids):
                raise MissionConflict("command_id already exists; use a new command ID.")
        commands = []
        errors = []
        for command_id, plan in zip(ids, request["plans"]):
            target = plan["target"]
            now_ms = int(time.time() * 1000)
            self.mission_command_sequence = max(self.mission_command_sequence + 1, time.time_ns())
            command = {
                "command_id": command_id, "operation": request["operation"],
                "target": target, "waypoints": plan["waypoints"], "state": "pending",
                "run_id": run_id, "audit_sequence": self.mission_command_sequence,
                "created_at": now_ms, "updated_at": now_ms, "published": False,
                **({"team": request["team"], "team_version": request["team_version"]}
                   if request.get("team") else {}),
                **({"time_scale": request["time_scale"]}
                   if request["operation"] == "set_time_scale" else {}),
                **({"loop_start": plan["loop_start"], "target_id": plan.get("target_id"),
                    "loiter_radius_m": plan.get("loiter_radius_m")}
                   if "loop_start" in plan else {}),
            }
            with self.cache_lock:
                self.mission_commands[command_id] = command
                self._bound_history(self.mission_commands)
            try:
                payload = {"v": MISSION_PAYLOAD_VERSION, "op": request["operation"]}
                if request.get("goal") and request["operation"] in {"route", "replace_route"}:
                    payload["g"] = request["goal"]
                elif request["operation"] == "return_to_base":
                    payload["w"] = [launch_position_for(target)]
                elif plan["waypoints"]:
                    payload["w"] = plan["waypoints"]
                if request.get("goal") or request["operation"] == "return_to_base":
                    payload["f"] = [boat["platform"] for boat in self.mission_inventory_snapshot()["platforms"]]
                if request["operation"] == "set_time_scale":
                    payload["t"] = request["time_scale"]
                if "loop_start" in plan and not request.get("goal"):
                    payload["l"] = plan["loop_start"]
                encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
                if len(encoded) > MAX_MISSION_PAYLOAD_BYTES:
                    raise ValueError("mission payload exceeds the 8192-byte ControlCommand limit")
                sample = dds.DynamicData(self.control_command_type)
                for field, value in {
                    "source": "Control_20", "destination": target, "run_id": run_id,
                    "source_node": "Control_20", "audit_sequence": self.mission_command_sequence,
                    "sent_at_ns": time.time_ns(), "command_id": command_id,
                    "command_type": "MISSION", "payload": list(encoded),
                    "timestamp": int(time.time() * 1_000_000),
                }.items():
                    sample[field] = value
                self.command_writer.write(sample)
                with self.cache_lock:
                    command["published"] = True
                self._log_mission_sent(sample)
            except (dds.Exception, ValueError, RuntimeError, OSError) as exc:
                logger.exception(
                    "Mission %s failed for %s (command_id=%s, published=%s)",
                    request["operation"], target, command_id, command["published"])
                with self.cache_lock:
                    if not command["published"]:
                        command["state"] = "failed"
                    command.update(error=str(exc), updated_at=int(time.time() * 1000))
                errors.append({"target": target, "message": str(exc)})
            with self.cache_lock:
                result = json.loads(json.dumps(command))
            commands.append(result)
            self._broadcast({"type": "mission_command", "data": result})
        self._broadcast({"type": "mission_inventory", "data": self.mission_inventory_snapshot()})
        return {"commands": commands, "errors": errors}

    def write_team_assignment(self, platform_node, team_name):
        sample = dds.DynamicData(self.team_type)
        sample["platform_node"] = platform_node
        sample["team_name"] = team_name
        self.writer.write(sample)

    def write_status_mode(self, platform_node, mode):
        mode_map = {
            "init": 0,
            "mission": 1,
            "debug": 2,
        }
        mode_value = mode_map.get(str(mode).lower())
        if mode_value is None:
            raise ValueError("mode must be one of: init, mission, debug")
        mode_label = str(mode).lower()

        sample = dds.DynamicData(self.status_mode_type)
        sample["platform_node"] = platform_node
        sample["resolution_mode"] = mode_value
        sample["request_id"] = f"ui-{int(time.time() * 1000)}"
        self.status_mode_writer.write(sample)

        # Clear cached data for levels above the new resolution so the dashboard
        # doesn't show stale higher-level data after switching back to a lower mode.
        levels_to_clear = {
            0: ["mission", "debug"],   # INIT: clear mission + debug
            1: ["debug"],              # MISSION: clear debug
            2: [],                     # DEBUG: clear nothing
        }
        with self.cache_lock:
            self.platform_status_modes[platform_node] = mode_label
            self.platform_status_mode_changed_ns[platform_node] = time.time_ns()
            entry = self.platform_cache.get(platform_node)
            if entry:
                for lvl in levels_to_clear.get(mode_value, []):
                    entry[lvl] = {}
                    entry[f"{lvl}_updated_at"] = 0
                payload = {
                    "type": "platform_status",
                    "platform": platform_node,
                    "data": json.loads(json.dumps(entry)),
                }
            else:
                payload = None
        if payload:
            self._broadcast(payload)

    def close(self):
        self._stop.set()
        self.participant.close()

    @staticmethod
    def _nem_id(node):
        if node == "Control_20":
            return 1
        match = re.fullmatch(r"Platform_(\d+)", str(node))
        if not match:
            raise ValueError(f"invalid node name: {node}")
        platform_id = int(match.group(1))
        if platform_id < 30 or platform_id > 99:
            raise ValueError(f"platform id out of range: {platform_id}")
        return platform_id - 28

    def emane_controller_status(self):
        missing_tools = [tool for tool in ("emaneevent-pathloss", "emaneevent-commeffect")
                         if shutil.which(tool) is None]
        with self.cache_lock:
            observed = self._aggregate_emane_locked()
        return {
            "available": EMANE_CONTROLLER_ENABLED and not missing_tools,
            "unavailable_reason": (None if not missing_tools
                                   else f"missing EMANE tools: {', '.join(missing_tools)}"),
            "event_group": EMANE_EVENT_GROUP,
            "event_port": int(EMANE_EVENT_PORT),
            "impairments": list(self.emane_impairments.values()),
            "observed_network": observed,
        }

    def _emane_pairs(self, source, destination, direction):
        if direction not in {"forward", "reverse", "bidirectional"}:
            raise ValueError("direction must be forward, reverse, or bidirectional")
        if source == destination:
            raise ValueError("source and destination must differ")
        source_nem = self._nem_id(source)
        destination_nem = self._nem_id(destination)
        pairs = []
        if direction in {"forward", "bidirectional"}:
            pairs.append((source_nem, destination_nem))
        if direction in {"reverse", "bidirectional"}:
            pairs.append((destination_nem, source_nem))
        return pairs

    def _emane_node_pairs(self, source, destination, direction):
        pairs = []
        if direction in {"forward", "bidirectional"}:
            pairs.append((source, destination))
        if direction in {"reverse", "bidirectional"}:
            pairs.append((destination, source))
        return pairs

    def _known_emane_nems(self, source, destination):
        nodes = {source, destination}
        with self.cache_lock:
            observed = dict(self.latest_emane_aggregate) if self.latest_emane_aggregate else {}
            nodes.update(observed.get("contributors", []))
            for sample in self.cache.values():
                if sample.get("observer_node"):
                    nodes.add(sample["observer_node"])
                for peer in sample.get("peers", []):
                    router = peer.get("health", {}).get("router", "")
                    if router:
                        nodes.add(router.split("/")[0])
        nems = set()
        for node in nodes:
            try:
                nems.add(self._nem_id(node))
            except ValueError:
                continue
        return sorted(nems)

    def _run_emane_event(self, executable_name, args):
        executable = shutil.which(executable_name)
        if executable is None:
            raise RuntimeError(f"{executable_name} is unavailable in this dashboard container")
        result = subprocess.run(
            [executable, "-i", "eth0", "-g", EMANE_EVENT_GROUP,
             "-p", str(EMANE_EVENT_PORT), *args],
            check=False, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"{executable_name} event failed: {detail or result.returncode}")

    def set_emane_impairment(self, source, destination, direction, kind, values):
        if not EMANE_CONTROLLER_ENABLED:
            raise RuntimeError("EMANE impairment commands are disabled")
        if kind not in {"pathloss", "commeffect"}:
            raise ValueError("kind must be pathloss or commeffect")
        pairs = self._emane_pairs(source, destination, direction)
        node_pairs = self._emane_node_pairs(source, destination, direction)
        if kind == "pathloss":
            return self._set_emane_pathloss(source, destination, direction, pairs, node_pairs, values)
        return self._set_emane_commeffect(source, destination, direction, pairs, node_pairs, values)

    def reset_all_emane_impairments(self):
        with self.cache_lock:
            impairments = list(self.emane_impairments.values())
        for impairment in impairments:
            values = {
                "pathloss_db": 0,
                "latency_ms": 0,
                "jitter_ms": 0,
                "unicast_bps": 0,
                "broadcast_bps": 0,
                "loss_percent": 0,
                "duplicate_percent": 0,
            }
            self.set_emane_impairment(
                impairment["source"], impairment["destination"], "forward",
                impairment.get("kind", "pathloss"), values)
        return self.emane_controller_status()

    def _set_emane_pathloss(self, source, destination, direction, pairs, node_pairs, values):
        pathloss_db = float(values.get("pathloss_db", 0))
        if pathloss_db < 0 or pathloss_db > EMANE_PATHLOSS_MAX_DB:
            raise ValueError(f"pathloss_db must be between 0 and {EMANE_PATHLOSS_MAX_DB}")
        for transmitter_nem, receiver_nem in pairs:
            self._run_emane_event("emaneevent-pathloss",
                                  [str(transmitter_nem), str(pathloss_db),
                                   "-t", str(receiver_nem), "-r", str(transmitter_nem)])
        updated_at = int(time.time() * 1000)
        with self.cache_lock:
            for transmitter_node, receiver_node in node_pairs:
                key = f"pathloss:{transmitter_node}->{receiver_node}"
                if pathloss_db == 0:
                    self.emane_impairments.pop(key, None)
                else:
                    self.emane_impairments[key] = {
                        "kind": "pathloss", "source": transmitter_node, "destination": receiver_node,
                        "direction": "forward", "pathloss_db": pathloss_db, "updated_at": updated_at,
                    }
        return self.emane_controller_status()

    def _set_emane_commeffect(self, source, destination, direction, pairs, node_pairs, values):
        latency_ms = float(values.get("latency_ms", 0))
        jitter_ms = float(values.get("jitter_ms", 0))
        loss_percent = float(values.get("loss_percent", 0))
        duplicate_percent = float(values.get("duplicate_percent", 0))
        unicast_bps = int(values.get("unicast_bps", 0))
        broadcast_bps = int(values.get("broadcast_bps", 0))
        if latency_ms < 0 or latency_ms > EMANE_COMMEFFECT_MAX_LATENCY_MS:
            raise ValueError(f"latency_ms must be between 0 and {EMANE_COMMEFFECT_MAX_LATENCY_MS}")
        if jitter_ms < 0 or jitter_ms > EMANE_COMMEFFECT_MAX_LATENCY_MS:
            raise ValueError(f"jitter_ms must be between 0 and {EMANE_COMMEFFECT_MAX_LATENCY_MS}")
        for name, value in (("loss_percent", loss_percent), ("duplicate_percent", duplicate_percent)):
            if value < 0 or value > 100:
                raise ValueError(f"{name} must be between 0 and 100")
        for name, value in (("unicast_bps", unicast_bps), ("broadcast_bps", broadcast_bps)):
            if value < 0 or value > EMANE_COMMEFFECT_MAX_BANDWIDTH_BPS:
                raise ValueError(f"{name} must be between 0 and {EMANE_COMMEFFECT_MAX_BANDWIDTH_BPS}")
        effects = [
            f"latency={latency_ms / 1000.0}", f"jitter={jitter_ms / 1000.0}",
            f"loss={loss_percent}", f"duplicate={duplicate_percent}",
            f"unicast={unicast_bps}", f"broadcast={broadcast_bps}",
        ]
        reset_effects = ["latency=0", "jitter=0", "loss=0", "duplicate=0", "unicast=0", "broadcast=0"]
        known_nems = self._known_emane_nems(source, destination)
        affected_receivers = {receiver_nem for _, receiver_nem in pairs}
        for receiver_nem in affected_receivers:
            for transmitter_nem in known_nems:
                if transmitter_nem != receiver_nem:
                    self._run_emane_event("emaneevent-commeffect",
                                          [str(transmitter_nem), *reset_effects,
                                           "-t", str(receiver_nem)])
        for transmitter_nem, receiver_nem in pairs:
            self._run_emane_event("emaneevent-commeffect",
                                  [str(transmitter_nem), *effects,
                                   "-t", str(receiver_nem), "-r", str(transmitter_nem)])
        active = any((latency_ms, jitter_ms, loss_percent, duplicate_percent, unicast_bps, broadcast_bps))
        updated_at = int(time.time() * 1000)
        with self.cache_lock:
            for transmitter_node, receiver_node in node_pairs:
                key = f"commeffect:{transmitter_node}->{receiver_node}"
                if not active:
                    self.emane_impairments.pop(key, None)
                else:
                    self.emane_impairments[key] = {
                        "kind": "commeffect", "source": transmitter_node, "destination": receiver_node,
                        "direction": "forward", "latency_ms": latency_ms, "jitter_ms": jitter_ms,
                        "loss_percent": loss_percent, "duplicate_percent": duplicate_percent,
                        "unicast_bps": unicast_bps, "broadcast_bps": broadcast_bps,
                        "updated_at": updated_at,
                    }
        return self.emane_controller_status()


def build_app(bridge: DdsBridge, static_dir: Path) -> web.Application:
    app = web.Application()
    emane_configuration = EmaneConfiguration()

    async def on_startup(_app):
        bridge.loop = asyncio.get_running_loop()
        bridge.start_poll_thread()

    app.on_startup.append(on_startup)

    async def get_mesh_status(_request):
        return web.json_response(bridge.snapshot())

    async def get_mission_inventory(_request):
        return web.json_response(bridge.mission_inventory_snapshot())

    async def get_mission_acks(_request):
        return web.json_response(bridge.mission_ack_snapshot())

    async def get_mission_commands(_request):
        return web.json_response(bridge.mission_command_snapshot())

    async def post_mission_scenario(request):
        try:
            body = await request.json()
            result = await asyncio.get_running_loop().run_in_executor(None, bridge.reveal_scenario_target, body)
        except MissionConflict as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except (dds.Exception, OSError, RuntimeError) as exc:
            logger.exception("Scenario target publication failed")
            return web.json_response({"error": str(exc)}, status=503)
        return web.json_response(result)

    async def post_mission_commands(request):
        try:
            mission_request = await request.json()
            result = await asyncio.get_running_loop().run_in_executor(
                None, bridge.write_mission_command, mission_request)
        except MissionConflict as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        status = (200 if not result["errors"] else
                  207 if any(command["published"] for command in result["commands"]) else 503)
        return web.json_response(result, status=status)

    async def post_mission_validate(request):
        try:
            body = await request.json()
            result = await asyncio.get_running_loop().run_in_executor(
                None, bridge.validate_mission_plan, body)
        except MissionConflict as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(result)

    async def team_assignment(request):
        try:
            body = await request.json()
            platform_node = body["platform_node"]
            team_name = body.get("team_name", "")
        except (json.JSONDecodeError, KeyError):
            return web.Response(status=400, text="expected JSON {platform_node, team_name}")
        await asyncio.get_running_loop().run_in_executor(
            None, bridge.write_team_assignment, platform_node, team_name)
        return web.Response(status=204)

    async def set_status_resolution(request):
        try:
            body = await request.json()
            platform_node = body["platform_node"]
            mode = body.get("mode", body.get("resolution_mode"))
            if mode is None:
                raise KeyError("mode")
        except (json.JSONDecodeError, KeyError):
            return web.Response(status=400,
                                text="expected JSON {platform_node, mode}")

        try:
            await asyncio.get_running_loop().run_in_executor(
                None, bridge.write_status_mode, platform_node, mode)
        except ValueError as exc:
            return web.Response(status=400, text=str(exc))
        # Broadcast mode change event for traffic chart annotations.
        bridge._broadcast({
            "type": "resolution_change",
            "platform": platform_node,
            "mode": str(mode),
            "resolution_mode": str(mode),
            "timestamp": int(time.time() * 1000),
        })
        return web.Response(status=204)

    async def get_platform_status(request):
        platform_node = request.rel_url.query.get("platform")
        return web.json_response(bridge.platform_snapshot(platform_node))

    async def websocket_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        bridge.ws_clients.add(ws)
        try:
            async for _msg in ws:
                pass  # the client never sends anything meaningful; just wait for close
        finally:
            bridge.ws_clients.discard(ws)
        return ws

    async def index(_request):
        return web.FileResponse(static_dir / "index.html")

    async def get_traffic_stats(_request):
        return web.json_response(bridge.traffic_snapshot())

    async def get_emane_stats(_request):
        return web.json_response(bridge.emane_snapshot())

    async def get_router_diagnostics(_request):
        return web.json_response(bridge.router_diagnostics_snapshot())

    async def get_link_stats(_request):
        return web.json_response(bridge.link_stats_snapshot())

    async def get_emane_controller(_request):
        return web.json_response(bridge.emane_controller_status())

    async def get_emane_configuration(_request):
        result = await asyncio.get_running_loop().run_in_executor(
            None, emane_configuration.snapshot)
        return web.json_response(result)

    async def post_emane_controller(request):
        try:
            body = await request.json()
            if body.get("reset_all"):
                result = bridge.reset_all_emane_impairments()
            else:
                result = bridge.set_emane_impairment(
                    body["source"], body["destination"], body.get("direction", "bidirectional"),
                    body.get("kind", "pathloss"), body)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return web.json_response({"error": str(exc)}, status=503)
        return web.json_response(result)

    async def get_delivery_stats(request):
        try:
            window_ms = int(request.rel_url.query.get("window_ms", "30000"))
        except ValueError:
            return web.Response(status=400, text="window_ms must be an integer")
        if window_ms < 1_000 or window_ms > 60_000:
            return web.Response(status=400, text="window_ms must be between 1000 and 60000")
        return web.json_response(bridge.delivery_snapshot(window_ms))

    async def get_delivery_config(_request):
        return web.json_response(bridge.delivery_config)

    async def get_system_config(_request):
        return web.json_response(bridge.system_config)

    async def post_traffic_stats(request):
        """Ingest per-node capture summaries and broadcast network-wide aggregates."""
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.Response(status=400, text="expected JSON array")
        samples = body if isinstance(body, list) else [body]
        try:
            for sample in samples:
                validate_sample(sample)
        except ValueError as exc:
            return web.Response(status=400, text=str(exc))
        for sample in samples:
            bridge._broadcast({"type": "traffic_node_stats", "data": sample})
            aggregate = bridge.update_traffic(sample)
            for total in aggregate or []:
                bridge._broadcast({"type": "traffic_stats", "data": total})
        return web.Response(status=204)

    async def post_emane_stats(request):
        try:
            sample = await request.json()
        except json.JSONDecodeError:
            return web.Response(status=400, text="expected JSON object")
        try:
            validate_sample(sample, mac=True)
        except ValueError as exc:
            return web.Response(status=400, text=str(exc))
        node_sample = dict(sample)
        node_sample["mac_tx_bytes"] = (
            node_sample.get("mac_tx_unicast_bytes", 0) +
            node_sample.get("mac_tx_broadcast_bytes", 0))
        node_sample["mac_rx_bytes"] = (
            node_sample.get("mac_rx_unicast_bytes", 0) +
            node_sample.get("mac_rx_broadcast_bytes", 0))
        bridge._broadcast({"type": "emane_node_stats", "data": node_sample})
        aggregate = bridge.update_emane(sample)
        if aggregate:
            bridge._broadcast({"type": "emane_stats", "data": aggregate})
        return web.Response(status=204)

    app.router.add_get("/api/mesh_status", get_mesh_status)
    app.router.add_get("/api/platform_status", get_platform_status)
    app.router.add_get("/api/mission_inventory", get_mission_inventory)
    app.router.add_get("/api/mission_acks", get_mission_acks)
    app.router.add_get("/api/mission_commands", get_mission_commands)
    app.router.add_post("/api/mission_commands", post_mission_commands)
    app.router.add_post("/api/mission_validate", post_mission_validate)
    app.router.add_post("/api/mission_scenario", post_mission_scenario)
    app.router.add_get("/api/traffic_stats", get_traffic_stats)
    app.router.add_get("/api/emane_stats", get_emane_stats)
    app.router.add_get("/api/router_diagnostics", get_router_diagnostics)
    app.router.add_get("/api/link_stats", get_link_stats)
    app.router.add_get("/api/emane_controller", get_emane_controller)
    app.router.add_get("/api/emane_configuration", get_emane_configuration)
    app.router.add_post("/api/emane_controller", post_emane_controller)
    app.router.add_get("/api/delivery_stats", get_delivery_stats)
    app.router.add_get("/api/delivery_config", get_delivery_config)
    app.router.add_get("/api/system_config", get_system_config)
    app.router.add_post("/api/traffic_stats", post_traffic_stats)
    app.router.add_post("/api/emane_stats", post_emane_stats)
    app.router.add_post("/api/team_assignment", team_assignment)
    app.router.add_post("/api/status_resolution", set_status_resolution)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_get("/", index)
    app.router.add_static("/", str(static_dir))  # must be added last (catch-all for assets)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", type=int, default=20, help="control_lan domain id")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--poll-interval", type=float, default=0.15,
                         help="seconds between reader.take() polls")
    parser.add_argument("--types-xml", default=str(DEFAULT_TYPES_XML))
    parser.add_argument("--static-dir", default=str(DEFAULT_STATIC_DIR))
    parser.add_argument("--participant-qos-profile",
                        help="optional DomainParticipant QoS profile")
    parser.add_argument("--traffic-observers", default="",
                        help="comma-separated monitors required for an aggregate sample")
    parser.add_argument("--router-config", type=Path,
                        help="active router YAML used for the read-only Config tab and WAN QoS sidebar")
    args = parser.parse_args()

    os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
    os.environ.setdefault("RTI_LICENSE_FILE",
                           os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

    bridge = DdsBridge(args.domain, Path(args.types_xml), args.poll_interval,
                       args.participant_qos_profile,
                       filter(None, args.traffic_observers.split(",")), args.router_config)
    try:
        app = build_app(bridge, Path(args.static_dir))
        web.run_app(app, host=args.host, port=args.port)
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
