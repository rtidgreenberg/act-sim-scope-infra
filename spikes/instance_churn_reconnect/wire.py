"""Bounded capture and packet-packing measurements for the isolated native spike."""

from collections import Counter
import math
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import tempfile
import time


def event_submessages(payload, excluded_writer_id):
    """Read Source user DATA, excluding its separately identified Status writer."""
    if payload[:4] != b"RTPS":
        return []
    result = []
    position = 20
    while position + 4 <= len(payload):
        kind, flags = payload[position:position + 2]
        endian = "little" if flags & 1 else "big"
        size = int.from_bytes(payload[position + 2:position + 4], endian)
        end = position + 4 + size if size else len(payload)
        body = payload[position + 4:end]
        writer_id = body[8:12].hex()
        if (kind in (0x14, 0x15, 0x16) and len(body) >= 20
                and body[11] in (2, 3) and writer_id != excluded_writer_id):
            disposed = False
            if flags & 2:
                inline = 4 + int.from_bytes(body[2:4], endian)
                while inline + 4 <= len(body):
                    parameter = int.from_bytes(body[inline:inline + 2], endian)
                    length = int.from_bytes(body[inline + 2:inline + 4], endian)
                    if parameter == 1:
                        break
                    value = body[inline + 4:inline + 4 + length]
                    if parameter == 0x71 and len(value) == 4:
                        disposed = bool(int.from_bytes(value, "big") & 1)
                    inline += 4 + ((length + 3) // 4) * 4
            sequence = body[20:28] if kind == 0x14 else body[12:20]
            result.append((writer_id + ":" + sequence.hex(), kind == 0x16, disposed))
        position = end
    return result


def packet_category(payload):
    if payload[:4] != b"RTPS":
        raise ValueError("Expected RTPS packet")
    if len(payload) < 20:
        return "prefixless_rtps"
    discovery, data, control = False, False, False
    position = 20
    while position + 4 <= len(payload):
        kind, flags = payload[position:position + 2]
        endian = "little" if flags & 1 else "big"
        size = int.from_bytes(payload[position + 2:position + 4], endian)
        end = position + 4 + size if size else len(payload)
        if end > len(payload) or end <= position:
            raise ValueError("Truncated RTPS submessage")
        body = payload[position + 4:end]
        if kind in (0x14, 0x15, 0x16) and len(body) >= 12:
            entity_kind = body[11]
            discovery |= bool(entity_kind & 0x80)
            data |= entity_kind in (2, 3)
        elif kind in (6, 7, 8, 0x12, 0x13, 0x1b) and len(body) >= 8:
            entity_kind = body[7]
            discovery |= bool(entity_kind & 0x80)
            control |= entity_kind in (2, 3)
        position = end
    if discovery:
        return "mixed_builtin_user" if data or control else "discovery"
    return "user_data" if data else "user_control" if control else "other"


class WireCapture:
    def __init__(self, path, domain, duration):
        if (os.environ.get("ACT_INSTANCE_CHURN_ISOLATED") != "1"
                or not Path("/.dockerenv").exists()
                or {name for _, name in socket.if_nameindex()} != {"lo"}):
            raise RuntimeError("Capture requires an owned --network none container")
        if not 0 <= domain <= 232:
            raise ValueError("Capture domain must be <=232")
        self.path = Path(path)
        self.temporary = tempfile.TemporaryDirectory(prefix="churn-wire-")
        self.capture_path = Path(self.temporary.name) / "capture.pcapng"
        self.log = self.path.with_suffix(".capture.txt").open("w+")
        port = 7400 + 250 * domain
        self.process = subprocess.Popen([
            "dumpcap", "-q", "-i", "lo", "-f", f"udp portrange {port}-{port + 249}",
            "-a", f"duration:{math.ceil(duration)}", "-w", str(self.capture_path)],
            stdout=subprocess.DEVNULL, stderr=self.log)
        until = time.monotonic() + 10
        while time.monotonic() < until:
            if self.process.poll() is not None:
                self.close()
                raise RuntimeError(f"Capture exited before readiness: {self.path}")
            if self.capture_path.exists() and self.capture_path.stat().st_size > 0:
                return
            time.sleep(0.01)
        self.close()
        raise TimeoutError(f"Capture did not become ready: {self.path}")

    def close(self):
        forced = False
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
                forced = True
        self.log.close()
        try:
            if self.capture_path.exists():
                shutil.copyfile(self.capture_path, self.path)
        finally:
            self.temporary.cleanup()
        if forced:
            raise RuntimeError(f"Capture required forced termination: {self.path}")
        if self.process.returncode != 0:
            raise RuntimeError(f"Capture failed ({self.process.returncode}): {self.path}")

    def summarize(self, result):
        if result["effective_qos"]["batch_enabled"]:
            raise NotImplementedError("Batched RTPS needs separate packet-decoder qualification")
        guid = result["effective_qos"]["writer_guid"].lower().replace(":", "")
        status_id = result["effective_qos"]["status_writer_guid"][-8:].lower()
        offset = result["wall_clock_offset_s"]
        phases = {}
        all_phases = {}
        prefixes = {result["effective_qos"][name].lower().replace(":", "")[:24]
                    for name in ("writer_guid", "ack_writer_guid")}
        errors = tempfile.TemporaryFile(mode="w+")
        command = ["tshark", "-r", str(self.path), "-Y", "rtps", "-T", "fields",
                   "-e", "frame.time_epoch", "-e", "frame.len", "-e", "udp.length",
                   "-e", "udp.payload"]
        try:
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True) as process:
                for line in process.stdout:
                    epoch, frame_length, udp_length, raw = line.rstrip("\n").split("\t")
                    payload = bytes.fromhex(raw.replace(":", ""))
                    prefix = payload[8:20].hex()
                    if prefix not in prefixes and len(payload) >= 20:
                        continue
                    at = float(epoch) - offset
                    if at > result["metrics"][-1]["at"]:
                        continue
                    if at < result["started"]:
                        phase = "startup"
                    elif result["outage_started"] is None or at < result["outage_started"]:
                        phase = "healthy"
                    elif at < result["outage_finished"]:
                        phase = "blackout"
                    elif at < result["stopped_producing"]:
                        phase = "recovery_producing"
                    else:
                        phase = "drain"
                    totals = all_phases.setdefault(phase, {
                        "packets": 0, "udp_bytes": 0, "frame_bytes": 0, "categories": {}})
                    category = totals["categories"].setdefault(
                        packet_category(payload), {"packets": 0, "udp_bytes": 0})
                    totals["packets"] += 1
                    totals["udp_bytes"] += int(udp_length)
                    totals["frame_bytes"] += int(frame_length)
                    category["packets"] += 1
                    category["udp_bytes"] += int(udp_length)
                    if prefix != guid[:24] or phase == "startup":
                        continue
                    messages = event_submessages(payload, status_id)
                    if not messages:
                        continue
                    values = phases.setdefault(phase, {
                        "packets": 0, "frame_bytes": 0, "udp_bytes": 0,
                        "data_submessages": 0, "data_frag_submessages": 0,
                        "dispose_submessages": 0, "packets_with_multiple_disposes": 0,
                        "max_disposes_per_packet": 0, "max_data_submessages_per_packet": 0,
                        "sequences": set(), "disposes": set(), "udp_length_histogram": Counter(),
                        "event_packet_bytes_per_second": Counter(),
                    })
                    values["packets"] += 1
                    values["frame_bytes"] += int(frame_length)
                    values["udp_bytes"] += int(udp_length)
                    values["udp_length_histogram"][int(udp_length)] += 1
                    values["event_packet_bytes_per_second"][math.floor(at)] += int(udp_length)
                    disposes = sum(disposed for _, _, disposed in messages)
                    values["dispose_submessages"] += disposes
                    values["packets_with_multiple_disposes"] += disposes > 1
                    values["max_disposes_per_packet"] = max(values["max_disposes_per_packet"], disposes)
                    values["max_data_submessages_per_packet"] = max(
                        values["max_data_submessages_per_packet"], len(messages))
                    for sequence, fragment, disposed in messages:
                        values["data_frag_submessages" if fragment else "data_submessages"] += 1
                        values["sequences"].add(sequence)
                        if disposed:
                            values["disposes"].add(sequence)
                if process.wait() != 0:
                    errors.seek(0)
                    raise RuntimeError("tshark failed: " + errors.read())
        finally:
            errors.close()
        included_topics = ["Event", "Contacts"] if result.get("contract") == "three_topic" else ["Event"]
        if not phases:
            raise AssertionError("Capture contains no attributed application DATA; check writer GUID/parser")
        for values in phases.values():
            values["unique_sequences"] = len(values.pop("sequences"))
            values["unique_disposes"] = len(values.pop("disposes"))
            values["mean_udp_bytes"] = values["udp_bytes"] / values["packets"]
            values["max_event_packet_udp_mbps_one_second"] = (
                max(values.pop("event_packet_bytes_per_second").values()) * 8 / 1_000_000)
        return {"capture": self.path.name, "phases": phases, "included_topics": included_topics,
                "all_rtps": {
                    "phases": all_phases,
                    "scope": "Both owned participant prefixes, Domain 201 loopback UDP bytes. "
                             "Startup includes discovery and initial state; categories are exclusive "
                             "packet classes, with mixed builtin/user packets separate. Discovery "
                             "recognizes standard/RTI builtin entity flags in DATA/FRAG and reliability "
                             "headers, including captured RTI 0x1b. Prefixless RTPS packets and unknown "
                             "vendor messages are separate categories. Capture ends before endpoint "
                             "teardown; dropped OUTPUT "
                             "packets are absent. Not RF bytes or a total bandwidth reservation."},
                "scope": "Source " + "/".join(included_topics) + " DATA-bearing loopback packets only; includes repairs, "
                         "not total DDS/control/RF traffic; outbound OUTPUT-dropped packets are absent"}
