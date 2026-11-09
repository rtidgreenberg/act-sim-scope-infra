"""Opt-in browser scenario using the real mesh's DDS mission data.

Run with MISSION_DASHBOARD_URL=http://localhost:8080 against an owned mesh with at
least five platforms (MISSION_PLATFORM_COUNT defaults to 5). This mutates the live
demo. Default router runs skip this live-container gate; map and contract tests
remain in the same suite. No browser mission/status responses are simulated.
Set MISSION_WAN_CONTRACT=two_topic_union for consolidated physical WAN row checks.
"""
import os
import math
import time
from pathlib import Path

import pytest


@pytest.fixture
def live_mission_dashboard():
    url = os.environ.get("MISSION_DASHBOARD_URL")
    if not url:
        pytest.skip("Set MISSION_DASHBOARD_URL for the live mission browser gate")
    platform_count = int(os.environ.get("MISSION_PLATFORM_COUNT", "5"))
    assert 5 <= platform_count <= 6, "The mission workflow supports five or six platforms"
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
        page.wait_for_function("count => window.ActMissionState?.platforms.length === count",
                               arg=platform_count)
        inventory = wait_for(page, lambda: (
            boats if len(boats := get(page, "/api/mission_inventory")["platforms"]) == platform_count
            and all(boat["available"] and boat["team_confirmed"] for boat in boats) else None))
        original = {boat["platform"]: boat["squad"] for boat in inventory}
        original_clock = {boat["platform"]: boat["time_scale"] for boat in inventory}
        reset = page.request.post("/api/emane_controller", data={"reset_all": True})
        assert reset.ok, reset.text()
        assert not get(page, "/api/emane_controller")["impairments"]
        try:
            yield page, errors, external
        finally:
            try:
                response = page.request.post("/api/emane_controller", data={"reset_all": True})
                assert response.ok, response.text()
                assert not get(page, "/api/emane_controller")["impairments"]
                wait_for(page, lambda: all(
                    boat["available"] for boat in get(page, "/api/mission_inventory")["platforms"]))
                command(page, {
                    "operation": "cancel", "targets": list(original),
                })
                for platform, squad in original.items():
                    response = page.request.post("/api/team_assignment", data={
                        "platform_node": platform, "team_name": squad,
                    })
                    assert response.ok, response.text()
                wait_for(page, lambda: all(boat["squad"] == original[boat["platform"]]
                                          for boat in get(page, "/api/mission_inventory")["platforms"]))
                for scale in set(original_clock.values()):
                    command(page, {
                        "operation": "set_time_scale", "time_scale": scale,
                        "targets": [node for node, value in original_clock.items() if value == scale],
                    })
                wait_for(page, lambda: all(boat["time_scale"] == original_clock[boat["platform"]]
                                          for boat in get(page, "/api/mission_inventory")["platforms"]))
            finally:
                browser.close()


def get(page, path):
    response = page.request.get(path)
    assert response.ok, response.text()
    return response.json()

def wan_topic(original):
    contract = os.environ.get("MISSION_WAN_CONTRACT", "legacy")
    if contract == "legacy":
        return original
    if contract != "two_topic_union":
        raise ValueError(f"Unknown MISSION_WAN_CONTRACT: {contract}")
    return {"ControlCommand": "Event", "PlatformCommandAck": "Event",
            "PlatformInitStatus": "Status"}[original]


def wait_for(page, predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        page.wait_for_timeout(250)
    raise AssertionError("Live DDS state did not reach the expected condition")

def command(page, body):
    response = page.request.post("/api/mission_commands", data=body)
    assert response.ok, response.text()
    result = response.json()
    assert not result.get("errors"), result
    ids = {entry["command_id"] for entry in result["commands"]}
    assert ids and len(ids) == len(result["commands"]), result
    def accepted():
        entries = [entry for entry in get(page, "/api/mission_commands")["commands"]
                   if entry["command_id"] in ids]
        return len(entries) == len(ids) and all(
            entry["state"] in ("accepted", "accepted_late") for entry in entries)
    wait_for(page, accepted)
    return result

def geographic_distance(first, last):
    lat1, lat2 = math.radians(first["latitude"]), math.radians(last["latitude"])
    delta_lon = math.radians(last["longitude"] - first["longitude"])
    haversine = math.sin((lat2 - lat1) / 2) ** 2 + (
        math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2)
    return 2 * 6371000 * math.asin(min(1, math.sqrt(haversine)))


def verify_fleet_motion(page, timeout=60):
    previous = {boat["platform"]: boat for boat in get(page, "/api/mission_inventory")["platforms"]}
    travelled = dict.fromkeys(previous, 0.0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        page.wait_for_timeout(500)
        current = {boat["platform"]: boat for boat in get(page, "/api/mission_inventory")["platforms"]}
        assert current.keys() == previous.keys()
        for node, boat in current.items():
            assert boat["available"], boat
            assert boat["time_scale"] == 20
            # Reported physical speed can decrease during onboard separation holds.
            assert 0 <= boat["position"]["speed_knots"] <= 10.02
            travelled[node] += geographic_distance(previous[node]["position"], boat["position"])
        previous = current
        if all(distance >= 100 for distance in travelled.values()):
            return
    raise AssertionError(f"Fleet did not sustain 100 m of DDS-reported movement: {travelled}")


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
    if value:
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


def test_wire_blackout_and_init_terminal_recovery(live_mission_dashboard):
    page, errors, external = live_mission_dashboard
    node = "Platform_30"
    command(page, {"operation": "set_time_scale", "targets": [node], "time_scale": 1})
    route = command(page, {"operation": "route", "targets": [node],
                           "waypoints": [[32.74, -117.38], [32.72, -117.40]]})
    mission_id = route["commands"][0]["command_id"]
    raw_path = f"/api/platform_status?platform={node}"
    detailed = wait_for(page, lambda: (
        status if (status := get(page, raw_path)["mission"].get("PlatformMissionStatus", {}))
        .get("mission_phase") == "TRANSIT" and status.get("mission_id") == mission_id else None))
    page.locator('[data-tab="experiment"]').click()
    wait_for(page, lambda: not page.locator("#experiment-apply").is_disabled())
    page.locator("#experiment-source").select_option(node)
    page.locator("#experiment-destination").select_option("Control_20")
    page.locator("#experiment-direction").select_option("forward")
    page.locator("#experiment-kind").select_option("commeffect")
    page.locator("#experiment-latency").fill("0")
    page.locator("#experiment-loss").fill("100")
    try:
        page.locator("#experiment-apply").click()
        wait_for(page, lambda: bool(get(page, "/api/emane_controller")["impairments"]))
        # Reverse-path cancellation arrives, but its ack and terminal reports cannot return.
        response = page.request.post("/api/mission_commands", data={
            "operation": "cancel", "targets": [node],
        })
        assert response.ok, response.text()
        assert not response.json().get("errors")
        page.wait_for_timeout(20_000)
        snapshot = get(page, "/api/delivery_stats")
        assert snapshot["scope"] == "wan_wire"
        assert snapshot["invalid_records"] == 0
        assert snapshot["capture"]["complete"]
        rows = [row for row in snapshot["rows"]
                if row["source"] == node and row["recipient"] == "Control_20"]
        for reliability in ("reliable", "best_effort"):
            assert any(row["reliability"] == reliability and row["lost"] > 0
                       for row in rows), rows
        assert any(row["source"] == "Platform_31" and row["recipient"] == "Control_20"
                   and row["topic"] == wan_topic("PlatformInitStatus") and row["percentage_ready"]
                   and row["lost"] == 0 for row in snapshot["rows"])
        assert get(page, raw_path)["mission"]["PlatformMissionStatus"]["mission_phase"] == "TRANSIT"
        page.locator("#experiment-reset").click()
        wait_for(page, lambda: not get(page, "/api/emane_controller")["impairments"])
    finally:
        response = page.request.post("/api/emane_controller", data={"reset_all": True})
        assert response.ok, response.text()
    recovered = wait_for(page, lambda: (
        status if (status := get(page, raw_path))["init"]["PlatformInitStatus"]
        .get("mission_phase") == "CANCELLED" and
        status["init"]["PlatformInitStatus"].get("mission_id") == mission_id else None))
    # No detailed terminal report arrived: reconciliation must use the compact Init summary.
    assert recovered["mission"]["PlatformMissionStatus"]["mission_phase"] == "TRANSIT"
    assert recovered["init"]["PlatformInitStatus"]["mission_id"] == detailed["mission_id"]
    wait_for(page, lambda: any(
        boat["platform"] == node and boat["mission"]["phase"] == "CANCELLED"
        for boat in get(page, "/api/mission_inventory")["platforms"]))
    wait_for(page, lambda: all(peer["presence"] == "PRESENCE_ALIVE"
                              for sample in get(page, "/api/mesh_status")
                              for peer in sample["data"]["peers"]))
    page.locator('[data-tab="delivery"]').click()
    wait_for(page, lambda: page.locator(".delivery-reliability-group").count() >= 2)
    assert not errors
    assert not external


def test_automatic_reporting_and_independent_target_toggles(live_mission_dashboard):
    page, errors, external = live_mission_dashboard
    node = "Platform_30"

    def mode(platform):
        peers = [peer for row in get(page, "/api/mesh_status")
                 for peer in row["data"]["peers"]
                 if peer["health"]["router"].split("/")[0] == platform]
        routes = {route["route_name"]: route["state"] for route in peers[0]["health"]["routes"]}
        return ("debug" if routes["platform_debug_status"] != "ROUTE_DISABLED" else
                "mission" if routes["platform_detail_status"] != "ROUTE_DISABLED" else "init")

    def set_mode(value):
        response = page.request.post("/api/status_resolution", data={"platform_node": node, "mode": value})
        assert response.ok, response.text()

    def phase(value):
        return any(boat["platform"] == node and boat["mission"]["phase"] == value
                   for boat in get(page, "/api/mission_inventory")["platforms"])

    platforms = [boat["platform"] for boat in get(page, "/api/mission_inventory")["platforms"]]
    wait_for(page, lambda: all(mode(platform) == "init" for platform in platforms))
    assert not get(page, "/api/mission_inventory")["scenario_targets"][0]["revealed"]
    assert not get(page, "/api/mission_inventory")["scenario_targets"][1]["revealed"]
    assert page.locator(".mission-target").count() == 0
    for target in ("target_2", "target_1"):
        button = page.locator("#mission-target-" + target[-1])
        for visible in (True, False, True, False):
            button.click()
            wait_for(page, lambda: next(t for t in get(page, "/api/mission_inventory")["scenario_targets"]
                                       if t["id"] == target)["revealed"] == visible)
            page.wait_for_function(
                "([id, visible]) => Boolean(document.querySelector(`.mission-target[data-target='${id}']`)) === visible",
                arg=[target, visible])
            assert button.get_attribute("aria-pressed") == str(visible).lower()

    assign_team(page, [node], "Alpha", "TEAM_A")
    command(page, {"operation": "route", "targets": [node], "waypoints": [[32.74, -117.38]]})
    wait_for(page, lambda: mode(node) == "mission")
    assert all(mode(platform) == "init" for platform in platforms if platform != node)
    command(page, {"operation": "cancel", "targets": [node]})
    wait_for(page, lambda: phase("CANCELLED"))
    wait_for(page, lambda: mode(node) == "init")
    assert phase("CANCELLED"), "Final mission status must survive return to Init"
    command(page, {"operation": "return_to_base", "targets": [node]})
    wait_for(page, lambda: phase("COMPLETE"))
    wait_for(page, lambda: mode(node) == "init")

    set_mode("debug")
    wait_for(page, lambda: mode(node) == "debug")
    command(page, {"operation": "route", "targets": [node], "waypoints": [[32.74, -117.38]]})
    command(page, {"operation": "cancel", "targets": [node]})
    wait_for(page, lambda: phase("CANCELLED"))
    page.wait_for_timeout(4500)
    assert mode(node) == "debug"
    set_mode("init")
    wait_for(page, lambda: mode(node) == "init")
    assert not errors and not external


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

    page.locator('.mission-boat[data-platform="Platform_30"]').click()
    page.locator('.mission-boat[data-platform="Platform_31"]').click(modifiers=["Shift"])
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
    wait_for(page, lambda: all(boat["position"]["speed_knots"] == pytest.approx(10, abs=.02) for boat in
                              get(page, "/api/mission_inventory")["platforms"] if boat["platform"] in ("Platform_30", "Platform_31")))

    page.locator('[data-tab="mesh"]').click()
    assert page.evaluate("ActMissionState.team") == "TEAM_A"
    assert page.evaluate("Object.keys(__network.getPositions()).length") == len(
        get(page, "/api/mission_inventory")["platforms"]) + 1
    assert page.evaluate("__network.getPositions()") == graph_positions
    page.locator('[data-tab="delivery"]').click()
    assert page.locator("#delivery-filter-node").input_value() == "mission"
    wait_for(page, lambda: any(row["topic"] == wan_topic("ControlCommand") and row["source"] == "Control_20" and
                              row["recipient"] in ("Platform_30", "Platform_31") and
                              row["sent"] > 0 and row["received"] > 0 for row in get(page, "/api/delivery_stats")["rows"]))
    wait_for(page, lambda: any(row["topic"] == wan_topic("PlatformCommandAck") and row["recipient"] == "Control_20" and
                              row["source"] in ("Platform_30", "Platform_31") and
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
    for topic, sources, recipients in (
            ("ControlCommand", ("Control_20",), ("Platform_30", "Platform_31")),
            ("PlatformCommandAck", ("Platform_30", "Platform_31"), ("Control_20",))):
        wait_for(page, lambda: any(row["topic"] == wan_topic(topic) and row.get("percentage_ready") and
                                  row.get("settled_received", 0) > 0 and row["lost"] == 0 and
                                  row["source"] in sources and row["recipient"] in recipients
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
    document = (Path(__file__).resolve().parents[2] / "gui/mesh_dashboard/static/index.html").read_text()
    for source in page.locator('script[src*="mission"]').evaluate_all(
            "scripts => scripts.map(script=>script.getAttribute('src'))"):
        assert f'src="{source}"' in document
        assert "?v=" in source


def test_multiple_teams_boats_and_missions_at_demo_speed(live_mission_dashboard):
    page, errors, external = live_mission_dashboard
    set_demo_clock(page)
    alpha = [boat["platform"] for boat in get(page, "/api/mission_inventory")["platforms"]
             if boat["platform"] not in ("Platform_30", "Platform_31")]
    bravo = ["Platform_30", "Platform_31"]
    assign_team(page, bravo, "Bravo", "TEAM_B")
    initial_targets = get(page, "/api/mission_inventory")["scenario_targets"]
    second_revealed = next(target for target in initial_targets if target["id"] == "target_2")["revealed"]
    if not next(target for target in initial_targets if target["id"] == "target_1")["revealed"]:
        page.locator("#mission-target-1").click()
    wait_for(page, lambda: page.locator(".mission-target").count() == (2 if second_revealed else 1))
    if not next(target for target in initial_targets if target["id"] == "target_1")["dispatched"]:
        assert page.locator("#mission-target-2").is_enabled()
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
    verify_fleet_motion(page)
    capture(page, "08-two-teams-orbiting-fleet")
    assert page.locator(".mission-route").evaluate_all(
        "routes=>routes.every(route=>getComputedStyle(route).strokeDasharray !== 'none')")
    assert page.evaluate("""() => ActMissionState.platforms.every(boat => {
      const icon=document.querySelector(`.mission-boat[data-platform="${boat.platform}"] polygon`);
      const color=__network.body.data.nodes.get(boat.platform).color.background;
      const normalized=`rgb(${parseInt(color.slice(1,3),16)}, ${parseInt(color.slice(3,5),16)}, ${parseInt(color.slice(5,7),16)})`;
      return icon.style.fill === normalized;
    })""")
    assert page.evaluate("""() => document.querySelectorAll('.mission-boat[visibility="visible"]').length +
      [...document.querySelectorAll('.mission-cluster')].reduce((n,g)=>n+g.dataset.platforms.split(',').length,0) === ActMissionState.platforms.length""")
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
    # Sample actual fixes through more than one 2 km-radius circuit at 20x.
    deadline = time.monotonic() + 140
    while time.monotonic() < deadline:
        fleet = get(page, "/api/mission_inventory")
        assert all(boat["mission"]["phase"] == "ORBIT" for boat in fleet["platforms"])
        assert page.evaluate("""fleet => {
          const m = MissionMap;
          const points = fleet.platforms.map(boat =>
            m.project([boat.position.latitude, boat.position.longitude]));
          return points.every(point => !m.isLand(point));
        }""", fleet)
        page.wait_for_timeout(1000)
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
        if boat["platform"] in set(alpha) - {"Platform_32"}:
            assert boat["mission"]["mission_id"] in alpha_ids
    assert not errors and not external


def test_live_team_goal_inheritance_and_reassignment(live_mission_dashboard):
    page, errors, external = live_mission_dashboard
    set_demo_clock(page)
    alpha = ["Platform_30", "Platform_31"]
    bravo = ["Platform_32", "Platform_33"]
    reserve = [boat["platform"] for boat in get(page, "/api/mission_inventory")["platforms"]
               if boat["platform"] not in alpha + bravo]
    assign_team(page, alpha, "Alpha", "TEAM_A")
    assign_team(page, bravo, "Bravo", "TEAM_B")
    assign_team(page, reserve, "Reserve", "TEAM_C")

    def fleet():
        return {boat["platform"]: boat for boat in get(page, "/api/mission_inventory")["platforms"]}

    def dispatch(team, node, destination):
        response = page.request.post("/api/mission_validate", data={
            "team": team, "targets": [node], "waypoints": [destination],
        })
        assert response.ok, response.text()
        plan = response.json()
        result = command(page, {"operation": "route", "plan_id": plan["plan_id"]})
        ids = {entry["target"]: entry["command_id"] for entry in result["commands"]}
        wait_for(page, lambda: all(fleet()[node]["mission"]["mission_id"] == cid
                                  for node, cid in ids.items()))
        return ids

    alpha_ids = dispatch("TEAM_A", alpha[0], [32.76, -117.44])
    bravo_ids = dispatch("TEAM_B", bravo[0], [32.67, -117.45])
    assert set(alpha_ids) == set(alpha)
    assert set(bravo_ids) == set(bravo)
    original = alpha_ids | bravo_ids
    joiner = reserve[0]
    assign_team(page, [joiner], "Alpha", "TEAM_A")
    alpha_version = get(page, "/api/mission_inventory")["team_missions"]["TEAM_A"]["version"]
    wait_for(page, lambda: fleet()[joiner]["mission"]["command"] and
             fleet()[joiner]["mission"]["command"].get("team_version") == alpha_version)
    inherited = fleet()[joiner]["mission"]["command"]
    assert inherited["team"] == "TEAM_A"
    assert inherited["state"] in ("accepted", "accepted_late")
    assert all(fleet()[node]["mission"]["mission_id"] == cid for node, cid in original.items())
    old_id = fleet()[joiner]["mission"]["mission_id"]
    bravo_version = get(page, "/api/mission_inventory")["team_missions"]["TEAM_B"]["version"]
    assign_team(page, [joiner], "Bravo", "TEAM_B")
    wait_for(page, lambda: fleet()[joiner]["mission"]["mission_id"] != old_id and
             fleet()[joiner]["mission"]["command"] and
             fleet()[joiner]["mission"]["command"].get("team_version") == bravo_version)
    assert all(fleet()[node]["mission"]["mission_id"] == cid for node, cid in original.items())
    assign_team(page, [joiner], "Unassigned", "")
    wait_for(page, lambda: fleet()[joiner]["mission"]["phase"] == "CANCELLED")
    page.wait_for_timeout(2000)
    assert fleet()[joiner]["mission"]["phase"] == "CANCELLED"
    assert all(fleet()[node]["mission"]["mission_id"] == cid for node, cid in original.items())
    assert not errors and not external
