"""Decode standard RTPS reliability submessages from our closed private capture."""

import json
import subprocess


def is_builtin_writer(entity_id):
    return bool(int(entity_id[-2:], 16) & 0x80)


def decode(payload, timestamp):
    if payload[:4] != b"RTPS":
        return []
    prefix = payload[8:20].hex()
    destination = None
    result = []
    at = 20
    while at + 4 <= len(payload):
        kind, flags = payload[at:at + 2]
        endian = "little" if flags & 1 else "big"
        size = int.from_bytes(payload[at + 2:at + 4], endian)
        end = at + 4 + size if size else len(payload)
        if end > len(payload) or end <= at:
            raise ValueError("Malformed RTPS submessage")
        body = payload[at + 4:end]

        def number(start, size=4):
            return int.from_bytes(body[start:start + size], endian)

        def sequence(start):
            return (int.from_bytes(body[start:start + 4], endian, signed=True) << 32
                    ) + number(start + 4)

        if kind == 0x0e and len(body) >= 12:
            destination = body[:12].hex()
        if kind in (6, 7, 0x14, 0x15, 0x1b) and len(body) >= 20:
            row = {"time": timestamp, "prefix": prefix, "destination": destination,
                   "kind": {6: "ACKNACK", 7: "HEARTBEAT", 0x1b: "HEARTBEAT",
                            0x14: "DATA", 0x15: "DATA"}[kind], "submessage_id": kind,
                   "flags": flags}
            if kind in (6, 7, 0x1b):
                row["final"] = bool(flags & 2)
            offset = 4 if kind in (0x14, 0x15) else 0
            row.update(reader=body[offset:offset + 4].hex(),
                       writer=body[offset + 4:offset + 8].hex())
            if kind == 6:
                bits = number(16)
                if bits > 256 or len(body) < 24 + 4 * ((bits + 31) // 32):
                    raise ValueError("Invalid ACKNACK sequence-number bitmap")
                base = sequence(8)
                row.update(base=base, num_bits=bits,
                           missing=[base + bit for bit in range(bits)
                                    if number(20 + (bit // 32) * 4) &
                                    (1 << (31 - bit % 32))],
                           count=number(20 + 4 * ((bits + 31) // 32)))
            elif kind == 7 and len(body) >= 28:
                row.update(first=sequence(8), last=sequence(16), count=number(24))
            elif kind == 0x1b and len(body) >= 44:
                row.update(first=sequence(24), last=sequence(32), count=number(40),
                           first_batch=sequence(8), last_batch=sequence(16))
            elif kind in (0x14, 0x15):
                row["sequence"] = sequence(20 if kind == 0x14 else 12)
            result.append(row)
        at = end
    return result


def capture_rows(path):
    command = ["tshark", "-r", str(path), "-Y", "rtps", "-T", "fields",
               "-e", "frame.time_epoch", "-e", "udp.payload"]
    output = subprocess.run(command, check=True, capture_output=True, text=True)
    rows = []
    for line in output.stdout.splitlines():
        timestamp, payload = line.split("\t")
        rows.extend(decode(bytes.fromhex(payload.replace(":", "")), float(timestamp)))
    return rows


def analyze(capture, manifest, output):
    rows = capture_rows(capture)
    source = manifest["endpoints"]["A"]["writers"]["Event"]
    target = manifest["endpoints"]["B"]["writers"]["Event"]
    writer = source[-8:]
    acks = [r for r in rows if r["kind"] == "ACKNACK" and r["writer"] == writer
            and r["prefix"] == target[:24] and r["destination"] == source[:24]]
    heartbeats = [r for r in rows if r["kind"] == "HEARTBEAT"
                  and r["writer"] == writer and r["prefix"] == source[:24]
                  and r["destination"] in (None, "0" * 24, target[:24])]
    data = [r for r in rows if r["kind"] == "DATA" and r["writer"] == writer
            and r["prefix"] == source[:24]]
    preemptive = []
    for row in acks:
        prior = [hb for hb in heartbeats if hb["time"] <= row["time"]]
        row["prior_heartbeat_count"] = len(prior)
        row["previous_heartbeat_delta_s"] = row["time"] - prior[-1]["time"] if prior else None
        if row["base"] == 0:
            preemptive.append(row)
    repair = [r for r in acks if 2 in r["missing"] and r["prior_heartbeat_count"] > 0]
    sequence_two = [r for r in data if r["sequence"] == 2]
    prefixes = {node: entry["writers"]["Event"][:24]
                for node, entry in manifest["endpoints"].items()}
    all_wan = [r for r in rows if r["kind"] == "ACKNACK"
               and r["prefix"] in prefixes.values()
               and r["destination"] in prefixes.values()
               and r["prefix"] != r["destination"]
               and int(r["writer"][-2:], 16) in (2, 3)]
    result = {
        "variant": manifest["variant"], "writer_guid": source, "reader_gateway_prefix": target[:24],
        "acknack_count": len(acks), "base_zero_count": len(preemptive),
        "base_zero_before_any_heartbeat": sum(r["prior_heartbeat_count"] == 0 for r in preemptive),
        "heartbeat_count": len(heartbeats), "data_count": len(data),
        "missing_sequence_2_requests": len(repair), "sequence_2_wire_count": len(sequence_two),
        "repair_request_precedes_sequence_2": bool(repair and sequence_two and
                                                 repair[0]["time"] < sequence_two[0]["time"]),
        "all_wan_user_acknack_count": len(all_wan),
        "all_wan_user_base_zero_count": sum(r["base"] == 0 for r in all_wan),
        "all_wan_acknacks": all_wan,
        "acknacks": acks, "heartbeats": heartbeats, "data": data,
    }
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result
