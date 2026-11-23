"""Verify 6 Connext Routing Service topics (2 P1, 3 P2, 1 P3) with IPv4 DSCP over EMANE TDMA."""

import argparse
import json
import statistics
import time
from pathlib import Path

TOPICS = [
    ("P1_Event", "LAN_P1_Event", "SixTopicEvent", "RS_SIX::lan_event", 1),
    ("P1_Periodic", "LAN_P1_Periodic", "SixTopicPeriodic", "RS_SIX::lan_periodic", 1),
    ("P2_Event", "LAN_P2_Event", "SixTopicEvent", "RS_SIX::lan_event", 2),
    ("P2_State", "LAN_P2_State", "SixTopicState", "RS_SIX::lan_state", 2),
    ("P2_Periodic", "LAN_P2_Periodic", "SixTopicPeriodic", "RS_SIX::lan_periodic", 2),
    ("P3_Periodic", "LAN_P3_Periodic", "SixTopicPeriodic", "RS_SIX::lan_periodic", 3),
]
BURST = 48
PAYLOAD = bytes([0x5A]) * 1024


def load_provider(qos_file: str, types_file: str):
    import rti.connextdds as dds

    return dds, dds.QosProvider(dds.QosProviderParams(url_profile=[qos_file, types_file]))


def run_tx(qos_file: str, types_file: str) -> None:
    dds, provider = load_provider(qos_file, types_file)
    with dds.DomainParticipant(101, provider.participant_qos_from_profile("RS_SIX::lan_v4")) as p:
        writers = {}
        for label, topic_name, type_name, profile, _ in TOPICS:
            topic = dds.DynamicData.Topic(p, topic_name, provider.type(type_name))
            writers[label] = (
                dds.DynamicData.DataWriter(
                    p.implicit_publisher, topic, provider.datawriter_qos_from_profile(profile)
                ),
                provider.type(type_name),
            )
        deadline = time.monotonic() + 15
        while not all(len(w.matched_subscriptions) >= 1 for w, _ in writers.values()):
            if time.monotonic() > deadline:
                raise RuntimeError("tx LAN discovery timed out")
            time.sleep(0.02)
        # Allow Routing Service WAN endpoints to finish matching before burst.
        time.sleep(2.5)
        # Interleave lowest-priority first within each round so priority must actively reorder.
        send_order = list(reversed(TOPICS))
        for seq in range(BURST):
            for label, _, type_name, _, _ in send_order:
                writer, dtype = writers[label]
                sample = dds.DynamicData(dtype)
                if type_name == "SixTopicState":
                    sample["key_id"] = f"k-{seq}"
                sample["seq"] = seq
                sample["sent_ns"] = time.monotonic_ns()
                sample["payload"] = PAYLOAD
                writer.write(sample)
        time.sleep(3.0)


def run_rx(qos_file: str, types_file: str) -> None:
    dds, provider = load_provider(qos_file, types_file)
    with dds.DomainParticipant(103, provider.participant_qos_from_profile("RS_SIX::lan_v4")) as p:
        readers = {}
        for label, topic_name, type_name, profile, _ in TOPICS:
            topic = dds.DynamicData.Topic(p, topic_name, provider.type(type_name))
            readers[label] = dds.DynamicData.DataReader(
                p.implicit_subscriber, topic, provider.datareader_qos_from_profile(profile)
            )
        deadline = time.monotonic() + 15
        while not all(len(r.matched_publications) >= 1 for r in readers.values()):
            if time.monotonic() > deadline:
                raise RuntimeError("rx LAN discovery timed out")
            time.sleep(0.02)
        print("Ready", flush=True)

        arrivals = []
        per_topic = {label: [] for label, *_ in TOPICS}
        seen = {label: set() for label, *_ in TOPICS}
        last_rx = time.monotonic()
        end_deadline = last_rx + 14.0
        target_total = BURST * len(TOPICS)
        while len(arrivals) < target_total and time.monotonic() < end_deadline:
            got = False
            for label, *_ in TOPICS:
                for sample in readers[label].take():
                    if sample.info.valid:
                        got = True
                        now_ns = time.monotonic_ns()
                        seq = int(sample.data["seq"])
                        sent_ns = int(sample.data["sent_ns"])
                        if seq not in seen[label]:
                            seen[label].add(seq)
                            idx = len(arrivals)
                            lat_ms = (now_ns - sent_ns) / 1e6
                            arrivals.append((idx, label, seq, lat_ms))
                            per_topic[label].append((idx, lat_ms))
            if got:
                last_rx = time.monotonic()
            else:
                if len(arrivals) > 0 and (time.monotonic() - last_rx) > 3.5:
                    break
                time.sleep(0.005)

        half = max(1, len(arrivals) // 2)
        summary = []
        for label, _, _, _, tier in TOPICS:
            entries = per_topic[label]
            lats = [lat for _, lat in entries]
            summary.append(
                {
                    "topic": label,
                    "tier": tier,
                    "received": len(entries),
                    "first_half": sum(1 for idx, _ in entries if idx < half),
                    "median_ms": round(statistics.median(lats), 1) if lats else None,
                }
            )
        print(json.dumps({"total_received": len(arrivals), "topics": summary}), flush=True)


def parse_queues(table: str) -> dict:
    parsed = {}
    for line in table.splitlines():
        fields = [part.strip() for part in line.split("|")[1:-1]]
        if fields and fields[0].isdigit():
            parsed[int(fields[0])] = tuple(map(int, fields[1:4]))
    return parsed


def parse_marks(path: Path) -> dict:
    counts = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        dscp_str = line.split("\t")[0]
        if not dscp_str:
            continue
        dscp = int(dscp_str)
        counts[dscp] = counts.get(dscp, 0) + 1
    return dict(sorted(counts.items()))


def run_report(work: Path, mode: str) -> None:
    rx_lines = [line for line in (work / "rx.log").read_text().splitlines() if line.startswith("{")]
    if not rx_lines:
        raise RuntimeError(f"Missing rx JSON summary in {work / 'rx.log'}")
    rx_summary = json.loads(rx_lines[-1])
    before = parse_queues((work / "queues-before.txt").read_text())
    after = parse_queues((work / "queues.txt").read_text())
    if before.keys() != after.keys() or not {0, 3, 4} <= after.keys():
        raise RuntimeError(f"Unexpected TDMA queue table: {after}")
    delta = {str(q): [b - a for a, b in zip(before[q], after[q])] for q in sorted(after)}

    tx_marks = parse_marks(work / "tx.marks")
    rx_marks = parse_marks(work / "rx.marks")

    if mode == "dscp":
        for dscp in (48, 34, 26):
            if tx_marks.get(dscp, 0) == 0 or rx_marks.get(dscp, 0) == 0:
                raise RuntimeError(f"Expected DSCP {dscp} on both ends: tx={tx_marks}, rx={rx_marks}")
        if delta["3"][0] == 0 or delta["2"][0] == 0 or delta["0"][0] == 0:
            raise RuntimeError(f"Expected Queue 3, Queue 2, and Queue 0 enqueue activity: delta={delta}")
    else:
        if tx_marks.get(0, 0) == 0 or rx_marks.get(0, 0) == 0:
            raise RuntimeError(f"Expected DSCP 0 baseline marks: tx={tx_marks}, rx={rx_marks}")
        if delta["0"][0] < 400 or delta["2"][0] != 0 or delta["3"][0] >= 20:
            raise RuntimeError(f"Expected data in Queue 0 only in baseline: delta={delta}")

    report = {
        "mode": mode,
        "burst_per_topic": BURST,
        "delivery": rx_summary,
        "tx_dscp_packets": tx_marks,
        "rx_dscp_packets": rx_marks,
        "queue_delta_enqueued_dequeued_overflow": delta,
    }
    print(json.dumps(report, indent=2), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("tx", "rx"):
        p = sub.add_parser(name)
        p.add_argument("--qos", default="/probe/rs_six_topic_qos.xml")
        p.add_argument("--types", default="/probe/rs_six_topic_types.xml")
    rep = sub.add_parser("report")
    rep.add_argument("--work", required=True)
    rep.add_argument("--mode", choices=("baseline", "dscp"), required=True)
    args = parser.parse_args()
    if args.cmd == "tx":
        run_tx(args.qos, args.types)
    elif args.cmd == "rx":
        run_rx(args.qos, args.types)
    else:
        run_report(Path(args.work), args.mode)


if __name__ == "__main__":
    main()
