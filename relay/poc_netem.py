#!/usr/bin/env python3
"""
Phase 1 ISC-relay PoC — netem transport-partition variant + ISC on/off control.

poc_test.py models "disconnect" by moving the reader to an isolated PARTITION, which is a
full endpoint *unmatch* and cannot recover NOT_ALIVE_NO_WRITERS. This variant injects a
*transport-level* partition instead: a 100%-packet-loss netem qdisc on loopback. Packets
are dropped, but because the outage is far shorter than the discovery/liveliness lease the
endpoints stay MATCHED (same GUIDs), so on restoration ISC reconciles the missed
instance-state transitions — including NO_WRITERS. This is the faithful "RF link dropped
and came back" model and the stepping-stone to the EMANE fabric (roadmap Phase 8).

METHODOLOGY — write-once, no periodic re-assertion:
Each instance is written exactly once (initial ALIVE), and each transition is applied
exactly once *during* the outage. Nothing is (re)published after the link is restored, so
the reader's final state can ONLY come from reconnect reconciliation — never from "the next
periodic sample overwrote it." That is what makes recovery attributable to ISC.

ISC ON/OFF CONTROL:
The direct path is run twice with an OTHERWISE-IDENTICAL QoS (RELIABLE + TRANSIENT_LOCAL +
KEEP_LAST(1) + serialize_key_with_dispose + propagate_dispose), flipping only
instance_state_consistency_kind. The delta between the two arms is ISC's specific
contribution — isolating it from ordinary reliable retransmission.

Root-free: runs inside an unprivileged user+network namespace (`unshare -rn`), where it is
mapped to root and drives tc/netem on that namespace's OWN loopback, touching nothing on
the host.

Run it:
    export RTI_LICENSE_FILE=/home/rti/rti_connext_dds-7.6.0/rti_license.dat
    unshare -rn python3 poc_netem.py
"""

import subprocess
import sys
import time

import rti.connextdds as dds

from isc_relay import IscRelay
from poc_test import (
    Source, Dest, KEYS, DOM_A, DOM_C_UP, DOM_C_DOWN, state_name, report_path, matches,
)

TOPIC = "ActState"


def _sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def ensure_netns():
    """Bring up loopback and confirm we can drive tc (i.e. we're in a netns as root)."""
    _sh("ip link set lo up")
    if _sh("tc qdisc show dev lo").returncode != 0:
        sys.exit("cannot drive tc on lo — run under: unshare -rn python3 poc_netem.py")


def partition(on: bool):
    """100%-loss netem qdisc on loopback == transport blackout (endpoints stay matched)."""
    _sh("tc qdisc del dev lo root")  # idempotent reset (ignore errors)
    if on:
        r = _sh("tc qdisc add dev lo root netem loss 100%")
        if r.returncode != 0:
            sys.exit(f"failed to add netem: {r.stderr.strip()}")


def run_path(source: Source, dest: Dest) -> dict:
    # write-once: no start_reassert() — recovery must come from reconciliation, not a
    # subsequent periodic sample.
    source.initial_writes()
    time.sleep(1.5)
    dest.drain()                       # baseline established while connected

    partition(True)                    # --- transport blackout (link down) ---
    time.sleep(1.0)
    source.apply_faults()              # dispose K1 / unregister K2 / replay K3 / K4 alive
    time.sleep(1.5)
    partition(False)                   # --- link restored; reconcile ---

    return dest.observe()


def run_direct(isc: bool) -> dict:
    source, dest = Source(DOM_A, isc=isc), Dest(DOM_A, isc=isc)
    try:
        return run_path(source, dest)
    finally:
        dest.close(); source.close()


def run_relay(isc: bool) -> dict:
    source = Source(DOM_C_UP, isc=isc)
    relay = IscRelay(DOM_C_UP, DOM_C_DOWN, TOPIC, isc=isc)
    relay.start()
    dest = Dest(DOM_C_DOWN, isc=isc)
    try:
        return run_path(source, dest)
    finally:
        dest.close(); relay.stop(); source.close()


def main():
    print("Phase 1 — ISC-relay PoC · netem transport-partition + ISC on/off control")
    print("(write-once; recovery attributable only to reconnect reconciliation)")
    ensure_netns()
    try:
        # -------- ISC ON: the proof (A = ground truth, C = relay) -------- #
        print("\n" + "=" * 62)
        print("ARM 1 · ISC ON — transport blackout during transitions")
        print("=" * 62)
        a = run_direct(isc=True)
        report_path("Path A (direct, ISC on) = ground truth", a)
        c = run_relay(isc=True)
        report_path("Path C (Python ISC relay, ISC on)", c)
        c_ok = matches(a, c, KEYS)
        print(f"\n  --> C matches A on {KEYS}: {'YES ✅' if c_ok else 'NO ❌'}")

        # -------- ISC OFF: the control (same QoS, ISC disabled) -------- #
        print("\n" + "=" * 62)
        print("ARM 2 · ISC OFF (control) — identical QoS, instance_state_consistency=NONE")
        print("=" * 62)
        a_off = run_direct(isc=False)
        report_path("Path A' (direct, ISC OFF)", a_off)

        # -------- Attribution: what did ISC specifically change? -------- #
        print("\n" + "=" * 62)
        print("ATTRIBUTION — ISC on vs off, per key (direct path)")
        print("=" * 62)
        isc_changed = []
        for k in KEYS:
            on, off = a.get(k), a_off.get(k)
            delta = "" if on == off else "   <-- ISC changed the outcome"
            if on != off:
                isc_changed.append(k)
            print(f"  {k}: ISC on = {state_name(on):11s}  ISC off = {state_name(off):11s}{delta}")

        print("\n" + "#" * 62)
        k2_on = a.get("K2") == dds.InstanceState.NOT_ALIVE_NO_WRITERS
        verdict = c_ok and k2_on
        print(f"Relay reproduces direct-ISC (C==A, incl NO_WRITERS): {'YES ✅' if verdict else 'NO ❌'}")
        if isc_changed:
            print(f"ISC changed the recovered state for: {isc_changed}")
            print("  -> those transitions are recovered by ISC specifically, not by plain")
            print("     reliable retransmission (the only QoS difference between arms).")
        else:
            print("ISC on and off produced identical results on this single-host run:")
            print("  -> across a SHORT loopback outage, reliable+transient_local retransmit")
            print("     already recovers these transitions; ISC's distinct value appears")
            print("     where reliable can't reach (purged history / longer or lossy RF).")
        print("#" * 62)
        return 0 if verdict else 1
    finally:
        partition(False)               # always tear down the qdisc


if __name__ == "__main__":
    sys.exit(main())
