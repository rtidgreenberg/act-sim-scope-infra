#!/usr/bin/env python3
"""Attribute recovery stalls to nonempty standard ACKNACKs in closed private captures."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "instance_churn_reconnect"))
from wire import event_submessages


def repair_requests(payload):
    requests = []
    position = 20
    while position + 4 <= len(payload):
        kind, flags = payload[position:position + 2]
        endian = "little" if flags & 1 else "big"
        size = int.from_bytes(payload[position + 2:position + 4], endian)
        end = position + 4 + size if size else len(payload)
        body = payload[position + 4:end]
        if kind == 6 and len(body) >= 24 and body[7] in (2, 3):
            bits = int.from_bytes(body[16:20], endian)
            if bits > 256:
                raise ValueError(f"Unexpected standard ACKNACK bitmap length: {bits}")
            if bits:
                words = (bits + 31) // 32
                if len(body) < 24 + 4 * words:
                    raise ValueError("Truncated standard ACKNACK bitmap")
                missing = False
                for index in range(words):
                    word = int.from_bytes(body[20 + 4 * index:24 + 4 * index], endian)
                    used = min(32, bits - 32 * index)
                    # RTPS bitmap positions start at each word's most significant bit.
                    missing |= bool(word & (((1 << used) - 1) << (32 - used)))
                if missing:
                    high = int.from_bytes(body[8:12], endian, signed=True)
                    low = int.from_bytes(body[12:16], endian)
                    requests.append((body[4:8].hex(), bits, (high << 32) + low))
        position = end
    return requests


def analyze(folder):
    manifest = json.loads((folder / "manifest.json").read_text())
    results = json.loads((folder / "results.json").read_text())
    result = next((r for r in results if r.get("outage")), None)
    if result is None:
        raise ValueError(f"Report has no outage trial: {folder}")
    capture = manifest["capture_directory"]
    if not capture or "wire" not in result:
        raise ValueError(f"Report has no qualified capture: {folder}")
    path = ROOT / "debug/pcap" / Path(capture).parent.name / Path(capture).name / result["wire"]["capture"]
    origin = result["outage_finished"] + result["wall_clock_offset_s"]
    prefix = result["effective_qos"]["writer_guid"].lower().replace(":", "")[:24]
    status_id = result["effective_qos"]["status_writer_guid"][-8:].lower()
    requests, packets = defaultdict(list), defaultdict(Counter)
    with tempfile.TemporaryFile(mode="w+") as errors:
        command = ["tshark", "-r", str(path), "-Y", "rtps", "-T", "fields",
                   "-e", "frame.time_epoch", "-e", "udp.length", "-e", "udp.payload"]
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True) as process:
            for line in process.stdout:
                epoch, length, raw = line.rstrip("\n").split("\t")
                at = float(epoch) - origin
                if not 0 <= at < 30:
                    continue
                payload = bytes.fromhex(raw.replace(":", ""))
                if payload[8:20].hex() == prefix:
                    data = event_submessages(payload, status_id)
                    if data:
                        bucket = packets[int(at)]
                        bucket["packets"] += 1
                        bucket["udp_bytes"] += int(length)
                        bucket["data_submessages"] += len(data)
                        bucket["dispose_submessages"] += sum(item[2] for item in data)
                else:
                    for writer, bits, base in repair_requests(payload):
                        requests[writer].append({"at": at, "bits": bits, "base": base})
            if process.wait() != 0:
                errors.seek(0)
                raise RuntimeError("tshark failed: " + errors.read())
    if not requests:
        raise AssertionError(f"No nonempty application ACKNACKs found: {path}")
    return {
        "report": folder.name, "recovery_s": result["first_recovery_convergence_s"],
        "scope": "first 30 s after restoration; nonempty standard user-writer ACKNACKs "
                 "from Receiver; Source application DATA excluding Status; no RF claim",
        "writers": {writer: {
            "request_count": len(rows), "first_request_s": rows[0]["at"],
            "last_request_s": rows[-1]["at"],
            "gaps_gt_1s": [{"from": a["at"], "to": b["at"], "seconds": b["at"] - a["at"]}
                           for a, b in zip(rows, rows[1:]) if b["at"] - a["at"] > 1],
            "requests": rows,
        } for writer, rows in requests.items()},
        "source_data_by_second": {str(k): dict(v) for k, v in sorted(packets.items())},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", nargs="*", default=[])
    parser.add_argument("--comparison", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    folders = [ROOT / "debug/test_reports" / Path(name).name for name in args.reports]
    if args.comparison:
        entries = json.loads((args.comparison / "comparison.json").read_text())
        folders.extend(ROOT / "debug/test_reports" / Path(entry["report"]).name
                       for entry in entries if entry["scenario"] == "outage")
    if not folders:
        parser.error("No outage reports selected; provide --reports or an outage --comparison")
    try:
        results = [analyze(folder) for folder in dict.fromkeys(folders)]
    except ValueError as error:
        parser.error(str(error))
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    for row in results:
        print(json.dumps({key: row[key] for key in ("report", "recovery_s")} | {
            "writers": {writer: {key: data[key] for key in (
                "request_count", "gaps_gt_1s")} for writer, data in row["writers"].items()}}),
            flush=True)


if __name__ == "__main__":
    main()
