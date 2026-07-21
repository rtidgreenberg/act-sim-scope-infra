#!/usr/bin/env python3
"""partition-visibility monitor spike -- can a single participant see every discovered
participant's partition, even mismatched ones, without bridging them to each other?

D73/D87 Finding 2 established that participants with disjoint concrete
`participant_partition` values are mutually invisible (not just SEDP-blocked). This spike
checks whether a wildcard-partition "monitor" participant can get discovery visibility into
all of them anyway -- for a global roster/C2 use case -- without breaking that isolation
between the participants it's observing.

Claims under test (see PLAN.md):
  A. Control: disjoint concrete participant_partition -> mutual invisibility (reconfirm).
  B. A participant_partition=["*"] monitor sees BOTH disjoint-partition peers.
  C. The monitor's presence does NOT bridge the two peers to each other.
  D. Does ParticipantBuiltinTopicData expose the remote's actual partition value? (MCP
     claimed yes; a static dir() check of this build's Python binding found no such field.)
  E. Workaround: if D is negative, can user_data carry the partition value instead?
  F (stretch, informative only): repeat B/C/E under SPDP2|SEDP with immediate autoenable.

Exit 0 = A, B, C hold (the safety-critical claims). Run:
  python3 spikes/spdp2_partition_visibility/spdp2_partition_visibility_spike.py [base_domain]
"""

import os
import sys

sys.path.insert(0, "/home/rti/act-sim-scope-infra/spikes/partition_retarget")
os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

import rti.connextdds as dds  # noqa: E402
import partition_retarget_spike as prs  # noqa: E402

DEFAULT_BASE_DOMAIN = 3000
HOLD_S = 4.0


def _set_user_data(p, value_bytes):
    q = p.qos
    q.user_data = dds.UserData(list(value_bytes))
    p.qos = q


def _discovered_user_data(p, handle):
    data = p.discovered_participant_data(handle)
    return bytes(data.user_data.value) if data.user_data is not None else b""


def _handle_for_user_data(local_p, target_bytes):
    """Find a discovered participant by its user_data content -- participant_name.name
    defaults to None for every participant in this binding, so it can't disambiguate
    which remote a handle belongs to. user_data is set uniquely per participant below."""
    for h in local_p.discovered_participants():
        try:
            data = local_p.discovered_participant_data(h)
        except dds.Error:
            continue
        ud = bytes(data.user_data.value) if data.user_data is not None else b""
        if ud == target_bytes:
            return h
    return None


def part_a_control_mismatch_invisible(base):
    print("Part A (control): disjoint concrete participant_partition -- reconfirm mutual "
          "invisibility")
    a = prs.make_participant(base, participant_partition="TEAM_A")
    b = prs.make_participant(base, participant_partition="TEAM_B")
    try:
        first_true = prs.hold_never({
            "a_sees_b": lambda: len(a.discovered_participants()) > 0,
            "b_sees_a": lambda: len(b.discovered_participants()) > 0,
        }, hold_s=HOLD_S)
        if first_true["a_sees_b"] is not None or first_true["b_sees_a"] is not None:
            raise prs.SpikeError(
                f"[A] disjoint concrete partitions did NOT stay mutually invisible: "
                f"a_sees_b={prs.fmt(first_true['a_sees_b'])} "
                f"b_sees_a={prs.fmt(first_true['b_sees_a'])}")
        print("  [A] held mutually invisible across the hold window  PASS")
    finally:
        a.close()
        b.close()


def part_bcde_wildcard_monitor(base, spdp2=False):
    tag = "SPDP2" if spdp2 else "plain SPDP"
    print(f"Part B/C/D/E ({tag}): wildcard monitor visibility, no bridging, partition-field "
          f"check, user_data workaround")
    m = prs.make_participant(base, participant_partition="*", spdp2=spdp2)
    a = prs.make_participant(base, participant_partition="TEAM_A", spdp2=spdp2)
    b = prs.make_participant(base, participant_partition="TEAM_B", spdp2=spdp2)
    _set_user_data(m, b"MONITOR")
    _set_user_data(a, b"TEAM_A")
    _set_user_data(b, b"TEAM_B")
    try:
        t_m_sees_a = prs.wait_until(
            lambda: _handle_for_user_data(m, b"TEAM_A") is not None,
            timeout_s=prs.UNMATCH_TIMEOUT_S)
        t_m_sees_b = prs.wait_until(
            lambda: _handle_for_user_data(m, b"TEAM_B") is not None,
            timeout_s=prs.UNMATCH_TIMEOUT_S)
        print(f"  [B] monitor sees A: {prs.fmt(t_m_sees_a)}; monitor sees B: "
              f"{prs.fmt(t_m_sees_b)}")
        if t_m_sees_a is None or t_m_sees_b is None:
            raise prs.SpikeError(
                f"[B] wildcard monitor failed to discover both peers "
                f"(sees_a={prs.fmt(t_m_sees_a)}, sees_b={prs.fmt(t_m_sees_b)})")
        print("  [B] wildcard monitor discovers both disjoint-partition peers  PASS")

        first_true = prs.hold_never({
            "a_sees_b": lambda: _handle_for_user_data(a, b"TEAM_B") is not None,
            "b_sees_a": lambda: _handle_for_user_data(b, b"TEAM_A") is not None,
        }, hold_s=HOLD_S)
        if first_true["a_sees_b"] is not None or first_true["b_sees_a"] is not None:
            raise prs.SpikeError(
                f"[C] monitor's presence BRIDGED the two peers: "
                f"a_sees_b={prs.fmt(first_true['a_sees_b'])} "
                f"b_sees_a={prs.fmt(first_true['b_sees_a'])}")
        print("  [C] A and B still mutually invisible with the monitor present  PASS")

        handle_a = _handle_for_user_data(m, b"TEAM_A")
        handle_b = _handle_for_user_data(m, b"TEAM_B")
        btd_a = m.discovered_participant_data(handle_a)
        partition_attrs = [attr for attr in dir(btd_a) if "part" in attr.lower()]
        print(f"  [D] ParticipantBuiltinTopicData attrs matching 'part*': {partition_attrs}")
        has_partition_field = any(attr not in ("partial_configuration", "participant_name")
                                   for attr in partition_attrs)
        if has_partition_field:
            print("  [D] a partition-shaped field EXISTS on this build -- MCP claim "
                  "confirmed, inspect it directly (see printed attrs above)")
        else:
            print("  [D] no partition-shaped field beyond partial_configuration/"
                  "participant_name -- MCP's 'ParticipantBuiltinTopicData includes a "
                  "partition field' claim does NOT hold on this rti.connextdds 7.7 Python "
                  "binding; falling through to the user_data workaround (E)")

        ud_a = _discovered_user_data(m, handle_a)
        ud_b = _discovered_user_data(m, handle_b)
        print(f"  [E] monitor read back user_data: A={ud_a!r} B={ud_b!r}")
        if ud_a != b"TEAM_A" or ud_b != b"TEAM_B":
            raise prs.SpikeError(
                f"[E] user_data workaround failed to round-trip each peer's partition "
                f"value: got A={ud_a!r} B={ud_b!r}")
        print("  [E] user_data workaround round-trips each peer's real partition value "
              "through the monitor, with A/B still mutually invisible  PASS")
    finally:
        m.close()
        a.close()
        b.close()


def main():
    base = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BASE_DOMAIN
    print(f"spdp2_partition_visibility spike on base domain {base} (UDPv4-only)\n")

    failures = []

    try:
        part_a_control_mismatch_invisible(base)
    except prs.SpikeError as e:
        failures.append(str(e))
        print(f"  FAIL: {e}")
    print()

    try:
        part_bcde_wildcard_monitor(base + 1, spdp2=False)
    except prs.SpikeError as e:
        failures.append(str(e))
        print(f"  FAIL: {e}")
    print()

    try:
        part_bcde_wildcard_monitor(base + 2, spdp2=True)
    except prs.SpikeError as e:
        failures.append(f"[SPDP2 stretch, informative] {e}")
        print(f"  INFO (non-gating, SPDP2 stretch): {e}")
    print()

    if failures:
        print(f"SPIKE FAILED ({len(failures)} failure(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SPIKE PASSED: disjoint participant_partition values stay mutually invisible (A); "
          "a wildcard-partition monitor discovers both without bridging them to each other "
          "(B/C); the monitor's own real-partition-value visibility depends on whether "
          "ParticipantBuiltinTopicData exposes it (D) with user_data as a working fallback "
          "either way (E).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
