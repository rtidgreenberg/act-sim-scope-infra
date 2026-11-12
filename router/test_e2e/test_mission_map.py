"""Offline browser geometry and mission-state regressions."""
import copy
import re
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def mission_map():
    playwright = pytest.importorskip("playwright.sync_api")
    static = Path(__file__).resolve().parents[2] / "gui/mesh_dashboard/static"
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()
        page.route("http://mission.test/", lambda route: route.fulfill(
            body="<html></html>", content_type="text/html"))
        page.route("**/san-diego-land.geojson", lambda route: route.fulfill(
            path=str(static / "san-diego-land.geojson"), content_type="application/json"))
        page.goto("http://mission.test/")
        page.add_script_tag(path=str(static / "mission_map.js"))
        page.evaluate("MissionMap.load()")
        yield page
        browser.close()


def test_projection_round_trip(mission_map):
    result = mission_map.evaluate("MissionMap.coordinate(MissionMap.project([32.7, -117.3]))")
    assert result == pytest.approx([32.7, -117.3])


def test_offshore_launches_are_on_water(mission_map):
    assert mission_map.evaluate("""() => [
      [32.6900, -117.3200], [32.7050, -117.3050], [32.7200, -117.3200],
      [32.7350, -117.3050], [32.7500, -117.3200],
    ].every(p => !MissionMap.isLand(MissionMap.project(p)))""")


def test_water_detour_avoids_actual_coastline(mission_map):
    result = mission_map.evaluate("""() => {
      const m = MissionMap, a = {x:600,y:100}, b = {x:620,y:220};
      const path = m.detour(a,b);
      return {crossesLand: !m.waterSegment(a,b), count: path.length,
        valid: path.every((p,i) => m.waterSegment(i ? path[i-1] : a,p)),
        destination: path[path.length-1]};
    }""")
    assert result["crossesLand"]
    assert result["count"] > 1
    assert result["valid"]
    assert result["destination"] == {"x": 620, "y": 220}


def test_land_destination_is_rejected(mission_map):
    result = mission_map.evaluate("""() => {
      const m = MissionMap;
      try { m.detour(m.project([32.7,-117.32]), m.project([32.8,-117.1])); }
      catch (error) { return error.message; }
      return null;
    }""")
    assert result == "Route endpoints must be on water."


@pytest.fixture
def mission_ui(mission_map):
    page = mission_map
    static = Path(__file__).resolve().parents[2] / "gui/mesh_dashboard/static"
    source = (static / "index.html").read_text()
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", source, flags=re.DOTALL)
    mission_asset = re.search(r'<script src="(mission\.js\?v=[^"]+)"', source).group(1)
    state = SimpleNamespace(
        page=page, history_fetches=0, before_history=None,
        fleet={"platforms": [{
            "platform": "Platform_30", "display_name": "Platform 30", "squad": "TEAM_A",
            "available": True, "team_confirmed": True, "health": "PRESENCE_ALIVE",
            "position": {"latitude": 32.69, "longitude": -117.32, "heading_deg": 0, "speed_knots": 10},
            "home": {"latitude": 32.69, "longitude": -117.32}, "time_scale": 1,
            "mission": {"mission_id": "running-goal", "phase": "TRANSIT",
                        "waypoint_index": 0, "waypoints_total": 1},
        }], "scenario_targets": []},
        history={"commands": [{
            "command_id": "running-goal", "target": "Platform_30", "state": "pending",
            "operation": "route", "created_at": 1, "updated_at": 1,
            "waypoints": [[32.74, -117.38]],
        }]},
    )

    def command_history(route):
        state.history_fetches += 1
        if state.before_history:
            state.before_history()
        route.fulfill(json=state.history)

    page.route("**/api/mission_inventory", lambda route: route.fulfill(json=state.fleet))
    page.route("**/api/mission_commands", command_history)
    page.route("**/mission.js?*", lambda route: route.fulfill(
        path=str(static / "mission.js"), content_type="application/javascript"))
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_content(html)
    page.evaluate("""() => {
      document.body.dataset.activeTab = 'mission';
      window.ActMissionStyle = {teamColor: () => '#3aa1e6',
        unassignedColor: '#617c8e', controlColor: '#ffffff'};
    }""")
    for asset in ("mission.js", "mission_map.js", "mesh_graph.js", "operator_tabs.js"):
        page.evaluate("source => { new Function(source); }", (static / asset).read_text())
    page.add_script_tag(url=f"http://mission.test/{mission_asset}")
    page.wait_for_function("window.ActMissionState?.commands.length === 1")
    assert page.locator('script[src*="mission.js?v="]').get_attribute("src").endswith(mission_asset)
    page.locator('#mission-teams [data-team="TEAM_A"]').click()
    yield state
    assert not errors


def push_mission(page, kind, data):
    page.evaluate("""([type, data]) => window.dispatchEvent(new CustomEvent(
      'act-dashboard-message', {detail: {type, data}}))""", [kind, data])


def test_boat_selection_ring_preserves_team_and_offline_colors(mission_ui):
    ui = mission_ui
    page = ui.page
    boat = page.locator('.mission-boat[data-platform="Platform_30"]')
    ring = boat.locator(".mission-boat-selection")
    hull = boat.locator("polygon")
    row = page.locator('.mission-row[data-platform="Platform_30"]')

    def style(locator, property_name):
        return locator.evaluate("(node, key) => getComputedStyle(node)[key]", property_name)

    assert style(ring, "display") == "inline"
    assert style(hull, "fill") == "rgb(58, 161, 230)"
    assert style(hull, "strokeWidth") == "3.5px"
    assert ring.locator("circle").nth(1).get_attribute("stroke-width") == "3"
    row.click()
    assert style(ring, "display") == "none"
    assert style(hull, "strokeWidth") == "2px"
    row.click()
    push_mission(page, "mission_inventory", ui.fleet)
    assert style(ring, "display") == "inline"
    ui.fleet["platforms"][0]["available"] = False
    push_mission(page, "mission_inventory", ui.fleet)
    assert style(ring, "display") == "inline"
    assert style(hull, "fill") == "rgb(89, 101, 111)"
    row.click()
    assert style(ring, "display") == "none"


def test_boat_selection_ring_follows_map_multiselect(mission_ui):
    ui = mission_ui
    second = copy.deepcopy(ui.fleet["platforms"][0])
    second.update(platform="Platform_31", display_name="Platform 31")
    second["position"].update(latitude=32.74, longitude=-117.38)
    ui.fleet["platforms"].append(second)
    push_mission(ui.page, "mission_inventory", ui.fleet)
    first_icon = ui.page.locator('.mission-boat[data-platform="Platform_30"] polygon')
    second_icon = ui.page.locator('.mission-boat[data-platform="Platform_31"] polygon')
    first_icon.click()
    second_icon.click(modifiers=["Shift"])
    assert ui.page.locator(".mission-boat.selected .mission-boat-selection:visible").count() == 2
    second_icon.click(modifiers=["Shift"])
    assert ui.page.locator(".mission-boat.selected .mission-boat-selection:visible").count() == 1
    assert ui.page.evaluate("ActMissionState.selected") == ["Platform_30"]


def test_waypoints_stay_small_through_zoom_resize_and_drag(mission_ui):
    ui = mission_ui
    page = ui.page
    push_mission(page, "mission_command",
                 dict(ui.history["commands"][0], state="accepted", updated_at=2))
    page.locator("#mission-create").click()
    page.get_by_role("menuitem", name="Draw waypoints", exact=True).click()
    point = page.locator("#mission-map svg").evaluate("""svg => {
      const p = new DOMPoint(500, 350).matrixTransform(svg.getScreenCTM());
      return {x: p.x, y: p.y};
    }""")
    page.mouse.click(point["x"], point["y"])
    marker = page.locator('[data-waypoint="0"]')
    circle = marker.locator("circle")

    def assert_compact():
        box = circle.bounding_box()
        assert box["width"] == pytest.approx(7, abs=.1)
        assert box["height"] == pytest.approx(7, abs=.1)
        assert marker.locator("text").evaluate("""node => {
          const matrix = node.getScreenCTM();
          return parseFloat(getComputedStyle(node).fontSize) * Math.hypot(matrix.a, matrix.b);
        }""") == pytest.approx(9, abs=.1)

    assert_compact()
    for _ in range(6):
        page.locator("#mission-zoom-in").click()
        assert_compact()
    push_mission(page, "mission_inventory", ui.fleet)
    assert_compact()
    page.set_viewport_size({"width": 1000, "height": 800})
    page.wait_for_function("""() => {
      const node = document.querySelector('[data-waypoint="0"] circle');
      return Math.abs(node.getBoundingClientRect().width - 6) < .1;
    }""")
    assert_compact()
    box = circle.bounding_box()
    start = {"x": box["x"] + box["width"] / 2 + 8,
             "y": box["y"] + box["height"] / 2}
    end = {"x": start["x"] + 30, "y": start["y"] + 15}
    expected = page.locator("#mission-map svg").evaluate("""(svg, point) => {
      const p = new DOMPoint(point.x, point.y).matrixTransform(svg.getScreenCTM().inverse());
      return [p.x, p.y];
    }""", end)
    page.mouse.move(start["x"], start["y"])
    page.mouse.down()
    page.mouse.move(end["x"], end["y"], steps=5)
    push_mission(page, "mission_inventory", ui.fleet)
    page.mouse.up()
    assert marker.evaluate("""node => {
      const transform = node.transform.baseVal.getItem(0).matrix;
      return [transform.e, transform.f];
    }""") == pytest.approx(expected, abs=.01)
    assert_compact()
    page.locator("#mission-map-reset").click()
    assert_compact()
    marker.locator(".mission-waypoint-hit").click(button="right", position={"x": 18, "y": 10})
    page.get_by_role("menuitem", name="Remove waypoint", exact=True).click()
    assert page.locator("[data-waypoint]").count() == 0


def test_missed_ack_recovers_during_healthy_inventory_pushes(mission_ui):
    ui = mission_ui
    ui.history["commands"][0].update(state="accepted", updated_at=2)
    for _ in range(8):
        push_mission(ui.page, "mission_inventory", ui.fleet)
        ui.page.wait_for_timeout(250)
    ui.page.wait_for_function("ActMissionState.commands[0].state === 'accepted'")
    assert ui.history_fetches >= 2
    assert ui.page.locator("#mission-cancel").is_visible()


def test_rest_history_does_not_overwrite_newer_push_or_inventory(mission_ui):
    ui = mission_ui
    accepted = dict(ui.history["commands"][0], state="accepted", updated_at=3)
    pushed = copy.deepcopy(ui.fleet)
    pushed["platforms"][0]["position"]["latitude"] = 32.70

    def push_newer_state():
        push_mission(ui.page, "mission_command", accepted)
        push_mission(ui.page, "mission_inventory", pushed)

    ui.before_history = push_newer_state
    ui.page.evaluate("window.dispatchEvent(new CustomEvent('act-workspace-change'))")
    ui.page.wait_for_function("ActMissionState.commands[0].state === 'accepted'")
    ui.page.wait_for_timeout(100)
    assert ui.page.evaluate("ActMissionState.platforms[0].position.latitude") == 32.70
    assert ui.page.evaluate("ActMissionState.commands[0].updated_at") == 3


def test_authoritative_empty_history_clears_previous_run_commands(mission_ui):
    ui = mission_ui
    ui.history["commands"] = []
    ui.fleet["platforms"][0]["mission"] = {"mission_id": "", "phase": "IDLE"}
    ui.page.evaluate("window.dispatchEvent(new CustomEvent('act-workspace-change'))")
    ui.page.wait_for_function("ActMissionState.commands.length === 0")
    assert ui.page.evaluate("ActMissionState.platforms[0].mission.phase") == "IDLE"


def test_periodic_history_sync_recovers_entirely_missed_command(mission_ui):
    ui = mission_ui
    accepted = dict(ui.history["commands"][0], state="accepted", updated_at=2)
    push_mission(ui.page, "mission_command", accepted)
    missed = dict(accepted, command_id="missed-goal", operation="replace_route", created_at=3, updated_at=3)
    ui.history["commands"] = [missed]
    ui.fleet["platforms"][0]["mission"]["mission_id"] = "missed-goal"
    for _ in range(25):
        push_mission(ui.page, "mission_inventory", ui.fleet)
        ui.page.wait_for_timeout(250)
    ui.page.wait_for_function("ActMissionState.commands[0].command_id === 'missed-goal'")
    assert ui.history_fetches >= 2
    assert ui.page.locator(".mission-route.active").count() == 1


@pytest.mark.parametrize("archived", [False, True])
def test_failed_replacement_preserves_actual_active_route(mission_ui, archived):
    ui = mission_ui
    accepted = dict(ui.history["commands"][0], state="accepted", updated_at=2)
    push_mission(ui.page, "mission_command", accepted)
    if archived:
        ui.fleet["platforms"][0]["mission"]["command"] = accepted
        push_mission(ui.page, "mission_inventory", ui.fleet)
    assert ui.page.locator(".mission-route.active").count() == 1
    failed = dict(accepted, command_id="failed-replacement", state="failed",
                  operation="replace_route", created_at=3, updated_at=3)
    push_mission(ui.page, "mission_command", failed)
    assert ui.page.evaluate("ActMissionState.platforms[0].mission.mission_id") == "running-goal"
    assert ui.page.locator(".mission-route.active").count() == 1


def test_archived_active_route_survives_frontend_history_eviction(mission_ui):
    ui = mission_ui
    accepted = dict(ui.history["commands"][0], state="accepted", updated_at=2)
    ui.fleet["platforms"][0]["mission"]["command"] = accepted
    push_mission(ui.page, "mission_inventory", ui.fleet)
    ui.page.evaluate("""() => {
      for (let index = 0; index < 200; index++) {
        window.dispatchEvent(new CustomEvent('act-dashboard-message', {detail: {
          type: 'mission_command', data: {command_id: `clock-${index}`, target: 'Platform_30',
            state: 'accepted', operation: 'set_time_scale', created_at: index + 3, updated_at: index + 3},
        }}));
      }
    }""")
    assert ui.page.evaluate("ActMissionState.commands.length") == 200
    assert not ui.page.evaluate("ActMissionState.commands.some(command => command.command_id === 'running-goal')")
    assert ui.page.locator(".mission-route.active").count() == 1
