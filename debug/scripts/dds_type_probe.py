#!/usr/bin/env python3
"""Standalone diagnostic for discovered inline TypeObjects.

This surfaces the same fact DiscoveryDispatcher.maybe_learn_type() checks: whether a
discovered endpoint's inline TypeObject resolved. It is useful when a route remains in
TOPIC_IDLE because a WIS publication did not propagate its TypeObject.

Usage:
    python3 debug/scripts/dds_type_probe.py --domain 20 \
        [--topic ActTeamAssignment] [--wait 5]
"""

import argparse
import os
import time

os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

import rti.connextdds as dds  # noqa: E402


def build_participant_name_map(participant):
    names = {}
    for handle in participant.discovered_participants():
        try:
            data = participant.discovered_participant_data(handle)
        except Exception:
            continue
        entity_name = data.participant_name
        name = entity_name.name or ""
        role = entity_name.role_name or ""
        if name and role:
            label = f"{name} ({role})"
        elif name:
            label = name
        elif role:
            label = f"({role})"
        else:
            label = "?"
        names[str(data.key.value)] = label
    return names


def collect_endpoints(participant, direction, topic_filter):
    reader = (participant.publication_reader if direction == "PUB"
              else participant.subscription_reader)
    rows = []
    for data, info in reader.read():
        if not info.valid:
            continue
        if topic_filter and data.topic_name != topic_filter:
            continue
        try:
            has_type = data.type is not None
        except Exception:
            has_type = False
        rows.append({
            "direction": direction,
            "topic": data.topic_name,
            "type_name": data.type_name,
            "participant_key": str(data.participant_key.value),
            "has_type": has_type,
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", type=int, required=True)
    parser.add_argument("--topic", default=None,
                        help="Only show endpoints for this topic name.")
    parser.add_argument("--wait", type=float, default=5.0,
                        help="Seconds to wait for discovery before reporting.")
    args = parser.parse_args()

    qos = dds.DomainParticipant.default_participant_qos
    qos.transport_builtin = dds.TransportBuiltin.udpv4
    participant = dds.DomainParticipant(args.domain, qos)

    time.sleep(args.wait)

    names = build_participant_name_map(participant)
    rows = (collect_endpoints(participant, "PUB", args.topic)
            + collect_endpoints(participant, "SUB", args.topic))

    print(f"\nDomain {args.domain} - discovered endpoints ({args.wait:g}s wait):\n")
    if not rows:
        print("  (none discovered)")
    for row in sorted(rows, key=lambda r: (r["topic"], r["direction"])):
        label = names.get(row["participant_key"], "?")
        type_flag = "YES" if row["has_type"] else "NO"
        print(f"  {row['direction']:<4} {row['topic']:<22} {row['type_name']:<18} "
              f"{row['participant_key']:<28} {label:<18} TypeObject: {type_flag}")

    by_topic = {}
    for row in rows:
        if row["direction"] != "PUB":
            continue
        by_topic.setdefault(row["topic"], []).append(row["has_type"])

    print()
    any_warned = False
    for topic, flags in sorted(by_topic.items()):
        n_typed = sum(flags)
        if n_typed < len(flags):
            any_warned = True
            print(f"WARNING {topic}: {n_typed} of {len(flags)} publications carry "
                  "inline TypeObject")
            print("  -> Router routes waiting on this type will stay TOPIC_IDLE")
    if not any_warned and by_topic:
        print("All discovered publications carry inline TypeObjects.")

    participant.close()


if __name__ == "__main__":
    main()