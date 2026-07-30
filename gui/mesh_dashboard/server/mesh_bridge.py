#!/usr/bin/env python3
"""Minimal DDS<->HTTP/WebSocket bridge for gui/mesh_dashboard/, replacing RTI Web
Integration Service (WIS). Design/rationale:
docs/cpp_router/mesh-dashboard-bridge-implementation-plan.md.

Reads ActRouterMeshStatus (control_lan, domain 20 by default) and serves it as:
  GET  /api/mesh_status        -> [{"data": {...}}, ...]  (current cached samples)
  GET  /ws                     -> WebSocket; pushes {"data": {...}} per new sample
  POST /api/team_assignment    -> body {"platform_node": ..., "team_name": ...};
                                   writes one ActTeamAssignment sample, 204 on success
Also serves the static dashboard page (gui/mesh_dashboard/static/) at "/".

The "data" envelope on the REST/WebSocket responses matches what mesh_graph.js's
ingestSampleArray() already expects (it was written against WIS's own REST1/WS
envelope shape), so the front-end's sample-parsing code needed no changes -- only
the URL/connection plumbing did.
"""
import argparse
import asyncio
import json
import os
import threading
import time
from pathlib import Path

from aiohttp import web
import rti.connextdds as dds

_THIS_FILE = Path(__file__).resolve()
DEFAULT_TYPES_XML = _THIS_FILE.parents[3] / "harness_v2" / "datamodel" / "gen" / "ActTypes.xml"
DEFAULT_STATIC_DIR = _THIS_FILE.parents[1] / "static"

MESH_STATUS_TOPIC = "ActRouterMeshStatus"
MESH_STATUS_TYPE = "RouterMeshStatus"
TEAM_ASSIGNMENT_TOPIC = "ActTeamAssignment"
TEAM_ASSIGNMENT_TYPE = "TeamAssignment"
STATUS_MODE_TOPIC = "ActPlatformStatusMode"
STATUS_MODE_TYPE = "PlatformStatusMode"

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
    return dds.QosProvider(str(types_xml))


class DdsBridge:
    """Owns the one DomainParticipant + reader + writer this service needs."""

    def __init__(self, domain_id, types_xml, poll_interval):
        self.poll_interval = poll_interval
        self.cache = {}          # observer_node -> latest sample dict
        self.platform_cache = {} # platform_node -> latest per-resolution topic samples
        self.cache_lock = threading.Lock()
        self.ws_clients = set()  # set[web.WebSocketResponse]
        self.loop = None         # set once the aiohttp event loop is running
        self._stop = threading.Event()

        qp = _qos_provider_for(types_xml)
        mesh_type = qp.type(MESH_STATUS_TYPE)
        team_type = qp.type(TEAM_ASSIGNMENT_TYPE)
        status_mode_type = qp.type(STATUS_MODE_TYPE)

        pqos = dds.DomainParticipantQos()
        pqos.transport_builtin = dds.TransportBuiltin.udpv4
        self.participant = dds.DomainParticipant(domain_id, pqos)

        # MeshStatusReaderQos equivalent: VOLATILE + BEST_EFFORT (D100).
        mesh_topic = dds.DynamicData.Topic(self.participant, MESH_STATUS_TOPIC, mesh_type)
        subscriber = dds.Subscriber(self.participant)
        rqos = dds.QosProvider.default.datareader_qos
        rqos.durability.kind = dds.DurabilityKind.VOLATILE
        rqos.reliability.kind = dds.ReliabilityKind.BEST_EFFORT
        self.reader = dds.DynamicData.DataReader(subscriber, mesh_topic, rqos)

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
            reader = dds.DynamicData.DataReader(subscriber, topic, rqos)
            self.platform_readers.append((level, topic_name, reader))

        # TeamAssignmentWriterQos equivalent: VOLATILE + RELIABLE.
        team_topic = dds.DynamicData.Topic(self.participant, TEAM_ASSIGNMENT_TOPIC, team_type)
        publisher = dds.Publisher(self.participant)
        wqos = dds.QosProvider.default.datawriter_qos
        wqos.durability.kind = dds.DurabilityKind.VOLATILE
        wqos.reliability.kind = dds.ReliabilityKind.RELIABLE
        self.writer = dds.DynamicData.DataWriter(publisher, team_topic, wqos)

        # StatusResolution writer (RELIABLE + VOLATILE).
        status_topic = dds.DynamicData.Topic(self.participant, STATUS_MODE_TOPIC,
                             status_mode_type)
        self.status_mode_writer = dds.DynamicData.DataWriter(publisher, status_topic, wqos)

        self.team_type = team_type
        self.status_mode_type = status_mode_type

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

                self._broadcast({"type": "mesh_status", "data": sample})

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
            return [{"data": v} for v in self.cache.values()]

    def platform_snapshot(self, platform_node=None):
        with self.cache_lock:
            if platform_node:
                return self.platform_cache.get(platform_node, {
                    "init": {}, "mission": {}, "debug": {}, "updated_at": 0,
                })
            return json.loads(json.dumps(self.platform_cache))

    def write_team_assignment(self, platform_node, team_name):
        sample = dds.DynamicData(self.team_type)
        sample["platform_node"] = platform_node
        sample["team_name"] = team_name
        self.writer.write(sample)

    def write_status_mode(self, platform_node, resolution_mode):
        mode_map = {
            "init": 0,
            "mission": 1,
            "debug": 2,
        }
        mode = mode_map.get(str(resolution_mode).lower())
        if mode is None:
            raise ValueError("resolution_mode must be one of: init, mission, debug")

        sample = dds.DynamicData(self.status_mode_type)
        sample["platform_node"] = platform_node
        sample["resolution_mode"] = mode
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
            entry = self.platform_cache.get(platform_node)
            if entry:
                for lvl in levels_to_clear.get(mode, []):
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


def build_app(bridge: DdsBridge, static_dir: Path) -> web.Application:
    app = web.Application()

    async def on_startup(_app):
        bridge.loop = asyncio.get_running_loop()
        bridge.start_poll_thread()

    app.on_startup.append(on_startup)

    async def get_mesh_status(_request):
        return web.json_response(bridge.snapshot())

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
            resolution_mode = body["resolution_mode"]
        except (json.JSONDecodeError, KeyError):
            return web.Response(status=400,
                                text="expected JSON {platform_node, resolution_mode}")

        try:
            await asyncio.get_running_loop().run_in_executor(
                None, bridge.write_status_mode, platform_node, resolution_mode)
        except ValueError as exc:
            return web.Response(status=400, text=str(exc))
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

    app.router.add_get("/api/mesh_status", get_mesh_status)
    app.router.add_get("/api/platform_status", get_platform_status)
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
    args = parser.parse_args()

    os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
    os.environ.setdefault("RTI_LICENSE_FILE",
                           os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

    bridge = DdsBridge(args.domain, Path(args.types_xml), args.poll_interval)
    try:
        app = build_app(bridge, Path(args.static_dir))
        web.run_app(app, host=args.host, port=args.port)
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
