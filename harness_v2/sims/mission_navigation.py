"""Onboard water navigation and conservative DDS-position collision buffering."""
import logging
import math

from mission_planning import BOUNDS, MIN_BOAT_SEPARATION_M, WaterPlanner, project


logger = logging.getLogger(__name__)
MAX_WALL_SPEED_MPS = 10 * 1852 / 3600 * 20
PEER_STALE_S = 2.5


def meters(point):
    return (point[1] * 111320 * math.cos(math.radians(32.725)), point[0] * 111320)


def geographic(point):
    return (point[1] / 111320, point[0] / (111320 * math.cos(math.radians(32.725))))


def segment_distance(start, end, point):
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    fraction = min(1, max(0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) /
                         length_squared)) if length_squared else 0
    return math.dist(point, (start[0] + fraction * dx, start[1] + fraction * dy))


class Navigator:
    def __init__(self, node, run_id, planner=None):
        self.node, self.run_id = node, run_id
        self.planner = planner or WaterPlanner()
        self.peers = {}
        self.expected_peers = set()
        self.detour = None
        self.exclusions = []

    def record_location(self, sample, now):
        node = sample.get("source_node")
        if node == self.node or sample.get("run_id") != self.run_id:
            return
        point = (sample.get("latitude"), sample.get("longitude"))
        sent = sample.get("sent_at_ns")
        if (not isinstance(node, str) or not node.startswith("Platform_") or
                sample.get("source") != node or
                any(isinstance(v, bool) or not isinstance(v, (float, int)) or
                    not math.isfinite(v) for v in (*point, sent)) or
                not BOUNDS[0] <= point[0] <= BOUNDS[1] or
                not BOUNDS[2] <= point[1] <= BOUNDS[3] or sent <= 0 or sent / 1e9 > now + .1):
            logger.warning("Invalid shared boat position from %s", node)
            return
        previous = self.peers.get(node)
        if previous and sent <= previous["sent"]:
            return
        self.peers[node] = {"position": meters(point), "sent": sent}

    def step(self, position, destination, travel_m, elapsed_wall_s, now):
        if self.expected_peers - self.peers.keys():
            return position, "WAITING_PEERS"
        if any(now - self.peers[node]["sent"] / 1e9 > PEER_STALE_S
               for node in self.expected_peers):
            return position, "STALE_PEERS"
        start, goal = meters(position), meters(destination)
        distance = math.dist(start, goal)
        if distance < .01 or travel_m <= 0:
            return position, "NAVIGATING"
        if self.detour and (self.detour["goal"] != tuple(destination) or
                            math.dist(start, self.detour["position"]) < 20):
            self.detour = None
        if not self.detour:
            clearance = MIN_BOAT_SEPARATION_M + MAX_WALL_SPEED_MPS * 1.5
            for peer in sorted(self.peers.values(), key=lambda peer: math.dist(start, peer["position"])):
                center = peer["position"]
                if (math.dist(start, center) > clearance * 3 or
                        segment_distance(start, goal, center) >= clearance):
                    continue
                dx, dy = (goal[0] - start[0]) / distance, (goal[1] - start[1]) / distance
                for sign in (-1, 1):
                    point = (center[0] - sign * dy * (clearance + 50),
                             center[1] + sign * dx * (clearance + 50))
                    coordinate = geographic(point)
                    if (BOUNDS[0] <= coordinate[0] <= BOUNDS[1] and
                            BOUNDS[2] <= coordinate[1] <= BOUNDS[3] and
                            all(math.dist(point, meters(c)) > r + 5 for c, r in self.exclusions) and
                            self.planner.water_segment(project(position), project(coordinate))):
                        self.detour = {"position": point, "goal": tuple(destination)}
                        break
                if self.detour:
                    break
        if self.detour:
            goal = self.detour["position"]
            distance = math.dist(start, goal)
        travel = min(travel_m, distance, MAX_WALL_SPEED_MPS * min(.1, elapsed_wall_s))
        angle = math.atan2(goal[1] - start[1], goal[0] - start[0])
        # Uncertainty covers all possible peer movement since its transmitted fix,
        # including 20x time, plus the next movement interval; not just end positions.
        obstacles = [
            (peer["position"], MIN_BOAT_SEPARATION_M + 1 +
             MAX_WALL_SPEED_MPS * (max(0, now - peer["sent"] / 1e9) + elapsed_wall_s))
            for peer in self.peers.values()
        ]
        candidates = []
        for turn in [0, *[sign * degrees for degrees in range(15, 181, 15) for sign in (-1, 1)]]:
            direction = angle + math.radians(turn)
            end = (start[0] + travel * math.cos(direction), start[1] + travel * math.sin(direction))
            safe = True
            for center, radius in obstacles:
                # Our speed is bounded by the same maximum used to expand the peer's
                # uncertainty disk. End clearance therefore bounds the entire step
                # by the reverse triangle inequality, including an outward escape.
                safe = math.dist(end, center) >= radius
                if not safe:
                    break
            point = geographic(end)
            for center, radius in self.exclusions:
                center = meters(center)
                initial = math.dist(start, center)
                safe = safe and segment_distance(start, end, center) >= min(radius, initial) - .001
                if initial < radius:
                    safe = safe and math.dist(end, center) > initial
            if (safe and BOUNDS[0] <= point[0] <= BOUNDS[1] and
                    BOUNDS[2] <= point[1] <= BOUNDS[3] and
                    self.planner.water_segment(project(position), project(point))):
                candidates.append((math.dist(end, goal) + abs(turn) * .001, point))
        if not candidates:
            return position, "BUFFER_HOLD"
        return min(candidates, key=lambda candidate: candidate[0])[1], "NAVIGATING"
