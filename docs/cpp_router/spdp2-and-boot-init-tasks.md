# Tasks: SPDP2-on-WAN feasibility, then wildcard-boot-then-revert init phase

Two sequential task assignments, each self-contained enough to hand to a fresh session with
no prior conversation context. **Do Task 1 first — its outcome changes Task 2's timing
assumptions.** Both are spikes: build under `spikes/<name>/` with `PLAN.md` + runner(s) +
`README.md`, following the pattern of `spikes/partition_retarget/`, `spikes/dp_partition_monitor/`,
and `spikes/spdp2_partition_visibility/` — read those READMEs for house style before starting.

Read first: `.github/copilot-instructions.md` (repo guardrails — vboxsf filesystem safety,
Connext env vars, the MCP-verification protocol, wire-capture conventions), and
`docs/cpp_router/design-decisions.md` entries **D73, D78, D83, D87** (participant-partition
isolation, the original SPDP2 proposal, multi-valued ADD/REMOVE partitions, and SPDP2's
retraction — all directly load-bearing for both tasks below).

---

## Task 1 — Is SPDP2 feasible on the WAN, root-caused (not just retracted)?

### Context

D78 proposed `builtin_discovery_plugins = SPDP2 | SEDP` on WAN participants so a live
`participant_partition` retarget propagates via SPDP2's reliable configuration channel
instead of waiting on plain SPDP's periodic best-effort announcement (measured
~10-20ms vs. ~86-684ms in `spikes/partition_retarget/` and `spikes/spdp2_wan_lan_mix/`).

D87 (2026-07-20) **retracted** this after building the real thing: every actual participant
in `router_main` is created **disabled**, has its builtin conditions attached, then is
`enable()`d only after `aws.start()` (D52's sequencing) — a pattern neither prior SPDP2 spike
exercised (both used immediate/default `autoenable`). A minimal `rti.connextdds` repro
isolating just that one variable (two SPDP2 participants on one domain, one created
disabled-then-`enable()`-after-a-delay, exactly like `ParticipantRegistry`) reproduced
**asymmetric, non-retrying discovery failure 3/3**: the earlier-enabled participant sees the
later one, but not vice versa, and it never self-heals (no periodic re-announce recovers it,
unlike plain SPDP). The same repro with immediate `autoenable` discovered symmetrically every
time. **D87's root cause for *why* the delayed-enable path breaks the handshake was left
unexamined** — that's this task.

Why it matters now: this session's live design discussion (not yet in design-decisions.md)
is considering a router startup pattern where a participant joins with
`participant_partition = ["*"]` (wildcard) before creating any routes/endpoints, collects a
full mesh roster (names via `participant_name`, presence via `discovered_participants()`),
then reverts to its real team-scoped partition before proceeding — see
`spikes/spdp2_partition_visibility/` and `spikes/dp_partition_monitor/` for the wildcard
-matching mechanism this rides on. Under **plain SPDP** (current router state, since SPDP2
is retracted), that revert is announcement-paced — `spikes/dp_partition_monitor/README.md`
measured a late-joining monitor's log-derived roster taking up to **~30s (one SPDP
re-announce period)** to converge, and by symmetry a revert-away could be similarly slow to
propagate to remotes. If SPDP2 *could* be made to work with the router's real disabled-then
-delayed-enable lifecycle, both the ongoing D83 team-retarget path and this new boot-time
wildcard-then-revert pattern would get a much smaller, more predictable exposure/convergence
window. This task decides whether that's worth pursuing before Task 2 builds on an assumed
timing model.

### Steps

1. **Reproduce D87's failing repro fresh** on this install to confirm the baseline still
   holds: two SPDP2 participants, one created disabled then `enable()`d after a delay
   (mirror `ParticipantRegistry.cxx`'s actual create-disabled → attach-conditions →
   `aws.start()` → `enable()` sequence as closely as a Python spike reasonably can — read
   that file plus `RouterController.cxx`/`.hpp` for the exact ordering). Confirm asymmetric,
   non-self-healing failure, 3/3.
2. **Instrument the wire, not just the API-level symptom.** Use this repo's established
   `dumpcap`/`tshark` methodology (`spikes/partition_retarget/bandwidth_compare.py` is the
   worked example; `.github/copilot-instructions.md`'s "Wire-level verification" section has
   the field/filter reference) to capture RTPS traffic for **both** the working
   (immediate-autoenable) and broken (delayed-enable) cases. Diff what SPDP2
   bootstrap/configuration messages are actually sent and received in each — is a
   bootstrap or configuration message sent *before* `enable()` that the late-enabling side
   never processes? Does the early-enabled side stop retrying after an initial attempt that
   arrived before the peer was ready? Get a concrete mechanistic answer, not a guess.
3. **Try candidate workarounds** informed by step 2 — e.g., forcing a liveliness
   assert/re-announce immediately after `enable()`, a short post-enable delay before relying
   on discovery, a `discovery_config` knob that changes retry behavior. Test each against the
   *real* delayed-enable pattern (not immediate-autoenable) for symmetric, self-healing
   discovery, 3/3 minimum per candidate.
4. **Cross-check any Connext-behavior claim against the MCP first, then verify against the
   build** per the repo's standard protocol (`.github/copilot-instructions.md` — check
   `docs/connext-ai-issues/connext-ai-issues.md` for a matching known-wrong entry before
   trusting an `ask_connext_question` answer; if the build contradicts the MCP, append a new
   entry there and push the submodule + re-pin the parent repo, per that doc's two-part sync
   instructions).
5. **Write up a definitive go/no-go**, in `spikes/spdp2_delayed_enable/README.md`:
   - **Go**: a workaround exists and is verified 3/3 against the real delayed-enable
     pattern. State exactly what it is and what config/code change it implies for
     `ParticipantRegistry.cxx`.
   - **No-go**: root cause is identified and no workaround was found. State the mechanism
     precisely (not just "it doesn't work") so a future attempt doesn't re-derive this.
   - Either way, add a dated entry to `docs/cpp_router/design-decisions.md` (a new D-number)
     cross-referencing D87, and — if the root cause reflects behavior RTI likely didn't
     intend or doesn't document (e.g., a handshake that should retry but doesn't) — add an
     entry to `docs/product-gaps.md` following the existing LP-N format.

### Deliverable / acceptance criteria

- `spikes/spdp2_delayed_enable/` with `PLAN.md`, runner script(s), `README.md`.
- A design-decisions.md entry stating go/no-go and why, referencing D87.
- If go: the specific fix, verified 3/3 against the real lifecycle pattern (not just
  immediate-autoenable).
- If no-go: mechanism identified precisely enough that Task 2 can assume plain-SPDP timing
  with confidence instead of an open question.

---

## Task 2 — Full init-phase spike: wildcard-boot-then-revert, before any routes exist

**Depends on Task 1's outcome** — use SPDP2 timing assumptions if Task 1 went "go," plain
-SPDP (~30s worst-case convergence, per `spikes/dp_partition_monitor/README.md`) if "no-go."

### Context

Live design discussion this session (not yet written up anywhere) converged on this pattern
for router startup:

1. Participant joins the WAN domain with `participant_partition = ["*"]` (wildcard) —
   **before creating any routes/topic sessions** (no DataWriters/DataReaders exist yet).
2. While wildcarded, it collects a full roster: every other participant's identity
   (`participant_name`/`role_name`, per `router/src/core/ParticipantRegistry.cxx`'s
   `"<node>/<router>"` / `"act.router"` convention) and presence
   (`discovered_participants()`). See `spikes/spdp2_partition_visibility/` (wildcard
   matching is pairwise, doesn't bridge two other participants to each other) and
   `spikes/dp_partition_monitor/` (the matched-vs-log-derived-vs-identity mechanics, and the
   `ParticipantBuiltinTopicData` has-no-partition-field gap, `docs/product-gaps.md` LP-5).
3. It reverts to its real (team-scoped, D83 protected-identity) partition.
4. **Only then** does it proceed with normal route/session creation.

Why this was considered safe: with zero endpoints existing during step 1-2, there's nothing
for SEDP to announce regardless of who matches the wildcarded participant — the metadata
-leak risk D85 flagged (WAN-participant-merge risked leaking team-topic SEDP metadata to C2)
doesn't apply here, because no team-topic metadata exists yet. The open question was whether
step 3→4 has a race: does a remote that matched the participant while it was wildcarded
retain stale "matched" state and potentially see routes created immediately after the
revert, before that remote's own view catches up (announcement-paced propagation, per D73/
D78/D87)? Resolved in-conversation: **only a real concern if route creation happens
immediately/atomically with the revert.** If the router's actual init sequence has any
natural gap between reverting the partition and building routes (config parsing, other
setup work), the remote's stale state has time to self-correct independent of exactly how
fast that correction is — this task should confirm that gap exists and is comfortable in the
*real* startup sequence, not assume it.

Two more open items from the discussion, not yet resolved:
- **Boot-time discovery storm shape.** If ~30 routers all do this "join-all" step
  simultaneously at startup, that's 30 participants briefly wildcard-matching each other at
  once. One-time cost, not steady-state — but its actual shape (peak rate, duration,
  total bytes) hasn't been measured.
- **Real D52 lifecycle integration.** Every prior spike in this space (including this
  task's own predecessors) risks testing a synthetic immediate-autoenable participant
  instead of the router's actual disabled-then-delayed-`enable()` sequence — exactly the gap
  that caused the SPDP2 surprise in D87. This task must exercise the pattern against that
  real lifecycle, not an idealized standalone script, or it risks the same class of miss.

### Steps

1. Build a spike participant that mirrors the router's **real** disabled-then-delayed
   -enable lifecycle (`ParticipantRegistry.cxx` / `RouterController.cxx` sequencing — same
   caveat as Task 1 step 1) with `participant_partition = ["*"]`, zero routes, alongside
   several peer participants representing a mix of default-partition and concrete
   -team-partition WAN members (reuse `spikes/dp_partition_monitor/partition_monitor.py`'s
   helpers where they fit).
2. Confirm the roster (names + presence) collected during the wildcard window matches
   ground truth for every peer.
3. Confirm **zero SEDP/endpoint metadata is exposed** during the wildcard window — every
   discovered peer's `matched_publications`/`matched_subscriptions` (or equivalent) stay at
   0 throughout, since no endpoints exist yet. Don't assume this — verify it.
4. Revert the spike participant's partition to a real team-scoped value (mirroring D83's
   `ADD_PARTICIPANT_PARTITION`/`REMOVE_PARTICIPANT_PARTITION` semantics — additive/removable
   from a multi-valued set, not a blanket replace). Confirm mutual invisibility is restored
   relative to out-of-team peers (same technique as `spikes/partition_retarget/` Part B/C).
5. **Route/session enablement is gated on an explicit verification signal, not elapsed
   time.** Per this session's direction: `team_wan` routes (`platform_team_to_wan`/
   `wan_team_to_platform`) must not enable until (a) the team join (`ADD_PARTICIPANT_PARTITION`)
   has actually been applied to the live participant, **and** (b) the revert-to-team-scoped
   partition has been positively confirmed in effect — e.g. re-querying the participant's own
   live `DomainParticipantQos.partition` and/or confirming mutual invisibility relative to a
   known out-of-team peer, not just "assume it's done because `set_qos` returned." Design and
   spike this as a concrete precondition check `RouterController`/`router_main` evaluates
   before flipping the route(s) active — replacing any timing-margin heuristic ("the gap is
   comfortably larger than propagation delay") with a real go/no-go signal the router checks.
   Still separately measure the natural gap in the real init sequence between revert and
   first route/endpoint creation, since the verification check itself takes nonzero time and
   that latency is part of what production needs to tolerate — but the *decision* to enable
   routes must never be timing-based alone.
6. Measure the boot-time discovery storm: N spike participants (representative WAN count,
   e.g. matching the ~30-router mesh size used in `spikes/dp_partition_monitor/`) all
   performing the wildcard-join step within a short window of each other. Use the
   `dumpcap`/`tshark` methodology to report peak rate, duration, and total bytes — same
   pattern as Task 1 step 2.
7. Write up `spikes/wildcard_boot_then_revert/README.md` with a go/no-go recommendation for
   adopting this as the router's actual startup sequence, and if adopted, what changes
   `ParticipantRegistry.cxx`/`RouterController.cxx`/`router_main.cxx` need.

### Deliverable / acceptance criteria

- `spikes/wildcard_boot_then_revert/` with `PLAN.md`, runner script(s), `README.md`.
- Empirical answers (not assumptions) for: metadata-leak-during-wildcard (should be zero,
  confirmed not assumed), the revert-to-route-creation timing margin against the router's
  *real* init sequence, and the boot-time storm shape at representative scale.
- A design-decisions.md entry (new D-number) with the go/no-go and, if adopted, the specific
  code changes implied.
