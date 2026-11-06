"""Demo water routing using the bundled coastline and MissionMap's 101x71 grid."""
import heapq
import json
import math
from functools import lru_cache
from pathlib import Path


BOUNDS = (32.5, 32.95, -117.65, -117.05)
MAX_WAYPOINTS = 64
SQUADS = [
    {"value": "TEAM_A", "label": "Alpha"},
    {"value": "TEAM_B", "label": "Bravo"},
    {"value": "TEAM_C", "label": "Reserve"},
    {"value": "", "label": "Unassigned"},
]
SCENARIO_TARGETS = [
    {"id": "target_1", "label": "Target 1", "latitude": 32.755, "longitude": -117.36},
    {"id": "target_2", "label": "Target 2", "latitude": 32.675, "longitude": -117.39},
]
LOITER_RADII_M = (500, 1000, 2000, 3000)
DEFAULT_LOITER_RADIUS_M = 1000
MIN_BOAT_SEPARATION_M = 91.44
FORMATION_SPACING_M = 250.0


def formation_destination(point, node):
    # Stable stations avoid five boats competing for the identical final coordinate.
    index = int(node.rsplit("_", 1)[1]) - 30
    angle = index * math.pi * (3 - math.sqrt(5))
    radius = FORMATION_SPACING_M * math.sqrt(index)
    return [point[0] + radius * math.cos(angle) / 111320,
            point[1] + radius * math.sin(angle) / (111320 * math.cos(math.radians(point[0])))]


def goal_plan(planner, start, node, goal):
    if goal["kind"] == "waypoints":
        destinations = [list(point) for point in goal["waypoints"]]
        destinations[-1] = formation_destination(destinations[-1], node)
        return {"waypoints": planner.plan(start, destinations)}
    target = goal["center"]
    angle = math.atan2((start[1] - target[1]) * math.cos(math.radians(target[0])),
                       start[0] - target[0])
    ring = orbit_points({"latitude": target[0], "longitude": target[1]}, angle, goal["radius_m"])
    approach = planner.plan(start, [ring[0]])
    circuit = planner.plan(approach[-1], ring[1:])
    if len(approach) + len(circuit) > MAX_WAYPOINTS:
        raise ValueError("Orbit detour exceeds the 64-waypoint limit.")
    return {"waypoints": approach + circuit, "loop_start": len(approach)}


def orbit_points(target, angle, radius_m=DEFAULT_LOITER_RADIUS_M):
    radius = (radius_m + 5) / math.cos(math.pi / 12)
    return [
        [round(target["latitude"] + math.degrees(radius * math.cos(angle + i * math.pi / 6) / 6371000), 6),
         round(target["longitude"] + math.degrees(radius * math.sin(angle + i * math.pi / 6) /
               (6371000 * math.cos(math.radians(target["latitude"])))), 6)]
        for i in range(13)
    ]
LAUNCH_POSITIONS = {
    "Platform_30": (32.6900, -117.3200),
    "Platform_31": (32.7050, -117.3050),
    "Platform_32": (32.7200, -117.3200),
    "Platform_33": (32.7350, -117.3050),
    "Platform_34": (32.7500, -117.3200),
}


def launch_position_for(source):
    if source in LAUNCH_POSITIONS:
        return LAUNCH_POSITIONS[source]
    try:
        offset = int(str(source).rsplit("_", 1)[1]) - 30
    except (IndexError, ValueError):
        offset = 0
    return (32.6900 + 0.015 * (offset % 5),
            -117.3200 - 0.015 * (offset // 5))


def project(coordinate):
    lat, lon = coordinate
    return ((lon - BOUNDS[2]) / (BOUNDS[3] - BOUNDS[2]) * 1000,
            (BOUNDS[1] - lat) / (BOUNDS[1] - BOUNDS[0]) * 700)


def coordinate(point):
    x, y = point
    return [round(BOUNDS[1] - y / 700 * (BOUNDS[1] - BOUNDS[0]), 6),
            round(BOUNDS[2] + x / 1000 * (BOUNDS[3] - BOUNDS[2]), 6)]


class WaterPlanner:
    def __init__(self, coastline=None):
        path = coastline or Path(__file__).resolve().parents[1] / "static/san-diego-land.geojson"
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.polygons = [
            [[project((lat, lon)) for lon, lat in ring] for ring in feature["geometry"]["coordinates"]]
            for feature in data["features"]
        ]
        self.boxes = [
            (min(x for x, y in rings[0]), max(x for x, y in rings[0]),
             min(y for x, y in rings[0]), max(y for x, y in rings[0]))
            for rings in self.polygons
        ]

    @staticmethod
    def in_ring(point, ring):
        x, y = point
        inside = False
        for index, a in enumerate(ring):
            b = ring[index - 1]
            if ((a[1] > y) != (b[1] > y) and
                    x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]):
                inside = not inside
        return inside

    def is_land(self, point):
        x, y = point
        return any(
            box[0] <= x <= box[1] and box[2] <= y <= box[3] and
            self.in_ring(point, rings[0]) and
            not any(self.in_ring(point, ring) for ring in rings[1:])
            for rings, box in zip(self.polygons, self.boxes)
        )

    def water_segment(self, start, finish):
        steps = max(1, math.ceil(math.dist(start, finish)))
        return all(not self.is_land((
            start[0] + (finish[0] - start[0]) * i / steps,
            start[1] + (finish[1] - start[1]) * i / steps,
        )) for i in range(steps + 1))

    @staticmethod
    def grid_point(key):
        return (key % 101 * 10, key // 101 * 10)

    @lru_cache(maxsize=60_000)
    def grid_segment(self, first, last):
        return self.water_segment(self.grid_point(first), self.grid_point(last))

    def nearest(self, point):
        candidates = sorted(range(101 * 71),
                            key=lambda key: math.dist(point, self.grid_point(key)))
        for key in candidates:
            candidate = self.grid_point(key)
            if not self.is_land(candidate) and self.water_segment(point, candidate):
                return key
        raise ValueError("No water connection to the planning grid.")

    def detour(self, start, finish):
        if self.is_land(start) or self.is_land(finish):
            raise ValueError("Route endpoints must be on water, not land.")
        if self.water_segment(start, finish):
            return [finish]
        first, last = self.nearest(start), self.nearest(finish)
        queue = [(math.dist(self.grid_point(first), self.grid_point(last)), first)]
        costs, parents, closed = {first: 0}, {}, set()
        while queue:
            _, current = heapq.heappop(queue)
            if current in closed:
                continue
            if current == last:
                path = [finish]
                key = last
                while key is not None:
                    path.insert(0, self.grid_point(key))
                    key = parents.get(key)
                path.insert(0, start)
                simplified = []
                index = 0
                while index < len(path) - 1:
                    following = len(path) - 1
                    while following > index + 1 and not self.water_segment(path[index], path[following]):
                        following -= 1
                    simplified.append(path[following])
                    index = following
                return simplified
            closed.add(current)
            cx, cy = current % 101, current // 101
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    x, y = cx + dx, cy + dy
                    key = y * 101 + x
                    if ((not dx and not dy) or not 0 <= x < 101 or
                            not 0 <= y < 71 or key in closed):
                        continue
                    if not self.grid_segment(min(current, key), max(current, key)):
                        continue
                    cost = costs[current] + math.hypot(dx, dy) * 10
                    if cost < costs.get(key, math.inf):
                        costs[key], parents[key] = cost, current
                        heapq.heappush(queue, (
                            cost + math.dist(self.grid_point(key), self.grid_point(last)), key))
        raise ValueError("No water-only detour was found inside this map.")

    def plan(self, start, waypoints):
        points = [start, *waypoints]
        for point in points:
            if (not isinstance(point, (list, tuple)) or len(point) != 2 or
                    any(isinstance(value, bool) or not isinstance(value, (int, float)) or
                        not math.isfinite(value) for value in point)):
                raise ValueError("Position and waypoints must be finite [latitude, longitude] pairs.")
            if not (BOUNDS[0] <= point[0] <= BOUNDS[1] and BOUNDS[2] <= point[1] <= BOUNDS[3]):
                raise ValueError("Position or waypoint is outside the San Diego demo map bounds.")
        result = []
        previous = project(start)
        for waypoint in waypoints:
            planned = [coordinate(point) for point in self.detour(previous, project(waypoint))]
            # Check the actual rounded DDS coordinates, not just grid coordinates.
            for point in planned:
                projected = project(point)
                if not self.water_segment(previous, projected):
                    raise ValueError("Rounded route crosses land; choose a waypoint farther offshore.")
                result.append(point)
                previous = projected
            if len(result) > MAX_WAYPOINTS:
                raise ValueError("Water detour exceeds the 64-waypoint limit; use fewer waypoints.")
        return result
