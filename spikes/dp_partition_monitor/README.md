# Spike: dp_partition_monitor — live participant/partition table, matched + log-derived +
named

## What this proves

Follow-up to `spikes/spdp2_partition_visibility/`. This builds the actual tool: a live
table of every DomainParticipant the monitor knows about, combining THREE mechanisms:
`discovered_participants()` (for participants sharing the monitor's own — default —
partition), parsing of Connext's `PRESParticipant_hasMatchingPartition:UNMATCH` diagnostic
log line (for participants that don't match, whose partition string that line names as
plain text), and a second, wildcard-partition (`["*"]`) local participant purely for
identity resolution (real router name, via `participant_name`) across peers regardless of
whether they matched the monitor's own posture. The router already sets a real identity —
`ParticipantRegistry.cxx` writes `participant_name = "<node>/<router>"`, `role_name =
"act.router"` — on every participant it creates, so this is reading data the router already
publishes, not something requiring a router-side change. Implementation lives in
`partition_monitor.py` (`PartitionMonitor` class); the spike script exercises it.

**The question this answers directly: can a table entry built from the log-derived
(unmatched) half be resolved back to a router name?** Yes — Part E confirms it. The
`UNMATCH` log line itself carries no identity field, only a GUID, so name resolution can't
come from that message alone; it comes from the wildcard identity sibling matching that
same GUID through an entirely different (non-mismatched) channel and joining on it.

## How to run

```
python3 spikes/dp_partition_monitor/dp_partition_monitor_spike.py [base_domain]
```

## Result — PASS, stable across every run (A/B/C/E fast and 100% reproducible; D
non-gating, see below)

- **A (matched half):** participants sharing the monitor's default partition show up via
  `discovered_participant_data()`, keyed by GUID prefix, correctly excluding the monitor's
  own wildcard identity sibling (see finding 3 below) — consistently correct.
- **B (unmatched half, incl. multi-valued):** a single-partition (`TEAM_A`) and a
  multi-valued-partition (`["TEAM_B", "TEAM_C"]`, per D83) participant both show up via
  log-parsing with correctly parsed partition lists — consistently correct. The wire format
  for a multi-valued partition in the log is one quoted, comma-joined string
  (`partitions ("TEAM_B,TEAM_C")`), not separate quoted tokens.
- **C (handler lifecycle):** registering `dds.Logger.instance.output_handler(...)` with
  raised verbosity and never resetting it segfaults the interpreter on normal process exit
  — reproduced in a subprocess with **zero** DomainParticipants involved, so it's a
  Logger-singleton/interpreter-teardown interaction, not anything to do with closing a
  participant. `PartitionMonitor.close()` (reset handler + verbosity) is required and
  sufficient — confirmed both directions (crashes without it every time; clean exit with it
  every time). **Any caller of `PartitionMonitor` must call `close()` before the process
  exits**, on every exit path (including exceptions) — a `finally`/`atexit` guarantee, not
  optional cleanup.
- **E (identity resolution):** a participant with partition `["TEAM_A"]` and a real
  router-style identity (`participant_name = "Node1/RouterAlpha"`, `role_name =
  "act.router"`) never gets discovered by the monitor's own (default-partition) posture —
  it's log-derived only. `table()` still attaches its correct name and role_name, resolved
  through the wildcard identity sibling matching it on an entirely separate channel and
  joining by GUID — consistently correct, typically resolved in well under 100ms.
- **D (scale, non-gating):** 12 others (4 default, 4 single-partition, 4 multi-valued) +
  a 2-participant monitor converged correctly (4/4 matched, 8/8 unmatched) every time it
  completed; convergence time varied run to run (roughly 700ms–28s) — see "Scale findings"
  below for why, and for a separate manually-verified data point at ~28-in-one-process
  scale. D is measurement-only and doesn't gate pass/fail.

## Three real findings surfaced while building this

**1. The `UNMATCH` message swaps its own partition values relative to their GUID labels —
added to `docs/product-gaps.md`.** Two participants on distinct concrete partitions
(`AAA`/`BBB`), with both `instance_handle` GUIDs printed independently as ground truth,
produced: `Remote DP (GUID: <B's real GUID>) with partitions ("AAA") does not match with
local DP(GUID: <A's real GUID>) with partitions ("BBB")` — i.e. `A`'s own partition
(`AAA`) is printed next to `B`'s GUID under "Remote DP," and `B`'s own partition (`BBB`)
is printed next to `A`'s GUID under "local DP." The GUIDs are correctly attributed (they
match `instance_handle` and the RTPS frame prefix); only the partition-string fields are
swapped. `partition_monitor.py` reads the fields swapped to compensate — see the comment
in `_on_log`. Anyone hand-rolling this parser without independently verifying GUID-to
-partition attribution would silently build a table with every entry's partition
attributed to the wrong neighbor.

**2. The Logger is process-global — it doesn't distinguish which of several co-located
local participants a message is "about."** In a process with more than one local
DomainParticipant (the router's own shape: multiple WAN/LAN participants in one process),
the handler receives `UNMATCH` lines for mismatches between *any* two co-located
participants, including ones that don't involve the monitor at all. Confirmed directly:
without filtering, a monitor + two unrelated concrete-partition participants in one
process produced a spurious table entry for the *monitor's own GUID* (as seen from one of
the other participants' perspective) with an empty partition. Fixed by extracting the
monitor's own GUID from `str(participant.instance_handle)` (note: a different hex layout
than the log's `0xXXXXXXXX,0xXXXXXXXX,0xXXXXXXXX` triples — needs explicit reformatting,
not a raw string comparison) and discarding any message whose local-DP GUID doesn't match.

**3. `PartitionMonitor`'s own two local participants (main + wildcard identity sibling)
mutually match each other, purely as a side effect of correct, documented PARTITION QoS
semantics — not a bug, but a self-inflicted artifact that has to be filtered.** The Connext
MCP's own claim (independently confirmed in `spikes/spdp2_partition_visibility/`) that
"wildcard-only membership also implicitly carries the default (`\"\"`) partition" means the
wildcard identity participant (`[\"*\"]`) and the main default-partition (`[\"\"]`)
participant necessarily share membership in `\"\"` — so `matched()` initially picked up the
identity sibling itself as a 4th "peer" in what should have been a 3-peer test, and
`identities()` would symmetrically pick up the main participant as a peer of itself. Fixed
by having each local participant remember its own GUID and excluding it from the other's
results (`_own_guid`/`_identity_own_guid` in `partition_monitor.py`). Anyone combining a
wildcard participant with any other local participant IN THE SAME PROCESS needs this same
self-exclusion — it isn't specific to this monitor's exact design.

## Scale findings — ~30-node manageability

- **Category-scope the verbosity; don't raise it globally.** The `UNMATCH` message is
  tagged under `LogCategory.entities`, **not** `LogCategory.discovery` (the first thing
  anyone would try, since this is nominally a discovery diagnostic — confirmed empirically
  that `discovery`-only scoping captures zero `UNMATCH` lines). Scoping to `entities`
  (`dds.Logger.instance.verbosity_by_category(dds.LogCategory.entities,
  dds.Verbosity.STATUS_REMOTE)`, global verbosity left at a low level) captures 100% of the
  `UNMATCH` lines at roughly a quarter to half the total line volume of raising verbosity
  globally (24–48% fewer lines across runs) — real, if partial, noise reduction at the
  native-logging layer itself, not just something to filter in Python after the fact.
- **A late-joining monitor has a real, bounded discovery latency for the log-derived half —
  up to ~30s (one SPDP re-announce period) worst case, but often much faster in practice —
  not a permanent gap.** Manually verified at both 13-total and 28-total participant scale
  with just the single main participant: participants that already existed before the
  monitor's participant was created stayed invisible to the log-derived half for up to
  ~27–30s before all of them appeared. With the two-participant design (main + identity
  sibling) added for Part E/D, convergence was consistently much faster (roughly 700ms–1s
  across repeated runs) — plausibly because the identity sibling's own SPDP activity
  increases the chance of catching an existing peer's initial announce burst rather than
  waiting for its next periodic re-announce, though the exact mechanism wasn't isolated
  further. The matched half has no such gap either way (DDS's own discovery protocol
  converges regardless of join order). **Plan for up to ~30s worst case** when a monitor
  joins an already-running ~30-node mesh; treat faster convergence as a bonus, not a
  guarantee.
- **Set the TTL with real margin over the re-announce period, not equal to it.** At
  `ttl_s=30.0` (equal to the observed period), a live peer's log-derived table entry
  transiently disappeared and reappeared a few seconds later — ordinary timing jitter
  across many simultaneously-announcing participants was enough to race a TTL set at
  exactly the interval it's supposed to tolerate. `PartitionMonitor` now defaults to
  `ttl_s=90.0` (3x margin) — same design principle as a DDS liveliness lease duration
  needing headroom over its assertion period, just reapplied here since this TTL is
  homegrown, not DDS-native.
- **28 participants co-located in one process is measurably slow and variable to construct
  and tear down** (intermittent multi-minute stalls hit during manual testing at that
  count, not reproduced at 13). This is almost certainly a property of cramming that many
  DomainParticipants into a single OS process — sockets, discovery threads, and
  participant-index search all scale with local participant count in one process — **not**
  a property of a ~30-router mesh, which in the real deployment is ~30 separate processes,
  each with only a handful of local participants. Don't extrapolate the construction/
  teardown slowness to "the whole mesh is slow" — it isn't representative of that shape.
  The log-volume and convergence-time numbers above, measured with the monitor as a single
  process observing many *remote* (not co-located) participants, are the representative
  numbers for the real deployment.

## Practical recommendation for a production monitor

1. Run the monitor as its own **dedicated process**, not embedded in `router_main` — this
   both isolates the Logger's process-global blast radius (finding 2 above) from the
   router's own participants, and isolates the segfault-on-exit risk (part C) from the
   router's critical path.
2. Use both local participants (`resolve_identity=True`, the default) — the main
   default-partition one for matched peers + log-derived partitions, the wildcard sibling
   for real router names. Neither alone gives the full picture: the main participant's
   posture can tell you a peer's *team* (via the log, once corrected for the field-swap
   bug) but never its *name*, and the log message itself carries no name field at all;
   the wildcard sibling can get every peer's *name* but `ParticipantBuiltinTopicData` has
   no partition field, so it can never tell you a peer's *team*. `table()` already does
   this join by GUID — don't try to get both properties from one local participant.
3. Scope verbosity to `LogCategory.entities`, never raise it globally.
4. Expect up to ~30s worst case to reach a complete table after (re)starting the monitor
   against an already-running mesh; don't alarm on "missing" peers faster than that.
5. Use a TTL with real margin (3x+) over whatever the mesh's actual re-announce period is,
   to avoid false-eviction flapping.
6. Always call `close()` (or equivalent handler/verbosity reset) before the process exits,
   on every exit path.
