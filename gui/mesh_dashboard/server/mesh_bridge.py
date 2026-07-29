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


def _qos_provider_for(types_xml: Path):
    """Avoid double-parsing types_xml's XML DOM in this process.

    ActTypes.idl's global `struct base_type` collides with an RTI-internal reserved name
    (`RTITypes::base_type`) if the SAME types XML gets parsed twice in one process --
    RTIXMLObject_addChild: XML object with name '::RTITypes::base_type' already exists.
    run_mesh.sh exports NDDS_QOS_PROFILES including this exact file for
    platform_control.py's benefit; Connext auto-loads that at process init, so if this
    process inherited that env var, loading the file again explicitly hits the collision.
    Reuse QosProvider.default (which already has it loaded) instead, in that case only.
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
        self.cache_lock = threading.Lock()
        self.ws_clients = set()  # set[web.WebSocketResponse]
        self.loop = None         # set once the aiohttp event loop is running
        self._stop = threading.Event()

        qp = _qos_provider_for(types_xml)
        mesh_type = qp.type(MESH_STATUS_TYPE)
        team_type = qp.type(TEAM_ASSIGNMENT_TYPE)

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

        # TeamAssignmentWriterQos equivalent: VOLATILE + RELIABLE.
        team_topic = dds.DynamicData.Topic(self.participant, TEAM_ASSIGNMENT_TOPIC, team_type)
        publisher = dds.Publisher(self.participant)
        wqos = dds.QosProvider.default.datawriter_qos
        wqos.durability.kind = dds.DurabilityKind.VOLATILE
        wqos.reliability.kind = dds.ReliabilityKind.RELIABLE
        self.writer = dds.DynamicData.DataWriter(publisher, team_topic, wqos)
        self.team_type = team_type

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
                self._broadcast(sample)
            self._stop.wait(self.poll_interval)

    def _broadcast(self, sample):
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._broadcast_async(sample)))

    async def _broadcast_async(self, sample):
        dead = []
        for ws in list(self.ws_clients):
            try:
                await ws.send_str(json.dumps({"data": sample}))
            except (ConnectionResetError, RuntimeError):
                dead.append(ws)
        for ws in dead:
            self.ws_clients.discard(ws)

    def snapshot(self):
        with self.cache_lock:
            return [{"data": v} for v in self.cache.values()]

    def write_team_assignment(self, platform_node, team_name):
        sample = dds.DynamicData(self.team_type)
        sample["platform_node"] = platform_node
        sample["team_name"] = team_name
        self.writer.write(sample)

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
    app.router.add_post("/api/team_assignment", team_assignment)
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
