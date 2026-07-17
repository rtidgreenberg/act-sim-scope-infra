#!/usr/bin/env python3
"""Partition-retarget spike — measure participant-partition retarget timing (D73).

D73 (docs/cpp_router/design-decisions.md) makes `participant_partition` the sole
team-scope mechanism for the `platform-team` router instance, and claims a live
`SET_PARTICIPANT_PARTITION` retarget is "announcement-paced" -- slower to rematch than a
pub/sub-level (SEDP) partition change. That claim is sourced from the Connext MCP alone
and is flagged in the doc itself as unconfirmed. This spike measures the real timing.

Claims under test (see PLAN.md):
  A. Baseline: two participants sharing one participant_partition value match normally.
  B. Mismatch: two participants on different participant_partition values never match
     (matched_publications/matched_subscriptions held at 0), and separately: does the
     peer even show up in discovered_participants() (SPDP-level visibility) or not?
  C. THE MONEY CLAIM: retarget participant A's partition away from a shared value (while
     matched to B), then back. Measure, from EACH side's own matched_* view:
       t_local_unmatch / t_remote_unmatch   (A changed; A = "local", B = "remote")
       t_local_rematch / t_remote_rematch
  D. Same retarget-while-matched experiment, but at the Publisher/Subscriber (SEDP)
     partition level instead of the participant level -- a same-run comparison number.
  E (stretch): repeat C with a short discovery_config.participant_liveliness_assert_period
     to see whether rematch time scales with that knob. Informative only, not required.
  F: repeat C with builtin_discovery_plugins=SPDP2|SEDP -- RTI's discovery plugin that
     sends participant configuration changes (e.g. a partition change) to already-matched
     peers over a reliable channel instead of waiting on the periodic best-effort
     announcement. Tests whether SPDP2 actually closes the C/D gap.

This is a measurement spike: A/B are structural (must hold), C/D/E/F print observed
numbers without gating pass/fail.

Exit 0 = structural parts (A, B) held. Run:
  python3 spikes/partition_retarget/partition_retarget_spike.py [base_domain]
"""

import os
import sys
import time

os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

import rti.connextdds as dds  # noqa: E402

DEFAULT_BASE_DOMAIN = 111
TOPIC = "PartitionRetargetData"

# Generous timeouts: we don't know a priori how slow announcement-paced rematch is.
UNMATCH_TIMEOUT_S = 15.0
REMATCH_TIMEOUT_S = 60.0
POLL_S = 0.01


class SpikeError(AssertionError):
    pass


# --------------------------------------------------------------------------------- types
_type_cache = None


def data_type():
    global _type_cache
    if _type_cache is None:
        t = dds.StructType("PartitionRetargetData")
        t.add_member(dds.Member("source", dds.StringType(128), is_key=True))
        t.add_member(dds.Member("seq", dds.Uint64Type()))
        _type_cache = t
    return _type_cache


# ------------------------------------------------------------------------- entity builders
# SPDP2 (RTI's reliable-configuration-channel discovery plugin) is not interoperable with
# plain SPDP -- both participants in a part must use it together. SEDP is still required
# for endpoint discovery; the two are independent bitmask flags.
SPDP2_SEDP = (dds.DiscoveryConfigBuiltinPluginKindMask.SPDP2
              | dds.DiscoveryConfigBuiltinPluginKindMask.SEDP)


def make_participant(domain, participant_partition=None, fast_assert=False, spdp2=False):
    q = dds.DomainParticipantQos()
    q.transport_builtin = dds.TransportBuiltin.udpv4
    if participant_partition is not None:
        q.partition = dds.Partition([participant_partition])
    if fast_assert or spdp2:
        dc = q.discovery_config
        if fast_assert:
            dc.participant_liveliness_assert_period = dds.Duration.from_milliseconds(200)
        if spdp2:
            dc.builtin_discovery_plugins = SPDP2_SEDP
        q.discovery_config = dc
    return dds.DomainParticipant(domain, q)


def set_participant_partition(p, value):
    """Read-modify-write retarget, exactly the SET_PARTICIPANT_PARTITION mechanism."""
    q = p.qos
    q.partition = dds.Partition([value] if value else [])
    p.qos = q


def set_pubsub_partition(entity, value):
    """Same read-modify-write shape, but at the Publisher/Subscriber (SEDP) level."""
    q = entity.qos
    q.partition = dds.Partition([value] if value else [])
    entity.qos = q


def _rel(qos):
    qos.reliability = dds.Reliability(kind=dds.ReliabilityKind.RELIABLE)
    return qos


def make_writer(participant, topic, publisher_partition=None):
    pubq = dds.PublisherQos()
    if publisher_partition is not None:
        pubq.partition = dds.Partition([publisher_partition])
    pub = dds.Publisher(participant, pubq)
    return pub, dds.DynamicData.DataWriter(pub, topic, _rel(dds.DataWriterQos()))


def make_reader(participant, topic, subscriber_partition=None):
    subq = dds.SubscriberQos()
    if subscriber_partition is not None:
        subq.partition = dds.Partition([subscriber_partition])
    sub = dds.Subscriber(participant, subq)
    return sub, dds.DynamicData.DataReader(sub, topic, _rel(dds.DataReaderQos()))


# ------------------------------------------------------------------------------- polling
def wait_until(pred, timeout_s, poll_s=POLL_S):
    """Poll pred() until True or timeout. Return elapsed seconds, or None on timeout."""
    t0 = time.monotonic()
    deadline = t0 + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return time.monotonic() - t0
        time.sleep(poll_s)
    return time.monotonic() - t0 if pred() else None


def wait_matched(count_fn, timeout_s=UNMATCH_TIMEOUT_S):
    return wait_until(lambda: count_fn() >= 1, timeout_s)


def hold_never(preds, hold_s, poll_s=POLL_S):
    """Poll every predicate for the WHOLE hold window (no early exit) and record the
    first elapsed time (from a shared t0) each one went true, or None if it never did."""
    t0 = time.monotonic()
    deadline = t0 + hold_s
    first_true = {name: None for name in preds}
    while time.monotonic() < deadline:
        now = time.monotonic()
        for name, pred in preds.items():
            if first_true[name] is None and pred():
                first_true[name] = now - t0
        time.sleep(poll_s)
    return first_true


def track_transitions(preds, timeout_s, poll_s=POLL_S):
    """Poll every predicate in `preds` (name -> zero-arg bool fn) from ONE shared t0, so
    times are directly comparable instead of each wait starting its own clock after the
    previous one already returned. Returns {name: elapsed_seconds_or_None}."""
    t0 = time.monotonic()
    deadline = t0 + timeout_s
    results = {name: None for name in preds}
    while time.monotonic() < deadline and any(v is None for v in results.values()):
        now = time.monotonic()
        for name, pred in preds.items():
            if results[name] is None and pred():
                results[name] = now - t0
        time.sleep(poll_s)
    now = time.monotonic()
    for name, pred in preds.items():
        if results[name] is None and pred():
            results[name] = now - t0
    return results


def fmt(t):
    return "never" if t is None else f"{t*1000:.0f}ms"


# ------------------------------------------------------------------------------ the parts
def part_a_baseline(base):
    print("Part A: baseline -- shared participant_partition matches normally")
    pa = make_participant(base, participant_partition="SHARED")
    pb = make_participant(base, participant_partition="SHARED")
    try:
        ta = dds.DynamicData.Topic(pa, TOPIC, data_type())
        tb = dds.DynamicData.Topic(pb, TOPIC, data_type())
        _, writer = make_writer(pa, ta)
        _, reader = make_reader(pb, tb)
        elapsed = wait_matched(lambda: len(reader.matched_publications))
        if elapsed is None:
            raise SpikeError("[A] reader never matched writer with shared "
                              "participant_partition")
        print(f"  [A] matched in {fmt(elapsed)}  PASS")
    finally:
        pa.close()
        pb.close()


def part_b_mismatch_invisible(base):
    print("Part B: different participant_partition -- matched_* held at 0; "
          "SPDP visibility reported separately")
    pa = make_participant(base, participant_partition="TEAM_A")
    pb = make_participant(base, participant_partition="TEAM_B")
    try:
        ta = dds.DynamicData.Topic(pa, TOPIC, data_type())
        tb = dds.DynamicData.Topic(pb, TOPIC, data_type())
        _, writer = make_writer(pa, ta)
        _, reader = make_reader(pb, tb)
        first_true = hold_never({
            "matched": lambda: len(reader.matched_publications) != 0,
            "b_sees_a": lambda: len(pb.discovered_participants()) > 0,
            "a_sees_b": lambda: len(pa.discovered_participants()) > 0,
        }, hold_s=4.0)
        if first_true["matched"] is not None:
            raise SpikeError(f"[B] mismatched participant_partition matched at "
                              f"{fmt(first_true['matched'])} -- invisibility NOT holding")
        print("  [B1] matched_publications held at 0 across the hold window "
              "(polled continuously, not just checked once)")
        b_sees_a, a_sees_b = first_true["b_sees_a"], first_true["a_sees_b"]
        print(f"  [B2] SPDP visibility: B sees A's participant = {fmt(b_sees_a)}, "
              f"A sees B's participant = {fmt(a_sees_b)}  "
              f"({'mutually invisible at SPDP' if b_sees_a is None and a_sees_b is None else 'SPDP-visible, only SEDP/endpoint matching suppressed'})")
        print("  PASS")
    finally:
        pa.close()
        pb.close()


def _retarget_timing(label, base, retarget_fn, restore_fn, rematch_timeout=REMATCH_TIMEOUT_S,
                      fast_assert=False, spdp2=False, settle_s=0.0):
    """Shared driver for Parts C/D/E/F: two participants matched on 'SHARED', retarget A
    away (mismatch), measure unmatch timing on both sides, then retarget back and measure
    rematch timing on both sides. `retarget_fn(pa_or_entity_a)` mismatches; `restore_fn`
    restores. Returns a dict of the four timings."""
    print(label)
    pa = make_participant(base, participant_partition="SHARED", fast_assert=fast_assert,
                           spdp2=spdp2)
    pb = make_participant(base, participant_partition="SHARED", fast_assert=fast_assert,
                           spdp2=spdp2)
    try:
        ta = dds.DynamicData.Topic(pa, TOPIC, data_type())
        tb = dds.DynamicData.Topic(pb, TOPIC, data_type())
        pub, writer = make_writer(pa, ta, publisher_partition="SHARED")
        sub, reader = make_reader(pb, tb, subscriber_partition="SHARED")

        elapsed = wait_matched(lambda: len(reader.matched_publications))
        if elapsed is None:
            raise SpikeError(f"[{label}] baseline match never happened")
        print(f"  baseline matched in {fmt(elapsed)}")
        if settle_s > 0:
            # Let any one-time post-match handshake (e.g. SPDP2's reliable configuration
            # channel, per the MCP: "sent once when discovery completes") finish before
            # we retarget, so we're not accidentally measuring a still-settling channel.
            time.sleep(settle_s)
            print(f"  settled {settle_s:.1f}s before retargeting")

        # Away and back legs each poll ALL four signals from one shared t0 (rapid,
        # 10ms poll via track_transitions) so local/remote and match/SPDP timings are
        # directly comparable instead of each wait starting its own clock late.
        retarget_fn(pa, pub)
        away = track_transitions({
            "local_unmatch": lambda: len(writer.matched_subscriptions) == 0,
            "remote_unmatch": lambda: len(reader.matched_publications) == 0,
            "local_spdp_lost": lambda: len(pa.discovered_participants()) == 0,
            "remote_spdp_lost": lambda: len(pb.discovered_participants()) == 0,
        }, timeout_s=UNMATCH_TIMEOUT_S)
        print(f"  retarget away: t_local_unmatch={fmt(away['local_unmatch'])} "
              f"(A's own view), t_remote_unmatch={fmt(away['remote_unmatch'])} (B's view), "
              f"t_local_spdp_lost={fmt(away['local_spdp_lost'])}, "
              f"t_remote_spdp_lost={fmt(away['remote_spdp_lost'])}")

        restore_fn(pa, pub)
        back = track_transitions({
            "local_rematch": lambda: len(writer.matched_subscriptions) >= 1,
            "remote_rematch": lambda: len(reader.matched_publications) >= 1,
            "local_spdp_regained": lambda: len(pa.discovered_participants()) >= 1,
            "remote_spdp_regained": lambda: len(pb.discovered_participants()) >= 1,
        }, timeout_s=rematch_timeout)
        print(f"  retarget back: t_local_rematch={fmt(back['local_rematch'])} "
              f"(A's own view), t_remote_rematch={fmt(back['remote_rematch'])} (B's view), "
              f"t_local_spdp_regained={fmt(back['local_spdp_regained'])}, "
              f"t_remote_spdp_regained={fmt(back['remote_spdp_regained'])}")

        return {
            "t_local_unmatch": away["local_unmatch"],
            "t_remote_unmatch": away["remote_unmatch"],
            "t_local_spdp_lost": away["local_spdp_lost"],
            "t_remote_spdp_lost": away["remote_spdp_lost"],
            "t_local_rematch": back["local_rematch"],
            "t_remote_rematch": back["remote_rematch"],
            "t_local_spdp_regained": back["local_spdp_regained"],
            "t_remote_spdp_regained": back["remote_spdp_regained"],
        }
    finally:
        pa.close()
        pb.close()


def part_c_participant_partition_retarget(base):
    return _retarget_timing(
        "Part C: THE MONEY CLAIM -- participant_partition retarget timing",
        base,
        retarget_fn=lambda pa, pub: set_participant_partition(pa, "MISMATCH"),
        restore_fn=lambda pa, pub: set_participant_partition(pa, "SHARED"),
    )


def part_d_sedp_partition_retarget(base):
    return _retarget_timing(
        "Part D: SEDP comparison -- Publisher/Subscriber-level partition retarget timing",
        base,
        retarget_fn=lambda pa, pub: set_pubsub_partition(pub, "MISMATCH"),
        restore_fn=lambda pa, pub: set_pubsub_partition(pub, "SHARED"),
    )


def part_e_fast_assert_stretch(base):
    return _retarget_timing(
        "Part E (stretch): participant_partition retarget with a short "
        "participant_liveliness_assert_period (200ms) -- does rematch scale with it?",
        base,
        retarget_fn=lambda pa, pub: set_participant_partition(pa, "MISMATCH"),
        restore_fn=lambda pa, pub: set_participant_partition(pa, "SHARED"),
        fast_assert=True,
    )


def part_f_spdp2_retarget(base):
    return _retarget_timing(
        "Part F: SPDP2 comparison -- participant_partition retarget timing with "
        "builtin_discovery_plugins=SPDP2|SEDP (reliable configuration channel)",
        base,
        retarget_fn=lambda pa, pub: set_participant_partition(pa, "MISMATCH"),
        restore_fn=lambda pa, pub: set_participant_partition(pa, "SHARED"),
        spdp2=True,
        settle_s=2.0,
    )


def main():
    base = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BASE_DOMAIN
    print(f"partition_retarget spike on base domain {base} (UDPv4-only)\n")

    failures = []
    timings = {}

    for part, name in ((part_a_baseline, "A"), (part_b_mismatch_invisible, "B")):
        try:
            part(base + {"A": 0, "B": 1}[name])
        except SpikeError as e:
            failures.append(str(e))
            print(f"  FAIL: {e}")
        print()

    for part, name, offset in (
            (part_c_participant_partition_retarget, "C", 2),
            (part_d_sedp_partition_retarget, "D", 3),
            (part_e_fast_assert_stretch, "E", 4),
            (part_f_spdp2_retarget, "F", 5)):
        try:
            timings[name] = part(base + offset)
        except SpikeError as e:
            failures.append(str(e))
            print(f"  FAIL: {e}")
        print()

    print("=== Timing summary (participant-partition C vs SEDP D vs fast-assert E vs SPDP2 F) ===")
    header = (f"{'':22}{'C (participant)':>18}{'D (SEDP)':>18}"
              f"{'E (fast-assert)':>18}{'F (SPDP2)':>18}")
    print(header)
    for key in ("t_local_unmatch", "t_remote_unmatch", "t_local_spdp_lost",
                "t_remote_spdp_lost", "t_local_rematch", "t_remote_rematch",
                "t_local_spdp_regained", "t_remote_spdp_regained"):
        row = f"{key:22}"
        for name in ("C", "D", "E", "F"):
            row += f"{fmt(timings.get(name, {}).get(key)):>18}"
        print(row)
    print()

    if failures:
        print(f"SPIKE FAILED ({len(failures)} structural failure(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1

    c_remote_rematch = timings.get("C", {}).get("t_remote_rematch")
    d_remote_rematch = timings.get("D", {}).get("t_remote_rematch")
    f_remote_rematch = timings.get("F", {}).get("t_remote_rematch")
    if c_remote_rematch is not None and d_remote_rematch is not None:
        verdict = ("slower" if c_remote_rematch > d_remote_rematch
                   else "NOT slower (contradicts D73)" if c_remote_rematch < d_remote_rematch
                   else "about the same")
        print(f"D73 verdict: participant-partition rematch ({fmt(c_remote_rematch)}) is "
              f"{verdict} than SEDP-level rematch ({fmt(d_remote_rematch)}).")
    if f_remote_rematch is not None and c_remote_rematch is not None:
        spdp2_verdict = ("fixes it" if f_remote_rematch < c_remote_rematch / 2
                          else "no material improvement" if f_remote_rematch >= c_remote_rematch
                          else "partial improvement")
        print(f"SPDP2 verdict: participant-partition rematch under SPDP2 "
              f"({fmt(f_remote_rematch)}) vs. plain SPDP ({fmt(c_remote_rematch)}) -- "
              f"{spdp2_verdict}.")
    print("SPIKE STRUCTURALLY PASSED: baseline match holds (A), mismatched "
          "participant_partition holds matched_*=0 (B). See timing summary above for the "
          "D73 announcement-paced-vs-SEDP claim and the SPDP2 comparison.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
