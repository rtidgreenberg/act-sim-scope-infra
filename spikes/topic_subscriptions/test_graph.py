import copy
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(args=["--no-sandbox"])
        self.addCleanup(self.playwright.stop)
        self.addCleanup(self.browser.close)
        self.state = dict(
            applied=dict(session=1, revision=0, snapshot_sequence=1, mode="Auto",
                         mission="IDLE", topics=[], user_topics=[], protected_topics=[],
                         confirmed=True, error=""),
            inventory=dict(entries=[dict(topic=topic, matched=True) for topic in
                                    ("ContactReport", "PlatformMissionStatus", "PlatformStatus")]))
        self.requests = []

    def open(self):
        page = self.browser.new_page()
        page.expose_function("nativeState", lambda: copy.deepcopy(self.state))
        page.expose_function("nativeSelect", lambda request:
                             self.requests.append(request) or
                             dict(accepted=True, session=1, snapshot_sequence=1))
        page.goto(Path(__file__).with_name("graph.html").as_uri())
        page.locator("#boat").click()
        page.wait_for_function("document.querySelectorAll('input').length === 3")
        return page

    def test_state_disconnect_disables_edits_and_same_snapshot_recovers(self):
        page = self.open()
        page.evaluate("""() => {
          window.savedState = window.nativeState;
          window.nativeState = async () => { throw new Error('disconnected'); };
        }""")
        page.wait_for_function("document.querySelector('#status').textContent.includes('State unavailable')")
        self.assertTrue(page.locator("#apply").is_disabled())
        self.assertTrue(page.locator("#auto").is_disabled())
        page.evaluate("() => { window.nativeState = window.savedState; }")
        page.wait_for_function("!document.querySelector('#apply').disabled")
        self.assertIn("Native state confirmed", page.locator("#status").inner_text())

    def test_degraded_empty_error_never_displays_confirmation(self):
        self.state["applied"]["confirmed"] = False
        page = self.open()
        self.assertTrue(page.locator("#apply").is_disabled())
        self.assertIn("unconfirmed", page.locator("#status").inner_text())
        self.assertNotIn("Native state confirmed", page.locator("#status").inner_text())

    def test_protected_navigation_is_not_added_to_user_choices(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                self.state["applied"].update(mission="ORBIT", topics=["PlatformStatus"],
                    protected_topics=["PlatformStatus"],
                    user_topics=["PlatformStatus"] if explicit else [])
                page = self.open()
                page.locator("#ContactReport").check()
                page.locator("#apply").click()
                page.wait_for_function("!document.querySelector('#apply').disabled")
                self.assertEqual(self.requests[-1]["topics"],
                                 ["ContactReport", "PlatformStatus"] if explicit else ["ContactReport"])
                page.close()


if __name__ == "__main__":
    unittest.main()
