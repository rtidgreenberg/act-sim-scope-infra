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
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from aiohttp import web
import rti.connextdds as dds

_THIS_FILE = Path(__file__).resolve()
DEFAULT_TYPES_XML = _THIS_FILE.parents[3] / "harness_v2" / "datamodel" / "gen" / "ActTypes.xml"
DEFAULT_QOS_XML = _THIS_FILE.parents[3] / "harness_v2" / "qos" / "act_qos_profiles.xml"
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
MISSION_MODE_TOPICS = set(MISSION_STATUS_TOPICS)
DEBUG_MODE_TOPICS = set(DEBUG_STATUS_TOPICS)
FIXED_CONTROL_RECIPIENT_TOPICS = {
    "PlatformInitStatus", "PlatformDetailStatus", "PlatformMissionStatus",
    "PlatformWaypointStatus", "PlatformDebugStatus", "PlatformThrusterStatus",
    "PlatformPowerStatus", "PlatformCommandAck", "ContactReport",
}
PERCENTAGE_MIN_SAMPLES = 30
EMANE_EVENT_GROUP = "224.1.2.8"
EMANE_EVENT_PORT = "45702"
EMANE_PATHLOSS_MAX_DB = 200


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
                 traffic_observers=None):
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

        pqos = (qp.participant_qos_from_profile(participant_qos_profile)
            if participant_qos_profile else dds.DomainParticipant.default_participant_qos)
        self.participant = dds.DomainParticipant(domain_id, pqos)

        mesh_topic = dds.DynamicData.Topic(self.participant, MESH_STATUS_TOPIC, mesh_type)
        subscriber = dds.Subscriber(self.participant)
        self.reader = dds.DynamicData.DataReader(subscriber, mesh_topic)

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
        self.traffic_observers = set(traffic_observers or [])
        self.traffic_published_at = {}  # (observer, domain_id) -> timestamp in last aggregate
        self.latest_traffic_aggregate = []
        self.emane_cache = {}  # observer -> latest MAC transmit delta
        self.emane_published_at = {}  # observer -> timestamp in last aggregate
        self.latest_emane_aggregate = None
        self.audit_offsets = {}  # events.jsonl path -> consumed byte offset
        self.audit_sent = {}  # (run_id, topic, source, sequence) -> sent event
        self.audit_received = {}  # (run_id, topic, source, sequence, receiver) -> receive event
        self.audit_versions = {}  # (topic, source, recipient) -> [sent, received]
        self.platform_status_modes = {}  # platform_node -> init|mission|debug
        self.platform_status_mode_changed_ns = {}  # platform_node -> monotonic mode-change cutoff
        self.emane_impairments = {}

        # TeamAssignmentWriterQos equivalent: VOLATILE + RELIABLE.
        team_topic = dds.DynamicData.Topic(self.participant, TEAM_ASSIGNMENT_TOPIC, team_type)
        publisher = dds.Publisher(self.participant)
        self.writer = dds.DynamicData.DataWriter(publisher, team_topic)

        # StatusResolution writer (RELIABLE + VOLATILE).
        status_topic = dds.DynamicData.Topic(self.participant, STATUS_MODE_TOPIC,
                             status_mode_type)
        self.status_mode_writer = dds.DynamicData.DataWriter(publisher, status_topic)

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

            if self.poll_audit_events():
                self._broadcast({"type": "delivery_stats", "data": self.delivery_snapshot()})

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

    def traffic_snapshot(self):
        with self.cache_lock:
            return list(self.latest_traffic_aggregate)

    def emane_snapshot(self):
        with self.cache_lock:
            return dict(self.latest_emane_aggregate) if self.latest_emane_aggregate else None

    def _platform_topic_expected(self, source_node, topic, sent_at_ns):
        if not source_node.startswith("Platform_"):
            return True
        mode = self.platform_status_modes.get(source_node, "init")
        cutoff_ns = self.platform_status_mode_changed_ns.get(source_node, 0)
        if topic in MISSION_MODE_TOPICS | DEBUG_MODE_TOPICS and sent_at_ns < cutoff_ns:
            return False
        if topic in DEBUG_MODE_TOPICS:
            return mode == "debug"
        if topic in MISSION_MODE_TOPICS:
            return mode in ("mission", "debug")
        return True

    def _expected_recipients(self, event):
        if event["topic"] == "ControlCommand":
            return [event["destination"]] if event.get("destination") else []
        if event["topic"] in FIXED_CONTROL_RECIPIENT_TOPICS \
                and event["source_node"].startswith("Platform_"):
            sent_at_ns = int(event.get("sent_at_ns", 0))
            if not self._platform_topic_expected(event["source_node"], event["topic"], sent_at_ns):
                return []
            return ["Control_20"]
        return []

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

    def poll_audit_events(self):
        changed = False
        debug_root = _THIS_FILE.parents[3] / "debug"
        for path in debug_root.glob("*_debug/events.jsonl"):
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

    def delivery_snapshot(self, window_ms=30_000):
        earliest_ns = time.time_ns() - window_ms * 1_000_000
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
                    node_modes[source] = self.platform_status_modes.get(source, "init")
                events.sort(key=lambda item: item[1]["sent_at_ns"])
                received = sum((key + (recipient,)) in self.audit_received for key, _ in events)
                rate_hz = None
                if len(events) > 1:
                    elapsed_ns = events[-1][1]["sent_at_ns"] - events[0][1]["sent_at_ns"]
                    if elapsed_ns > 0:
                        rate_hz = (len(events) - 1) * 1_000_000_000 / elapsed_ns
                sent_version, received_version = self._audit_version((topic, source, recipient))
                required_samples = self._required_samples_for_full_set(rate_hz, window_ms)
                percentage_ready = len(events) >= required_samples
                percentage = 100.0
                if percentage_ready:
                    percentage = 100 * received / len(events)
                rows.append({
                    "topic": topic,
                    "source": source,
                    "recipient": recipient,
                    "expected": len(events),
                    "received": received,
                    "missing": len(events) - received,
                    "percentage": percentage,
                    "percentage_ready": percentage_ready,
                    "percentage_min_samples": PERCENTAGE_MIN_SAMPLES,
                    "percentage_required_samples": required_samples,
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
        totals = {}
        fields = ("discovery_packets", "discovery_bytes", "data_packets", "data_bytes",
                  "reliability_packets", "reliability_bytes", "mixed_packets", "mixed_bytes",
                  "unknown_packets", "unknown_bytes", "discovery_tx_packets",
                  "discovery_tx_bytes", "data_tx_packets", "data_tx_bytes",
                  "total_packets", "total_bytes")
        for sample in self.traffic_cache.values():
            domain_id = sample["domain_id"]
            total = totals.setdefault(domain_id, {
                "domain_id": domain_id,
                "observer": "network",
                "scope": "network",
                "capture_timestamp": 0,
                "interval_ms": sample.get("interval_ms", 0),
                "contributors": [],
                "discovery_packets": 0,
                "discovery_bytes": 0,
                "data_packets": 0,
                "data_bytes": 0,
                "reliability_packets": 0,
                "reliability_bytes": 0,
                "mixed_packets": 0,
                "mixed_bytes": 0,
                "unknown_packets": 0,
                "unknown_bytes": 0,
                "discovery_tx_packets": 0,
                "discovery_tx_bytes": 0,
                "data_tx_packets": 0,
                "data_tx_bytes": 0,
                "total_packets": 0,
                "total_bytes": 0,
            })
            total["capture_timestamp"] = max(total["capture_timestamp"],
                                             sample.get("capture_timestamp", 0))
            total["contributors"].append(sample.get("observer", "unknown"))
            for field in fields:
                total[field] += sample.get(field, 0)

        for total in totals.values():
            observer_count = len(total["contributors"])
            total["observer_count"] = observer_count
            # Every EMANE receiver can observe the same multicast RTPS frame. Preserve the
            # raw sums for diagnostics, but give the UI a rate that is not multiplied by
            # the number of monitors.
            for field in fields:
                total[f"mean_{field}"] = total[field] / observer_count if observer_count else 0
        return list(totals.values())

    def update_traffic(self, sample):
        """Store one node interval and return a complete aggregate when available."""
        domain_id = sample.get("domain_id")
        observer = sample.get("observer")
        if domain_id is None or not observer:
            return None
        with self.cache_lock:
            self.traffic_cache[(observer, domain_id)] = sample
            observers = self.traffic_observers or {
                name for name, cached_domain in self.traffic_cache if cached_domain == domain_id
            }
            keys = {(name, domain_id) for name in observers}
            if not keys.issubset(self.traffic_cache):
                return None
            if any(self.traffic_cache[key].get("capture_timestamp", 0) <=
                   self.traffic_published_at.get(key, 0) for key in keys):
                return None
            self.latest_traffic_aggregate = self._aggregate_traffic_locked()
            for key in keys:
                self.traffic_published_at[key] = self.traffic_cache[key].get(
                    "capture_timestamp", 0)
            return list(self.latest_traffic_aggregate)

    def update_emane(self, sample):
        """Store one MAC TX delta and return an all-node aggregate when complete."""
        observer = sample.get("observer")
        timestamp = sample.get("capture_timestamp", 0)
        if not observer or not timestamp:
            return None
        fields = ("mac_tx_unicast_bytes", "mac_tx_broadcast_bytes",
                  "mac_tx_unicast_packets", "mac_tx_broadcast_packets",
                  "mac_tx_unicast_drops", "mac_tx_broadcast_drops",
                  "mac_rx_unicast_bytes", "mac_rx_broadcast_bytes",
                  "mac_rx_unicast_packets", "mac_rx_broadcast_packets",
                  "mac_rx_unicast_drops", "mac_rx_broadcast_drops")
        if any(field not in sample for field in fields):
            return None

        with self.cache_lock:
            self.emane_cache[observer] = sample
            observers = self.traffic_observers or set(self.emane_cache)
            if not observers.issubset(self.emane_cache):
                return None
            if any(self.emane_cache[name].get("capture_timestamp", 0) <=
                   self.emane_published_at.get(name, 0) for name in observers):
                return None

            aggregate = {
                "scope": "network",
                "observer": "network",
                "capture_timestamp": max(self.emane_cache[name]["capture_timestamp"]
                                         for name in observers),
                "interval_ms": max(self.emane_cache[name].get("interval_ms", 0)
                                   for name in observers),
                "contributors": sorted(observers),
            }
            for field in fields:
                aggregate[field] = sum(self.emane_cache[name][field] for name in observers)
            aggregate["mac_tx_bytes"] = (aggregate["mac_tx_unicast_bytes"] +
                                         aggregate["mac_tx_broadcast_bytes"])
            aggregate["mac_tx_packets"] = (aggregate["mac_tx_unicast_packets"] +
                                           aggregate["mac_tx_broadcast_packets"])
            aggregate["mac_tx_drops"] = (aggregate["mac_tx_unicast_drops"] +
                                         aggregate["mac_tx_broadcast_drops"])
            aggregate["mac_rx_bytes"] = (aggregate["mac_rx_unicast_bytes"] +
                                         aggregate["mac_rx_broadcast_bytes"])
            aggregate["mac_rx_packets"] = (aggregate["mac_rx_unicast_packets"] +
                                           aggregate["mac_rx_broadcast_packets"])
            aggregate["mac_rx_drops"] = (aggregate["mac_rx_unicast_drops"] +
                                         aggregate["mac_rx_broadcast_drops"])
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
        return {
            "available": shutil.which("emaneevent-pathloss") is not None,
            "event_group": EMANE_EVENT_GROUP,
            "event_port": int(EMANE_EVENT_PORT),
            "impairments": list(self.emane_impairments.values()),
        }

    def set_emane_pathloss(self, source, destination, direction, pathloss_db):
        if direction not in {"forward", "reverse", "bidirectional"}:
            raise ValueError("direction must be forward, reverse, or bidirectional")
        if source == destination:
            raise ValueError("source and destination must differ")
        source_nem = self._nem_id(source)
        destination_nem = self._nem_id(destination)
        if pathloss_db < 0 or pathloss_db > EMANE_PATHLOSS_MAX_DB:
            raise ValueError(f"pathloss_db must be between 0 and {EMANE_PATHLOSS_MAX_DB}")
        executable = shutil.which("emaneevent-pathloss")
        if executable is None:
            raise RuntimeError("emaneevent-pathloss is unavailable in this dashboard container")
        pairs = [(source_nem, destination_nem)]
        if direction in {"reverse", "bidirectional"}:
            pairs.append((destination_nem, source_nem))
        for from_nem, to_nem in pairs:
            result = subprocess.run(
                [executable, str(from_nem), str(pathloss_db), "--target", str(to_nem)],
                check=False, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                raise RuntimeError(f"EMANE pathloss event failed: {detail or result.returncode}")
        key = f"{source}->{destination}"
        self.emane_impairments[key] = {
            "source": source, "destination": destination, "direction": direction,
            "pathloss_db": pathloss_db, "updated_at": int(time.time() * 1000),
        }
        return self.emane_controller_status()


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

    async def get_emane_controller(_request):
        return web.json_response(bridge.emane_controller_status())

    async def post_emane_controller(request):
        try:
            body = await request.json()
            result = bridge.set_emane_pathloss(
                body["source"], body["destination"], body.get("direction", "bidirectional"),
                float(body.get("pathloss_db", 0)))
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

    async def post_traffic_stats(request):
        """Ingest per-node capture summaries and broadcast network-wide aggregates."""
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.Response(status=400, text="expected JSON array")
        samples = body if isinstance(body, list) else [body]
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
    app.router.add_get("/api/traffic_stats", get_traffic_stats)
    app.router.add_get("/api/emane_stats", get_emane_stats)
    app.router.add_get("/api/emane_controller", get_emane_controller)
    app.router.add_post("/api/emane_controller", post_emane_controller)
    app.router.add_get("/api/delivery_stats", get_delivery_stats)
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
    args = parser.parse_args()

    os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
    os.environ.setdefault("RTI_LICENSE_FILE",
                           os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

    bridge = DdsBridge(args.domain, Path(args.types_xml), args.poll_interval,
                       args.participant_qos_profile,
                       filter(None, args.traffic_observers.split(",")))
    try:
        app = build_app(bridge, Path(args.static_dir))
        web.run_app(app, host=args.host, port=args.port)
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
