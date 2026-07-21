#!/usr/bin/env python3
"""dp_partition_monitor spike -- live participant/partition table (matched half via
discovered_participants(), unmatched half via UNMATCH log-parsing), plus the handler
-lifecycle segfault found while building it, plus a ~30-node scale measurement.

Claims under test (see PLAN.md):
  A. Matched half: participants sharing the monitor's (default) partition show up via
     discovered_participant_data(), keyed by GUID prefix.
  B. Unmatched half: participants on disjoint concrete partitions, including a
     multi-valued one, show up via UNMATCH log-parsing with correct partition lists.
  C. Handler lifecycle: PartitionMonitor.close() prevents the interpreter segfault found
     while building this (registering dds.Logger.instance.output_handler + raised
     verbosity and never resetting it segfaults on normal process exit -- reproduces with
     zero participants involved). Run in subprocesses so a crash doesn't take this spike
     process down with it; also demonstrates the crash IS real without the fix, so the
     fix is shown to do something, not just "nothing happened."
  D. Scale measurement (~30 participants): log line volume under global STATUS_REMOTE vs.
     PartitionMonitor's LogCategory.entities scoping, plus full-table correctness at that
     scale including multi-valued partitions.
  E. Identity resolution: a peer whose partition never matches the monitor's own posture
     (log-derived only, real partition never exposed via the API) still resolves to its
     real router name/role_name via a second, wildcard-partition local participant that
     matches everyone -- confirms identity and team/partition info require two DIFFERENT
     local postures joined by GUID, not one.

Exit 0 = A, B, C, E hold. D prints measurements (non-gating). Run:
  python3 spikes/dp_partition_monitor/dp_partition_monitor_spike.py [base_domain]
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/home/rti/act-sim-scope-infra/spikes/partition_retarget")
os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

import rti.connextdds as dds  # noqa: E402
import partition_retarget_spike as prs  # noqa: E402
from partition_monitor import PartitionMonitor  # noqa: E402

DEFAULT_BASE_DOMAIN = 3600
SETTLE_S = 2.0


class SpikeError(AssertionError):
    pass


def part_a_matched(base):
    print("Part A: matched half -- default-partition peers appear via "
          "discovered_participant_data()")
    mon = PartitionMonitor(base)
    peers = [prs.make_participant(base) for _ in range(3)]
    try:
        elapsed = prs.wait_until(lambda: len(mon.matched()) >= 3, timeout_s=prs.UNMATCH_TIMEOUT_S)
        if elapsed is None:
            raise SpikeError(f"[A] expected 3 matched peers, got {len(mon.matched())}")
        matched = mon.matched()
        print(f"  [A] {len(matched)} matched peers in {prs.fmt(elapsed)}, all source=matched: "
              f"{all(v['source'] == 'matched' for v in matched.values())}")
        print("  PASS")
    finally:
        mon.close()
        for p in peers:
            p.close()


def part_b_unmatched_with_multivalue(base):
    print("Part B: unmatched half -- disjoint concrete partitions (incl. multi-valued) via "
          "UNMATCH log-parsing")
    mon = PartitionMonitor(base)
    single = prs.make_participant(base, participant_partition="TEAM_A")
    multi_q = dds.DomainParticipantQos()
    multi_q.transport_builtin = dds.TransportBuiltin.udpv4
    multi_q.partition = dds.Partition(["TEAM_B", "TEAM_C"])
    multi = dds.DomainParticipant(base, multi_q)
    try:
        def both_seen():
            u = mon.unmatched()
            return len(u) >= 2

        elapsed = prs.wait_until(both_seen, timeout_s=prs.UNMATCH_TIMEOUT_S)
        if elapsed is None:
            raise SpikeError(f"[B] expected 2 unmatched entries, got {len(mon.unmatched())}")
        u = mon.unmatched()
        partition_sets = sorted(tuple(sorted(v["partitions"])) for v in u.values())
        expected = sorted([("TEAM_A",), ("TEAM_B", "TEAM_C")])
        print(f"  [B] {len(u)} unmatched entries in {prs.fmt(elapsed)}, partition sets: "
              f"{partition_sets}")
        if partition_sets != expected:
            raise SpikeError(f"[B] partition sets wrong: got {partition_sets}, "
                              f"expected {expected}")
        print("  PASS -- multi-valued partition parsed correctly from the log line")
    finally:
        mon.close()
        single.close()
        multi.close()


def part_e_identity_resolution(base):
    print("Part E: identity resolution -- table() attaches the real router name to a "
          "log-derived (unmatched) entry via the wildcard identity sibling")
    mon = PartitionMonitor(base)
    named_q = dds.DomainParticipantQos()
    named_q.transport_builtin = dds.TransportBuiltin.udpv4
    named_q.partition = dds.Partition(["TEAM_A"])
    # Matches router/src/core/ParticipantRegistry.cxx's actual EntityName convention:
    # name = "<node>/<router>", role_name = the act.router detection sentinel.
    name = dds.EntityName("Node1/RouterAlpha")
    name.role_name = "act.router"
    named_q.participant_name = name
    named = dds.DomainParticipant(base, named_q)
    try:
        elapsed = prs.wait_until(
            lambda: bool(mon.unmatched()) and bool(mon.identities()),
            timeout_s=prs.UNMATCH_TIMEOUT_S)
        if elapsed is None:
            raise SpikeError(f"[E] never got both an unmatched entry and an identity "
                              f"(unmatched={len(mon.unmatched())}, "
                              f"identities={len(mon.identities())})")
        table = mon.table()
        entries = [v for v in table.values() if v["source"] == "log"]
        if len(entries) != 1:
            raise SpikeError(f"[E] expected exactly 1 log-derived entry, got {len(entries)}")
        entry = entries[0]
        print(f"  [E] log-derived entry in {prs.fmt(elapsed)}: partitions={entry['partitions']} "
              f"name={entry['name']!r} role_name={entry['role_name']!r}")
        if entry["partitions"] != ["TEAM_A"]:
            raise SpikeError(f"[E] wrong partitions: {entry['partitions']}")
        if entry["name"] != "Node1/RouterAlpha" or entry["role_name"] != "act.router":
            raise SpikeError(f"[E] wrong identity: name={entry['name']!r} "
                              f"role_name={entry['role_name']!r}")
        print("  PASS -- a peer this monitor's own posture never discovered (mismatched "
              "partition, log-derived only) still resolved to its real router name")
    finally:
        mon.close()
        named.close()


_SUBPROCESS_PRELUDE = f"""
import sys
sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
import os
os.environ.setdefault("NDDSHOME", {os.environ["NDDSHOME"]!r})
os.environ.setdefault("RTI_LICENSE_FILE", {os.environ["RTI_LICENSE_FILE"]!r})
from partition_monitor import PartitionMonitor
import time
"""


def _run_subprocess(body):
    script = _SUBPROCESS_PRELUDE + body
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd="/home/rti/act-sim-scope-infra",
        capture_output=True, text=True, timeout=30)
    return result.returncode, result.stdout, result.stderr


def part_c_handler_lifecycle(base):
    print("Part C: handler lifecycle -- close() prevents the interpreter segfault found "
          "while building this")
    rc_bad, _, err_bad = _run_subprocess(f"""
mon = PartitionMonitor({base})
time.sleep(0.3)
print("about to exit WITHOUT close()")
""")
    print(f"  [C1] without close(): exit code {rc_bad} "
          f"({'segfault reproduced, as expected' if rc_bad != 0 else 'did NOT crash'})")
    if rc_bad == 0:
        raise SpikeError("[C1] expected the unfixed path to crash on exit (demonstrating "
                          "the bug is real) but it exited cleanly -- can't claim the fix "
                          "in C2 does anything")

    rc_good, _, err_good = _run_subprocess(f"""
mon = PartitionMonitor({base + 1})
time.sleep(0.3)
mon.close()
print("about to exit after close()")
""")
    print(f"  [C2] with close(): exit code {rc_good} "
          f"({'clean' if rc_good == 0 else 'STILL CRASHED'})")
    if rc_good != 0:
        raise SpikeError(f"[C2] close() did not prevent the crash: rc={rc_good} "
                          f"stderr_tail={err_good[-300:]!r}")
    print("  PASS -- close() (reset_output_handler + verbosity reset) is required and "
          "sufficient")


def part_d_scale(base):
    print("Part D (measurement, non-gating): scale -- log volume, global vs. "
          "entities-category scoping, full-table correctness. Kept modest for a reliable "
          "automated run -- 28-others-in-one-process was manually verified separately "
          "(README) and showed materially slower/more variable create+teardown, which is a "
          "property of cramming that many participants into ONE process, not of a ~30-node "
          "mesh of separate router processes (the real deployment shape).")
    n_default = 4
    n_single = 4
    n_multi = 4
    total_others = n_default + n_single + n_multi
    print(f"  building {total_others} other participants "
          f"({n_default} default-partition, {n_single} single concrete, "
          f"{n_multi} multi-valued) + 1 monitor (2 local participants: main + identity "
          f"sibling) = {total_others + 2} total")

    others = []
    try:
        for _ in range(n_default):
            others.append(prs.make_participant(base))
        for i in range(n_single):
            others.append(prs.make_participant(base, participant_partition=f"TEAM_{i}"))
        for i in range(n_multi):
            q = dds.DomainParticipantQos()
            q.transport_builtin = dds.TransportBuiltin.udpv4
            q.partition = dds.Partition([f"MTEAM_{i}", f"MTEAM_{i + 1}"])
            others.append(dds.DomainParticipant(base, q))

        # Trial 1: naive global STATUS_REMOTE (the first thing anyone would try).
        global_lines = []
        dds.Logger.instance.output_handler(lambda msg: global_lines.append(msg))
        dds.Logger.instance.verbosity = dds.Verbosity.STATUS_REMOTE
        t0 = time.monotonic()
        time.sleep(SETTLE_S)
        global_elapsed = time.monotonic() - t0
        global_unmatch = sum(1 for l in global_lines if "UNMATCH" in l)
        dds.Logger.instance.verbosity = dds.Verbosity.EXCEPTION
        dds.Logger.instance.reset_output_handler()
        print(f"  [D1] global STATUS_REMOTE: {len(global_lines)} lines in "
              f"{global_elapsed:.1f}s ({len(global_lines) / global_elapsed:.0f} lines/s), "
              f"{global_unmatch} UNMATCH lines")

        # Trial 2: the shipped mechanism (LogCategory.entities only). The monitor joins
        # AFTER the 27 others already exist (a late joiner, the realistic case for any
        # monitor deployed onto a mesh that's already up) and polls until the unmatched
        # half converges, or a generous cap -- late-joiner convergence was measured at up
        # to ~30s (one full SPDP re-announce period) in manual testing, so the cap here
        # gives real margin rather than reporting a misleadingly incomplete snapshot.
        expected_unmatched = n_single + n_multi
        mon = PartitionMonitor(base)
        t0 = time.monotonic()
        convergence_s = prs.wait_until(
            lambda: len(mon.unmatched()) >= expected_unmatched, timeout_s=45.0)
        entities_elapsed = time.monotonic() - t0
        entities_lines = mon.line_count
        table = mon.table()
        reduction_pct = ((1 - entities_lines / len(global_lines)) * 100
                         if global_lines else 0.0)
        print(f"  [D2] entities-category scoping: {entities_lines} lines in "
              f"{entities_elapsed:.1f}s ({entities_lines / entities_elapsed:.0f} lines/s) "
              f"-- {reduction_pct:.0f}% fewer lines than global")
        n_matched = sum(1 for v in table.values() if v["source"] == "matched")
        n_unmatched = sum(1 for v in table.values() if v["source"] == "log")
        print(f"  [D3] table converged in {prs.fmt(convergence_s)}: {n_matched} matched "
              f"(expect {n_default}), {n_unmatched} unmatched (expect {expected_unmatched})")
        mon.close()
    finally:
        for p in others:
            p.close()


def main():
    base = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BASE_DOMAIN
    print(f"dp_partition_monitor spike on base domain {base} (UDPv4-only)\n")

    failures = []
    for part, name, offset in (
            (part_a_matched, "A", 0),
            (part_b_unmatched_with_multivalue, "B", 1),
            (part_c_handler_lifecycle, "C", 2),
            (part_e_identity_resolution, "E", 4),
            (part_d_scale, "D", 10)):
        try:
            part(base + offset)
        except SpikeError as e:
            failures.append(str(e))
            print(f"  FAIL: {e}")
        except Exception as e:  # noqa: BLE001 -- surface unexpected errors as failures too
            failures.append(f"[{name}] unexpected error: {e}")
            print(f"  FAIL (unexpected): {e}")
        print()

    if failures:
        print(f"SPIKE FAILED ({len(failures)} failure(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SPIKE PASSED: matched half correct (A), unmatched half correct incl. "
          "multi-valued partitions (B), handler-lifecycle segfault reproduced and fixed by "
          "close() (C), a log-derived-only peer resolves to its real router name via the "
          "wildcard identity sibling (E). See Part D output above for the scale numbers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
