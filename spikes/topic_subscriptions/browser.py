#!/usr/bin/env python3
"""Drive the graph fixture against the isolated container's real DDS state."""

import json
from pathlib import Path
import sys
import time

from playwright.sync_api import sync_playwright


work = Path(sys.argv[1]).resolve()
here = Path(__file__).resolve().parent
deadline = time.monotonic() + 360
while not (work / "browser_ready").exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("native experiment never reached browser stage")
    if (work / "native_exited").exists():
        raise RuntimeError((work / "console.log").read_text()[-5000:])
    time.sleep(0.1)

requests = []
conflict_next = False


def state():
    return json.loads((work / "browser_state.json").read_text())


def select(request):
    global conflict_next
    request_id = f"browser-{len(requests) + 1}"
    if conflict_next:
        request["revision"] -= 1
        conflict_next = False
    target = work / "commands" / f"{request_id}.json"
    temporary = target.with_suffix(".new")
    temporary.write_text(json.dumps(request))
    temporary.replace(target)
    result_path = work / f"result-{request_id}.json"
    deadline = time.monotonic() + 20
    while not result_path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("native subscription result was not received")
        time.sleep(0.02)
    result = json.loads(result_path.read_text())
    requests.append(dict(request=request, result=result))
    return result


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1000, "height": 800})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.expose_function("nativeState", state)
    page.expose_function("nativeSelect", select)
    page.goto((here / "graph.html").as_uri())
    page.locator("#boat").click()
    page.wait_for_function("document.querySelectorAll('input').length === 3")
    assert "Auto" in page.locator("#mode").inner_text()
    assert "producer matched" in page.locator("#available").inner_text()
    (work / "browser_mission_ORBIT").write_text("ORBIT\n")
    page.wait_for_function("document.querySelector('#mode').textContent.includes('ORBIT')")
    assert page.locator("#PlatformStatus").is_checked() and page.locator("#PlatformStatus").is_disabled()
    page.locator("#PlatformMissionStatus").uncheck()
    page.locator("#ContactReport").check()
    (work / "producer_absent").write_text("absent\n")
    page.wait_for_function("document.querySelector('#available').textContent.includes('producer unavailable')")
    assert page.locator("#ContactReport").is_checked()
    (work / "producer_present").write_text("present\n")
    page.wait_for_function("!document.querySelector('#available').textContent.includes('producer unavailable')")
    assert page.locator("#ContactReport").is_checked()
    page.locator("#apply").click()
    page.wait_for_function("document.querySelector('#mode').textContent.includes('User')")
    assert "ContactReport" in page.locator("#subscribed").inner_text()
    assert state()["applied"]["user_topics"] == ["ContactReport"]
    (work / "browser_mission_CANCELLED").write_text("CANCELLED\n")
    page.wait_for_function("document.querySelector('#mode').textContent.includes('CANCELLED')")
    assert state()["applied"]["topics"] == ["ContactReport"]
    page.screenshot(path=str(work / "graph-user.png"))
    conflict_next = True
    page.locator("#PlatformMissionStatus").check()
    page.locator("#apply").click()
    page.wait_for_function("document.querySelector('#status').textContent.includes('conflicting')")
    assert "PlatformMissionStatus" not in state()["applied"]["topics"]
    page.locator("#ContactReport").uncheck()
    page.locator("#PlatformMissionStatus").uncheck()
    page.locator("#apply").click()
    page.wait_for_function("document.querySelector('#subscribed').textContent.includes('(none')")
    assert state()["applied"]["mode"] == "User"
    page.locator("#auto").click()
    page.wait_for_function("document.querySelector('#mode').textContent.includes('Auto')")
    page.screenshot(path=str(work / "graph-auto.png"))
    assert not errors, errors
    (work / "browser_done").write_text("done\n")
    deadline = time.monotonic() + 20
    while not (work / "degraded_state.json").exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("native rollback-failure demonstration did not complete")
        time.sleep(0.05)
    page.wait_for_function("document.querySelector('#subscribed').textContent.includes('diverged')")
    assert page.locator("#apply").is_disabled() and page.locator("#auto").is_disabled()
    assert not state()["applied"]["confirmed"]
    assert "Native state confirmed" not in page.locator("#status").inner_text()
    page.screenshot(path=str(work / "graph-degraded.png"))
    browser.close()

assert len(requests) == 4 and [item["result"]["accepted"] for item in requests] == [
    True, False, True, True], requests
(work / "browser_report.json").write_text(json.dumps(dict(
    result="PASS", requests=requests, page_errors=errors,
    real_native_state=True, standalone_spike_graph=True,
    unsubmitted_selection_preserved_through_native_churn=True,
    protected_navigation_not_stored_as_user_choice=True,
    native_divergence_displayed_and_editing_blocked=True), indent=2) + "\n")
(work / "browser_fault_done").write_text("done\n")
print("PASS: graph selection, native confirmation, conflict error, empty selection, Auto restoration")
