"""Compare two equal synthetic UDP streams on a private TDMA link."""

import argparse
import json
import socket
import statistics
import struct
import time
from pathlib import Path

PORT = 31999
PAIRS = 96
CASES = ((0, 0), (8, 0), (0, 8))
HEADER = struct.Struct("!BBHQ")


def send():
    with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
        sock.bind(("fd42:88::1", 0))
        for case, marks in enumerate(CASES):
            for seq in range(PAIRS):
                for lane in ((0, 1) if seq % 2 == 0 else (1, 0)):
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_TCLASS, marks[lane] << 2)
                    payload = HEADER.pack(case, lane, seq, time.monotonic_ns())
                    sock.sendto(payload.ljust(900, b"\0"), ("fd42:88::2", PORT))
                time.sleep(0.001)
            time.sleep(3)


def receive():
    with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_RECVTCLASS, 1)
        sock.bind(("fd42:88::2", PORT))
        sock.settimeout(12)
        print("Ready", flush=True)
        packets = {}
        while len(packets) < len(CASES) * 2 * PAIRS:
            data, ancillary, _, peer = sock.recvmsg(900, socket.CMSG_SPACE(4))
            case, lane, seq, sent = HEADER.unpack_from(data)
            key = (case, lane, seq)
            if peer[0] != "fd42:88::1" or key in packets or seq >= PAIRS:
                raise RuntimeError(f"Unexpected packet {key} from {peer[0]}")
            classes = [struct.unpack("i", value)[0] >> 2 for level, kind, value in ancillary
                       if (level, kind) == (socket.IPPROTO_IPV6, socket.IPV6_TCLASS)]
            if classes != [CASES[case][lane]]:
                raise RuntimeError(f"Unexpected DSCP at receiver: {key}: {classes}")
            packets[key] = (len(packets), (time.monotonic_ns() - sent) / 1e6)
    rows = []
    for case, marks in enumerate(CASES):
        order = sorted((rank, lane) for (c, lane, _), (rank, _) in packets.items() if c == case)
        first_half = [lane for _, lane in order[:PAIRS]]
        rows.append([{
            "stream": chr(65 + lane), "dscp": mark, "received": PAIRS,
            "first_half": first_half.count(lane),
            "median_ms": round(statistics.median(
                delay for (c, stream, _), (_, delay) in packets.items()
                if (c, stream) == (case, lane)), 1),
        } for lane, mark in enumerate(marks)])
    if any(not 40 <= row["first_half"] <= 56 for row in rows[0]):
        raise RuntimeError(f"Baseline unfair: {rows[0]}")
    if any(trial[lane]["first_half"] < 72 or
           trial[lane]["median_ms"] >= trial[1 - lane]["median_ms"]
           for trial, lane in ((rows[1], 0), (rows[2], 1))):
        raise RuntimeError(f"Delivery advantage did not follow the mark: {rows[1:]}")
    print(json.dumps(rows), flush=True)


def report(work):
    for side in ("tx", "rx"):
        counts = {}
        for line in (work / f"{side}.marks").read_text().splitlines():
            mark, payload = line.split("\t")
            case, lane, seq, _ = HEADER.unpack_from(bytes.fromhex(payload))
            dscp = int(mark)
            if not (0 <= seq < PAIRS) or dscp != CASES[case][lane]:
                raise RuntimeError(f"Unexpected mark in {side} capture: {case}/{lane}/{seq}")
            key = (case, lane, seq)
            if key in counts:
                raise RuntimeError(f"Duplicate in {side} capture: {key}")
            counts[key] = dscp
        if len(counts) != len(CASES) * 2 * PAIRS:
            raise RuntimeError(f"Incomplete {side} capture: {len(counts)} packets")
    queues = (work / "queues.txt").read_text().strip()
    before = (work / "queues-before.txt").read_text().strip()
    def counts(table):
        return {int(fields[0]): tuple(map(int, fields[1:4]))
                for line in table.splitlines()
                if (fields := [part.strip() for part in line.split("|")[1:-1]])
                and fields[0].isdigit()}

    start, end = counts(before), counts(queues)
    if start.keys() != end.keys() or not {0, 1} <= end.keys():
        raise RuntimeError(f"Missing TDMA queue counters: {queues}")
    delta = {q: tuple(b - a for a, b in zip(start[q], end[q])) for q in end}
    # Queue 0 can also carry link-local IPv6 housekeeping on this interface.
    if (delta[1] != (2 * PAIRS, 2 * PAIRS, 0)
            or min(delta[0][:2]) < 4 * PAIRS or delta[0][2]
            or any(any(values) for q, values in delta.items() if q not in (0, 1))):
        raise RuntimeError(f"Unexpected TDMA queue deltas: {delta}")
    lines = (work / "priority.log").read_text().splitlines()
    print(json.dumps({"delivery": json.loads(lines[-1]),
                      "queue_delta_enqueued_dequeued_overflow": delta, "queue_counters_before": before,
                      "queue_counters_after": queues}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("tx", "rx", "report"))
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    if args.role == "report":
        if args.work is None:
            parser.error("--work is required for report")
        report(args.work)
    elif args.role == "tx":
        send()
    else:
        receive()
