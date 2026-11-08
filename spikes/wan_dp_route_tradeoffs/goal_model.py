"""Proposed squad/team scopes, distinct from the historical reference workload."""

SQUAD_KINDS = (9, 10, 13)
GOAL_NAMES = {
    1: "TaskProgress", 2: "SharedObservation", 3: "MissionState",
    4: "RendezvousState", 11: "CoordinationIntent",
}
TEAM_SETS = {1: (12,), 3: (1, 3, 12), 6: (1, 2, 3, 4, 11, 12)}
GOAL_RATES = {1: 0.2, 2: 0.8, 3: 0.2, 4: 0.2, 11: 0.2, 12: 0.4}
GOAL_SIZES = {1: (80, 160), 3: (96, 256), 4: (96, 160), 11: (96, 332)}
GOAL_FIELDS = {
    1: [("task_id", "uint32"), ("state", "uint32"), ("progress", "float32")],
    3: [("mission_id", "uint32"), ("state", "uint32"), ("progress", "float32")],
    4: [("coordination_id", "uint32"), ("latitude", "float64"),
        ("longitude", "float64"), ("state", "uint32")],
    11: [("intent_id", "uint32"), ("valid_for_s", "float32"),
         ("heading_deg", "float32"), ("speed_mps", "float32")],
}
OPTIMIZED_KINDS = (0, 3, 4, 5, 9, 12)
OPTIMIZED_NAMES = {
    0: "ControlCommand", 3: "MissionState", 4: "CoordinationState",
    5: "ControlFeedback", 9: "NavigationState", 12: "SituationalAwareness",
}
OPTIMIZED_RATES = {3: 0.2, 4: 0.2, 5: 0, 9: 1, 12: 0.8}
OPTIMIZED_SIZES = {3: (112, 256), 4: (128, 332), 5: (96, 648),
                   9: (144, 332), 12: (120, 4212)}
OPTIMIZED_FIELDS = {
    0: [("command_kind", "uint32"), ("command_id", "uint32"),
        ("mission_id", "uint32"), ("priority", "uint32"), ("task_count", "uint32")],
    3: [("mission_id", "uint32"), ("state", "uint32"), ("progress", "float32"),
        ("task_id", "uint32"), ("task_state", "uint32"), ("task_progress", "float32")],
    4: GOAL_FIELDS[4] + GOAL_FIELDS[11],
    5: [("feedback_kind", "uint32"), ("command_id", "uint32"), ("state", "uint32"),
        ("reason", "uint32"), ("target_id", "uint32")],
    9: [("latitude", "float64"), ("longitude", "float64"), ("altitude_m", "float32"),
        ("heading_deg", "float32"), ("north_mps", "float32"), ("east_mps", "float32"),
        ("down_mps", "float32"), ("metres_per_second", "float32")],
    12: [("track_count", "uint32"), ("track_id", "uint32"), ("classification", "uint32"),
         ("latitude", "float64"), ("longitude", "float64"), ("altitude_m", "float32")],
}
OPTIMIZED_SAMPLE_VALUES = {
    0: {"command_kind": 1, "command_id": 1, "mission_id": 1, "priority": 2, "task_count": 1},
    3: {"mission_id": 1, "state": 1, "progress": 0.5, "task_id": 2,
        "task_state": 1, "task_progress": 0.25},
    4: {"coordination_id": 1, "latitude": 37.0, "longitude": -122.0, "state": 1,
        "intent_id": 2, "valid_for_s": 30.0, "heading_deg": 90.0, "speed_mps": 2.0},
    5: {"feedback_kind": 1, "command_id": 1, "state": 1, "reason": 0, "target_id": 1},
    9: {"latitude": 37.0, "longitude": -122.0, "altitude_m": 10.0, "heading_deg": 90.0,
        "north_mps": 0.0, "east_mps": 2.0, "down_mps": 0.0, "metres_per_second": 2.0},
    12: {"track_count": 1, "track_id": 1, "classification": 2,
         "latitude": 37.0, "longitude": -122.0, "altitude_m": 10.0},
}


def active_kinds(optimized=False):
    return OPTIMIZED_KINDS if optimized else tuple(range(14))


def squad_kinds(optimized=False):
    return (9,) if optimized else SQUAD_KINDS


def endpoint_inventory(platforms, count, optimized=False):
    if optimized:
        team_kinds(count, True)
        return 12 * platforms + 8
    return (19 + count) * platforms + 16


def team_kinds(count, optimized=False):
    if optimized:
        if count != 3:
            raise ValueError("optimized model requires three consolidated team topics")
        return (3, 4, 12)
    if count not in TEAM_SETS:
        raise ValueError("team topic count must be 1 (historical), 3, or 6")
    return TEAM_SETS[count]


def initial_membership(platforms, team_size):
    if platforms < 1 or team_size < 1:
        raise ValueError("population and team size must be positive")
    return {node: (node - 1) // team_size for node in range(1, platforms + 1)}


def membership_groups(membership):
    groups = {}
    for node, team in sorted(membership.items()):
        groups.setdefault(team, []).append(node)
    return groups


def swap_membership(membership):
    groups = membership_groups(membership)
    if len(groups) < 2:
        raise ValueError("a team swap requires at least two teams")
    first, second = sorted(groups)[:2]
    left, right = groups[first][0], groups[second][0]
    result = membership.copy()
    result[left], result[right] = second, first
    return result


def reassignment_membership(membership):
    groups = membership_groups(membership)
    teams = sorted(groups)
    counts = {len(members) for members in groups.values()}
    if len(teams) < 3 or len(counts) != 1 or min(counts) < 2:
        raise ValueError("mixing reassignment requires at least three equal teams of size >=2")
    result = {}
    for index, team in enumerate(teams):
        for slot, node in enumerate(groups[team]):
            offset = 1 + slot % (len(teams) - 1)
            result[node] = teams[(index + offset) % len(teams)]
    return result


def datamodel(count, optimized=False):
    selected = team_kinds(count, optimized)
    if count == 1:
        raise ValueError("the goal datamodel requires three or six team topics")
    if optimized:
        return {
            "version": 2, "application_type_count": 6, "dds_topic_count": 7,
            "squad_topics": ["NavigationState"],
            "team_topics": [OPTIMIZED_NAMES[k] for k in selected],
            "c2_topics": ["ControlCommand", "ControlFeedback"],
            "team_endpoints_per_platform": 6, "endpoints_per_platform": 12,
            "control_endpoints": 8, "control_wire_learned_types": 4,
            "consolidation": {
                "NavigationState": ["Pose", "Velocity", "Speed"],
                "MissionState": ["MissionState", "TaskProgress"],
                "SituationalAwareness": ["ContactReport", "SharedObservation"],
                "CoordinationState": ["RendezvousState", "CoordinationIntent"],
                "ControlCommand": ["MissionAdd", "operator commands"],
                "ControlFeedback": ["command acknowledgments", "ControlReport",
                                    "TransferReport", "TransferRequest", "ControlRequest"],
            },
            "health": "Separate synthetic RouterHealth topic; not the production roster",
            "fidelity": "Proposed consolidated synthetic snapshot/envelope fields, not reference schemas",
        }
    names = {
        0: "MissionAdd", 1: "TaskProgress", 2: "SharedObservation",
        3: "MissionState", 4: "RendezvousState", 5: "TransferRequest",
        6: "ControlReport", 7: "TransferReport", 8: "ControlRequest",
        9: "Pose", 10: "Velocity", 11: "CoordinationIntent",
        12: "ContactReport", 13: "Speed",
    }
    return {
        "version": 1,
        "squad_topics": [names[k] for k in SQUAD_KINDS],
        "team_topics": [names[k] for k in selected],
        "c2_topics": [name for kind, name in names.items()
                      if kind not in SQUAD_KINDS and kind not in selected],
        "team_endpoints_per_platform": 2 * count,
        "health": "Unchanged synthetic C2/platform heartbeat; not a production roster",
        "fidelity": "Proposed synthetic roles with inherited size ranges, not the missing reference configuration",
    }
