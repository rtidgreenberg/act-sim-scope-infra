"""Bridge JSON contracts without importing Connext or creating DDS entities."""

import ast
import json
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


class PassiveRttJsonTests(unittest.TestCase):
    def test_single_wan_health_and_direct_peer_delivery_audiences(self):
        path = Path(__file__).resolve().parents[1] / "server/mesh_bridge.py"
        source = ast.parse(path.read_text())
        bridge_class = next(node for node in source.body
                            if isinstance(node, ast.ClassDef) and node.name == "DdsBridge")
        bridge_class.body = [node for node in bridge_class.body
                             if isinstance(node, ast.FunctionDef) and node.name in
                             {"_expected_recipients", "_team_names_for_node"}]
        namespace = {"FIXED_CONTROL_RECIPIENT_TOPICS": set()}
        exec(compile(ast.Module(body=[bridge_class], type_ignores=[]), str(path), "exec"),
             namespace)
        bridge = namespace["DdsBridge"]()
        nodes = ["Control_20", "Platform_30", "Platform_31", "Platform_32"]
        bridge._audit_nodes_for_run = lambda _: nodes
        bridge._health_discovery_is_global = lambda _: True
        event = {"topic": "RouterHealth", "source_node": "Platform_30", "run_id": "run"}
        self.assertEqual(bridge._expected_recipients(event),
                         ["Control_20", "Platform_31", "Platform_32"])
        bridge._health_discovery_is_global = lambda _: False
        self.assertEqual(bridge._expected_recipients(event), ["Control_20"])
        bridge.cache = {"observer": {"peers": [
            {"health": {"router": "Platform_30/router", "team_partition": ["Platform_30"]}},
            {"health": {"router": "Platform_31/router",
                        "team_partition": ["Platform_31", "Platform_30"]}},
            {"health": {"router": "Platform_32/router", "team_partition": ["Platform_32"]}},
        ]}}
        event["topic"] = "PlatformData"
        self.assertEqual(bridge._expected_recipients(event), ["Platform_31"])
        event["source_node"] = "Platform_31"
        self.assertEqual(bridge._expected_recipients(event), ["Platform_30"])
        event["source_node"] = "Platform_32"
        self.assertEqual(bridge._expected_recipients(event), [])

    def test_poll_and_snapshots_preserve_session_ack_and_rtt_metadata(self):
        path = Path(__file__).resolve().parents[1] / "server/mesh_bridge.py"
        source = ast.parse(path.read_text())
        bridge_class = next(node for node in source.body
                            if isinstance(node, ast.ClassDef) and node.name == "DdsBridge")
        bridge_class.body = [node for node in bridge_class.body
                             if isinstance(node, ast.FunctionDef) and node.name in
                             {"_poll_loop", "snapshot", "link_stats_snapshot"}]
        clock = Mock(wraps=time)
        clock.monotonic.return_value = 100
        namespace = {"json": json, "time": clock, "uuid": uuid}
        exec(compile(ast.Module(body=[bridge_class], type_ignores=[]), str(path), "exec"),
             namespace)
        bridge = namespace["DdsBridge"]()
        session = (1 << 64) - 1
        ack = {"router": "Control_20/router", "presence": "PRESENCE_ALIVE",
               "last_seen_delta_ms": 700, "ack_session_id": session,
               "ack_heartbeat_seq": session - 1, "ack_age_at_send_us": 680_000}
        health = {"router": "Platform_30/router", "session_id": session - 2,
                  "heartbeat_seq": 42, "peers_seen": [ack]}
        mesh = {"observer_node": "Control_20",
                "peers": [{"health": health, "presence": "PRESENCE_ALIVE",
                           "last_seen_delta_ms": 1000}]}
        stats = {"observer_router": "Control_20/router", "peer_router": health["router"],
                 "network": "wan", "rtt_valid": True, "rtt_count": 1,
                 "rtt_min_us": 45_000, "rtt_mean_us": 45_000, "rtt_max_us": 45_000,
                 "rtt_last_sample_age_ms": 250, "rtt_last_ack_age_us": 680_000}

        def reader(payload=None):
            samples = [] if payload is None else [
                (SimpleNamespace(to_json=lambda: json.dumps(payload)),
                 SimpleNamespace(valid=True))]
            return SimpleNamespace(take=lambda: samples)

        bridge.reader = reader(mesh)
        bridge.command_ack_reader = reader()
        bridge.diagnostics_reader = reader()
        bridge.scenario_reader = reader()
        bridge.platform_readers = [("init", "PlatformInitStatus", reader())]
        bridge.link_stats_reader = reader(stats)
        bridge.cache_lock = threading.RLock()
        bridge.cache = {}
        bridge.mesh_updated_at = {}
        bridge.mesh_received_at = {}
        bridge.link_stats_cache = {}
        bridge.link_stats_received_at = {}
        bridge._stop = threading.Event()
        bridge._stop.wait = lambda _: bridge._stop.set()
        bridge.poll_interval = 0
        bridge._broadcast = Mock()
        bridge.mission_inventory_snapshot = lambda: {}
        bridge.poll_audit_events = Mock()
        bridge.poll_wire_events = lambda: False
        bridge.expire_mission_commands = Mock()
        bridge.reconcile_team_missions = Mock()
        bridge._poll_loop()

        self.assertEqual(json.loads(json.dumps(bridge.snapshot())),
                         [{"data": mesh, "telemetry_age_ms": 0}])
        self.assertEqual(bridge.snapshot()[0]["data"]["peers"][0]["health"]["peers_seen"][0], ack)
        snapshot = bridge.link_stats_snapshot()
        stats["telemetry_sample_id"] = snapshot["rows"][0]["telemetry_sample_id"]
        self.assertTrue(stats["telemetry_sample_id"])
        expected = {"rows": [dict(stats, telemetry_age_ms=0)]}
        self.assertEqual(json.loads(json.dumps(snapshot)), expected)
        payloads = [call.args[0] for call in bridge._broadcast.call_args_list]
        self.assertIn({"type": "mesh_status", "data": mesh}, payloads)
        self.assertIn({"type": "link_stats", "data": expected}, payloads)
        clock.monotonic.return_value = 106
        for wall_time in (-1_000_000, 1_000_000):
            clock.time.return_value = wall_time
            aged = bridge.link_stats_snapshot()["rows"][0]
            self.assertEqual(aged["telemetry_age_ms"], 6000)
            self.assertEqual(bridge.snapshot()[0]["telemetry_age_ms"], 6000)
            self.assertEqual(aged["telemetry_sample_id"], stats["telemetry_sample_id"])
        self.assertNotIn("telemetry_age_ms", next(iter(bridge.link_stats_cache.values())))

        stats.update(rtt_valid=False, rtt_count=0, rtt_last_sample_age_ms=0,
                     rtt_last_ack_age_us=0)
        bridge.link_stats_cache = {("Control_20/router", health["router"], "wan"): stats}
        self.assertEqual(bridge.link_stats_snapshot()["rows"][0]["rtt_count"], 0)
        self.assertIs(bridge.link_stats_snapshot()["rows"][0]["rtt_valid"], False)
