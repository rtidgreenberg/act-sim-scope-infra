"""Opt-in browser scenario using the real mesh's DDS mission data.

Run with MISSION_DASHBOARD_URL=http://localhost:8080 against the owned five-platform
demo. Default router runs skip this live-container gate; map and contract tests
remain in the same suite. No browser mission/status responses are simulated.
"""
import os
import time
from pathlib import Path

import pytest


@pytest.fixture
def live_mission_dashboard():
    url = os.environ.get("MISSION_DASHBOARD_URL")
    if not url:
        pytest.skip("Set MISSION_DASHBOARD_URL for the live five-boat browser gate")
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(base_url=url, viewport={"width": 1440, "height": 1000})
        errors, external = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: external.append(request.url)
                if not request.url.startswith(url.rstrip("/") + "/") else None)
        page.goto(url, wait_until="load")
        page.locator('[data-tab="mission"]').click()
        page.wait_for_function("window.ActMissionState?.platforms.length === 5")
        original = {boat["platform"]: boat["squad"] for boat in get(page, "/api/mission_inventory")["platforms"]}
        try:
            yield page, errors, external
        finally:
            response = page.request.post("/api/mission_commands", data={
                "operation": "cancel", "targets": list(original),
            })
            assert response.ok, response.text()
            for platform, squad in original.items():
                response = page.request.post("/api/team_assignment", data={
                    "platform_node": platform, "team_name": squad,
                })
                assert response.ok, response.text()
            wait_for(page, lambda: all(boat["squad"] == original[boat["platform"]]
                                      for boat in get(page, "/api/mission_inventory")["platforms"]))
            response = page.request.post("/api/emane_controller", data={"reset_all": True})
            assert response.ok, response.text()
            assert not get(page, "/api/emane_controller")["impairments"]
            browser.close()


def get(page, path):
    response = page.request.get(path)
    assert response.ok, response.text()
    return response.json()


def wait_for(page, predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        page.wait_for_timeout(250)
    raise AssertionError("Live DDS state did not reach the expected condition")


def point(page, x, y):
    return page.evaluate("""([x,y]) => {
      const svg = document.querySelector('#mission-map svg');
      const p = new DOMPoint(x,y).matrixTransform(svg.getScreenCTM());
      return {x:p.x,y:p.y};
    }""", [x, y])


def click_point(page, x, y):
    p = point(page, x, y)
    page.mouse.click(p["x"], p["y"])


def capture(page, name):
    directory = os.environ.get("MISSION_SCREENSHOT_DIR")
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path / f"{name}.png"))


def select_boats(page, ids):
    current = page.locator('.mission-row[aria-selected="true"]').evaluate_all("rows=>rows.map(row=>row.dataset.platform)")
    for platform in current:
        page.locator(f'.mission-row[data-platform="{platform}"]').click()
    for platform in ids:
        row = page.locator(f'.mission-row[data-platform="{platform}"]')
        row.click()
        assert row.get_attribute("aria-selected") == "true"
        assert row.locator("button").count() == 0


def assign_team(page, ids, label, value):
    select_boats(page, ids)
    if all(boat["squad"] == value for boat in get(page, "/api/mission_inventory")["platforms"]
           if boat["platform"] in ids):
        return
    page.locator(f'.mission-row[data-platform="{ids[0]}"]').click(button="right")
    page.locator("#mission-context").get_by_role("menuitem", name=f"Assign to {label}", exact=True).click()
    wait_for(page, lambda: all(boat["squad"] == value for boat in get(page, "/api/mission_inventory")["platforms"]
                              if boat["platform"] in ids))
    page.wait_for_function("!document.querySelector('#mission-create').disabled")


def set_demo_clock(page):
    if all(boat["time_scale"] == 20 for boat in get(page, "/api/mission_inventory")["platforms"]):
        return
    page.locator("#mission-time-scale").select_option("20")
    page.locator("#mission-apply-time-scale").click()
    wait_for(page, lambda: all(boat["time_scale"] == 20 for boat in get(page, "/api/mission_inventory")["platforms"]))
    page.wait_for_function("!document.querySelector('#mission-time-scale').disabled")


def new_draft(page, team="TEAM_A", target=None):
    button = page.locator(f'#mission-teams [data-team="{team}"]')
    button.click()
    button.click(button="right")
    page.locator("#mission-context").get_by_role("menuitem", name="Set mission", exact=True).click()
    if target:
        page.locator("#mission-context").get_by_role("menuitem", name="Select target", exact=True).click()
        page.locator("#mission-context").get_by_role("menuitem", name="Target 1" if target == "target_1" else "Target 2", exact=True).click()
    else:
        page.locator("#mission-context").get_by_role("menuitem", name="Draw waypoints", exact=True).click()


def validate(page):
    with page.expect_response(lambda response: response.url.endswith("/api/mission_validate")) as result:
        page.locator("#mission-validate").click()
    response = result.value
    assert response.ok, response.text()
    page.wait_for_function("!document.querySelector('#mission-go').disabled")
    return response.json()


def go(page):
    with page.expect_response(lambda response: response.url.endswith("/api/mission_commands") and
                             response.request.method == "POST") as result:
        page.locator("#mission-go").click()
    response = result.value
    assert response.ok, response.text()
    commands = response.json()["commands"]
    ids = {entry["command_id"] for entry in commands}
    assert len(ids) == len(commands)
    wait_for(page, lambda: all(entry["state"] in ("accepted", "accepted_late")
                              for entry in get(page, "/api/mission_commands")["commands"]
                              if entry["command_id"] in ids) and
             len([entry for entry in get(page, "/api/mission_commands")["commands"]
                  if entry["command_id"] in ids]) == len(ids))
    return commands


def test_drag_team_validate_go_and_cross_tab_dds(live_mission_dashboard):
    page, errors, external = live_mission_dashboard
    reset = page.request.post("/api/emane_controller", data={"reset_all": True})
    assert reset.ok, reset.text()
    assert not get(page, "/api/emane_controller")["impairments"]
    set_demo_clock(page)
    assert page.locator("#mission-go").is_hidden()
    capture(page, "01-select-boats")
    if page.locator(".mission-cluster").count():
        page.locator(".mission-cluster").first.click()

    page.locator('.mission-boat[data-platform="Platform_30"] polygon').click()
    page.locator('.mission-boat[data-platform="Platform_31"] polygon').click(modifiers=["Shift"])
    assert sorted(page.evaluate("ActMissionState.selected")) == ["Platform_30", "Platform_31"]
    page.locator('.mission-row[data-platform="Platform_30"]').click(button="right")
    assert page.locator("#mission-context").get_by_role("menuitem", name="Assign to ").count() == 3
    page.locator("#mission-context").get_by_role("menuitem", name="Assign to Alpha", exact=True).click()
    wait_for(page, lambda: all(boat["squad"] == "TEAM_A" for boat in
                              get(page, "/api/mission_inventory")["platforms"]
                              if boat["platform"] in ("Platform_30", "Platform_31")))
    page.wait_for_function("!document.querySelector('#mission-create').disabled")
    page.locator('#mission-teams [data-team="TEAM_A"]').click()
    assert "Step 3" in page.locator("#mission-hint").inner_text()
    capture(page, "02-team-create-mission")
    page.locator("#mission-map-reset").click()
    new_draft(page)
    click_point(page, 600, 100); click_point(page, 620, 220)
    assert page.locator("#mission-validate").is_visible()
    assert page.locator("#mission-go").is_hidden()
    capture(page, "03-edit-waypoints")
    before = {entry["command_id"] for entry in get(page, "/api/mission_commands")["commands"]}
    plan = validate(page)
    assert page.locator("#mission-validate").is_hidden()
    assert page.locator("#mission-go").is_visible()
    assert page.locator(".mission-actions button:visible").all_text_contents() == ["Go"]
    page.locator('#mission-teams [data-team="TEAM_A"]').click(button="right")
    assert page.locator("#mission-context [role=menuitem]").all_text_contents() == ["Go", "Edit waypoints", "Discard draft"]
    assert page.locator("#mission-go").is_visible()
    page.locator("#mission-plan-state").click()
    assert page.locator("#mission-go").bounding_box()["y"] < 1000
    capture(page, "04-validated-go")
    assert {entry["target"] for entry in plan["plans"]} == {"Platform_30", "Platform_31"}
    assert any(len(entry["waypoints"]) > 2 for entry in plan["plans"])
    assert before == {entry["command_id"] for entry in get(page, "/api/mission_commands")["commands"]}
    assert page.evaluate("""plans => plans.every(plan => {
      const boat = ActMissionState.platforms.find(b=>b.platform===plan.target), m=MissionMap;
      let last = m.project([boat.position.latitude,boat.position.longitude]);
      return plan.waypoints.every(wp => {const next=m.project(wp), ok=m.waterSegment(last,next); last=next; return ok;});
    })""", plan["plans"])

    marker = page.locator('[data-waypoint="1"] circle')
    box = marker.bounding_box()
    target = point(page, 625, 230)
    page.mouse.move(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
    page.mouse.down(); page.mouse.move(target["x"], target["y"], steps=10)
    page.wait_for_timeout(1200)  # Refresh must not destroy an in-progress drag.
    page.mouse.up()
    assert page.locator("#mission-go").is_disabled()
    assert page.locator('[data-waypoint="1"]').evaluate(
        "node => [node.transform.baseVal.getItem(0).matrix.e,node.transform.baseVal.getItem(0).matrix.f]") == pytest.approx([625, 230])
    click_point(page, 400, 300)
    page.locator('[data-waypoint="2"] circle').click(button="right")
    page.locator("#mission-context").get_by_role("menuitem", name="Remove waypoint", exact=True).click()
    assert page.locator("[data-waypoint]").count() == 2
    page.locator("#mission-zoom-in").click()
    original_view = page.locator("#mission-map svg").get_attribute("viewBox")
    a = point(page, 400, 500); b = point(page, 440, 530)
    page.mouse.move(a["x"], a["y"]); page.mouse.down()
    page.mouse.move(b["x"], b["y"], steps=5); page.mouse.up()
    assert sorted(page.evaluate("ActMissionState.selected")) == ["Platform_30", "Platform_31"]
    assert page.locator("[data-waypoint]").count() == 2
    assert page.locator("#mission-map svg").get_attribute("viewBox") != original_view
    page.locator("#mission-map-reset").click()
    assert page.locator("#mission-map svg").get_attribute("viewBox") == "0 0 1000 700"
    validate(page)
    graph_positions = page.evaluate("__network.getPositions()")
    commands = go(page)
    assert len(commands) == 2
    ids = {entry["command_id"] for entry in commands}
    wait_for(page, lambda: len([boat for boat in get(page, "/api/mission_inventory")["platforms"]
                               if boat["mission"]["mission_id"] in ids and boat["mission"]["phase"] == "TRANSIT"]) == 2)
    wait_for(page, lambda: page.locator(".mission-route.active").count() == 2)
    assert page.locator(".mission-route.active").evaluate_all(
        "routes=>routes.every(route=>getComputedStyle(route).strokeDasharray !== 'none')")
    capture(page, "05-mission-running")
    wait_for(page, lambda: all(boat["position"]["speed_knots"] == pytest.approx(10) for boat in
                              get(page, "/api/mission_inventory")["platforms"] if boat["platform"] in ("Platform_30", "Platform_31")))

    page.locator('[data-tab="mesh"]').click()
    assert page.evaluate("ActMissionState.team") == "TEAM_A"
    assert page.evaluate("Object.keys(__network.getPositions()).length") == 6
    assert page.evaluate("__network.getPositions()") == graph_positions
    page.locator('[data-tab="delivery"]').click()
    assert page.locator("#delivery-filter-node").input_value() == "mission"
    wait_for(page, lambda: any(row["topic"] == "ControlCommand" and row["recipient"] in ("Platform_30", "Platform_31") and
                              row["sent"] > 0 and row["received"] > 0 for row in get(page, "/api/delivery_stats")["rows"]))
    wait_for(page, lambda: any(row["topic"] == "PlatformCommandAck" and row["source"] in ("Platform_30", "Platform_31") and
                              row["sent"] > 0 and row["received"] > 0 for row in get(page, "/api/delivery_stats")["rows"]))
    page.locator('[data-tab="experiment"]').click()
    assert page.locator("#experiment-source").input_value() in ("Platform_30", "Platform_31")
    page.wait_for_function("document.querySelector('#experiment-source option:checked').textContent.includes('Alpha')")
    assert "Alpha" in page.locator("#experiment-source option:checked").inner_text()
    page.locator("#experiment-kind").select_option("commeffect")
    page.locator("#experiment-latency").fill("50")
    page.locator("#experiment-loss").fill("0")
    page.locator("#experiment-apply").click()
    wait_for(page, lambda: len(get(page, "/api/emane_controller")["impairments"]) > 0)
    observed = wait_for(page, lambda: get(page, "/api/emane_controller").get("observed_network"))
    assert observed["mac_rx_packets"] > 0
    page.locator('[data-tab="mission"]').click()
    new_draft(page); click_point(page, 500, 370); validate(page)
    assert all(entry["operation"] == "replace_route" for entry in go(page))
    for topic in ("ControlCommand", "PlatformCommandAck"):
        wait_for(page, lambda: any(row["topic"] == topic and row.get("percentage_ready") and
                                  row.get("settled_received", 0) > 0 and row["lost"] == 0 and
                                  (row["source"] in ("Platform_30", "Platform_31") or
                                   row["recipient"] in ("Platform_30", "Platform_31"))
                                  for row in get(page, "/api/delivery_stats")["rows"]))
    wait_for(page, lambda: all(peer["presence"] == "PRESENCE_ALIVE" for sample in get(page, "/api/mesh_status")
                              for peer in sample["data"]["peers"]))
    page.locator('[data-tab="experiment"]').click()
    page.locator("#experiment-reset").click()
    wait_for(page, lambda: not get(page, "/api/emane_controller")["impairments"])
    wait_for(page, lambda: all(peer["presence"] == "PRESENCE_ALIVE" for sample in get(page, "/api/mesh_status")
                              for peer in sample["data"]["peers"]))

    page.locator('[data-tab="mission"]').click()
    with page.expect_response(lambda response: response.url.endswith("/api/mission_commands") and
                             response.request.method == "POST"):
        page.locator("#mission-cancel").click()
    wait_for(page, lambda: all(boat["mission"]["phase"] == "CANCELLED" for boat in
                              get(page, "/api/mission_inventory")["platforms"] if boat["platform"] in ("Platform_30", "Platform_31")))
    page.wait_for_function("!document.querySelector('#mission-rtb').disabled")
    page.locator("#mission-rtb").click()
    wait_for(page, lambda: all(boat["mission"]["phase"] == "COMPLETE" for boat in
                              get(page, "/api/mission_inventory")["platforms"] if boat["platform"] in ("Platform_30", "Platform_31")), timeout=150)
    for boat in get(page, "/api/mission_inventory")["platforms"]:
        if boat["platform"] in ("Platform_30", "Platform_31"):
            assert boat["position"]["latitude"] == pytest.approx(boat["home"]["latitude"], abs=0.00002)
            assert boat["position"]["longitude"] == pytest.approx(boat["home"]["longitude"], abs=0.00002)
            assert boat["position"]["speed_knots"] == 0

    select_boats(page, ["Platform_31"])
    page.locator('.mission-row[data-platform="Platform_31"]').click(button="right")
    page.locator("#mission-context").get_by_role("menuitem", name="Assign to Bravo", exact=True).click()
    wait_for(page, lambda: next(boat for boat in get(page, "/api/mission_inventory")["platforms"]
                               if boat["platform"] == "Platform_31")["squad"] == "TEAM_B")
    assert next(boat for boat in get(page, "/api/mission_inventory")["platforms"]
                if boat["platform"] == "Platform_30")["squad"] == "TEAM_A"
    assert not errors
    assert not external
    assert page.locator("#mission-go").is_hidden()
    assert all("20261103" in source for source in page.locator('script[src*="mission"]').evaluate_all(
        "scripts => scripts.map(script=>script.src)"))


def test_multiple_teams_boats_and_missions_at_demo_speed(live_mission_dashboard):
    page, errors, external = live_mission_dashboard
    set_demo_clock(page)
    alpha = ["Platform_32", "Platform_33", "Platform_34"]
    bravo = ["Platform_30", "Platform_31"]
    assign_team(page, bravo, "Bravo", "TEAM_B")
    initial_targets = get(page, "/api/mission_inventory")["scenario_targets"]
    second_revealed = next(target for target in initial_targets if target["id"] == "target_2")["revealed"]
    if not next(target for target in initial_targets if target["id"] == "target_1")["revealed"]:
        page.locator("#mission-target-1").click()
    wait_for(page, lambda: page.locator(".mission-target").count() == (2 if second_revealed else 1))
    if not next(target for target in initial_targets if target["id"] == "target_1")["dispatched"]:
        assert page.locator("#mission-target-2").is_disabled()
    assign_team(page, alpha, "Alpha", "TEAM_A")
    new_draft(page, "TEAM_A", "target_1")
    plan = validate(page)
    assert all(route["target_id"] == "target_1" and route["loop_start"] > 0 for route in plan["plans"])
    assert all(route["loiter_radius_m"] == 1000 for route in plan["plans"])
    capture(page, "06-target-one-alpha-validated")
    alpha_commands = go(page)
    assert {command["target"] for command in alpha_commands} == set(alpha)
    alpha_ids = {command["command_id"] for command in alpha_commands}
    if not second_revealed:
        page.wait_for_function("!document.querySelector('#mission-target-2').disabled")
        page.locator("#mission-target-2").click()
    wait_for(page, lambda: page.locator(".mission-target").count() == 2)
    assign_team(page, bravo, "Bravo", "TEAM_B")
    new_draft(page, "TEAM_B", "target_2")
    page.locator("#mission-loiter-radius").select_option("2000")
    plan = validate(page)
    assert all(route["target_id"] == "target_2" for route in plan["plans"])
    assert all(route["loiter_radius_m"] == 2000 for route in plan["plans"])
    page.locator("#mission-loiter-radius").select_option("1000")
    assert page.locator("#mission-go").is_hidden()
    page.locator("#mission-loiter-radius").select_option("2000")
    validate(page)
    capture(page, "07-target-two-bravo-validated")
    bravo_commands = go(page)
    assert {command["target"] for command in bravo_commands} == set(bravo)
    bravo_ids = {command["command_id"] for command in bravo_commands}
    wait_for(page, lambda: all(boat["mission"]["phase"] == "ORBIT" for boat in get(page, "/api/mission_inventory")["platforms"]), timeout=180)
    before = get(page, "/api/mission_inventory")["platforms"]
    page.wait_for_timeout(6000)
    after = get(page, "/api/mission_inventory")["platforms"]
    for first, last in zip(before, after):
        distance = page.evaluate("""([first,last]) => {
          const m=MissionMap,a=m.project([first.latitude,first.longitude]),b=m.project([last.latitude,last.longitude]);
          return Math.hypot(a.x-b.x,a.y-b.y);
        }""", [first["position"], last["position"]])
        assert distance > 6, f"{last['platform']} did not show accelerated DDS movement"
        assert last["position"]["speed_knots"] == pytest.approx(10)
        assert last["time_scale"] == 20
    capture(page, "08-two-teams-five-orbiting-boats")
    assert page.locator(".mission-route").evaluate_all(
        "routes=>routes.every(route=>getComputedStyle(route).strokeDasharray !== 'none')")
    assert page.evaluate("""() => ActMissionState.platforms.every(boat => {
      const icon=document.querySelector(`.mission-boat[data-platform="${boat.platform}"] polygon`);
      const color=__network.body.data.nodes.get(boat.platform).color.background;
      const normalized=`rgb(${parseInt(color.slice(1,3),16)}, ${parseInt(color.slice(3,5),16)}, ${parseInt(color.slice(5,7),16)})`;
      return icon.style.fill === normalized;
    })""")
    assert page.evaluate("""() => document.querySelectorAll('.mission-boat[visibility="visible"]').length +
      [...document.querySelectorAll('.mission-cluster')].reduce((n,g)=>n+g.dataset.platforms.split(',').length,0) === 5""")
    assert page.locator('.mission-boat[visibility="visible"], .mission-cluster').evaluate_all("""boats => boats.every((boat,index) => {
      const a=boat.getBoundingClientRect();
      return getComputedStyle(boat).visibility === 'visible' && a.width > 0 &&
        boats.slice(index+1).every(other => {const b=other.getBoundingClientRect();
        return a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top;});
    })""")
    icon_width = page.locator(".mission-boat circle").first.evaluate("circle=>circle.getBoundingClientRect().width")
    select_boats(page, alpha + bravo)
    page.locator("#mission-zoom-selection").click()
    assert page.locator(".mission-boat circle").first.evaluate("circle=>circle.getBoundingClientRect().width") == pytest.approx(icon_width, abs=.01)
    assert page.locator(".mission-base text").text_content() == "P20"
    assert page.locator(".mission-base text").get_attribute("x") == "0"
    assert page.locator(".mission-boat text").evaluate_all(
        "labels => labels.every(label=>label.getAttribute('x')==='0' && /^P\\d+$/.test(label.textContent))")
    assert page.locator('.mission-boat[visibility="visible"], .mission-cluster').evaluate_all("""boats => boats.every((boat,index) => {
      const a=boat.getBoundingClientRect();
      return getComputedStyle(boat).visibility === 'visible' && a.width > 0 &&
        boats.slice(index+1).every(other => {const b=other.getBoundingClientRect();
          return a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top;});
    })""")
    assert page.evaluate("""() => ActMissionState.platforms.every(boat => {
      const point=MissionMap.project([boat.position.latitude,boat.position.longitude]);
      const matrix=document.querySelector(`.mission-boat[data-platform="${boat.platform}"]`).transform.baseVal.getItem(0).matrix;
      return Math.abs(matrix.e-point.x)<0.001 && Math.abs(matrix.f-point.y)<0.001;
    })""")
    assert page.locator("#mission-map svg line").count() == 0
    capture(page, "09-zoomed-team-icons")
    if page.locator(".mission-cluster").count():
        cluster = page.locator(".mission-cluster").first
        members = cluster.get_attribute("data-platforms").split(",")
        cluster.click()
        assert all(page.locator(f'.mission-boat[data-platform="{member}"]').get_attribute("visibility") == "visible"
                   for member in members)
        capture(page, "10-true-position-boats")
    page.locator("#mission-map-reset").click()
    page.wait_for_timeout(140000)  # More than one 2 km-radius circuit at the 20x demo rate.
    assert all(boat["mission"]["phase"] == "ORBIT" and boat["mission"]["mission_id"] in alpha_ids | bravo_ids
               for boat in get(page, "/api/mission_inventory")["platforms"])

    select_boats(page, ["Platform_32"])
    page.locator("#mission-cancel").click()
    wait_for(page, lambda: next(boat for boat in get(page, "/api/mission_inventory")["platforms"]
                               if boat["platform"] == "Platform_32")["mission"]["phase"] == "CANCELLED")
    for boat in get(page, "/api/mission_inventory")["platforms"]:
        if boat["platform"] != "Platform_32":
            assert boat["mission"]["phase"] == "ORBIT"
            assert boat["mission"]["mission_id"] in alpha_ids | bravo_ids
    page.wait_for_function("!document.querySelector('#mission-rtb').disabled")
    page.locator("#mission-rtb").click()
    wait_for(page, lambda: next(boat for boat in get(page, "/api/mission_inventory")["platforms"]
                               if boat["platform"] == "Platform_32")["mission"]["phase"] == "COMPLETE", timeout=120)
    new_draft(page, "TEAM_B", "target_2"); validate(page)
    assert all(command["operation"] == "replace_route" for command in go(page))
    for boat in get(page, "/api/mission_inventory")["platforms"]:
        if boat["platform"] in ("Platform_33", "Platform_34"):
            assert boat["mission"]["mission_id"] in alpha_ids
    assert not errors and not external
