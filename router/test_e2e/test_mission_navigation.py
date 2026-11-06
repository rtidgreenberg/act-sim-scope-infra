"""Pure navigation contracts; no DDS participants or subprocesses."""
import math
import sys

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "gui/mesh_dashboard/server"))
sys.path.insert(0, str(REPO_ROOT / "harness_v2/sims"))
from mission_navigation import Navigator, geographic, meters, segment_distance, MAX_WALL_SPEED_MPS
from mission_planning import MIN_BOAT_SEPARATION_M, formation_destination, launch_position_for, orbit_points, project


def location(node, point, now):
    return {"source": node, "source_node": node, "run_id": "navigation-test",
            "latitude": point[0], "longitude": point[1], "sent_at_ns": int(now * 1e9)}


@pytest.mark.parametrize("starts,ends", [
    ([(-400, 0), (400, 0)], [(600, 0), (-600, 0)]),
    ([(0, 500), (-500, 0)], [(0, -500), (500, 0)]),
    ([(-500, 0), (0, 0)], [(1250, 0), (1000, 0)]),
])
def test_crossing_head_on_overtaking_keep_actual_300_foot_buffer(starts, ends):
    origin = meters((32.72, -117.4))
    positions = [geographic((origin[0] + x, origin[1] + y)) for x, y in starts]
    destinations = [geographic((origin[0] + x, origin[1] + y)) for x, y in ends]
    navigators = [Navigator(f"Platform_{30 + index}", "navigation-test") for index in range(2)]
    for nav in navigators:
        nav.expected_peers = {other.node for other in navigators if other is not nav}
    minimum = math.inf
    for tick in range(500):
        now = 1000 + tick * .1
        if tick % 10 == 0:
            for nav in navigators:
                for other, point in zip(navigators, positions):
                    nav.record_location(location(other.node, point, now), now)
        following = [nav.step(point, destination, MAX_WALL_SPEED_MPS * .1, .1, now)[0]
                     for nav, point, destination in zip(navigators, positions, destinations)]
        for nav, start, finish in zip(navigators, positions, following):
            assert nav.planner.water_segment(project(start), project(finish))
            assert math.dist(meters(start), meters(finish)) <= MAX_WALL_SPEED_MPS * .1 + .01
        before = [meters(point) for point in positions]
        after = [meters(point) for point in following]
        relative = lambda points: tuple(a - b for a, b in zip(*points))
        assert segment_distance(relative(before), relative(after), (0, 0)) >= MIN_BOAT_SEPARATION_M
        positions = following
        minimum = min(minimum, math.dist(*[meters(point) for point in positions]))
    assert minimum >= MIN_BOAT_SEPARATION_M
    assert all(math.dist(meters(point), meters(goal)) < 1 for point, goal in zip(positions, destinations))


def test_missing_stale_duplicate_and_invalid_peer_locations(caplog):
    nav = Navigator("Platform_30", "navigation-test")
    nav.expected_peers = {"Platform_31"}
    start, goal = (32.72, -117.4), (32.73, -117.4)
    assert nav.step(start, goal, 10, .1, 1000) == (start, "WAITING_PEERS")
    sample = location("Platform_31", (32.74, -117.4), 1000)
    nav.record_location(sample, 1000)
    nav.record_location(dict(sample, latitude=32.5), 1001)
    assert nav.peers["Platform_31"]["position"] == meters((32.74, -117.4))
    assert nav.step(start, goal, 10, .1, 1003) == (start, "STALE_PEERS")
    nav.record_location(dict(sample, latitude=float("nan")), 1003)
    assert "Invalid shared boat position" in caplog.text
    nav.record_location(location("Platform_31", (32.74, -117.4), 1003), 1003)
    assert nav.step(start, goal, 10, .1, 1003)[0] != start


def test_final_goal_stations_are_separated_and_stable():
    goals = [formation_destination((32.72, -117.4), f"Platform_{number}") for number in range(30, 35)]
    assert all(math.dist(meters(a), meters(b)) >= MIN_BOAT_SEPARATION_M
               for index, a in enumerate(goals) for b in goals[index + 1:])
    assert goals == [formation_destination((32.72, -117.4), f"Platform_{number}") for number in range(30, 35)]


def test_all_supported_launch_positions_start_separated():
    positions = [meters(launch_position_for(f"Platform_{node}")) for node in range(30, 100)]
    assert all(math.dist(a, b) >= MIN_BOAT_SEPARATION_M
               for index, a in enumerate(positions) for b in positions[index + 1:])


def test_five_boats_reach_shared_waypoint_goal_without_collapsing():
    origin = meters((32.72, -117.4))
    navigators = [Navigator(f"Platform_{30 + index}", "navigation-test") for index in range(5)]
    positions = [geographic((origin[0] + 1500 * math.cos(index * math.tau / 5),
                             origin[1] + 1500 * math.sin(index * math.tau / 5))) for index in range(5)]
    goals = [formation_destination(geographic(origin), nav.node) for nav in navigators]
    for nav in navigators:
        nav.expected_peers = {other.node for other in navigators if other is not nav}
    for tick in range(1600):
        now = 1000 + tick * .1
        if tick % 10 == 0:
            for nav in navigators:
                for other, point in zip(navigators, positions):
                    nav.record_location(location(other.node, point, now), now)
        following = [nav.step(point, goal, MAX_WALL_SPEED_MPS * .1, .1, now)[0]
                     for nav, point, goal in zip(navigators, positions, goals)]
        for index in range(5):
            for other in range(index):
                a, b, c, d = map(meters, (positions[index], positions[other],
                                          following[index], following[other]))
                assert segment_distance(tuple(x-y for x, y in zip(a, b)),
                                        tuple(x-y for x, y in zip(c, d)), (0, 0)) >= MIN_BOAT_SEPARATION_M
        positions = following
    assert all(math.dist(meters(point), meters(goal)) < 1 for point, goal in zip(positions, goals))


def test_loiter_navigation_preserves_target_standoff():
    target = {"latitude": 32.72, "longitude": -117.4}
    center = (target["latitude"], target["longitude"])
    nav = Navigator("Platform_30", "navigation-test")
    nav.exclusions = [(center, 1000)]
    ring = orbit_points(target, 0)
    position, index, laps = ring[0], 1, 0
    for tick in range(1500):
        position, _ = nav.step(position, ring[index], MAX_WALL_SPEED_MPS * .1, .1, 1000 + tick * .1)
        assert math.dist(meters(position), meters(center)) >= 1000
        if math.dist(meters(position), meters(ring[index])) < .1:
            index += 1
            if index == len(ring):
                index, laps = 1, laps + 1
    assert laps >= 2
