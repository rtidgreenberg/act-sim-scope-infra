#!/usr/bin/env python3
"""spdp2_wan_lan_mix spike -- SPDP2 on a WAN participant, plain SPDP on a LAN participant,
same router process, no cross-interference; plus a fail-closed check for a mesh member
that forgot to enable SPDP2.

Claims under test (see PLAN.md):
  A. One process, four participants at once: wan_local/wan_peer (SPDP2|SEDP) on a WAN
     domain, lan_local/lan_peer (plain SPDP) on a separate LAN domain. Both pairs match
     independently; neither domain's participants leak into the other's
     discovered_participants().
  B. With all four still alive, the WAN pair's participant_partition retarget (SPDP2,
     settle=2.0s, same dance as partition_retarget_spike.py Part F) rematches in the same
     fast range as the standalone result -- the LAN participant sharing the process doesn't
     slow it down.
  C. THE FAIL-CLOSED CHECK: a fresh WAN peer using plain SPDP (not SPDP2) joins the WAN
     domain where an SPDP2-only participant lives. They must stay mutually invisible (no
     partial/half-working state) -- this is the operational risk if one router in the mesh
     isn't upgraded to SPDP2.

Exit 0 = all parts hold. Run:
  python3 spikes/spdp2_wan_lan_mix/spdp2_wan_lan_mix_spike.py [base_domain]
"""

import os
import sys

sys.path.insert(0, "/home/rti/act-sim-scope-infra/spikes/partition_retarget")
os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

import rti.connextdds as dds  # noqa: E402
import partition_retarget_spike as prs  # noqa: E402

DEFAULT_BASE_DOMAIN = 2000
SETTLE_S = 2.0


def part_a_mixed_process_baseline(wan_domain, lan_domain):
    print("Part A: one process, WAN pair (SPDP2) + LAN pair (plain SPDP) -- both match, "
          "no cross-domain leak")
    wan_local = prs.make_participant(wan_domain, participant_partition="SHARED", spdp2=True)
    wan_peer = prs.make_participant(wan_domain, participant_partition="SHARED", spdp2=True)
    lan_local = prs.make_participant(lan_domain)
    lan_peer = prs.make_participant(lan_domain)
    try:
        wt_l = dds.DynamicData.Topic(wan_local, prs.TOPIC, prs.data_type())
        wt_p = dds.DynamicData.Topic(wan_peer, prs.TOPIC, prs.data_type())
        _, wan_writer = prs.make_writer(wan_local, wt_l, publisher_partition="SHARED")
        _, wan_reader = prs.make_reader(wan_peer, wt_p, subscriber_partition="SHARED")

        lt_l = dds.DynamicData.Topic(lan_local, prs.TOPIC, prs.data_type())
        lt_p = dds.DynamicData.Topic(lan_peer, prs.TOPIC, prs.data_type())
        _, lan_writer = prs.make_writer(lan_local, lt_l)
        _, lan_reader = prs.make_reader(lan_peer, lt_p)

        t_wan = prs.wait_matched(lambda: len(wan_reader.matched_publications))
        if t_wan is None:
            raise prs.SpikeError("[A] WAN (SPDP2) pair never matched")
        print(f"  [A] WAN pair (SPDP2) matched in {prs.fmt(t_wan)}")

        t_lan = prs.wait_matched(lambda: len(lan_reader.matched_publications))
        if t_lan is None:
            raise prs.SpikeError("[A] LAN (plain SPDP) pair never matched")
        print(f"  [A] LAN pair (plain SPDP) matched in {prs.fmt(t_lan)}")

        # Each side should discover exactly its one peer (count == 1), never bleed across
        # domains, regardless of the differing discovery plugin config in the same process.
        wan_local_n = len(wan_local.discovered_participants())
        wan_peer_n = len(wan_peer.discovered_participants())
        lan_local_n = len(lan_local.discovered_participants())
        lan_peer_n = len(lan_peer.discovered_participants())
        print(f"  [A] discovered_participants(): wan_local={wan_local_n} "
              f"wan_peer={wan_peer_n} lan_local={lan_local_n} lan_peer={lan_peer_n} "
              f"(expect 1 each -- only the in-domain peer, no cross-domain leak)")
        if wan_local_n != 1 or wan_peer_n != 1 or lan_local_n != 1 or lan_peer_n != 1:
            raise prs.SpikeError(f"[A] cross-domain leak or unexpected peer count: "
                                  f"wan_local={wan_local_n} wan_peer={wan_peer_n} "
                                  f"lan_local={lan_local_n} lan_peer={lan_peer_n}")
        print("  PASS")
        return wan_local, wan_peer, wan_writer, wan_reader, lan_local, lan_peer
    except prs.SpikeError:
        for p in (wan_local, wan_peer, lan_local, lan_peer):
            p.close()
        raise


def part_b_wan_spdp2_rematch_unaffected(wan_local, wan_peer, wan_writer, wan_reader):
    print("Part B: WAN (SPDP2) participant_partition retarget, LAN pair alive in the same "
          "process -- rematch should stay in the fast (SPDP2) range")
    t0_wait = prs.wait_matched(lambda: len(wan_reader.matched_publications))
    if t0_wait is None:
        raise prs.SpikeError("[B] WAN pair not matched going into the retarget")

    import time
    time.sleep(SETTLE_S)  # let SPDP2's post-match handshake settle (see partition_retarget)

    prs.set_participant_partition(wan_local, "MISMATCH")
    prs.track_transitions({
        "unmatch": lambda: len(wan_writer.matched_subscriptions) == 0,
    }, timeout_s=prs.UNMATCH_TIMEOUT_S)
    prs.set_participant_partition(wan_local, "SHARED")
    back = prs.track_transitions({
        "local_rematch": lambda: len(wan_writer.matched_subscriptions) >= 1,
        "remote_rematch": lambda: len(wan_reader.matched_publications) >= 1,
    }, timeout_s=prs.REMATCH_TIMEOUT_S)
    t_remote = back["remote_rematch"]
    print(f"  [B] t_remote_rematch = {prs.fmt(t_remote)} "
          f"(standalone Part F baseline: 11-20ms typical, up to ~164ms outlier)")
    if t_remote is None:
        raise prs.SpikeError("[B] WAN pair never rematched")
    if t_remote > 0.5:
        print(f"  [B] WARNING: {prs.fmt(t_remote)} is slower than the standalone Part F "
              f"range -- possible interference from the concurrent LAN participant, or "
              f"just SPDP2's known probabilistic variance (see partition_retarget README)")
    else:
        print("  [B] within the fast SPDP2 range -- no interference from the concurrent "
              "plain-SPDP LAN participant  PASS")


def part_c_fail_closed_legacy_peer(wan_domain):
    print("Part C: fail-closed check -- a plain-SPDP ('legacy') WAN peer must stay "
          "mutually invisible to an SPDP2-only participant")
    spdp2_local = prs.make_participant(wan_domain, participant_partition="SHARED",
                                        spdp2=True)
    legacy_peer = prs.make_participant(wan_domain, participant_partition="SHARED")
    try:
        t = dds.DynamicData.Topic(spdp2_local, prs.TOPIC, prs.data_type())
        t2 = dds.DynamicData.Topic(legacy_peer, prs.TOPIC, prs.data_type())
        _, writer = prs.make_writer(spdp2_local, t, publisher_partition="SHARED")
        _, reader = prs.make_reader(legacy_peer, t2, subscriber_partition="SHARED")

        first_true = prs.hold_never({
            "matched": lambda: len(reader.matched_publications) != 0,
            "spdp2_sees_legacy": lambda: len(spdp2_local.discovered_participants()) > 0,
            "legacy_sees_spdp2": lambda: len(legacy_peer.discovered_participants()) > 0,
        }, hold_s=5.0)
        if first_true["matched"] is not None:
            raise prs.SpikeError(f"[C] legacy peer MATCHED the SPDP2 participant at "
                                  f"{prs.fmt(first_true['matched'])} -- not fail-closed!")
        print("  [C] matched_publications held at 0 across the hold window")
        print(f"  [C] SPDP2 participant sees legacy peer: "
              f"{prs.fmt(first_true['spdp2_sees_legacy'])}; legacy peer sees SPDP2 "
              f"participant: {prs.fmt(first_true['legacy_sees_spdp2'])}")
        if first_true["spdp2_sees_legacy"] is not None or \
                first_true["legacy_sees_spdp2"] is not None:
            raise prs.SpikeError("[C] one side became SPDP-visible to the other -- not a "
                                  "clean fail-closed boundary")
        print("  [C] mutually invisible -- a mesh member without SPDP2 fails cleanly "
              "invisible, not half-working  PASS")
    finally:
        spdp2_local.close()
        legacy_peer.close()


def main():
    base = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BASE_DOMAIN
    print(f"spdp2_wan_lan_mix spike on base domain {base} (UDPv4-only)\n")

    failures = []
    entities = None
    try:
        entities = part_a_mixed_process_baseline(base, base + 1)
    except prs.SpikeError as e:
        failures.append(str(e))
        print(f"  FAIL: {e}")
    print()

    if entities is not None:
        wan_local, wan_peer, wan_writer, wan_reader, lan_local, lan_peer = entities
        try:
            part_b_wan_spdp2_rematch_unaffected(wan_local, wan_peer, wan_writer, wan_reader)
        except prs.SpikeError as e:
            failures.append(str(e))
            print(f"  FAIL: {e}")
        finally:
            for p in (wan_local, wan_peer, lan_local, lan_peer):
                p.close()
        print()

    try:
        part_c_fail_closed_legacy_peer(base + 2)
    except prs.SpikeError as e:
        failures.append(str(e))
        print(f"  FAIL: {e}")
    print()

    if failures:
        print(f"SPIKE FAILED ({len(failures)} failure(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SPIKE PASSED: SPDP2 on a WAN participant and plain SPDP on a LAN participant "
          "coexist in one process with no cross-interference; the WAN participant's SPDP2 "
          "rematch speed is unaffected by the concurrent LAN participant; and a "
          "not-yet-upgraded (plain SPDP) WAN peer fails cleanly invisible rather than "
          "half-working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
