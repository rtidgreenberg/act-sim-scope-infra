"""Packet accounting for the closed domain-102 comparison."""

from collections import Counter
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "preemptive_acknack"))
from wire import decode


SEDP = {"000003c2": "publication", "000004c2": "subscription"}


def packets(path):
    fields = ("frame.time_epoch", "ip.len", "udp.dstport", "ip.dsfield", "udp.payload")
    command = ["tshark", "-r", str(path), "-Y", "rtps", "-T", "fields"]
    for field in fields:
        command.extend(("-e", field))
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    parsed = []
    for line in result.stdout.splitlines():
        stamp, length, destination, dsfield, payload = line.split("\t")
        raw = bytes.fromhex(payload.replace(":", ""))
        parsed.append(dict(time=float(stamp), ip_bytes=int(length),
                           discovery=int(destination) % 2 == 0,
                           dsfield=int(dsfield, 16), payload=raw,
                           rows=decode(raw, float(stamp))))
    return parsed


def summarize(values, phase, prefix=None):
    selected = [packet for packet in values if phase["start"] <= packet["time"] < phase["end"]
                and (prefix is None or packet["payload"][8:20].hex() == prefix)]
    discovery = [packet for packet in selected if packet["discovery"]]
    sedp_packets = [packet for packet in discovery if any(
        row["writer"] in SEDP for row in packet["rows"])]
    records = {kind: set() for kind in SEDP.values()}
    submessages = Counter()
    for packet in selected:
        for row in packet["rows"]:
            builtin = bool(int(row["writer"][-2:], 16) & 0x80)
            submessages[("builtin_" if builtin else "user_") + row["kind"]] += 1
            if row["kind"] == "DATA" and row["writer"] in SEDP:
                records[SEDP[row["writer"]]].add((row["prefix"], row["sequence"]))
    return dict(packets=len(selected), ip_bytes=sum(p["ip_bytes"] for p in selected),
                discovery_packets=len(discovery),
                discovery_ip_bytes=sum(p["ip_bytes"] for p in discovery),
                sedp_packets=len(sedp_packets),
                sedp_ip_bytes=sum(p["ip_bytes"] for p in sedp_packets),
                unique_publication_records=len(records["publication"]),
                unique_subscription_records=len(records["subscription"]),
                submessages=dict(submessages))
