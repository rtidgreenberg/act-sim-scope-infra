#!/usr/bin/env python3
"""Continuous DDS domain-traffic monitor using tshark on loopback.

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
  python3 harness_v2/scripts/domain_traffic_monitor.py [--domains 20,21] [--interval 2]
  python3 harness_v2/scripts/domain_traffic_monitor.py --help
"""

import argparse
import json
import os
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


def classify_entity_id(entity_id_hex: str) -> str:
    """Classify an RTPS writerEntityId hex string as 'discovery', 'data', or 'other'."""
    try:
        # entity_id_hex could be "000100c2" or "00:01:00:c2"
        clean = entity_id_hex.replace(":", "").strip().lower()
        if len(clean) < 2:
            return "other"
        kind_byte = int(clean[-2:], 16)
        if kind_byte in DISCOVERY_ENTITY_KINDS:
            return "discovery"
        if kind_byte in DATA_ENTITY_KINDS:
            return "data"
    except (ValueError, IndexError):
        pass
    return "other"


class TrafficAccumulator:
    """Accumulates packet counts/bytes per domain, per traffic class."""

    def __init__(self):
        self.lock = threading.Lock()
        # domain_id -> {discovery_packets, discovery_bytes, data_packets, data_bytes,
        #               total_packets, total_bytes}
        self.stats: Dict[int, Dict[str, int]] = {}

    def _ensure_domain(self, domain_id: int):
        if domain_id not in self.stats:
            self.stats[domain_id] = {
                "discovery_packets": 0, "discovery_bytes": 0,
                "data_packets": 0, "data_bytes": 0,
                "total_packets": 0, "total_bytes": 0,
            }

    def add_packet(self, domain_id: int, frame_len: int, traffic_class: str):
        with self.lock:
            self._ensure_domain(domain_id)
            s = self.stats[domain_id]
            s["total_packets"] += 1
            s["total_bytes"] += frame_len
            if traffic_class == "discovery":
                s["discovery_packets"] += 1
                s["discovery_bytes"] += frame_len
            elif traffic_class == "data":
                s["data_packets"] += 1
                s["data_bytes"] += frame_len

    def drain(self) -> Dict[int, Dict[str, int]]:
        """Return and reset all accumulated stats."""
        with self.lock:
            snapshot = self.stats
            self.stats = {}
            return snapshot


def build_port_filter(domain_ids: List[int], max_participants: int = 8) -> str:
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
                       stop_event: threading.Event):
    """Run tshark in live capture mode, parsing RTPS fields and feeding the accumulator."""
    cmd = [
        "tshark", "-i", interface, "-l",   # -l = line-buffered
        "-f", bpf_filter,                  # BPF capture filter
        "-Y", "rtps",                      # display filter: only RTPS
        "-T", "fields",
        "-e", "udp.dstport",
        "-e", "frame.len",
        "-e", "rtps.sm.wrEntityId",
        "-E", "separator=|",
        "-E", "occurrence=f",              # first occurrence only (per-packet)
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, bufsize=1)
    try:
        while not stop_event.is_set():
            line = proc.stdout.readline()
            if not line:
                break
            parts = line.strip().split("|")
            if len(parts) < 3:
                continue
            try:
                dst_port = int(parts[0])
                frame_len = int(parts[1])
            except (ValueError, IndexError):
                continue
            entity_id_hex = parts[2]
            domain_id = port_to_domain(dst_port)
            if domain_id is None:
                continue
            traffic_class = classify_entity_id(entity_id_hex)
            accumulator.add_packet(domain_id, frame_len, traffic_class)
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


def main():
    parser = argparse.ArgumentParser(
        description="DDS domain traffic monitor — captures RTPS on loopback, "
                    "POSTs stats to the mesh dashboard bridge.")
    parser.add_argument("--domains", default="20",
                        help="Comma-separated domain IDs to monitor (default: 20)")
    parser.add_argument("--dashboard-url", default="http://localhost:8080",
                        help="Base URL of the mesh dashboard bridge (default: http://localhost:8080)")
    parser.add_argument("--interval", type=float, default=0.1,
                        help="Stats publish interval in seconds (default: 0.1)")
    parser.add_argument("--interface", default="lo",
                        help="Network interface to capture on (default: lo)")
    parser.add_argument("--observer", default=None,
                        help="Observer name for published samples (default: hostname)")
    parser.add_argument("--max-participants", type=int, default=8,
                        help="Max participant index per domain for port filter (default: 8)")
    args = parser.parse_args()

    domain_ids = [int(d.strip()) for d in args.domains.split(",")]
    observer = args.observer or os.uname().nodename
    post_url = args.dashboard_url.rstrip("/") + "/api/traffic_stats"

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
    print(f"[traffic-monitor] BPF: {bpf_filter[:120]}...")

    capture_thread = threading.Thread(
        target=_start_tshark_live,
        args=(args.interface, bpf_filter, accumulator, stop_event),
        daemon=True, name="tshark-capture")
    capture_thread.start()

    # ── Graceful shutdown ──
    def _shutdown(signum, frame):
        print(f"\n[traffic-monitor] Caught signal {signum}, shutting down...")
        stop_event.set()
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ── Periodic POST loop ──
    interval_ms = int(args.interval * 1000)
    print("[traffic-monitor] Running (no DDS participant — pure tshark + HTTP). Ctrl+C to stop.")
    try:
        while not stop_event.is_set():
            stop_event.wait(args.interval)
            snapshot = accumulator.drain()
            now_ms = int(time.time() * 1000)

            # Build a batch of samples (one per monitored domain)
            batch = []
            for d in domain_ids:
                s = snapshot.get(d, {
                    "discovery_packets": 0, "discovery_bytes": 0,
                    "data_packets": 0, "data_bytes": 0,
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
                    "total_packets": s["total_packets"],
                    "total_bytes": s["total_bytes"],
                })

                total = s["total_packets"]
                disc = s["discovery_packets"]
                data = s["data_packets"]
                other = total - disc - data
                print(f"  domain {d}: {total} pkts "
                      f"({s['total_bytes']} B) — "
                      f"discovery={disc} data={data} other={other}")

            if not _post_stats(post_url, batch):
                print("  [warn] POST failed (bridge not ready?)")

    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        capture_thread.join(timeout=5)
        print("[traffic-monitor] Done.")


if __name__ == "__main__":
    main()
