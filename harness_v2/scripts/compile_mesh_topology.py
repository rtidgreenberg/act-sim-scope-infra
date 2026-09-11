#!/usr/bin/env python3
"""Validate a Compose/EMANE mesh topology and emit tab-separated node records."""

import argparse
import ipaddress
import json
import re
from pathlib import Path


NODE_ID = re.compile(r"(control|platform)_(\d+)")


def compile_topology(topology):
    if topology.get("schema_version") != 1:
        raise ValueError("topology schema_version must be 1")
    nodes = topology.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("topology nodes must be a non-empty list")

    compiled = []
    identifiers = set()
    nem_ids = set()
    rf_ips = set()
    control_ips = set()
    control_nodes = []
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("topology nodes must be objects")
        identifier = node.get("id")
        role = node.get("role")
        match = NODE_ID.fullmatch(identifier or "")
        if not match or match.group(1) != role:
            raise ValueError(f"invalid node identity or role: {identifier!r}")
        if identifier in identifiers:
            raise ValueError(f"duplicate node identifier: {identifier}")
        identifiers.add(identifier)
        numeric_id = int(match.group(2))
        if role == "control":
            control_nodes.append(identifier)
        elif not 30 <= numeric_id <= 99:
            raise ValueError(f"platform ID must be 30-99: {identifier}")

        lan_domain = node.get("lan_domain")
        if lan_domain != numeric_id or not 0 <= lan_domain <= 232:
            raise ValueError(f"LAN domain must equal the node numeric ID and be 0-232: {identifier}")
        emane = node.get("emane")
        if not isinstance(emane, dict):
            raise ValueError(f"missing EMANE configuration: {identifier}")
        nem_id = emane.get("nem_id")
        if not isinstance(nem_id, int) or nem_id < 1 or nem_id in nem_ids:
            raise ValueError(f"invalid or duplicate EMANE NEM ID: {identifier}")
        nem_ids.add(nem_id)
        try:
            rf_ip = str(ipaddress.IPv4Address(emane["ip"]))
            control_ip = str(ipaddress.IPv4Address(emane["control_ip"]))
        except (KeyError, ipaddress.AddressValueError) as error:
            raise ValueError(f"invalid EMANE address: {identifier}") from error
        if rf_ip in rf_ips or control_ip in control_ips:
            raise ValueError(f"duplicate EMANE address: {identifier}")
        rf_ips.add(rf_ip)
        control_ips.add(control_ip)
        compiled.append((identifier, role, lan_domain, nem_id, rf_ip, control_ip))

    if control_nodes != ["control_20"]:
        raise ValueError("topology needs exactly one control_20 node")
    if not any(role == "platform" for _, role, *_ in compiled):
        raise ValueError("topology needs at least one platform node")
    return compiled


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--topology", required=True, type=Path)
    arguments = parser.parse_args()
    topology = json.loads(arguments.topology.read_text(encoding="utf-8"))
    for node in compile_topology(topology):
        print("\t".join(map(str, node)))


if __name__ == "__main__":
    main()