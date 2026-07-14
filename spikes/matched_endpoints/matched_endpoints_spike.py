#!/usr/bin/env python3
"""Matched-endpoints spike — validate the create-and-observe matching authority (D64).

The D64 pivot makes DDS the matching authority: the router creates a route entity, then reads
the entity's OWN `matched_publications()` / `matched_subscriptions()` as the truth about
connectivity, instead of the controller re-deriving matching by topic name (the source of the
D61 partition false-green). Before retiring the controller matching + the D39/D51 gate, the
`matched_publications` surface for DynamicData route entities must be proven. This does that.

Claims under test:
  A. matched_publications()/matched_subscriptions() reflect a real match, both directions, and
     go non-empty within a short create-then-observe window.
  B. THE MONEY CLAIM — a PARTITION MISMATCH yields an EMPTY matched set (no false-green): a
     route reader on partition PLATFORM never matches an app writer on partition CONTROL, so a
     create-and-observe router sees zero matches and correctly reports "not connected" — while
     a same-partition writer DOES match. (Route reader is a ContentFilteredTopic, so this also
     covers partition + CFT together, as control_command needs.)
  C. A created-but-UNMATCHED route reader reports zero matches (the honest "built but not
     connected" signal, a status reason — not a false ENABLED), then transitions to matched
     when the peer appears (the "observe" half).

App endpoints are built exactly as the ACT sim builds them (rti.connextdds + DynamicData from
act_types.xml). Each part runs on its own domain with fresh participants.

Exit 0 = all parts pass. Run:  python3 spikes/matched_endpoints/matched_endpoints_spike.py [base_domain]
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

import rti.connextdds as dds  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
ACT_TYPES_XML = str(REPO_ROOT / "harness/act/node_sim/datamodel/act_types.xml")

TOPIC = "ControlCommand"
TYPE = "control_command"
FIELD = "msg.destination"
DOMAIN = int(sys.argv[1]) if len(sys.argv) > 1 else 61

_types_provider = None


def _types():
    global _types_provider
    if _types_provider is None:
        _types_provider = dds.QosProvider(ACT_TYPES_XML)
    return _types_provider


class SpikeError(AssertionError):
    pass


def _udp():
    q = dds.DomainParticipantQos()
    q.transport_builtin = dds.TransportBuiltin.udpv4
    return q


def _subscriber(p, partition=None):
    q = dds.SubscriberQos()
    if partition:
        q.partition = dds.Partition([partition])
    return dds.Subscriber(p, q)


def _publisher(p, partition=None):
    q = dds.PublisherQos()
    if partition:
        q.partition = dds.Partition([partition])
    return dds.Publisher(p, q)


def _topic(p):
    return dds.DynamicData.Topic(p, TOPIC, _types().type(TYPE))


def wait_matched(count_fn, want, timeout_s=8.0, poll_s=0.1):
    """Poll count_fn() until it reaches `want` (or timeout); return the final count and the
    elapsed seconds to reach it (or None if never)."""
    t0 = time.monotonic()
    deadline = t0 + timeout_s
    reached = None
    while time.monotonic() < deadline:
        c = count_fn()
        if c >= want and reached is None:
            reached = time.monotonic() - t0
            return c, reached
        time.sleep(poll_s)
    return count_fn(), None


def stays_zero(count_fn, hold_s=4.0, poll_s=0.1):
    """Return True iff count_fn() stays 0 for the whole hold window (proves NO match)."""
    deadline = time.monotonic() + hold_s
    while time.monotonic() < deadline:
        if count_fn() != 0:
            return False
        time.sleep(poll_s)
    return count_fn() == 0


def part_a_match_truth(domain):
    print("Part A: matched_publications/matched_subscriptions reflect a real match")
    routerp = dds.DomainParticipant(domain, _udp())
    appp = dds.DomainParticipant(domain, _udp())
    try:
        rt = _topic(routerp)   # one Topic per participant (a duplicate name would throw)
        at = _topic(appp)
        # route reader vs app writer
        route_reader = dds.DynamicData.DataReader(_subscriber(routerp), rt)
        app_writer = dds.DynamicData.DataWriter(_publisher(appp), at)
        n, t = wait_matched(lambda: len(route_reader.matched_publications), 1)
        if t is None:
            raise SpikeError("[A] route reader never saw the app writer in "
                             "matched_publications")
        print(f"  [A] route reader matched the app writer in {t*1000:.0f}ms "
              f"(matched_publications={n})")
        # route writer vs app reader (reuse the same per-participant topics)
        route_writer = dds.DynamicData.DataWriter(_publisher(routerp), rt)
        app_reader = dds.DynamicData.DataReader(_subscriber(appp), at)
        n2, t2 = wait_matched(lambda: len(route_writer.matched_subscriptions), 1)
        if t2 is None:
            raise SpikeError("[A] route writer never saw the app reader in "
                             "matched_subscriptions")
        print(f"  [A] route writer matched the app reader in {t2*1000:.0f}ms "
              f"(matched_subscriptions={n2})  PASS")
    finally:
        appp.close()
        routerp.close()


def part_b_partition_dissolves_falsegreen(domain):
    print("Part B: partition mismatch => EMPTY matched set (false-green dissolved); "
          "route reader is a CFT (partition + CFT together)")
    routerp = dds.DomainParticipant(domain, _udp())
    appp = dds.DomainParticipant(domain, _udp())
    try:
        # Route input reader: CFT, on partition PLATFORM (like control_command's dest side).
        base = _topic(routerp)
        at = _topic(appp)   # one Topic per participant; both app writers reuse it
        cft = dds.DynamicData.ContentFilteredTopic(
            base, "route_cft", dds.Filter(f"{FIELD} = %0", ["'Platform_30'"]))
        route_reader = dds.DynamicData.DataReader(
            _subscriber(routerp, "PLATFORM"), cft)

        # (b1) app writer on the WRONG partition (CONTROL): must NEVER match.
        wrong = dds.DynamicData.DataWriter(  # noqa: F841
            _publisher(appp, "CONTROL"), at)
        if not stays_zero(lambda: len(route_reader.matched_publications)):
            raise SpikeError("[B] cross-partition writer MATCHED — false-green NOT dissolved")
        print("  [B1] cross-partition (CONTROL) writer never matched "
              "(matched_publications stayed 0 over the hold window)")

        # (b2) app writer on the RIGHT partition (PLATFORM): must match. Distinct Publisher
        # (different partition) but the same per-participant Topic.
        right = dds.DynamicData.DataWriter(  # noqa: F841
            _publisher(appp, "PLATFORM"), at)
        n, t = wait_matched(lambda: len(route_reader.matched_publications), 1)
        if t is None:
            raise SpikeError("[B] same-partition (PLATFORM) writer never matched")
        print(f"  [B2] same-partition (PLATFORM) writer matched in {t*1000:.0f}ms "
              f"(matched_publications={n})  PASS")
    finally:
        appp.close()
        routerp.close()


def part_c_created_then_observed(domain):
    print("Part C: a created-but-unmatched reader reports zero, then observes the peer")
    routerp = dds.DomainParticipant(domain, _udp())
    appp = dds.DomainParticipant(domain, _udp())
    try:
        route_reader = dds.DynamicData.DataReader(_subscriber(routerp), _topic(routerp))
        # No peer yet: the honest "built but not connected" signal is zero matches.
        if not stays_zero(lambda: len(route_reader.matched_publications), hold_s=2.0):
            raise SpikeError("[C] reader reported a match with no writer present")
        print("  [C] created reader with no writer: matched_publications=0 "
              "(honest 'not connected' signal)")
        # Peer appears -> observe the transition.
        app_writer = dds.DynamicData.DataWriter(_publisher(appp), _topic(appp))  # noqa: F841
        n, t = wait_matched(lambda: len(route_reader.matched_publications), 1)
        if t is None:
            raise SpikeError("[C] reader never observed the writer after it appeared")
        print(f"  [C] writer appeared -> observed in {t*1000:.0f}ms "
              f"(matched_publications={n})  PASS")
    finally:
        appp.close()
        routerp.close()


def main():
    print(f"matched-endpoints spike on domain {DOMAIN} (UDPv4-only)\n")
    failures = []
    parts = (part_a_match_truth,
             part_b_partition_dissolves_falsegreen,
             part_c_created_then_observed)
    for i, part in enumerate(parts):
        try:
            part(DOMAIN + i)
        except SpikeError as e:
            failures.append(str(e))
            print(f"  FAIL: {e}")
        print()
    if failures:
        print(f"SPIKE FAILED ({len(failures)} failure(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SPIKE PASSED: DDS matched_publications/matched_subscriptions are the connectivity "
          "authority — a partition mismatch yields zero matches (no false-green), and "
          "created-but-unmatched is an observable zero. Create-and-observe is viable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
