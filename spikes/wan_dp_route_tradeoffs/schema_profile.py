"""Local synthetic workload targets; no customer schemas or captures are used."""

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from goal_model import (GOAL_NAMES, GOAL_RATES, GOAL_SIZES, GOAL_FIELDS, active_kinds,
                        OPTIMIZED_NAMES, OPTIMIZED_RATES, OPTIMIZED_SIZES, OPTIMIZED_FIELDS)


PROFILES = [
    ("MissionAdd", 236, 4420, 0),
    ("TaskElement", 148, 4332, 0),
    ("ContactElement", 156, 4212, 0.8),
    ("MissionElement", 144, 2716, 0),
    ("WaypointElement", 117, 1521, 0),
    ("TransferRequest", 72, 648, 0),
    ("ControlReport", 68, 456, 0),
    ("TransferReport", 60, 384, 0),
    ("ControlRequest", 56, 380, 0),
    ("Pose", 144, 332, 1),
    ("Velocity", 120, 256, 1),
    ("MissionReport", 144, 180, 0),
    ("ContactReport", 100, 120, 0.4),
    ("Speed", 60, 104, 1),
]
POSE = 9
MISSION_FIELDS = {
    "MissionAdd": [("mission_id", "uint32"), ("priority", "uint32"), ("task_count", "uint32")],
    "TaskElement": [("mission_id", "uint32"), ("task_id", "uint32"), ("duration_s", "float32")],
    "ContactElement": [("track_id", "uint32"), ("classification", "uint32"),
                       ("latitude", "float64"), ("longitude", "float64"), ("altitude_m", "float32")],
    "MissionElement": [("mission_id", "uint32"), ("state", "uint32"), ("progress", "float32")],
    "WaypointElement": [("latitude", "float64"), ("longitude", "float64"),
                        ("altitude_m", "float32"), ("speed_mps", "float32")],
    "TransferRequest": [("owner_id", "uint32"), ("target_id", "uint32")],
    "ControlReport": [("state", "uint32"), ("reason", "uint32")],
    "TransferReport": [("status", "uint32")],
    "ControlRequest": [],
    "Pose": [("latitude", "float64"), ("longitude", "float64"),
             ("altitude_m", "float32"), ("heading_deg", "float32")],
    "Velocity": [("north_mps", "float32"), ("east_mps", "float32"), ("down_mps", "float32")],
    "MissionReport": [("mission_id", "uint32"), ("state", "uint32"), ("progress", "float32")],
    "ContactReport": [("track_count", "uint32")],
    "Speed": [("metres_per_second", "float32")],
}


def profiles(goal=False, optimized=False):
    names = OPTIMIZED_NAMES if optimized else GOAL_NAMES
    sizes = OPTIMIZED_SIZES if optimized else GOAL_SIZES
    rates = OPTIMIZED_RATES if optimized else GOAL_RATES
    return [(names.get(index, name) if goal else name,
             *(sizes.get(index, (minimum, maximum)) if goal else (minimum, maximum)),
             rates.get(index, rate) if goal else rate)
            for index, (name, minimum, maximum, rate) in enumerate(PROFILES)]


def render(path, goal=False, optimized=False):
    root = ET.Element("dds")
    types = ET.SubElement(root, "types")
    inventory = profiles(goal, optimized)
    for index in (*active_kinds(optimized), *((14,) if optimized else ())):
        name, _, maximum, _ = inventory[index] if index != 14 else ("RouterHealth", 60, 104, 1)
        struct = ET.SubElement(types, "struct", name=f"Scale{name}", extensibility="mutable")
        for field, kind, key in (
                ("source", "uint32", True), ("instance", "uint32", True),
                ("stamp", "uint64", False), ("session", "uint32", False)):
            attributes = dict(name=field, type=kind)
            if key:
                attributes["key"] = "true"
            ET.SubElement(struct, "member", attributes)
        original_name = PROFILES[index][0] if index != 14 else "Speed"
        fields = ([("health_code", "uint32")] if index == 14 else
                  OPTIMIZED_FIELDS[index] if optimized else
                  GOAL_FIELDS.get(index, MISSION_FIELDS[original_name]) if goal else MISSION_FIELDS[original_name])
        for field, kind in fields:
            ET.SubElement(struct, "member", name=field, type=kind)
        if name == "ControlRequest":
            ET.SubElement(struct, "member", name="action", type="uint32", optional="true")
        ET.SubElement(struct, "member", name="payload", type="octet",
                      sequenceMaxLength=str(maximum))
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def generate(directory, goal=False, optimized=False):
    directory.mkdir(parents=True, exist_ok=True)
    xml = directory / "schemas.xml"
    render(xml, goal, optimized)
    manifest = dict(
        sizing="Serialized sample sizes only; no TypeObject-size calibration or padding",
        fidelity="Synthetic mission fields and bounded payloads; no UMAA definitions or historical replay",
        type_count=7 if optimized else len(PROFILES),
        types=[dict(type=f"Scale{name}", topic=f"Scale{name}",
                    mission_fields=([field for field, _ in
                                     (OPTIMIZED_FIELDS[index] if optimized else
                                      GOAL_FIELDS.get(index, MISSION_FIELDS[PROFILES[index][0]])
                                      if goal else MISSION_FIELDS[PROFILES[index][0]])]
                                    + (["action"] if name == "ControlRequest" else [])),
                    min_sample_target=minimum, max_sample_target=maximum, rate_hz=rate)
               for index, (name, minimum, maximum, rate) in enumerate(profiles(goal, optimized))
               if index in active_kinds(optimized)])
    if goal:
        manifest["goal_model_version"] = 2 if optimized else 1
        manifest["fidelity"] = "Proposed squad/team roles with compact synthetic coordination states, not reference schemas"
    if optimized:
        manifest["application_type_count"] = 6
        manifest["types"].append(dict(type="ScaleRouterHealth", topic="ScaleHealth",
                                     mission_fields=["health_code"], min_sample_target=60,
                                     max_sample_target=104, rate_hz=1))
    (directory / "schema_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return xml


def size_payload(sample, target, minimum, maximum):
    if not minimum <= target <= maximum:
        raise ValueError(f"sample target {target} outside {minimum}..{maximum}")
    sample["payload"] = []
    base_size = len(sample.to_cdr_buffer())
    if base_size > target:
        raise ValueError(f"base sample {base_size} cannot fit target {target}")
    payload_size = target - base_size
    sample["payload"] = [0] * payload_size
    measured = len(sample.to_cdr_buffer())
    if measured > maximum:
        payload_size -= measured - maximum
        if payload_size < 0:
            raise ValueError(f"base sample cannot fit maximum {maximum}")
        sample["payload"] = [0] * payload_size
        measured = len(sample.to_cdr_buffer())
    if not minimum <= measured <= maximum or abs(measured - target) > 3:
        raise ValueError(f"serialized sample {measured} cannot fit target {target} in {minimum}..{maximum}")
    return measured


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    arguments = parser.parse_args()
    print(generate(arguments.directory))
