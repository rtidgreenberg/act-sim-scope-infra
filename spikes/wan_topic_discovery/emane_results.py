import json
from pathlib import Path
import re
import statistics
import sys


def read_packets(path):
    packets = {}
    for row in path.read_text().splitlines():
        stamp, dscp, payload = row.split("\t")
        markers = re.findall(rb"RFTEST-([CAB])-([AB])-(\d{4})", bytes.fromhex(payload))
        if len(markers) != 1 or markers[0] in packets:
            raise RuntimeError(f"Expected one unique sample per packet in {path}")
        packets[markers[0]] = (float(stamp), int(dscp))
    return packets


def main(work):
    tx, rx = (read_packets(work / f"{side}.rows") for side in ("tx", "rx"))
    delivered = {tuple(m) for m in re.findall(rb"^RFTEST-([CAB])-([AB])-(\d{4})$", (work / "rx.log").read_bytes(), re.M)}
    if not rx.keys() <= delivered <= tx.keys():
        raise RuntimeError("Receiver capture, DDS receipt, and sender capture disagree")
    trials = []
    for case, expected_count in ((b"C", 60), (b"A", 1000), (b"B", 1000)):
        for lane in (b"A", b"B"):
            expected_dscp = 46 if lane == (b"A" if case == b"C" else case) else 0
            keys = {(case, lane, f"{i:04}".encode()) for i in range(expected_count)}
            received = keys & rx.keys()
            if not keys <= tx.keys() or not received:
                raise RuntimeError(f"Incomplete sender capture or no receiver data: {case}/{lane}")
            if any(tx[k][1] != expected_dscp for k in keys) or any(rx[k][1] != expected_dscp for k in received):
                raise RuntimeError(f"DSCP did not match {expected_dscp} at both ends: {case}/{lane}")
            if case == b"C" and received != keys:
                raise RuntimeError(f"Loss in uncongested control: {lane}")
            delay = sorted((rx[k][0] - tx[k][0]) * 1000 for k in received)
            trials.append(dict(case=case.decode(), lane=lane.decode(), dscp=expected_dscp,
                               sent=len(keys), delivered=len(keys & delivered),
                               captured_at_receiver=len(received),
                               median_ms=round(statistics.median(delay), 2),
                               p95_ms=round(delay[(95 * len(delay) + 99) // 100 - 1], 2)))
    if not all(t["delivered"] < t["sent"] for t in trials if t["case"] != "C"):
        raise RuntimeError("Overload did not produce observable loss")
    print(json.dumps({"trials": trials, "dds_samples": len(delivered),
                      "dds_samples_missing_from_receiver_capture": len(delivered - rx.keys())}, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
