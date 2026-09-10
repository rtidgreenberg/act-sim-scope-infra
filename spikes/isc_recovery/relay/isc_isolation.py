#!/usr/bin/env python3
"""
ISC isolation test — proves instance_state_consistency (RECOVER_STATE) does something
that plain RELIABLE + durability cannot, under a real network dropout.

Everything else in this PoC proves the relay is *transparent* (C == A). But on localhost
with RELIABLE + TRANSIENT_LOCAL, ordinary durable replay / reliable retransmission already
recovers instance state on reconnect — so those tests do NOT isolate ISC's own
contribution. This test does, by removing every other recovery path:

  - real NETWORK DROPOUT via netem (packets stop) — NOT a discovery unmatch. Endpoints
    stay matched; only the writer's LIVELINESS lapses (MANUAL_BY_TOPIC, short lease), so
    the reader marks the instance NOT_ALIVE_NO_WRITERS while keeping the writer + minimum
    state — the exact precondition ISC reconciles from.
  - the instance is UNCHANGED during the outage (no dispose, no re-write), so there is NO
    sample for RELIABLE to retransmit on restore.
  - VOLATILE durability, so there is no durable last-value to replay.
  - liveliness is regained with assert_liveliness() (no data sample).

On restore, the reader's instance is NOT_ALIVE_NO_WRITERS with no sample coming. The only
mechanism that can return it to the writer's true current state (ALIVE) is ISC's builtin
ServiceRequest reconcile:

    ISC ON  ->  ALIVE   (recovered via ISC)
    ISC OFF ->  NO_WRITERS  (stuck until a fresh write that never comes)

This is the localhost analogue of the RF-link-drop convergence the relay must provide at
scale (roadmap Phase 8 / scenario S8).

Root-free: runs inside an unprivileged user+network namespace.
Run it:
    export RTI_LICENSE_FILE=/home/rti/rti_connext_dds-7.6.0/rti_license.dat
    unshare -rn python3 isc_isolation.py
"""

import subprocess
import sys
import time

import rti.connextdds as dds

import act_state


def _sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def _qos(isc: bool):
    """RELIABLE + VOLATILE + finite MANUAL liveliness; ISC toggled. Everything else equal."""
    kind = (dds.InstanceStateConsistencyKind.RECOVER_STATE if isc
            else dds.InstanceStateConsistencyKind.NONE)
    wq = dds.DataWriterQos()
    wq.reliability.kind = dds.ReliabilityKind.RELIABLE
    wq.reliability.instance_state_consistency_kind = kind
    wq.durability.kind = dds.DurabilityKind.VOLATILE
    wq.history.kind = dds.HistoryKind.KEEP_LAST; wq.history.depth = 1
    wq.liveliness.kind = dds.LivelinessKind.MANUAL_BY_TOPIC   # only writer action asserts it
    wq.liveliness.lease_duration = dds.Duration(2)
    wq.writer_data_lifecycle.autodispose_unregistered_instances = False
    wq.data_writer_protocol.serialize_key_with_dispose = True

    rq = dds.DataReaderQos()
    rq.reliability.kind = dds.ReliabilityKind.RELIABLE
    rq.reliability.instance_state_consistency_kind = kind
    rq.durability.kind = dds.DurabilityKind.VOLATILE
    rq.history.kind = dds.HistoryKind.KEEP_LAST; rq.history.depth = 1
    rq.liveliness.kind = dds.LivelinessKind.AUTOMATIC        # requested (weaker) — RxO ok
    rq.liveliness.lease_duration = dds.Duration(4)
    rq.data_reader_resource_limits.keep_minimum_state_for_instances = True
    rq.data_reader_protocol.propagate_dispose_of_unregistered_instances = True
    return wq, rq


def _state_name(s):
    if s == dds.InstanceState.ALIVE: return "ALIVE"
    if s == dds.InstanceState.NOT_ALIVE_DISPOSED: return "DISPOSED"
    if s == dds.InstanceState.NOT_ALIVE_NO_WRITERS: return "NO_WRITERS"
    return "<absent>"


def run_arm(isc: bool, domain: int) -> str:
    """One arm: write once, drop the link until liveliness lapses, restore, observe."""
    wq, rq = _qos(isc)
    dp = dds.DomainParticipant(domain, act_state.participant_qos())
    topic = dds.Topic(dp, "ActState", act_state.ActState)
    w = dds.DataWriter(dds.Publisher(dp), topic, wq)
    r = dds.DataReader(dds.Subscriber(dp), topic, rq)
    cache = {}

    def snap():
        st = {}
        for data, info in r.read():
            k = act_state.resolve_key_id(r, data, info, cache)
            if k:
                st[k] = info.state.instance_state
        return st.get("K1")

    try:
        time.sleep(1.5)
        w.write(act_state.ActState(key_id="K1", seq=1))   # single write; never again
        time.sleep(1.0); r.read()
        before = snap()

        _sh("tc qdisc add dev lo root netem loss 100%")   # --- network dropout ---
        time.sleep(4.0)                                   # > lease: liveliness lapses
        during = snap()
        # no write / no dispose — the instance is unchanged and ALIVE at the writer
        _sh("tc qdisc del dev lo root")                   # --- link restored ---
        w.assert_liveliness()                             # regain liveliness, no sample
        time.sleep(4.0)
        after = snap()

        arm = "ISC ON " if isc else "ISC OFF"
        print(f"  {arm}: before={_state_name(before)}  during={_state_name(during)}  "
              f"after={_state_name(after)}")
        return _state_name(after)
    finally:
        _sh("tc qdisc del dev lo root")
        dp.close()


def main():
    print("ISC isolation — network dropout + liveliness expiration, unchanged instance")
    if _sh("tc qdisc show dev lo").returncode != 0:
        sys.exit("cannot drive tc on lo — run under: unshare -rn python3 isc_isolation.py")
    _sh("ip link set lo up")

    print("\nExpectation: ISC recovers the instance to its true ALIVE state on link\n"
          "restore; without ISC it stays NOT_ALIVE_NO_WRITERS (no sample to deliver).\n")
    on = run_arm(isc=True, domain=40)
    off = run_arm(isc=False, domain=41)

    print("\n" + "#" * 60)
    isolated = (on == "ALIVE" and off == "NO_WRITERS")
    if isolated:
        print("RESULT: ✅ ISC ISOLATED — RECOVER_STATE returned the instance to ALIVE;")
        print("        NONE left it stuck at NOT_ALIVE_NO_WRITERS. Reliable/durability")
        print("        cannot do this (no sample exists to retransmit or replay).")
    else:
        print(f"RESULT: ⚠ inconclusive — ISC on -> {on}, ISC off -> {off}")
        print("        (expected ALIVE vs NO_WRITERS; check timing/liveliness lease).")
    print("#" * 60)
    return 0 if isolated else 1


if __name__ == "__main__":
    sys.exit(main())
