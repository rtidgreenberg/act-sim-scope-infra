# Spike plan: DP partition monitor — live table, matched + log-derived

## Motivation

Follow-up to `spikes/spdp2_partition_visibility/`. That spike confirmed there's no
diagnostic in the discovery API itself for a participant-partition mismatch, and separately
confirmed the internal middleware log (`STATUS_REMOTE` verbosity) emits a
`PRESParticipant_hasMatchingPartition:UNMATCH` line that names **both sides'** actual
partition values as plain text. This spike builds the actual tool: a live table of every
DomainParticipant the monitor knows about — via `discovered_participants()` for whichever
ones share a partition with the monitor ("matched"), and via log-parsing for whichever ones
don't ("unmatched", tracked from the `UNMATCH` line's partition strings). Then it stress
-tests the mechanism at a scale representative of the router mesh (~30 nodes) and figures
out what's actually needed to keep it manageable there.

## Design

The monitor is an ordinary `DomainParticipant` with the **default (empty) partition** — not
the wildcard-partition trick from the prior spike. Rationale: D87 Finding 3 already
established that non-team-scoped router participants (`control_wan`/`platform_wan`-style)
stay on the default partition, so a default-partition monitor naturally matches those via
normal discovery (no cooperation needed) and naturally *fails* to match every
`team_wan`-style concrete-partition participant — which is exactly the set the log-parsing
path needs to cover anyway. No wildcard, no risk of the monitor's own endpoints becoming
SEDP-visible to every team plane, no `user_data` cooperation required from existing router
code.

**Added mid-spike (see part E below): a second, wildcard-partition (`["*"]`) local
participant, purely for identity resolution.** The router already sets a real identity —
`participant_name = "<node>/<router>"`, `role_name = "act.router"` — on every participant
it creates (`router/src/core/ParticipantRegistry.cxx`). But the `UNMATCH` log line carries
no identity field, only a GUID, so the log-derived (unmatched) half of the table has no way
to attach a name from that message alone. A wildcard-partition sibling matches every peer
regardless of its own partition (verified pairwise, no bridging risk, in the prior spike),
so its real `participant_name` becomes readable via the standard
`discovered_participant_data()` API even for peers the main participant's posture would
never discover. `table()` joins the two local participants' independent views by GUID.

Corollary this forces: the two properties (name, team/partition) are **mutually exclusive
on a single local participant's own posture** — a wildcard partition matches everyone (so
it never triggers `UNMATCH`, hence never learns partition values) and `ParticipantBuiltinTopicData`
has no partition field at all even once matched (confirmed, `docs/product-gaps.md` LP-5);
a default/concrete partition can learn a mismatched peer's partition via the log but never
its name if it's never actually discovered. There is no single-participant posture that
gets both.

## Claims under test

**A. Matched half correctness.** Participants sharing the monitor's (default) partition
show up via `discovered_participant_data()`, keyed by GUID prefix (`key.value[0:3]`,
formatted to match the log's `0xXXXXXXXX,0xXXXXXXXX,0xXXXXXXXX` hex form).

**B. Unmatched half correctness, including multi-valued partitions.** Participants on
disjoint concrete partitions — including a multi-valued one (`["TEAM_A", "TEAM_C"]`, per
D83's multi-valued `participant_partition`) — get picked up via the `UNMATCH` log line, with
partition names parsed correctly (the wire format is `partitions ("TEAM_A,TEAM_C")` — one
quoted, comma-joined string, not separate quoted tokens; confirmed against a real capture
before writing the regex).

**C. Handler lifecycle safety (found accidentally, now a gating claim).** Registering
`dds.Logger.instance.output_handler(...)` and raising verbosity, then letting the Python
process exit normally **without** resetting them first, segfaults the interpreter —
reproduced with **zero** DomainParticipants involved, so it's a Logger-singleton/Python
-interpreter-teardown interaction, not a participant-close issue. Confirmed fix: call
`dds.Logger.instance.reset_output_handler()` and drop verbosity back down before the process
exits. The monitor's `close()`/shutdown path must do this, tested here as a gating claim
(exit code 0, no coredump) rather than an aside.

**D. Volume at ~30-node scale, and whether category-scoping actually reduces it.** Spin up
enough participants to approximate the router mesh (a mix of default-partition and
concrete-partition, including multi-valued) and measure captured log line rate under three
verbosity configurations: (1) global `STATUS_REMOTE` (baseline, the naive approach), (2)
`verbosity_by_category(LogCategory.discovery, STATUS_REMOTE)` (the first thing anyone would
try, since this is a *discovery* diagnostic), (3) `verbosity_by_category(LogCategory.entities,
STATUS_REMOTE)`. Bisected empirically before writing this plan: the `UNMATCH` message is
**not** tagged under `discovery` (category (2) captured 0 `UNMATCH` lines in a hand test) —
it's tagged under `entities`, which captures 100% of `UNMATCH` lines at roughly 1/6th the
line volume of global `STATUS_REMOTE`. This part re-verifies that ratio at scale and reports
a concrete lines/sec number to extrapolate from.

**E. Identity resolution for a log-derived (unmatched) entry.** A participant with a
mismatched concrete partition AND a real router-style `participant_name`/`role_name` (the
router's actual convention, `"<node>/<router>"` / `"act.router"`) never gets discovered by
the monitor's own posture. `table()` must still attach the correct name and role_name to
that entry, resolved via the wildcard identity sibling matching it independently and
joining by GUID — this is the claim that answers "can we get the router name associated
with a log-derived GUID."

## Method

Reuse `spikes/partition_retarget/partition_retarget_spike.py`'s `make_participant`/`fmt`/
`SpikeError` helpers. UDPv4-only, one domain, run from the repo (never `/tmp` — every
command in this investigation, including throwaway verification, runs from inside
`act-sim-scope-infra`).

Exit 0 = A, B, C hold. D is measurement-only (prints numbers, doesn't gate pass/fail) but
its output is what answers "how do we make this manageable at ~30 nodes" — see README.

## Findings that changed the design mid-spike (all empirically forced, not anticipated)

- **The `UNMATCH` message swaps its own partition values relative to their GUID labels.**
  Verified with two participants on distinct concrete partitions (`AAA`/`BBB`) and their
  real `instance_handle` GUIDs printed independently for ground truth: the value printed
  next to "Remote DP (GUID: X)" is actually X's counterpart's (the *local* one's) own
  partition, and vice versa. `partition_monitor.py`'s `_on_log` reads the printed field
  positions swapped to compensate. **Added to `docs/product-gaps.md`** — this makes the
  log-scraping workaround itself unreliable to hand-roll without hitting this.
- **The Logger is process-global, not scoped to the participant that registers it.** In a
  process hosting multiple local participants (exactly the router's own shape — multiple
  WAN/LAN participants per process), the handler receives `UNMATCH` lines for *every*
  co-located participant's mismatches, not just the monitor's own. Fixed by comparing the
  message's local-DP GUID against the monitor's own (`_own_guid_prefix`, derived from
  `str(participant.instance_handle)` — a different hex layout than the log's
  `0xXXXXXXXX,0xXXXXXXXX,0xXXXXXXXX` triples, requiring explicit reformatting, not a
  straight string comparison).
- **A late-joining monitor has a real, bounded discovery latency for the log-derived half
  — up to ~30s (one full SPDP re-announce period) — not instant, and not permanent.**
  Measured directly: participants created before the monitor existed were invisible to it
  for up to ~27–30s before all of them appeared. The matched half (`discovered_participants()`)
  has no such gap — DDS's own discovery protocol converges regardless of join order.
  Part D's automated run creates the monitor after the others specifically to measure this
  realistic (not artificially-favorable) ordering.
- **The default `ttl_s=30.0` raced the ~30s re-announce period and caused flapping** — a
  live peer's table entry disappeared and reappeared a few seconds later purely from timing
  jitter across many simultaneously-announcing participants. Fixed by defaulting to 90s (3x
  margin over the observed period) in `partition_monitor.py`.
- **28 participants co-located in one process is measurably slow/variable to construct and
  tear down** (intermittent multi-minute stalls during manual scale testing at that count).
  This is a property of cramming that many DomainParticipants into a single OS process, not
  of a ~30-router mesh made of ~30 separate processes (the real deployment shape, each with
  a handful of local participants) — so Part D's automated run uses 13 total participants
  for a reliable run, and the 28-participant numbers are reported in README as a manually
  -verified data point with that caveat attached, not re-run automatically every time.
