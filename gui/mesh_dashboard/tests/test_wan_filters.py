"""Browser contracts for writer-observed WAN filters carried in RouterHealth."""

import copy
import json
import re
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError as error:
    if error.name not in ("playwright", "playwright.sync_api"):
        raise
    sync_playwright = None


STATIC = Path(__file__).resolve().parents[1] / "static"


@unittest.skipIf(sync_playwright is None, "Playwright is required for browser verification")
class WanFilterDashboardTests(unittest.TestCase):
    def test_team_change_local_writer_errors_and_freshness(self):
        document = (STATIC / "index.html").read_text()
        html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", document)
        revision = re.search(r'<script src="(mesh_graph\.js[^"]+)"', document).group(1)
        reader = {
            "peer_router": "Platform_31/router", "reader_id": "reader-31",
            "has_filter": True, "expression":
                "source_id <> %0 AND (original_topic = 'PlatformData' AND audience.destination_ids[0] = %1)",
            "parameters": ["'Platform_31'", "'TEAM_A'"], "last_error": "",
        }
        writers = [{
            "topic_name": name, "poll_ok": True, "matched_readers": 1,
            "truncated": False, "last_error": "", "reader_filters": [copy.deepcopy(reader)],
        } for name in ("Event", "Status")]
        health = {
            "router": "Platform_30/router", "role": "platform", "overall_state": "ROUTER_OK",
            "heartbeat_seq": 1, "session_id": 1, "routes": [], "peers_seen": [],
            "wan_writer_filters": writers,
        }
        mesh = {
            "observer_node": "Control_20", "observer_router": "Control_20/router",
            "observer_health": dict(health, router="Control_20/router"),
            "observer_health_age_ms": 100,
            "peers": [{"health": health, "presence": "PRESENCE_ALIVE",
                       "last_seen_delta_ms": 0, "health_age_ms": 250}],
        }
        cache_age_ms = 0
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def serve(route):
                url = route.request.url
                if "/mesh_graph.js" in url:
                    route.fulfill(content_type="text/javascript", path=str(STATIC / "mesh_graph.js"))
                elif "/api/" in url:
                    payload = [{"data": mesh, "telemetry_age_ms": cache_age_ms}] \
                        if "mesh_status" in url else {"rows": []}
                    route.fulfill(content_type="application/json", body=json.dumps(payload))
                else:
                    route.fulfill(content_type="text/html", body=html)

            page.route("**/*", serve)
            page.goto("http://filters.test/")
            page.evaluate("""() => {
                window.monotonicNow = 1000;
                performance.now = () => window.monotonicNow;
                window.WebSocket = class { constructor() { window.meshSocket = this; } };
            }""")
            page.add_script_tag(path=str(STATIC / "vendor/vis-network.min.js"))
            page.add_script_tag(path=str(STATIC / "traffic_rates.js"))
            page.add_script_tag(url=f"http://filters.test/{revision}")
            self.assertIn("20261111-readable-wan-filters-v4", revision)
            page.wait_for_function("window.__network.body.data.nodes.get('Platform_30') && window.meshSocket")

            def select(node):
                page.evaluate("id => window.__network.emit('selectNode', {nodes: [id]})", node)
                section = page.locator('#detail [data-section-key$=":wan-filters"]')
                if section.locator("button").first.get_attribute("aria-expanded") == "false":
                    section.locator("button").first.click()
                return section

            def send():
                page.evaluate("""data => window.meshSocket.onmessage({
                    data: JSON.stringify({type: 'mesh_status', data})
                })""", mesh)

            section = select("Platform_30")
            self.assertIn("Event", section.inner_text())
            self.assertIn("Status", section.inner_text())
            self.assertIn("Platform_31", section.inner_text())
            self.assertEqual(section.locator(".wan-filter-reader > strong").first.get_attribute("title"),
                             "Platform_31/router")
            self.assertIn("TEAM_A", section.inner_text())
            self.assertIn("Audience filters:", section.inner_text())
            self.assertNotIn("Parameter values:", section.inner_text())
            self.assertIn("Topic filters", section.inner_text())
            self.assertIn("PlatformData", section.inner_text())
            sql = section.locator(".wan-filter-sql").first
            self.assertFalse(sql.is_visible(), "Raw SQL must be collapsed independently of its parent")
            section.get_by_role("button", name="View SQL filter").first.click()
            self.assertTrue(sql.is_visible())
            self.assertIn("\nAND ", sql.inner_text())
            self.assertIn("%1", section.inner_text())
            self.assertIn("250 ms", section.inner_text())
            cache_age_ms = 7750
            page.evaluate("window.monotonicNow += 7750")
            page.wait_for_function("document.querySelector('#detail').textContent.includes('8000 ms')")
            self.assertNotIn("(stale)", section.inner_text())
            cache_age_ms = 7751
            page.evaluate("window.monotonicNow += 1")
            page.wait_for_function("document.querySelector('#detail').textContent.includes('8001 ms (stale)')")
            cache_age_ms = 0
            page.evaluate("window.monotonicNow = 1000")
            writers[1]["reader_filters"][0]["parameters"] = ["'Platform_31'", "'TEAM_B'"]
            health["heartbeat_seq"] = 2
            send()
            page.wait_for_function("document.querySelector('#detail').textContent.includes('TEAM_B')")
            self.assertEqual(section.locator(".wan-filter-reader").count(), 2)
            self.assertTrue(sql.is_visible(), "Reader expansion must survive health refreshes")
            self.assertFalse(section.locator(".wan-filter-sql").nth(1).is_visible())

            mesh["peers"][0]["health_age_ms"] = 9000
            send()
            page.wait_for_function("document.querySelector('#detail').textContent.includes('(stale)')")
            mesh["peers"][0]["health_age_ms"] = None
            writers[1]["poll_ok"] = False
            writers[1]["truncated"] = True
            writers[1]["last_error"] = "<poll failed>"
            writers[1]["reader_filters"][0]["last_error"] = "reader disappeared"
            send()
            page.wait_for_function("document.querySelector('#detail').textContent.includes('reader disappeared')")
            self.assertIn("unknown", section.inner_text())
            self.assertIn("Incomplete observation (truncated): <poll failed>", section.inner_text())

            writers[0]["reader_filters"][0]["peer_router"] = "<img src=x onerror=alert(1)>"
            writers[0]["reader_filters"][0]["has_filter"] = False
            send()
            page.wait_for_function("document.querySelector('#detail').textContent.includes('Unfiltered reader')")
            self.assertEqual(section.locator("img").count(), 0)
            writers[1]["poll_ok"] = True
            writers[1]["truncated"] = False
            writers[1]["reader_filters"][0]["last_error"] = ""
            writers[1]["reader_filters"][0]["expression"] = "payload._d = 2"
            send()
            page.wait_for_function("document.querySelector('#detail').textContent.includes('no named topic selectors')")
            writers[0]["reader_filters"][0]["peer_router"] = ""
            send()
            page.wait_for_function("document.querySelector('#detail').textContent.includes('Unidentified reader (reader-31)')")
            writers[0]["reader_filters"] = []
            send()
            page.wait_for_function("document.querySelector('#detail').textContent.includes('No matched readers')")
            self.assertEqual(section.locator(".wan-filter-reader").count(), 1)

            local = select("Control_20")
            self.assertIn("Observed WAN filters", local.inner_text())
            self.assertIn("Event", local.inner_text())
            health["wan_writer_filters"] = []
            mesh["observer_health"] = dict(health, router="Control_20/router")
            send()
            page.wait_for_function("document.querySelector('#detail').textContent.includes('No writer filter observations')")
            self.assertEqual(errors, [])
            browser.close()
