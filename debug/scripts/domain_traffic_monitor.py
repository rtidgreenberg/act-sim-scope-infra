#!/usr/bin/env python3
"""Continuous DDS domain-traffic monitor using tshark on a selected interface.

Captures RTPS packets on lo, classifies them by DDS domain ID (derived from UDP
port via the standard RTPS port-mapping formula) and by traffic class (discovery
vs user-data, derived from the RTPS writerEntityId), then POSTs periodic stats
to the mesh dashboard bridge (HTTP), which broadcasts them to WebSocket clients.

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
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

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


def classify_packet(entity_ids: str, submessage_ids: str) -> str:
    """Classify a complete RTPS frame without assigning bundled bytes arbitrarily."""
    categories = set()
    for entity_id_hex in _field_values(entity_ids):
        try:
            kind_byte = int(entity_id_hex.replace(":", "").lower()[-2:], 16)
        except ValueError:
            continue
        if kind_byte in DISCOVERY_ENTITY_KINDS:
            categories.add("discovery")
        elif kind_byte in DATA_ENTITY_KINDS:
            categories.add("data")

    for submessage_id in _field_values(submessage_ids):
        try:
            value = int(submessage_id, 0)
        except ValueError:
            continue
        if value in RELIABILITY_SUBMESSAGE_IDS:
            categories.add("reliability")

    if len(categories) == 1:
        return categories.pop()
    if len(categories) > 1:
        return "mixed"
    return "unknown"


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

    def _ensure_domain(self, domain_id: int):
        if domain_id not in self.stats:
            self.stats[domain_id] = {
                "discovery_packets": 0, "discovery_bytes": 0,
                "data_packets": 0, "data_bytes": 0,
                "reliability_packets": 0, "reliability_bytes": 0,
                "mixed_packets": 0, "mixed_bytes": 0,
                "unknown_packets": 0, "unknown_bytes": 0,
                "discovery_tx_packets": 0, "discovery_tx_bytes": 0,
                "data_tx_packets": 0, "data_tx_bytes": 0,
                     "rx_packets": 0, "rx_bytes": 0,
                "total_packets": 0, "total_bytes": 0,
            }

    def add_packet(self, domain_id: int, frame_len: int, traffic_class: str,
                         is_local_transmit: bool, discovery_writer_transmit: bool,
                         data_writer_transmit: bool):
        with self.lock:
            self._ensure_domain(domain_id)
            s = self.stats[domain_id]
            s["total_packets"] += 1
            s["total_bytes"] += frame_len
            s[f"{traffic_class}_packets"] += 1
            s[f"{traffic_class}_bytes"] += frame_len
            if not is_local_transmit:
                s["rx_packets"] += 1
                s["rx_bytes"] += frame_len
            if discovery_writer_transmit:
                s["discovery_tx_packets"] += 1
                s["discovery_tx_bytes"] += frame_len
            if data_writer_transmit:
                s["data_tx_packets"] += 1
                s["data_tx_bytes"] += frame_len

    def drain(self) -> Dict[int, Dict[str, int]]:
        """Return and reset all accumulated stats."""
        with self.lock:
            snapshot = self.stats
            self.stats = {}
            return snapshot


def build_port_filter(domain_ids: List[int], max_participants: int = 32) -> str:
    """Build a BPF capture filter string for the given domain IDs, covering all
    possible participant indices up to max_participants."""
    ports = set()
    for d in domain_ids:
        ports.add(PB + DG * d + D0)      # SPDP multicast meta
        ports.add(PB + DG * d + D2)      # user multicast
        for p in range(max_participants):
            ports.add(PB + DG * d + D1 + PG * p)  # meta unicast
            ports.add(PB + DG * d + D3 + PG * p)  # user unicast
    # BPF portrange won't work well here; just enumerate
    port_list = sorted(ports)
    # Build a compact BPF filter
    clauses = " or ".join(f"port {p}" for p in port_list)
    return f"udp and ({clauses})"


def _start_tshark_live(interface: str, bpf_filter: str, accumulator: TrafficAccumulator,
                       stop_event: threading.Event, local_ips: set[str]):
    """Run tshark in live capture mode, parsing RTPS fields and feeding the accumulator."""
    cmd = [
        "tshark", "-i", interface, "-l",   # -l = line-buffered
        "-a", "duration:60",              # bounded capture; restart below
        "-f", bpf_filter,                  # BPF capture filter
        "-Y", "rtps",                      # display filter: only RTPS
        "-T", "fields",
        "-e", "udp.dstport",
        "-e", "frame.len",
        "-e", "rtps.sm.wrEntityId",
        "-e", "rtps.sm.id",
        "-e", "ip.src",
        "-E", "separator=|",
        "-E", "occurrence=a",              # inspect every submessage in each frame
    ]
    while not stop_event.is_set():
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, bufsize=1)
        try:
            while not stop_event.is_set():
                readable, _, _ = select.select([proc.stdout], [], [], 0.5)
                if not readable:
                    if proc.poll() is not None:
                        break
                    continue
                line = proc.stdout.readline()
                if not line:
                    break
                parts = line.strip().split("|")
                if len(parts) < 5:
                    continue
                try:
                    dst_port = int(parts[0])
                    frame_len = int(parts[1])
                except (ValueError, IndexError):
                    continue
                domain_id = port_to_domain(dst_port)
                if domain_id is None:
                    continue
                traffic_class = classify_packet(parts[2], parts[3])
                is_local_transmit = parts[4] in local_ips
                accumulator.add_packet(
                    domain_id, frame_len, traffic_class, is_local_transmit,
                    is_local_transmit and has_writer_entity(parts[2], DISCOVERY_WRITER_ENTITY_KINDS),
                    is_local_transmit and has_writer_entity(parts[2], DATA_ENTITY_KINDS))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


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
    """Return non-negative counter deltas, treating an EMANE restart as a new baseline."""
    return {
        name: value - previous[name] if value >= previous[name] else 0
        for name, value in current.items()
    }


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
                        help="Max participant index per domain for port filter (default: 32)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"JSONL traffic output (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

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
        print(f"  domain {d}: ports {base}..{base + D3 + PG * (args.max_participants - 1)}")

    # ── Set up tshark capture thread ──
    accumulator = TrafficAccumulator()
    stop_event = threading.Event()
    bpf_filter = build_port_filter(domain_ids, args.max_participants)
    local_ips = interface_ipv4_addresses(args.interface)
    if not local_ips:
        print(f"[traffic-monitor] warning: no IPv4 address found on {args.interface}; "
              "writer TX metrics will remain zero")
    print(f"[traffic-monitor] BPF: {bpf_filter[:120]}...")

    capture_thread = threading.Thread(
        target=_start_tshark_live,
        args=(args.interface, bpf_filter, accumulator, stop_event, local_ips),
        daemon=True, name="tshark-capture")
    capture_thread.start()

    # ── Graceful shutdown ──
    def _shutdown(signum, frame):
        print(f"\n[traffic-monitor] Caught signal {signum}, shutting down...")
        stop_event.set()
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ── Periodic POST loop ──
    previous_emane_counters = None
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
                s = snapshot.get(d, {
                    "discovery_packets": 0, "discovery_bytes": 0,
                    "data_packets": 0, "data_bytes": 0,
                    "reliability_packets": 0, "reliability_bytes": 0,
                    "mixed_packets": 0, "mixed_bytes": 0,
                    "unknown_packets": 0, "unknown_bytes": 0,
                    "discovery_tx_packets": 0, "discovery_tx_bytes": 0,
                    "data_tx_packets": 0, "data_tx_bytes": 0,
                    "rx_packets": 0, "rx_bytes": 0,
                    "total_packets": 0, "total_bytes": 0,
                })
                batch.append({
                    "domain_id": d,
                    "observer": observer,
                    "capture_timestamp": now_ms,
                    "interval_ms": interval_ms,
                    "discovery_packets": s["discovery_packets"],
                    "discovery_bytes": s["discovery_bytes"],
                    "data_packets": s["data_packets"],
                    "data_bytes": s["data_bytes"],
                    "reliability_packets": s["reliability_packets"],
                    "reliability_bytes": s["reliability_bytes"],
                    "mixed_packets": s["mixed_packets"],
                    "mixed_bytes": s["mixed_bytes"],
                    "unknown_packets": s["unknown_packets"],
                    "unknown_bytes": s["unknown_bytes"],
                    "discovery_tx_packets": s["discovery_tx_packets"],
                    "discovery_tx_bytes": s["discovery_tx_bytes"],
                    "data_tx_packets": s["data_tx_packets"],
                    "data_tx_bytes": s["data_tx_bytes"],
                    "rx_packets": s["rx_packets"],
                    "rx_bytes": s["rx_bytes"],
                    "total_packets": s["total_packets"],
                    "total_bytes": s["total_bytes"],
                })

            posted = _post_stats(post_url, batch)
            emane_sample = None
            if args.emane_nem_id is not None:
                emane_counters = read_emane_mac_counters(args.emane_nem_id)
                if emane_counters is None:
                    print("  [warn] EMANE MAC statistics unavailable")
                elif previous_emane_counters is not None:
                    delta = counter_delta(emane_counters, previous_emane_counters)
                    emane_sample = {
                        "observer": observer,
                        "nem_id": args.emane_nem_id,
                        "capture_timestamp": now_ms,
                        "interval_ms": interval_ms,
                        **delta,
                    }
                    if not _post_stats(emane_post_url, emane_sample):
                        print("  [warn] EMANE stats POST failed (bridge not ready?)")
                previous_emane_counters = emane_counters
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
        print("[traffic-monitor] Done.")


if __name__ == "__main__":
    main()
