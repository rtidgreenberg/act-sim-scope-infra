#!/usr/bin/env python3
"""spdp2_delayed_enable spike -- does SPDP2 actually break under the router's real
disabled-then-delayed-enable (D52) startup sequence, as D87 claimed?

D87 (2026-07-20) retracted D78's SPDP2 proposal after a live-debugging repro allegedly
showed asymmetric, non-retrying discovery failure when one of two SPDP2 participants is
created disabled then enable()'d after a delay (mirroring ParticipantRegistry.cxx). That
repro was never saved. This spike rebuilds it fresh and sweeps the delay range to find out
whether it reproduces at all, before deciding whether a workaround is needed.

Claims under test (see PLAN.md): A (control/sanity), B (money test: one-process
disabled-then-delayed-enable, delay swept 20ms-45s), D (extended hold, self-heal check).
Part C (cross-process replay) and E (wire-level SPDP2-is-really-active confirmation) are
separate scripts/commands -- see README.md "How to run" for the full sequence, since they
need dumpcap/subprocess orchestration this file doesn't do itself.

Exit 0 = A holds and B/D show no failure across the swept range (D87's claim does NOT
reproduce here). Exit 1 = the claimed failure reproduced at least once.

Run: python3 spikes/spdp2_delayed_enable/spdp2_delayed_enable_spike.py [base_domain]
"""

import os
import sys
import time

sys.path.insert(0, "/home/rti/act-sim-scope-infra/spikes/partition_retarget")
os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

import rti.connextdds as dds  # noqa: E402
import partition_retarget_spike as prs  # noqa: E402

DEFAULT_BASE_DOMAIN = 5500


class SpikeError(AssertionError):
    pass


def fmt(t):
    return "never" if t is None else f"{t*1000:.0f}ms"


def make_disabled(domain, partition):
    """Mirror ParticipantRegistry::ParticipantRegistry: flip the process-global factory
    to ManuallyEnable, create the participant (disabled), restore the factory immediately
    -- same sequence, same restore-right-after-creation timing."""
    factory = dds.DomainParticipant.participant_factory_qos
    ef = factory.entity_factory
    ef.autoenable_created_entities = False
    factory.entity_factory = ef
    dds.DomainParticipant.participant_factory_qos = factory

    q = dds.DomainParticipantQos()
    q.transport_builtin = dds.TransportBuiltin.udpv4
    q.partition = dds.Partition([partition])
    dc = q.discovery_config
    dc.builtin_discovery_plugins = prs.SPDP2_SEDP
    q.discovery_config = dc
    p = dds.DomainParticipant(domain, q)

    factory2 = dds.DomainParticipant.participant_factory_qos
    ef2 = factory2.entity_factory
    ef2.autoenable_created_entities = True
    factory2.entity_factory = ef2
    dds.DomainParticipant.participant_factory_qos = factory2
    return p


def part_a_control(base):
    print("Part A (control): immediate-autoenable SPDP2, both sides -- must discover fast "
          "and symmetrically")
    pa = prs.make_participant(base, participant_partition="SHARED", spdp2=True)
    pb = prs.make_participant(base, participant_partition="SHARED", spdp2=True)
    try:
        t_a = prs.wait_until(lambda: len(pa.discovered_participants()) > 0, timeout_s=10.0)
        t_b = prs.wait_until(lambda: len(pb.discovered_participants()) > 0, timeout_s=10.0)
        print(f"  a_sees_b={fmt(t_a)}, b_sees_a={fmt(t_b)}")
        if t_a is None or t_b is None:
            raise SpikeError(f"[A] immediate-autoenable SPDP2 failed to discover "
                              f"symmetrically: a_sees_b={fmt(t_a)} b_sees_a={fmt(t_b)}")
        print("  PASS")
    finally:
        pa.close()
        pb.close()


def one_process_delayed_enable(domain, b_delay_s, hold_s):
    """Two SPDP2 participants, ONE process: A enable()s immediately, B enable()s after
    b_delay_s. Poll both directions of discovered_participants() for hold_s. Returns
    (a_sees_b, b_sees_a) elapsed-seconds-or-None, measured from each one's OWN enable()."""
    pa = make_disabled(domain, "SHARED")
    pb = make_disabled(domain, "SHARED")
    try:
        t0 = time.monotonic()
        pa.enable()
        time.sleep(b_delay_s)
        pb.enable()

        t_poll_start = time.monotonic()
        a_sees_b = None
        b_sees_a = None
        deadline = t_poll_start + hold_s
        while time.monotonic() < deadline:
            now = time.monotonic()
            if a_sees_b is None and len(pa.discovered_participants()) > 0:
                a_sees_b = now - t_poll_start
            if b_sees_a is None and len(pb.discovered_participants()) > 0:
                b_sees_a = now - t_poll_start
            if a_sees_b is not None and b_sees_a is not None:
                break
            time.sleep(0.02)
        return a_sees_b, b_sees_a
    finally:
        pa.close()
        pb.close()


def part_b_money_test(base):
    print("Part B (THE MONEY TEST): one-process disabled-then-delayed-enable, delay swept "
          "20ms-45s, 3 reps at representative points")
    failures = []
    domain = base
    for delay in (0.02, 0.1, 0.5, 2.0, 5.0, 10.0, 20.0, 45.0):
        for rep in range(3):
            a_sees_b, b_sees_a = one_process_delayed_enable(domain, delay, hold_s=15.0)
            domain += 1
            status = "PASS" if (a_sees_b is not None and b_sees_a is not None) else "FAIL"
            print(f"  delay={delay:>5.2f}s rep={rep}: a_sees_b={fmt(a_sees_b)} "
                  f"b_sees_a={fmt(b_sees_a)}  {status}")
            if status == "FAIL":
                failures.append(f"[B] delay={delay}s rep={rep}: a_sees_b={fmt(a_sees_b)} "
                                 f"b_sees_a={fmt(b_sees_a)}")
    if failures:
        raise SpikeError(f"[B] {len(failures)} trial(s) failed to discover symmetrically: "
                          + "; ".join(failures))
    print("  PASS: every trial discovered symmetrically -- D87's claimed asymmetric "
          "failure did NOT reproduce across the swept delay range")


def part_d_extended_hold(base):
    print("Part D: extended-hold self-heal check (90s hold, delay=10s, 2 full "
          "participant_announcement_period cycles at the 30s AUTO default)")
    a_sees_b, b_sees_a = one_process_delayed_enable(base, b_delay_s=10.0, hold_s=90.0)
    print(f"  a_sees_b={fmt(a_sees_b)}, b_sees_a={fmt(b_sees_a)}")
    if a_sees_b is None or b_sees_a is None:
        raise SpikeError(f"[D] did not discover symmetrically within 90s hold: "
                          f"a_sees_b={fmt(a_sees_b)} b_sees_a={fmt(b_sees_a)}")
    print("  PASS")


def main():
    base = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BASE_DOMAIN
    print(f"spdp2_delayed_enable spike on base domain {base} (UDPv4-only)\n")

    failures = []
    for part, name, offset in ((part_a_control, "A", 0), (part_b_money_test, "B", 1),
                                (part_d_extended_hold, "D", 200)):
        try:
            part(base + offset)
        except SpikeError as e:
            failures.append(str(e))
            print(f"  FAIL: {e}")
        print()

    if failures:
        print(f"SPIKE FAILED ({len(failures)} failure(s)) -- D87's claimed bug DID "
              f"reproduce at least once:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SPIKE PASSED: D87's claimed asymmetric, non-self-healing SPDP2 "
          "disabled-then-delayed-enable failure did NOT reproduce -- every trial across "
          "the swept delay range (20ms-45s) and an extended 90s hold discovered "
          "symmetrically. See README.md for the cross-process replay (Part C) and "
          "wire-level SPDP2-is-genuinely-active confirmation (Part E), which this script "
          "does not run itself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
