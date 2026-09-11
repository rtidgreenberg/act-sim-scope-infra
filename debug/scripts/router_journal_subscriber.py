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
DEFAULT_OUTPUT = REPO_ROOT / "debug" / "logs" / "journal" / "router_journal.jsonl"
TOPICS = (
    ("ActRouterControllerJournal", "ControllerJournalRecord", True),
    ("ActRouterStatus", "RouterStatus", False),
)


def reader_qos(keep_all):
    qos = dds.DataReaderQos()
    qos.reliability = dds.Reliability(kind=dds.ReliabilityKind.RELIABLE)
    if keep_all:
        qos.history = dds.History.keep_all
    else:
        qos.durability = dds.Durability(kind=dds.DurabilityKind.TRANSIENT_LOCAL)
    return qos


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", type=int, default=20)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"JSONL output path (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--wait", type=float, default=2.0,
                        help="Seconds to wait for discovery before reading (default: 2)")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="Seconds to capture; 0 means run until interrupted")
    args = parser.parse_args()

    if not TYPES_XML.is_file():
        raise FileNotFoundError(f"DDS types XML not found: {TYPES_XML}")
    args.output.parent.mkdir(parents=True, exist_ok=True)

    provider = dds.QosProvider(str(TYPES_XML))
    participant_qos = dds.DomainParticipant.default_participant_qos
    participant_qos.transport_builtin = dds.TransportBuiltin.udpv4
    participant = dds.DomainParticipant(args.domain, participant_qos)
    subscriber = dds.Subscriber(participant)
    readers = []
    for topic_name, type_name, keep_all in TOPICS:
        dtype = provider.type(type_name)
        topic = dds.DynamicData.Topic(participant, topic_name, dtype)
        readers.append((topic_name, dds.DynamicData.DataReader(
            subscriber, topic, reader_qos(keep_all))))

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
