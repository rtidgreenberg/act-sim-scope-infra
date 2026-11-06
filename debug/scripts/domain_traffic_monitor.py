#!/usr/bin/env python3
"""Continuous DDS domain-traffic monitor using tshark on a selected interface.

Captures UDPv4 RTPS and all constituent IPv4 fragments on the selected interface,
classifies physical frame bytes by domain and protocol/data role, then POSTs
interval summaries to the dashboard. Topic roles use domain + participant GUID +
writer entity ID learned from SEDP; late unknown writers stay explicitly unknown.
EMANE MAC counters have independent sampling timestamps and accounting units.

This design avoids creating a DDS participant (which would add its own discovery
traffic to the capture) — pure tshark capture + HTTP push.

Port-mapping formulas (OMG interoperable defaults):
  PB=7400, DG=250, PG=2, d0=0, d1=10, d2=1, d3=11
  SPDP multicast meta:  PB + DG*D + d0         = 7400 + 250*D
  User-data multicast:  PB + DG*D + d2         = 7401 + 250*D
  Discovery unicast:    PB + DG*D + d1 + PG*P  = 7410 + 250*D + 2*P
  User-data unicast:    PB + DG*D + d3 + PG*P  = 7411 + 250*D + 2*P

  Given a port, domain = (port - PB) // DG  (if port falls in a valid slot).

Entity-ID classification (last byte = entity kind):
  0xC2 = builtin writer with key (SPDP, SEDP, participant-message) -> discovery
  0xC7 = builtin reader with key -> discovery
  0x02 = user-defined writer with key -> data
  0x03 = user-defined writer without key -> data

Requires: tshark (unprivileged on this VM via cap_net_raw on dumpcap).

Usage:
    python3 debug/scripts/domain_traffic_monitor.py [--domains 200] [--interval 2]
    python3 debug/scripts/domain_traffic_monitor.py --help

When launched by the mesh harness, its log is written to debug/logs/mesh/traffic_monitor.log.
Traffic samples are also appended to debug/logs/network_monitor/traffic_stats.jsonl.
"""

import argparse
import json
import os
import re
import select
import signal
import subprocess
import sys
import threading
import time
import tempfile
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional
from traffic_accounting import (FrameAccounting, TX_CLASSES, classify_packet,
                                empty_counters, tshark_fields)
from rtps_delivery import WireCapture

# ── RTPS port-mapping constants (OMG interoperable defaults) ──
PB = 7400   # port base
DG = 250    # domain-ID gain
PG = 2      # participant-ID gain
D0 = 0      # meta multicast offset
D1 = 10     # meta unicast offset
D2 = 1      # user multicast offset
D3 = 11     # user unicast offset

# ── RTPS entity-kind byte classification ──
DISCOVERY_ENTITY_KINDS = {0xc2, 0xc7, 0xc3, 0xc4}   # builtin writers/readers
DATA_ENTITY_KINDS = {0x02, 0x03, 0x04, 0x07}          # user-defined writers/readers
DISCOVERY_WRITER_ENTITY_KINDS = {0xc2, 0xc3}
RELIABILITY_SUBMESSAGE_IDS = {0x06, 0x07, 0x08, 0x12, 0x13}
DEFAULT_OUTPUT = (Path(__file__).resolve().parents[2] / "debug" / "logs"
                  / "network_monitor" / "traffic_stats.jsonl")


def port_to_domain(port: int) -> Optional[int]:
    """Derive the DDS domain ID from a UDP port, or None if it doesn't match
    the standard RTPS port mapping."""
    if port < PB:
        return None
    offset = port - PB
    domain = offset // DG
    remainder = offset % DG
    # Valid remainders: d0=0 (meta mcast), d2=1 (user mcast),
    # d1+2*P = 10,12,14,... (meta unicast), d3+2*P = 11,13,15,... (user unicast)
    if remainder == D0 or remainder == D2:
        return domain
    if remainder >= D1:
        # Could be meta-unicast (even >= 10) or user-unicast (odd >= 11)
        if remainder >= D1:
            return domain
    return None


def _field_values(value: str) -> List[str]:
    return [entry.strip() for entry in value.split(",") if entry.strip()]


def has_writer_entity(entity_ids: str, entity_kinds: set[int]) -> bool:
    """Return whether a frame contains a writer entity in entity_kinds."""
    for entity_id_hex in _field_values(entity_ids):
        try:
            kind_byte = int(entity_id_hex.replace(":", "").lower()[-2:], 16)
        except ValueError:
            continue
        if kind_byte in entity_kinds:
            return True
    return False


def interface_ipv4_addresses(interface: str) -> set[str]:
    """Return the IPv4 addresses assigned to the monitored interface."""
    try:
        result = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", interface],
                                text=True, capture_output=True, timeout=3, check=True)
    except (OSError, subprocess.SubprocessError):
        return set()
    return set(re.findall(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/", result.stdout))


class TrafficAccumulator:
    """Accumulates packet counts/bytes per domain, per traffic class."""

    def __init__(self):
        self.lock = threading.Lock()
        # domain_id -> byte/packet counters for mutually exclusive frame classes.
        self.stats: Dict[int, Dict[str, int]] = {}
        self.capture_status = "starting"
        self.capture_error = None
        self.capture_restarts = 0
        self.capture_invalid_rows = 0
        self.capture_gap_at_ms = 0
        self.orphan_fragment_packets = 0
        self.orphan_fragment_bytes = 0

    def _ensure_domain(self, domain_id: int):
        if domain_id not in self.stats:
            self.stats[domain_id] = empty_counters()

    def add_packet(self, domain_id: int, frame_len: int, traffic_class: str,
                   is_local_transmit: bool, tx_class: str, packet_count: int = 1,
                   incomplete: bool = False):
        with self.lock:
            self._ensure_domain(domain_id)
            s = self.stats[domain_id]
            s["total_packets"] += packet_count
            s["total_bytes"] += frame_len
            s[f"{traffic_class}_packets"] += packet_count
            s[f"{traffic_class}_bytes"] += frame_len
            if not is_local_transmit:
                s["rx_packets"] += packet_count
                s["rx_bytes"] += frame_len
            else:
                s["tx_packets"] += packet_count
                s["tx_bytes"] += frame_len
                s[f"{tx_class}_tx_packets"] += packet_count
                s[f"{tx_class}_tx_bytes"] += frame_len
                if tx_class in {"application", "infrastructure"}:
                    s["data_tx_packets"] += packet_count
                    s["data_tx_bytes"] += frame_len
            if incomplete:
                s["incomplete_fragment_packets"] += packet_count
                s["incomplete_fragment_bytes"] += frame_len

    def drain(self) -> Dict[int, Dict[str, int]]:
        """Return and reset all accumulated stats."""
        with self.lock:
            snapshot = self.stats
            self.stats = {}
            return snapshot


def build_port_filter(domain_ids: List[int], max_participants: int = 32) -> str:
    """Capture domain port ranges and noninitial fragments for reassembly."""
    if not domain_ids or any(not 0 <= domain <= 232 for domain in domain_ids):
        raise ValueError("DDS domain IDs must be between 0 and 232")
    clauses = " or ".join(
        f"portrange {PB + DG * domain}-{min(65535, PB + DG * domain + DG - 1)}"
        for domain in domain_ids)
    return f"ip proto 17 and ((udp and ({clauses})) or (ip[6:2] & 0x1fff != 0))"


def _start_tshark_live(interface: str, bpf_filter: str, accumulator: TrafficAccumulator,
                       stop_event: threading.Event, local_ips: set[str],
                       domain_ids: List[int], topic_cache: Path, ready_file: Optional[Path],
                       wire_capture=None):
    """Run tshark in live capture mode, parsing RTPS fields and feeding the accumulator."""
    cmd = [
        "tshark", "-i", interface, "-l",   # -l = line-buffered
        "-a", "duration:300",
        "-f", bpf_filter,                  # BPF capture filter
    ] + tshark_fields()
    accounting = FrameAccounting(domain_ids, local_ips, accumulator, topic_cache, wire_capture)
    while not stop_event.is_set():
        with tempfile.TemporaryFile(mode="w+t") as errors:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors, bufsize=0)
            pending = b""
            try:
                while not stop_event.is_set():
                    diagnostic = os.pread(errors.fileno(), 16384, 0).decode("utf-8", errors="replace")
                    if "Capturing on" in diagnostic:
                        if accumulator.capture_status == "restarting":
                            accumulator.capture_gap_at_ms = int(time.time() * 1000)
                        accumulator.capture_status = "live"
                        if ready_file and not ready_file.exists():
                            ready_file.write_text("ready\n")
                    accounting.expire(time.time())
                    readable, _, _ = select.select([proc.stdout], [], [], 0.2)
                    if not readable:
                        if proc.poll() is not None:
                            break
                        continue
                    chunk = os.read(proc.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    pending += chunk
                    lines = pending.split(b"\n")
                    pending = lines.pop()
                    for line in lines:
                        try:
                            accounting.process(line.decode("utf-8"))
                        except ValueError as exc:
                            accumulator.capture_invalid_rows += 1
                            print(f"[traffic-monitor] invalid capture row: {exc}", file=sys.stderr)
                if not stop_event.is_set() and proc.wait(timeout=3) != 0:
                    errors.seek(0)
                    raise RuntimeError(f"tshark failed: {errors.read()[-4000:]}")
            finally:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                accounting.save_topics()
            accumulator.capture_restarts += 1
            accumulator.capture_gap_at_ms = int(time.time() * 1000)
            accumulator.capture_status = "restarting"


def _post_stats(url: str, payload: List[dict]) -> bool:
    """POST a JSON array of stats samples to the bridge. Returns True on success."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                headers={"Content-Type": "application/json"},
                                method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 204
    except Exception:
        return False


EMANE_MAC_COUNTERS = {
    "numDownstreamBytesUnicastTx0": "mac_tx_unicast_bytes",
    "numDownstreamBytesBroadcastTx0": "mac_tx_broadcast_bytes",
    "numDownstreamPacketsUnicastTx0": "mac_tx_unicast_packets",
    "numDownstreamPacketsBroadcastTx0": "mac_tx_broadcast_packets",
    "numDownstreamPacketsUnicastDrop0": "mac_tx_unicast_drops",
    "numDownstreamPacketsBroadcastDrop0": "mac_tx_broadcast_drops",
    "numUpstreamBytesUnicastRx0": "mac_rx_unicast_bytes",
    "numUpstreamBytesBroadcastRx0": "mac_rx_broadcast_bytes",
    "numUpstreamPacketsUnicastRx0": "mac_rx_unicast_packets",
    "numUpstreamPacketsBroadcastRx0": "mac_rx_broadcast_packets",
    "numUpstreamPacketsUnicastDrop0": "mac_rx_unicast_drops",
    "numUpstreamPacketsBroadcastDrop0": "mac_rx_broadcast_drops",
}


def read_emane_mac_counters(nem_id: int) -> Optional[Dict[str, int]]:
    """Read cumulative RF Pipe MAC transmit counters from the local EMANE daemon."""
    try:
        result = subprocess.run(
            ["emanesh", "localhost", f"get stat {nem_id} mac"],
            text=True, capture_output=True, timeout=3, check=True)
    except (OSError, subprocess.SubprocessError):
        return None

    counters = {}
    for emane_name, output_name in EMANE_MAC_COUNTERS.items():
        match = re.search(rf"\b{re.escape(emane_name)}\s*=\s*(\d+)", result.stdout)
        if match is None:
            return None
        counters[output_name] = int(match.group(1))
    return counters


def counter_delta(current: Dict[str, int], previous: Dict[str, int]) -> Dict[str, int]:
    """A reset invalidates the interval, not just the individual counter."""
    if any(value < previous[name] for name, value in current.items()):
        raise ValueError("EMANE counters reset; rebaseline required")
    return {name: value - previous[name] for name, value in current.items()}


class EmaneSampler:
    def __init__(self, nem_id, observer):
        self.nem_id = nem_id
        self.observer = observer
        self.previous = None
        self.previous_at = None

    def poll(self):
        start = time.monotonic()
        current = read_emane_mac_counters(self.nem_id)
        end = time.monotonic()
        sampled_at = (start + end) / 2
        if current is None:
            print("[traffic-monitor] EMANE read failed; rebaselining", file=sys.stderr)
            self.previous = self.previous_at = None
            return None
        result = None
        if self.previous is not None:
            try:
                delta = counter_delta(current, self.previous)
            except ValueError as exc:
                print(f"[traffic-monitor] {exc}", file=sys.stderr)
            else:
                result = {
                    "observer": self.observer, "nem_id": self.nem_id,
                    "capture_timestamp": round((time.time() - (end - start) / 2) * 1000),
                    "interval_ms": (sampled_at - self.previous_at) * 1000,
                    "measurement_layer": "emane_mac",
                    **delta,
                }
        self.previous, self.previous_at = current, sampled_at
        return result


def main():
    parser = argparse.ArgumentParser(
        description="DDS domain traffic monitor — captures RTPS on loopback, "
                    "POSTs stats to the mesh dashboard bridge.")
    parser.add_argument("--domains", default="200",
                        help="Comma-separated domain IDs to monitor (default: 200, WAN)")
    parser.add_argument("--dashboard-url", default="http://localhost:8080",
                        help="Base URL of the mesh dashboard bridge (default: http://localhost:8080)")
    parser.add_argument("--interval", type=float, default=0.1,
                        help="Stats publish interval in seconds (default: 0.1)")
    parser.add_argument("--interface", default="lo",
                        help="Network interface to capture on (default: lo)")
    parser.add_argument("--observer", default=None,
                        help="Observer name for published samples (default: hostname)")
    parser.add_argument("--emane-nem-id", type=int,
                        help="Poll this local EMANE NEM's RF Pipe MAC counters")
    parser.add_argument("--max-participants", type=int, default=32,
                        help="Deprecated compatibility option; captures the full domain port range")
    parser.add_argument("--topic-cache", type=Path, help="Local persistent SEDP topic cache")
    parser.add_argument("--wan-nodes", default=os.environ.get("WAN_DELIVERY_NODES", ""),
                        help="Comma-separated Node=IPv4 mappings for wire delivery")
    parser.add_argument("--ready-file", type=Path, help="Write after tshark confirms capture startup")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"JSONL traffic output (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be positive")

    domain_ids = [int(d.strip()) for d in args.domains.split(",")]
    observer = args.observer or os.uname().nodename
    dashboard_url = args.dashboard_url.rstrip("/")
    post_url = dashboard_url + "/api/traffic_stats"
    emane_post_url = dashboard_url + "/api/emane_stats"
    args.output.parent.mkdir(parents=True, exist_ok=True)

    print(f"[traffic-monitor] Monitoring domains {domain_ids} on {args.interface}")
    print(f"[traffic-monitor] POSTing to {post_url}, interval {args.interval}s")

    # Compute port range for display
    for d in domain_ids:
        base = PB + DG * d
        print(f"  domain {d}: ports {base}..{min(65535, base + DG - 1)}")

    # ── Set up tshark capture thread ──
    accumulator = TrafficAccumulator()
    stop_event = threading.Event()
    bpf_filter = build_port_filter(domain_ids, args.max_participants)
    local_ips = interface_ipv4_addresses(args.interface)
    if not local_ips:
        raise RuntimeError(f"No IPv4 address found on {args.interface}; cannot identify TX")
    print(f"[traffic-monitor] BPF: {bpf_filter[:120]}...")
    wire_capture = None
    if args.wan_nodes:
        nodes = {}
        for entry in args.wan_nodes.split(","):
            node, address = entry.split("=", 1)
            if address in nodes:
                raise ValueError(f"Duplicate WAN address: {address}")
            nodes[address] = node
        run_id = os.environ.get("ACT_RUN_ID") or args.output.with_name("run_id").read_text().strip()
        wire_capture = WireCapture(
            args.output.with_name("wan_events.jsonl"), observer, run_id, nodes, local_ips)

    def capture():
        try:
            _start_tshark_live(
                args.interface, bpf_filter, accumulator, stop_event, local_ips,
                domain_ids, args.topic_cache or Path("/tmp") /
                ("act-traffic-topics-" + re.sub(r"[^A-Za-z0-9_-]", "_", observer) + ".json"),
                args.ready_file, wire_capture)
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
            accumulator.capture_error = str(exc)
            accumulator.capture_status = "failed"
            print(f"[traffic-monitor] capture failed: {exc}", file=sys.stderr)
            stop_event.set()

    capture_thread = threading.Thread(
        target=capture,
        daemon=True, name="tshark-capture")
    capture_thread.start()

    # ── Graceful shutdown ──
    def _shutdown(signum, frame):
        print(f"\n[traffic-monitor] Caught signal {signum}, shutting down...")
        stop_event.set()
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ── Periodic POST loop ──
    emane_sampler = EmaneSampler(args.emane_nem_id, observer)
    if args.emane_nem_id is not None:
        emane_sampler.poll()
    previous_sample_at = time.monotonic()
    print("[traffic-monitor] Running (no DDS participant — pure tshark + HTTP). Ctrl+C to stop.")
    try:
        output = args.output.open("a", encoding="utf-8")
        while not stop_event.is_set():
            stop_event.wait(args.interval)
            snapshot = accumulator.drain()
            sample_at = time.monotonic()
            interval_ms = max(1, round((sample_at - previous_sample_at) * 1000))
            previous_sample_at = sample_at
            now_ms = int(time.time() * 1000)

            # Build a batch of samples (one per monitored domain)
            batch = []
            for d in domain_ids:
                s = snapshot.get(d, empty_counters())
                batch.append({
                    "schema_version": 2,
                    "domain_id": d,
                    "observer": observer,
                    "capture_timestamp": now_ms,
                    "interval_ms": interval_ms,
                    "capture_status": accumulator.capture_status,
                    "capture_restarts": accumulator.capture_restarts,
                    "capture_invalid_rows": accumulator.capture_invalid_rows,
                    "capture_gap": accumulator.capture_gap_at_ms >= now_ms - interval_ms,
                    "kernel_drop_count": None,
                    "orphan_fragment_packets": accumulator.orphan_fragment_packets,
                    "orphan_fragment_bytes": accumulator.orphan_fragment_bytes,
                    "wire_delivery_enabled": wire_capture is not None,
                    "wire_incomplete_fragments": wire_capture.incomplete_fragments if wire_capture else 0,
                    "wire_unknown_destinations": wire_capture.unknown_destinations if wire_capture else 0,
                    **s,
                })

            posted = _post_stats(post_url, batch)
            emane_sample = None
            if args.emane_nem_id is not None:
                emane_sample = emane_sampler.poll()
                if emane_sample is not None:
                    if not _post_stats(emane_post_url, emane_sample):
                        print("  [warn] EMANE stats POST failed (bridge not ready?)")
            output.write(json.dumps({
                "capture_interface": args.interface,
                "dashboard_posted": posted,
                "samples": batch,
                "emane_mac_tx": emane_sample,
            }, separators=(",", ":")) + "\n")
            output.flush()
            if not posted:
                print("  [warn] POST failed (bridge not ready?)")

    except KeyboardInterrupt:
        pass
    finally:
        if "output" in locals():
            output.close()
        stop_event.set()
        capture_thread.join(timeout=5)
        if wire_capture and not capture_thread.is_alive():
            wire_capture.close()
        print("[traffic-monitor] Done.")
    if accumulator.capture_error:
        raise RuntimeError(accumulator.capture_error)


if __name__ == "__main__":
    main()
