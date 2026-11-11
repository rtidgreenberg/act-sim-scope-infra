"""Deterministic source truth; no DDS or wall-clock dependency."""

from dataclasses import dataclass
import math


TICK_SECONDS = 0.05
STATIONARY_CONTACT_ID = -1_000_000


def positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("duration must be finite and positive")
    return seconds


@dataclass(frozen=True)
class Operation:
    kind: str
    identity: int


def operations(tick, pattern="steady"):
    if pattern == "short":
        return [
            operation
            for identity in range(tick * 5, tick * 5 + 5)
            for operation in (Operation("create", identity), Operation("dispose", identity - 50))
        ]
    if pattern not in ("steady", "revival"):
        raise ValueError(f"Unknown workload: {pattern}")
    result = [
        Operation("dispose", tick - 160),
        Operation("update", tick - 120),
        Operation("update", tick - 80),
        Operation("update", tick - 40),
        Operation("create", tick),
    ]
    if pattern == "revival":
        return [Operation(item.kind, item.identity % 320) for item in result]
    return result


def tick_seconds(pattern):
    return 0.1 if pattern == "short" else TICK_SECONDS


def initial_identities(pattern):
    if pattern == "short":
        return range(-50, 0)
    if pattern == "revival":
        return range(160, 320)
    if pattern == "steady":
        return range(-160, 0)
    raise ValueError(f"Unknown workload: {pattern}")


def discrepancies(expected, actual):
    ghosts, missing, stale = [], [], []
    for key, truth in expected.items():
        seen = actual.get(key)
        if truth["state"] == "disposed":
            if seen is not None and seen["state"] == "alive":
                ghosts.append(key)
        elif seen is None or seen["state"] != "alive":
            missing.append(key)
        elif seen["sequence"] != truth["sequence"]:
            stale.append(key)
    unexpected = [key for key, value in actual.items()
                  if value["state"] == "alive" and key not in expected]
    return {"ghosts": sorted(ghosts), "missing": sorted(missing),
            "stale": sorted(stale), "unexpected": sorted(unexpected)}


def converged(expected, actual):
    return not any(discrepancies(expected, actual).values())
