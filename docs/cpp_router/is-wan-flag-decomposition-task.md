# Task: Decompose the conflated `is_wan` participant flag into two distinct concepts

Self-contained enough to hand to a fresh session with no prior conversation context. Read
first: `.github/copilot-instructions.md` (repo guardrails, MCP-verification protocol) and
`docs/cpp_router/design-decisions.md` entries **D14, D18, D81, D82** (link-metrics
capture), **D83, D85, D87** (participant-partition scoping), and **D93** (the mesh-dashboard
team-grouping feature that surfaced this — it deliberately sidestepped `is_wan` rather than
touch it, and that decision is why this task exists).

## Context — one flag, two unrelated jobs, a misleading name

`ParticipantRegistry::Config::is_wan` (YAML `participants.<name>.wan: true`) is read in
three places, and they do **not** want the same set of participants:

1. **D83 protected-identity partition default** — `RouteConfigParser.cxx` (~line 236): if
   `is_wan`, seed the participant's `participant_partition` with its own `${node.name}`
   protected entry.
2. **D83 non-removable protection** — `RouterState.hpp`'s `is_protected_partition_name` is
   literally `ps.is_wan && candidate == node_name`; the `REMOVE_PARTICIPANT_PARTITION`
   accept path (`RouterController`) uses it to reject removing a node's own identity.
3. **Phase 9 link-stats WAN-leg detection** — `RouteEntityFactory.hpp` (~line 177) calls
   `registry_.is_wan(participant)` for both endpoints to decide whether a route leg is a
   "WAN leg" whose per-matched-peer protocol stats `RouteTopicRuntime` should poll (D81).

Jobs **1+2 are team-scoping** and want `team_wan` **only**. Job **3 is "this endpoint is on
the WAN"** and genuinely wants **both** `platform_wan` **and** `team_wan` (both are real WAN
links you'd want link metrics on). The single flag can't satisfy both.

The name compounds it: `control_wan`, `platform_wan`, and `team_wan` are **all** on WAN
domain 200, so "is_wan" reads as "is on the WAN" — but after D87 removed `wan: true` from
`control_wan`/`platform_wan`, the flag actually means "is team-partition-scoped," which is
the opposite of what a reader expects.

## The concrete suspected defect (verify first — do not assume)

Because D87 set `platform_wan`'s `is_wan` to **false**, `registry_.is_wan("platform_wan")`
now returns false, so a route leg living on `platform_wan` gets `reader_is_wan/
writer_is_wan == false` → `has_wan_leg() == false` → **its per-peer link stats are never
polled.** On a platform node, that's the entire platform↔C2 traffic family
(`platform_primary_status`, `platform_events`, `platform_detail_status`). The
`PresenceMonitor` `RouterHealth` bellwether pair (also on the presence/WAN participant) is
still polled via `IWanStatsSource`, so *some* WAN stats exist — but the actual data-route
legs on `platform_wan` appear to be uncovered.

**Step 0 of this task is to confirm or refute that empirically** — it is stated here as a
strong suspicion from code reading (D93 session), not an established fact. Was it always
this way, or did the D87 revert introduce it? Check `git log`/`git blame` on the
`platform_wan` `wan:` line and `RouteEntityFactory`'s WAN-leg logic; check whether
`test_link_stats.py`'s fixture (`e2e_link_stats.yaml`) even exercises a `platform_wan` data
leg or only the bellwether — if the test can't see the gap, that's part of the finding.

## Steps

### Part A — Establish ground truth

1. Reproduce the link-stats coverage question against a real 2-process run of the production
   `control-platform.yaml` (or a fixture that has a genuine `platform_wan` data leg with a
   matched remote peer): does `ActRouterLinkStats` advance for a `platform_wan` route leg's
   peer today, or only for the `RouterHealth` bellwether? Attribute by router name (D82).
   Wire-capture (tshark/dumpcap per `.github/copilot-instructions.md`) if the status topic
   alone is ambiguous.
2. Decide the intended policy with the product owner: **should** `platform_wan` data legs be
   link-stats-covered? (Near-certainly yes — it's the primary operational WAN link.) Record
   the answer; it determines whether this is a bug fix or a documented non-goal.

### Part B — Decompose

3. Split `Config::is_wan` into two orthogonal fields:
   - **`team_scoped`** (D83) — drives jobs 1+2 (protected-identity default + non-removable
     protection). `team_wan` only. This is the flag `is_protected_partition_name` and
     `RouteConfigParser`'s seeding key off.
   - **`on_wan`** (link-stats) — drives job 3 (WAN-leg detection). `platform_wan` **and**
     `team_wan` (and any future real WAN participant). `control_wan` is a judgment call —
     it carries the control→platform command leg on the C2 side, so likely yes too.
   Pick final names during implementation; `team_scoped`/`on_wan` are suggestions, not
   pinned. Keep the YAML backward-compatible or migrate both shipped configs in the same
   change (there are only two: `control-platform.yaml`, and the e2e fixtures).
4. Update the three read sites to use the correct new field each. Update
   `ParticipantRegistry` (the `is_wan_` map and `is_wan()` accessor) accordingly — likely
   two maps/accessors, or one struct.
5. Re-point D93's mesh-dashboard team_partition sourcing if desired: it currently looks up
   `team_wan` **by hardcoded name** in `router_main.cxx` specifically to avoid this flag. Once
   `team_scoped` exists and is trustworthy, that lookup *could* switch back to
   `registry` filtering by `team_scoped` (removing the hardcoded string) — optional, and
   only if it reads more clearly than the direct name lookup. Not required for correctness.

### Part C — Verify

6. `ctest` (4 targets) + full `router/test_e2e/` suite green. If Part A found the link-stats
   gap real and Part B fixes it, **add or extend a test that fails before the fix and passes
   after** — e.g. assert `ActRouterLinkStats` advances for a `platform_wan` data leg's peer,
   not just the bellwether (extend `test_link_stats.py`).
7. Add a dated `design-decisions.md` entry (amending D14/D81/D82/D83/D87) recording the
   decomposition and whatever Part A found about link-stats coverage — same pattern D87 used
   to amend D83/D78 with real implementation findings.

## Deliverable / acceptance criteria

- A definitive, empirically-checked answer to whether `platform_wan` data-route legs are
  link-stats-covered today (expected: not, since D87 — but verify, don't assume).
- `is_wan` replaced by two orthogonal, accurately-named flags, each read only where its
  concept applies; both shipped configs migrated; the misleading single flag gone.
- Green `ctest` + full e2e suite, including a test that would have caught the link-stats gap
  if Part A confirmed it.
- A `design-decisions.md` entry with the findings, referencing D14/D81/D82/D83/D87/D93.

## Explicit non-goals

- Does not change the team-scoping *behavior* (D83/D85/D87) — only how the participant that
  gets it is identified in config/code.
- Does not touch the mesh-dashboard feature (D93) beyond the optional Part B step 5
  re-pointing; that feature ships independently and correctly via the direct `team_wan`
  name lookup.
