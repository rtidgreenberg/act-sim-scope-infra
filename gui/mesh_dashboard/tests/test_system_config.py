"""Read-only system configuration, source selection, API and browser contracts."""

import ast
import asyncio
import json
import re
import tempfile
import unittest
from enum import IntFlag
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from gui.mesh_dashboard.server.system_config import duration, load_system_config

ROOT = Path(__file__).resolve().parents[3]
STATIC = ROOT / "gui/mesh_dashboard/static"


class Mask(IntFlag):
    UDPv4 = 1
    SHMEM = 2
    UDPv6 = 8


def fake_provider(deadline=2, countdown="30000"):
    def endpoint(depth):
        return SimpleNamespace(
            reliability=SimpleNamespace(kind="ReliabilityKind.BEST_EFFORT"),
            history=SimpleNamespace(kind="HistoryKind.KEEP_LAST", depth=depth),
            durability=SimpleNamespace(kind="DurabilityKind.VOLATILE"),
            deadline=SimpleNamespace(period=deadline),
            liveliness=SimpleNamespace(kind="LivelinessKind.AUTOMATIC", lease_duration=float("inf")),
            property=SimpleNamespace(get=lambda _: countdown))

    participant = SimpleNamespace(
        transport_builtin=SimpleNamespace(mask=Mask.UDPv4),
        discovery_config=SimpleNamespace(participant_liveliness_lease_duration=300),
        discovery=SimpleNamespace(initial_peers=["192.0.2.1"], multicast_receive_addresses=[]))
    return SimpleNamespace(
        datawriter_qos_from_profile=Mock(side_effect=lambda _: endpoint(50)),
        datareader_qos_from_profile=Mock(side_effect=lambda _: endpoint(1)),
        participant_qos_from_profile=Mock(return_value=participant))


def configuration():
    return {
        "router": {"wan_contract": "two_topic_union", "presence_participant": {"control": "wan"}},
        "qos_libraries": [str(ROOT / "harness_v2/qos/act_qos_profiles.xml")],
        "qos_profiles": {"wan_event": "Test::Event", "wan_status": "Test::Status",
                         "participant": "Test::WAN"},
        "participants": {"wan": {"role": "control", "domain": 200, "on_wan": True, "qos": "participant"}},
        "routes": [{"name": "example", "enabled": True, "source": "control", "destination": "platform",
                    "source_side": {"enabled": False}, "topics": [{"name": "Example"}]}],
        "unrelated_secret": "must-not-be-published",
    }


class SystemConfigurationTests(unittest.TestCase):
    def load(self, config=None, provider=None):
        with tempfile.TemporaryDirectory(prefix="system-config-") as directory:
            path = Path(directory) / "router.yaml"
            path.write_text(json.dumps(config if config is not None else configuration()))
            return load_system_config(path, lambda _: provider or fake_provider(), float("inf"))

    def test_presence_uses_resolved_status_reader_and_distinguishes_timers(self):
        provider = fake_provider(deadline=4.5, countdown="42000")
        result = self.load(provider=provider)
        presence = next(s for s in result["sections"] if s["id"] == "presence")
        values = {row[0]: row[1] for row in presence["rows"]}
        self.assertEqual(values["ALIVE to STALE"], "4.5 s")
        self.assertEqual(values["STALE to DEAD"], "42 s")
        self.assertEqual(values["DDS endpoint liveliness lease"], "Infinite (no expiry)")
        self.assertEqual(values["Recovery to ALIVE"], "Any valid received Status")
        self.assertEqual(values["Heartbeat publication period"], "Not reported")
        self.assertIn("Test::Status", [call.args[0] for call in provider.datareader_qos_from_profile.call_args_list])
        self.assertNotIn("ACT_QOS_LIB::router_health",
                         [call.args[0] for call in provider.datareader_qos_from_profile.call_args_list])
        rows = next(s for s in result["sections"] if s["id"] == "wan-qos")["rows"]
        self.assertEqual(len(rows), 4)
        self.assertIn("50 per instance", rows[0][4])
        self.assertIn("1 per instance", rows[1][4])
        self.assertNotIn("must-not-be-published", json.dumps(result))

    def test_discovery_units_route_side_overrides_and_provenance(self):
        result = self.load()
        rows = {s["id"]: s["rows"] for s in result["sections"]}
        self.assertIn("Configuration SHA-256", [r[0] for r in rows["system"]])
        self.assertEqual(rows["participants"][0][5:], ["UDPv4", "5 min (300 s)", "192.0.2.1", "Disabled"])
        self.assertEqual(rows["routes"][0][2], "No")
        self.assertEqual(rows["routes"][0][4], "Yes")
        self.assertGreater(result["loaded_at_ms"], 0)

    def test_legacy_presence_and_disabled_presence_do_not_claim_shared_countdown(self):
        config = configuration()
        config["router"]["wan_contract"] = "legacy"
        result = self.load(config, fake_provider(deadline=8))
        presence = next(s for s in result["sections"] if s["id"] == "presence")
        self.assertIn(["Writer liveliness loss to DEAD", "Infinite (no expiry)",
                       "ACT_QOS_LIB::router_health: reader liveliness lease; legacy presence behavior."],
                      presence["rows"])
        self.assertNotIn("STALE to DEAD", [row[0] for row in presence["rows"]])
        config["router"]["presence_participant"] = {}
        result = self.load(config)
        presence = next(s for s in result["sections"] if s["id"] == "presence")
        self.assertEqual(presence["rows"][0][1], "Disabled")
        self.assertNotIn("ALIVE to STALE", [row[0] for row in presence["rows"]])

    def test_partial_shared_and_legacy_route_profiles_preserve_optional_configuration(self):
        config = configuration()
        config["router"]["presence_participant"] = {}
        del config["qos_profiles"]["wan_status"]
        result = self.load(config)
        rows = next(s for s in result["sections"] if s["id"] == "wan-qos")["rows"]
        self.assertEqual({row[0] for row in rows}, {"Event"})
        config["router"]["wan_contract"] = "legacy"
        config["routes"][0]["source_side"]["output"] = {
            "participant": "wan", "writer_qos": "wan_event"}
        config["routes"][0]["destination_side"] = {"input": {"participant": "wan"}}
        result = self.load(config)
        rows = next(s for s in result["sections"] if s["id"] == "wan-qos")["rows"]
        self.assertEqual(rows[0][:3], ["Example", "Writer", "Test::Event"])
        self.assertEqual(rows[1][:3], ["Example", "Reader", "Automatic QoS"])

    def test_invalid_inputs_fail_explicitly_and_absent_configuration_is_unavailable(self):
        self.assertFalse(load_system_config(None, None, None)["available"])
        for value in ("0", "-1", "invalid", "1.5", None, "\u0661"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "positive integer"):
                self.load(provider=fake_provider(countdown=value))
        config = configuration()
        config["router"]["presence_participant"] = ["wan"]
        with self.assertRaisesRegex(ValueError, "presence_participant"):
            self.load(config)
        config = configuration()
        config["router"]["wan_contract"] = "typo"
        with self.assertRaisesRegex(ValueError, "Unknown WAN contract"):
            self.load(config)
        with self.assertRaises(FileNotFoundError):
            load_system_config(ROOT / "missing-config.yaml", None, None)

    def test_human_durations_preserve_fractional_seconds_and_infinity(self):
        self.assertEqual(duration(0.15), "0.15 s")
        self.assertEqual(duration(300), "5 min (300 s)")
        self.assertEqual(duration(None), "Infinite (no expiry)")


class NativeSystemConfigurationTests(unittest.TestCase):
    def test_installed_provider_for_shared_and_legacy_configuration(self):
        try:
            import rti.connextdds as dds
        except ModuleNotFoundError as exc:
            if exc.name not in ("rti", "rti.connextdds"):
                raise
            self.skipTest("Installed Connext binding required; no DDS entities created")
        import yaml

        config = yaml.safe_load((ROOT / "router/config/control-platform.yaml").read_text())
        config["router"]["wan_contract"] = "two_topic_union"
        config["qos_profiles"]["wan_status"] = "ACT_QOS_LIB::wan_union_status"
        with tempfile.TemporaryDirectory(prefix="native-system-config-") as directory:
            path = Path(directory) / "router.yaml"
            path.write_text(yaml.safe_dump(config))
            result = load_system_config(path, dds.QosProvider, dds.Duration.infinite)
        presence = next(s for s in result["sections"] if s["id"] == "presence")
        values = {r[0]: r[1] for r in presence["rows"]}
        self.assertEqual(values["ALIVE to STALE"], "2 s")
        self.assertEqual(values["STALE to DEAD"], "30 s")
        self.assertEqual(values["DDS endpoint liveliness lease"], "Infinite (no expiry)")
        legacy = load_system_config(ROOT / "router/config/control-platform.yaml",
                                    dds.QosProvider, dds.Duration.infinite)
        presence = next(s for s in legacy["sections"] if s["id"] == "presence")
        values = {r[0]: r[1] for r in presence["rows"]}
        self.assertEqual(values["ALIVE to STALE"], "2 s")
        self.assertEqual(values["DDS endpoint liveliness lease"], "3 s")
        qos_rows = next(s for s in legacy["sections"] if s["id"] == "wan-qos")["rows"]
        self.assertTrue(any(row[0] == "ControlCommand" and row[3] == "Reliable" for row in qos_rows))


class SystemConfigurationApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_returns_snapshot_and_mutations_are_not_supported(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        path = ROOT / "gui/mesh_dashboard/server/mesh_bridge.py"
        build_app = next(node for node in ast.parse(path.read_text()).body
                         if isinstance(node, ast.FunctionDef) and node.name == "build_app")
        namespace = {"DdsBridge": object, "Path": Path, "web": web, "asyncio": asyncio,
                     "EmaneConfiguration": Mock}
        exec(compile(ast.Module(body=[build_app], type_ignores=[]), str(path), "exec"), namespace)
        bridge = Mock()
        bridge.system_config = {"available": True, "sections": []}
        app = namespace["build_app"](bridge, STATIC)
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/api/system_config")
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), bridge.system_config)
            for method in (client.post, client.put, client.delete):
                response = await method("/api/system_config", json={})
                self.assertEqual(response.status, 405)


class SystemConfigurationBrowserTests(unittest.TestCase):
    def test_tab_tables_layout_errors_recovery_and_read_only_requests(self):
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            if exc.name not in ("playwright", "playwright.sync_api"):
                raise
            self.skipTest("Playwright required for browser verification")

        document = (STATIC / "index.html").read_text()
        html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", document)
        snapshot = SystemConfigurationTests().load()
        snapshot["sections"][0]["rows"].append(["Unsafe text", "<img src=x onerror=alert(1)>", "Literal only"])
        state = {"mode": "ok"}
        requests = []
        with sync_playwright() as driver:
            browser = driver.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def serve(route):
                if "/api/system_config" in route.request.url:
                    requests.append(route.request.method)
                    if state["mode"] == "error":
                        route.fulfill(status=503)
                    elif state["mode"] == "malformed":
                        route.fulfill(json={"available": True, "sections": [{"title": "Bad", "rows": []}]})
                    elif state["mode"] == "unavailable":
                        route.fulfill(json={"available": False, "reason": "No router config supplied", "sections": []})
                    else:
                        route.fulfill(json=snapshot)
                elif "/api/" in route.request.url:
                    route.fulfill(json={})
                else:
                    route.fulfill(content_type="text/html", body=html)

            page.route("**/*", serve)
            page.goto("http://config.test/")
            page.evaluate("() => { window.WebSocket = class {}; }")
            page.add_script_tag(path=str(STATIC / "operator_tabs.js"))
            page.add_script_tag(path=str(STATIC / "config_tab.js"))
            page.locator('[data-tab="config"]').click()
            page.wait_for_selector("#config-sections table")
            self.assertTrue(page.locator("#config-panel").is_visible())
            self.assertFalse(page.locator("#graph").is_visible())
            self.assertEqual(page.locator('[data-tab="config"]').get_attribute("aria-selected"), "true")
            self.assertIn("30 s", page.locator("#config-summary").inner_text())
            self.assertIn("not per-node runtime introspection", page.locator("#config-status").inner_text())
            self.assertIn("Unsafe text", page.locator("#config-sections").inner_text())
            self.assertEqual(page.locator("#config-panel img").count(), 0)
            self.assertEqual(page.locator("#config-panel input, #config-panel select, #config-panel textarea").count(), 0)
            self.assertEqual(page.locator("#config-sections table").count(), 5)
            for width in (1440, 720):
                page.set_viewport_size({"width": width, "height": 1000})
                self.assertLessEqual(page.locator("#config-panel").evaluate(
                    "el => el.scrollWidth - el.clientWidth"), 1)
            for mode, message in (("error", "HTTP 503"), ("malformed", "Invalid configuration table"),
                                  ("unavailable", "No router config supplied")):
                state["mode"] = mode
                page.locator("#config-refresh").click()
                page.wait_for_function(
                    "text => document.querySelector('#config-status').textContent.includes(text)", arg=message)
                self.assertEqual(page.locator("#config-sections table").count(), 0)
                state["mode"] = "ok"
                page.locator("#config-refresh").click()
                page.wait_for_selector("#config-sections table")
            page.locator('[data-tab="mesh"]').click()
            self.assertFalse(page.locator("#config-panel").is_visible())
            self.assertTrue(page.locator("#graph").is_visible())
            self.assertTrue(all(method == "GET" for method in requests))
            self.assertEqual(errors, [])
            browser.close()


if __name__ == "__main__":
    unittest.main()
