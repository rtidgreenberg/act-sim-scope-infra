#!/usr/bin/env python3
"""Write the immutable route/topology expectation snapshot for the container baseline."""

import argparse
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
        output.write(content)
        temporary_name = output.name
    os.replace(temporary_name, path)


def compile_expectations(manifest):
    nodes = manifest["nodes"]
    control_nodes = [node for node in nodes if node == "control_20"]
    platform_nodes = [node for node in nodes if node.startswith("platform_")]
    if control_nodes != ["control_20"] or not platform_nodes:
        raise ValueError("baseline topology needs control_20 and at least one platform node")
    required_topics = manifest["topics"]
    expected_topics = {"ControlCommand", "PlatformInitStatus"}
    if set(required_topics) != expected_topics:
        raise ValueError("baseline expectations support ControlCommand and PlatformInitStatus only")
    return {
        "schema_version": 1,
        "run_id": manifest["run_id"],
        "nodes": nodes,
        "topics": {
            "ControlCommand": {
                "route": "control_command",
                "enabled": True,
                "source_nodes": control_nodes,
                "recipient_rule": {"kind": "sample_destination", "nodes": platform_nodes},
            },
            "PlatformInitStatus": {
                "route": "platform_init_status",
                "enabled": True,
                "source_nodes": platform_nodes,
                "recipient_rule": {"kind": "fixed_nodes", "nodes": control_nodes},
            },
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
    snapshot = compile_expectations(manifest)
    atomic_write(arguments.output, json.dumps(snapshot, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()