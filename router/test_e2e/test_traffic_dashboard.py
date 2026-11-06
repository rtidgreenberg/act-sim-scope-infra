"""Headless chart contracts using real dashboard HTML and JavaScript."""

import json
import re
import time
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError as error:
    if error.name not in ("playwright", "playwright.sync_api"):
        raise
    sync_playwright = None


STATIC = Path(__file__).resolve().parents[2] / "gui/mesh_dashboard/static"


@unittest.skipIf(sync_playwright is None, "Playwright is required for browser verification")
class TrafficDashboardTests(unittest.TestCase):
    def test_default_view_toggle_history_scale_and_layout(self):
        document = (STATIC / "index.html").read_text()
        html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", document)
        chart_src = re.search(r'<script src="(traffic_chart\.js[^"]+)"', document).group(1)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def serve(route):
                if "/api/" in route.request.url:
                    route.fulfill(content_type="application/json", body="[]"
                                  if "traffic_stats" in route.request.url else "null")
                elif "/traffic_chart.js" in route.request.url:
                    route.fulfill(content_type="text/javascript", path=str(STATIC / "traffic_chart.js"))
                else:
                    route.fulfill(content_type="text/html", body=html)

            page.route("**/*", serve)
            page.goto("http://telemetry.test/")
            page.evaluate("""() => {
                window.WebSocket = class {
                    constructor() { window.trafficSocket = this; }
                };
                window.drawn = [];
                const original = CanvasRenderingContext2D.prototype.fillText;
                CanvasRenderingContext2D.prototype.fillText = function(text, ...args) {
                    window.drawn.push(text);
                    return original.call(this, text, ...args);
                };
            }""")
            page.add_script_tag(url=f"http://telemetry.test/{chart_src}")
            self.assertIn("?v=", chart_src)
            self.assertTrue(page.locator(f'script[src="http://telemetry.test/{chart_src}"]').count())
            toggle = page.locator("#toggle-infrastructure-stats")
            self.assertFalse(toggle.is_checked())
            sample = {
                "scope": "network", "domain_id": 200, "schema_version": 2,
                "capture_timestamp": int(time.time() * 1000), "fresh_until_ms": int(time.time() * 1000) + 6000,
                "interval_ms": 1000, "contributors": ["C2", "platform"], "complete": True,
                "capture_complete": True, "classification_complete": True,
                "application_tx_bytes_per_second": 100,
                "infrastructure_tx_bytes_per_second": 100000,
                "discovery_tx_bytes_per_second": 10, "reliability_tx_bytes_per_second": 20,
                "mixed_tx_bytes_per_second": 0, "unknown_tx_bytes_per_second": 0,
            }

            def send(payload, kind="traffic_stats"):
                page.evaluate("(data) => window.trafficSocket.onmessage({data: JSON.stringify(data)})",
                              {"type": kind, "data": payload})

            send(sample)
            sample["capture_timestamp"] += 1
            send(sample)
            self.assertTrue(page.locator('[data-series="application_tx"]').is_visible())
            self.assertFalse(page.locator('[data-series="infrastructure_tx"]').is_visible())
            self.assertFalse(page.locator("#rf-summary").is_visible())
            self.assertIn("shared scale 0.8 kb/s", page.evaluate("window.drawn"))
            page.evaluate("window.drawn = []")
            toggle.check()
            self.assertTrue(page.locator('[data-series="infrastructure_tx"]').is_visible())
            self.assertIn("shared scale 800.0 kb/s", page.evaluate("window.drawn"))
            self.assertTrue(any("heartbeat/probe TX peak" in text for text in page.evaluate("window.drawn")))
            page.evaluate("window.drawn = []")
            toggle.uncheck()
            self.assertIn("shared scale 0.8 kb/s", page.evaluate("window.drawn"))
            self.assertFalse(any("heartbeat/probe TX peak" in text for text in page.evaluate("window.drawn")))
            self.assertTrue(page.locator("#toggle-direct-links").is_checked())
            self.assertTrue(page.locator("#toggle-relayed-links").is_checked())
            sample["aggregated_at_ms"] = sample["capture_timestamp"] + 1
            sample["application_tx_bytes_per_second"] = 10000
            page.evaluate("window.drawn = []")
            send(sample)
            application_label = page.locator('[data-series="application_tx"] .tp-subplot-average')
            self.assertEqual(application_label.inner_text(), "3s avg 0.9 kb/s")
            self.assertIn("shared scale 80.0 kb/s", page.evaluate("window.drawn"))
            send(dict(sample, aggregated_at_ms=sample["aggregated_at_ms"] - 1,
                      application_tx_bytes_per_second=1))
            self.assertEqual(application_label.inner_text(), "3s avg 0.9 kb/s")
            for _ in range(10):
                sample["aggregated_at_ms"] += 1
                send(sample)
            self.assertEqual(application_label.inner_text(), "3s avg 0.9 kb/s")
            sample["capture_timestamp"] += 1000
            sample["aggregated_at_ms"] = sample["capture_timestamp"] + 1
            send(sample)
            self.assertEqual(application_label.inner_text(), "3s avg 40.4 kb/s")
            mac = dict(sample, mac_tx_bytes_per_second=1000, mac_rx_bytes_per_second=0,
                       mac_tx_packets_per_second=10, mac_tx_drops_per_second=0,
                       mac_rx_drops_per_second=0)
            send(mac, "emane_stats")
            self.assertIn("TX 8.0 kb/s", page.locator("#rf-summary").text_content())
            send(dict(mac, aggregated_at_ms=mac["aggregated_at_ms"] - 1,
                      mac_tx_bytes_per_second=10000), "emane_stats")
            self.assertIn("TX 8.0 kb/s", page.locator("#rf-summary").text_content())
            page.evaluate("""() => {
                const detail = document.querySelector('#detail');
                detail.style.display = 'block';
                detail.innerHTML = '<pre>' + 'diagnostics\\n'.repeat(300) + '</pre>';
            }""")
            metrics = page.evaluate("""() => ({
                graphBottom: document.querySelector('#graph').getBoundingClientRect().bottom,
                panelTop: document.querySelector('#traffic-panel').getBoundingClientRect().top,
                detailBottom: document.querySelector('#detail').getBoundingClientRect().bottom,
                detailScroll: document.querySelector('#detail').scrollHeight,
                detailHeight: document.querySelector('#detail').clientHeight
            })""")
            self.assertLessEqual(abs(metrics["graphBottom"] - metrics["panelTop"]), 1)
            self.assertLess(metrics["detailBottom"], metrics["panelTop"])
            self.assertGreater(metrics["detailScroll"], metrics["detailHeight"])
            sample["aggregated_at_ms"] += 1
            sample["stale"] = True
            send(sample)
            self.assertIn("unavailable", page.locator('[data-series="application_tx"]').inner_text())
            self.assertEqual(errors, [])
            browser.close()

    def test_diagnostics_polling_respects_closed_detail_and_expires_failed_or_pending_refresh(self):
        document = (STATIC / "index.html").read_text()
        html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", document)
        graph_src = re.search(r'<script src="(mesh_graph\.js[^"]+)"', document).group(1)
        now = int(time.time() * 1000)
        router = "Platform_30/router"
        health = {
            "router": router, "role": "platform", "config_hash": "current",
            "state_revision": 5, "overall_state": "ROUTER_OK", "n_routes": 1,
            "routes": [{"route_name": "platform_debug_status", "state": "ROUTE_ENABLED",
                        "discovery_state": "DISCOVERY_READY", "samples_forwarded": 1}],
            "peers_seen": [], "team_partition": [],
        }
        mesh = {"observer_node": "Control_20", "observer_router": "control",
                "peers": [{"health": health, "presence": "PRESENCE_ALIVE", "last_seen_delta_ms": 0}]}
        diagnostics = {"rows": [{
            "router": router, "state": "live", "received_at": now,
            "data": dict(health, send_timestamp=now * 1000000, snapshot_seq=1),
        }]}
        fail_diagnostics = False
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def serve(route):
                url = route.request.url
                if "/mesh_graph.js" in url:
                    route.fulfill(content_type="text/javascript", path=str(STATIC / "mesh_graph.js"))
                elif "/api/" not in url:
                    route.fulfill(content_type="text/html", body=html)
                elif "router_diagnostics" in url and fail_diagnostics:
                    route.fulfill(status=503, body="unavailable")
                else:
                    payload = ([{"data": mesh}] if "mesh_status" in url else diagnostics
                               if "router_diagnostics" in url else {"rows": []}
                               if "link_stats" in url else {})
                    route.fulfill(content_type="application/json", body=json.dumps(payload))

            page.route("**/*", serve)
            page.goto("http://telemetry.test/")
            page.evaluate("""(now) => {
                window.clockNow = now;
                Date.now = () => window.clockNow;
                window.WebSocket = class { constructor() {} };
                const original = window.setInterval;
                window.setInterval = (fn, delay) => {
                    if (delay === 2000) { window.refreshDiagnostics = fn; return 0; }
                    return original(fn, delay);
                };
            }""", now)
            page.add_script_tag(path=str(STATIC / "vendor/vis-network.min.js"))
            with page.expect_response("**/api/router_diagnostics"):
                page.add_script_tag(url=f"http://telemetry.test/{graph_src}")
            self.assertTrue(page.locator(f'script[src="http://telemetry.test/{graph_src}"]').count())
            page.wait_for_function("window.__network.body.data.nodes.get('Platform_30')")

            def open_platform():
                page.evaluate("""() => {
                    window.__network.selectNodes(['Platform_30']);
                    window.__network.emit('selectNode', {nodes: ['Platform_30']});
                }""")

            section = page.locator('#detail [data-section-key$=":router-diagnostics"]')
            open_platform()
            page.wait_for_function(
                "document.querySelector('#detail').textContent.includes('Full router diagnostics')")
            self.assertEqual(section.count(), 1)
            page.locator("#detail .detail-close").click()
            self.assertFalse(page.locator("#detail").is_visible())
            with page.expect_response("**/api/router_diagnostics"):
                page.evaluate("window.refreshDiagnostics()")
            page.wait_for_timeout(100)
            self.assertFalse(page.locator("#detail").is_visible())
            self.assertEqual(page.evaluate("window.__network.getSelectedNodes()"), ["Platform_30"])

            open_platform()
            fail_diagnostics = True
            page.evaluate("window.refreshDiagnostics()")
            page.wait_for_function(
                "document.querySelector('#detail').textContent.includes('unavailable')")
            self.assertEqual(section.count(), 0)
            fail_diagnostics = False
            page.evaluate("window.refreshDiagnostics()")
            page.wait_for_function(
                "document.querySelector('#detail').textContent.includes('Full router diagnostics')")
            page.evaluate("""() => {
                const original = window.fetch;
                window.pendingDiagnostics = [];
                window.fetch = (...args) => String(args[0]).includes('/api/router_diagnostics')
                    ? new Promise(resolve => window.pendingDiagnostics.push(resolve))
                    : original(...args);
                window.clockNow += 6001;
                window.refreshDiagnostics();
            }""")
            self.assertIn("stale", page.locator("#detail").text_content())
            self.assertEqual(section.count(), 0)
            page.evaluate("""() => {
                window.refreshDiagnostics();
                window.pendingDiagnostics[1](new Response('unavailable', {status: 503}));
            }""")
            page.wait_for_function(
                "document.querySelector('#detail').textContent.includes('unavailable')")
            page.evaluate("""(snapshot) => {
                snapshot.rows[0].received_at = window.clockNow;
                snapshot.rows[0].data.send_timestamp = window.clockNow * 1000000;
                window.pendingDiagnostics[0](new Response(JSON.stringify(snapshot), {
                    headers: {'Content-Type': 'application/json'}
                }));
            }""", diagnostics)
            page.wait_for_timeout(100)
            self.assertIn("unavailable", page.locator("#detail").text_content())
            self.assertEqual(section.count(), 0)
            self.assertEqual(errors, [])
            browser.close()


if __name__ == "__main__":
    unittest.main()
