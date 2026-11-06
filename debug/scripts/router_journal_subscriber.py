#!/usr/bin/env python3
"""Capture router status and controller-journal DDS samples as JSON lines."""

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

import rti.connextdds as dds  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
TYPES_XML = REPO_ROOT / "harness_v2" / "datamodel" / "gen" / "ActTypes.xml"
QOS_XML = REPO_ROOT / "harness_v2" / "qos" / "act_qos_profiles.xml"
DEFAULT_OUTPUT = REPO_ROOT / "debug" / "logs" / "journal" / "router_journal.jsonl"
TOPICS = (
    ("ActRouterControllerJournal", "ControllerJournalRecord", "router_controller_journal"),
    ("ActRouterStatus", "RouterStatus", "router_admin_status"),
    ("ActRouterLinkStats", "RouterLinkStats", "router_link_stats"),
)


def journal_participant_qos():
    return dds.QosProvider(str(QOS_XML)).participant_qos_from_profile(
        "ACT_QOS_LIB::router_journal_participant")


def reader_qos(profile):
    return dds.QosProvider(str(QOS_XML)).datareader_qos_from_profile("ACT_QOS_LIB::" + profile)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", type=int, default=20)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"JSONL output path (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--wait", type=float, default=2.0,
                        help="Seconds to wait for discovery before reading (default: 2)")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="Seconds to capture; 0 means run until interrupted")
    parser.add_argument("--node-name", default="",
                        help="Logical node name to include in each envelope")
    parser.add_argument("--participant-name", default="",
                        help="DDS participant EntityName.name for discovery tools")
    parser.add_argument("--participant-role", default="act.journal_subscriber",
                        help="DDS participant EntityName.role_name for discovery tools")
    args = parser.parse_args()

    if not TYPES_XML.is_file():
        raise FileNotFoundError(f"DDS types XML not found: {TYPES_XML}")
    args.output.parent.mkdir(parents=True, exist_ok=True)

    provider = dds.QosProvider(str(TYPES_XML))
    participant_qos = journal_participant_qos()
    if args.participant_name or args.participant_role:
        entity_name = participant_qos.participant_name
        if args.participant_name:
            entity_name.name = args.participant_name
        if args.participant_role:
            entity_name.role_name = args.participant_role
        participant_qos.participant_name = entity_name
    participant = dds.DomainParticipant(args.domain, participant_qos)
    subscriber = dds.Subscriber(participant)
    readers = []
    for topic_name, type_name, profile in TOPICS:
        dtype = provider.type(type_name)
        topic = dds.DynamicData.Topic(participant, topic_name, dtype)
        readers.append((topic_name, dds.DynamicData.DataReader(
            subscriber, topic, reader_qos(profile))))

    stop = False

    def request_stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    print(f"[router-journal] domain={args.domain}")
    print(f"[router-journal] output={args.output}")
    time.sleep(args.wait)
    started = time.monotonic()
    count = 0
    with args.output.open("a", encoding="utf-8") as output:
        while not stop and (args.duration <= 0 or time.monotonic() - started < args.duration):
            received = False
            for topic_name, reader in readers:
                for data, info in reader.take():
                    if not info.valid:
                        continue
                    record = {
                        "received_at_unix_nanos": time.time_ns(),
                        "domain": args.domain,
                        "topic": topic_name,
                        "sample": json.loads(data.to_json()),
                    }
                    if args.node_name:
                        record["node"] = args.node_name
                    output.write(json.dumps(record, separators=(",", ":")) + "\n")
                    output.flush()
                    count += 1
                    received = True
            if not received:
                time.sleep(0.05)

    participant.close()
    print(f"[router-journal] captured={count}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
