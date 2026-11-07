"""EMANE runtime configuration contracts without Connext or DDS entities."""

import ast
import asyncio
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from gui.mesh_dashboard.server import emane_config

OUTPUT = """nem 1 transport bitrate = 1000000
nem 1 transport device = emane0
nem 1 mac datarate = 1000000
nem 1 mac delay = 0.025
nem 1 mac jitter = 0.001
nem 1 phy bandwidth = 1000000
nem 1 phy frequency = 2347000000
nem 1 phy txpower = 0.0
nem 1 phy systemnoisefigure = 4.0
nem 1 phy propagationmodel = precomputed
nem 1 phy fading.model = none
nem 1 phy noisemode = outofband
nem 1 shim0 defaultconnectivitymode = True
nem 1 shim0 enablepromiscuousmode = False
nem 1 shim0 filterfile =
nem 2 mac datarate = 4000000
"""
MODELS = {"mac": "rfpipemaclayer", "transport": "transvirtual", "shim0": "commeffectshim"}


class EmaneConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(emane_config.os.environ, {
            "EMANE_ENABLED": "1", "EMANE_NEM_ID": "1", "NODE_NAME": "Control_20"},
            clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_parameters_preserve_native_units_and_only_selected_nem(self):
        components = emane_config.parse_configuration(OUTPUT, 1)
        self.assertEqual(components["mac"]["datarate"], "1000000")
        self.assertEqual(components["mac"]["delay"], "0.025")
        self.assertEqual(components["phy"]["frequency"], "2347000000")
        self.assertEqual(components["shim0"]["defaultconnectivitymode"], "True")
        self.assertEqual(components["shim0"]["filterfile"], "")
        self.assertEqual(sum(len(values) for values in components.values()), 15)

    def test_partial_wrong_nem_and_invalid_numeric_output_rejected(self):
        for output in ("", "error: disconnected", OUTPUT.replace("nem 1", "nem 2"),
                       OUTPUT.replace("datarate = 1000000", "datarate = nan"),
                       OUTPUT.replace("bitrate = 1000000", "bitrate = -1")):
            with self.subTest(output=output), self.assertRaises(ValueError):
                emane_config.parse_configuration(output, 1)

    def test_model_libraries_follow_selected_generated_nem(self):
        with tempfile.TemporaryDirectory(prefix="emane-config-test-") as directory:
            root = Path(directory)
            (root / "platform.xml").write_text(
                '<platform><nem id="1" definition="nem.xml"/></platform>')
            (root / "nem.xml").write_text(
                '<nem><transport definition="transport.xml"/>'
                '<shim definition="shim.xml"/><mac definition="mac.xml"/><phy/></nem>')
            for kind, library in (("transport", MODELS["transport"]),
                                  ("shim", MODELS["shim0"]), ("mac", MODELS["mac"])):
                (root / f"{kind}.xml").write_text(f'<{kind} library="{library}"/>')
            self.assertEqual(emane_config.model_libraries(root, 1), MODELS)
            with self.assertRaisesRegex(ValueError, "does not contain NEM 2"):
                emane_config.model_libraries(root, 2)

    def test_snapshot_cached_then_refreshed_and_query_is_bounded(self):
        reader = emane_config.EmaneConfiguration()
        with patch.object(emane_config.shutil, "which", return_value="/usr/bin/emanesh"), \
                patch.object(emane_config, "model_libraries", return_value=MODELS), \
                patch.object(emane_config.time, "monotonic", return_value=100) as clock, \
                patch.object(emane_config.subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 0, OUTPUT, "")) as run:
            snapshot = reader.snapshot()
            self.assertTrue(snapshot["available"])
            self.assertEqual(snapshot["observer_node"], "Control_20")
            self.assertEqual(snapshot["nem_id"], 1)
            self.assertEqual(snapshot["models"], MODELS)
            self.assertGreater(snapshot["checked_at_ms"], 0)
            self.assertEqual(snapshot["cache_ttl_ms"], 5000)
            self.assertEqual(reader.snapshot(), snapshot)
            run.assert_called_once_with(
                ["/usr/bin/emanesh", "localhost", "get", "config", "1", "all"],
                capture_output=True, text=True, timeout=3)
            clock.return_value = 106
            run.return_value = subprocess.CompletedProcess(
                [], 0, OUTPUT.replace("datarate = 1000000", "datarate = 4000000"), "")
            self.assertEqual(reader.snapshot()["components"]["mac"]["datarate"], "4000000")
            self.assertEqual(run.call_count, 2)

    def test_concurrent_requests_share_one_query(self):
        reader = emane_config.EmaneConfiguration()
        with patch.object(emane_config.shutil, "which", return_value="emanesh"), \
                patch.object(emane_config, "model_libraries", return_value=MODELS), \
                patch.object(emane_config.subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 0, OUTPUT, "")) as run:
            with ThreadPoolExecutor(max_workers=4) as executor:
                snapshots = list(executor.map(lambda _: reader.snapshot(), range(4)))
            self.assertTrue(all(snapshot["available"] for snapshot in snapshots))
            run.assert_called_once()

    def test_failure_discards_previous_success_and_is_visible_and_cached(self):
        reader = emane_config.EmaneConfiguration()
        with patch.object(emane_config.shutil, "which", return_value="emanesh"), \
                patch.object(emane_config, "model_libraries", return_value=MODELS), \
                patch.object(emane_config.time, "monotonic", return_value=100) as clock, \
                patch.object(emane_config.subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 0, OUTPUT, "")) as run:
            self.assertTrue(reader.snapshot()["available"])
            clock.return_value = 106
            run.side_effect = subprocess.TimeoutExpired("emanesh", 3)
            with self.assertLogs(emane_config.logger, level="WARNING"):
                failed = reader.snapshot()
            self.assertFalse(failed["available"])
            self.assertIn("timed out", failed["unavailable_reason"])
            self.assertEqual(failed["components"], {})
            self.assertEqual(failed["models"], {})
            self.assertEqual(reader.snapshot(), failed)
            self.assertEqual(run.call_count, 2)

    def test_nonzero_query_and_missing_tool_are_explicit(self):
        for executable, completed, reason in (
                (None, None, "not installed"),
                ("emanesh", subprocess.CompletedProcess([], 1, "", "control socket failed"),
                 "control socket failed"),
                ("emanesh", subprocess.CompletedProcess([], 0, "", ""), "Missing runtime")):
            with self.subTest(reason=reason), \
                    patch.object(emane_config.shutil, "which", return_value=executable), \
                    patch.object(emane_config.subprocess, "run", return_value=completed), \
                    self.assertLogs(emane_config.logger, level="WARNING"):
                status = emane_config.EmaneConfiguration().snapshot()
            self.assertFalse(status["available"])
            self.assertIn(reason, status["unavailable_reason"])

    def test_disabled_emane_and_invalid_id_do_not_query(self):
        with patch.object(emane_config.subprocess, "run") as run:
            with patch.dict(emane_config.os.environ, {"EMANE_ENABLED": "0"}):
                status = emane_config.EmaneConfiguration().snapshot()
            self.assertFalse(status["available"])
            self.assertIn("not enabled", status["unavailable_reason"])
            for nem_id in ("", "bad", "0"):
                with patch.dict(emane_config.os.environ, {"EMANE_NEM_ID": nem_id}), \
                        self.assertLogs(emane_config.logger, level="WARNING"):
                    status = emane_config.EmaneConfiguration().snapshot()
                self.assertFalse(status["available"])
                self.assertIn("positive integer", status["unavailable_reason"])
            run.assert_not_called()


class ConfigurationApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_uses_snapshot_without_blocking_event_loop(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        path = Path(__file__).resolve().parents[1] / "server/mesh_bridge.py"
        source = ast.parse(path.read_text())
        build_app = next(node for node in source.body
                         if isinstance(node, ast.FunctionDef) and node.name == "build_app")
        reader = Mock()
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        def snapshot():
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(3):
                raise TimeoutError("Test did not release configuration query")
            return {"available": True, "components": {"mac": {"datarate": "1000000"}}}

        reader.snapshot.side_effect = snapshot
        namespace = {"DdsBridge": object, "Path": Path, "web": web, "asyncio": asyncio,
                     "EmaneConfiguration": lambda: reader}
        exec(compile(ast.Module(body=[build_app], type_ignores=[]), str(path), "exec"),
             namespace)
        bridge = Mock()
        bridge.snapshot.return_value = []
        app = namespace["build_app"](bridge, path.parents[1] / "static")
        async with TestClient(TestServer(app)) as client:
            request = asyncio.create_task(client.get("/api/emane_configuration"))
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                mesh = await asyncio.wait_for(client.get("/api/mesh_status"), timeout=1)
                self.assertEqual(await mesh.json(), [])
            finally:
                release.set()
            response = await asyncio.wait_for(request, timeout=2)
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), {
                "available": True, "components": {"mac": {"datarate": "1000000"}}})


class ConfigurationBrowserTests(unittest.TestCase):
    def test_rendered_units_parameters_layout_and_unavailable_state(self):
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError:
            self.skipTest("Playwright is not installed")
        root = Path(__file__).resolve().parents[1] / "static"
        html = (root / "index.html").read_text()
        styles = html.split("<style>", 1)[1].split("</style>", 1)[0]
        card = html.split('<section class="experiment-card experiment-config"', 1)[1]
        card = '<section class="experiment-card experiment-config"' + card.split("</section>", 1)[0] + "</section>"
        source = (root / "operator_tabs.js").read_text()
        function = source.split("  async function refreshExperimentConfiguration()", 1)[1]
        function = "async function refreshExperimentConfiguration()" + function.split(
            "\n  function rebuildExperimentNodeOptions", 1)[0]
        status = {"available": True, "observer_node": "Control_20", "nem_id": 1,
                  "cache_ttl_ms": 5000, "checked_at_ms": 1700000000000,
                  "components": emane_config.parse_configuration(OUTPUT, 1), "models": MODELS}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--no-sandbox"])
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.set_content(f"<style>{styles}</style>{card}")
                page.evaluate("(source) => { new Function(source); }", source)
                page.add_script_tag(content="""
                    const configurationUrl = "/api/emane_configuration";
                    const configurationStatus = document.getElementById("experiment-config-status");
                    const configurationValues = document.getElementById("experiment-config-values");
                    const configurationDetails = document.getElementById("experiment-config-details");
                    const configurationParameters = document.getElementById("experiment-config-parameters");
                    let configurationRequestVersion = 0;
                    window.fetch = async () => ({ok: true, json: async () => window.testStatus});
                """ + function)
                page.evaluate("(status) => window.testStatus = status", status)
                page.evaluate("() => refreshExperimentConfiguration()")
                for label, value in (("MAC nominal rate", "1 Mbps"), ("Virtual transport rate", "1 Mbps"),
                                     ("RF channel width", "1 MHz"), ("RF frequency", "2.347 GHz"),
                                     ("TX power", "0 dBm"), ("System noise figure", "4 dB"),
                                     ("MAC delay", "25 ms"), ("MAC jitter", "1 ms")):
                    entry = page.locator("#experiment-config-values div").filter(
                        has=page.locator("dt", has_text=label))
                    self.assertEqual(entry.locator("dd").inner_text(), value)
                self.assertIn("Control_20 / NEM 1 only", page.locator("#experiment-config-status").inner_text())
                self.assertIn("RF Pipe (rfpipemaclayer)", page.locator("#experiment-config-values").inner_text())
                self.assertEqual(page.locator("#experiment-config-parameters tr").count(), 15)
                page.locator("#experiment-config-details summary").click()
                for width in (1280, 390):
                    page.set_viewport_size({"width": width, "height": 900})
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                page.evaluate("""() => {
                    window.testStatus = {available: false, unavailable_reason: "control socket timed out"};
                }""")
                page.evaluate("() => refreshExperimentConfiguration()")
                self.assertIn("control socket timed out", page.locator("#experiment-config-status").inner_text())
                self.assertFalse(page.locator("#experiment-config-values").is_visible())
                self.assertFalse(page.locator("#experiment-config-details").is_visible())
                self.assertEqual(page.locator("#experiment-config-parameters tr").count(), 0)
                page.evaluate("""() => {
                    window.fetch = async () => ({ok: false, status: 503});
                }""")
                page.evaluate("() => refreshExperimentConfiguration()")
                self.assertIn("HTTP 503", page.locator("#experiment-config-status").inner_text())
                self.assertEqual(errors, [])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
