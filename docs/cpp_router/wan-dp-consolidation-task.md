# Task 3: WAN DomainParticipant consolidation — does it actually buy diagnostic simplicity?

Self-contained enough to hand to a fresh session with no prior conversation context. Read
first: `.github/copilot-instructions.md` (repo guardrails, MCP-verification protocol,
wire-capture conventions) and `docs/cpp_router/design-decisions.md` entries **D73, D83,
D85, D87**.

## Context

A router process today owns (at minimum) two separate WAN `DomainParticipant`s per D85:
- `platform_wan` (or `control_wan`) — default (empty) partition, unconditionally matched by
  everyone, per D87 Finding 3.
- `team_wan` — D83's multi-valued, protected-identity `participant_partition`, matched only
  by fellow team members (or anyone else who's added its specific partition value).

D84 (same day as D85, 2026-07-20) proposed merging these into one participant and was
**reverted by D85**: merging risked leaking team-topic SEDP metadata to C2, because
participant-level matching exposes SEDP endpoint metadata (topic name, type, QoS) for *all*
of a participant's endpoints, not just the ones related to whichever specific
partition-QoS value caused the match.

This session's live discussion floated a variant: keep the participant merged, but have
whoever needs to observe a specific platform (e.g. C2) match it via a **per-platform unique
partition ID** added explicitly to their own partition list — rather than the shared
team-name value — never using a wildcard (`"*"`) except possibly at an init/bootstrap
phase. The open question: **does matching via a unique ID instead of a team name change
the SEDP-metadata-exposure math that killed D84?**

Then the motivating question, from the person driving this thread: **does any of this
actually buy anything?** Specifically: with two separate WAN participants per node, an
external diagnostic observer has to track presence/discovery state for *both* separately to
know whether a node is fully healthy — doubling the operational complexity of "is this node
up" versus a single consolidated participant. Investigating this surfaced something more
concrete than "double the tracking effort": **the router's existing presence mechanism
doesn't track `team_wan` at all, today.** `PresenceMonitor`/`RouterHealth`
(`router/src/core/PresenceMonitor.hpp`, wired in `router/src/router_main.cxx:398-402`) binds
to exactly one participant — `cfg.presence_participant`, resolved per-role to
`control_wan`/`platform_wan` (`router/src/config/RouteConfigParser.cxx:158-171`,
`router/config/control-platform.yaml:29-31`). `team_wan` is never passed to it.
`RouterHealth`'s IDL (`router/admin/RouterAdminTypes.idl:228-243`) is scoped to one
participant identity (`router` name, one `overall_state`), not an array over multiple local
participants. **An external observer today has zero visibility into whether `team_wan` is
up, down, or partitioned** — not "tracked separately," genuinely absent. No existing design
decision addresses the asymmetric-failure case (team_wan down while platform_wan stays up,
or vice versa).

That reframes the real question this task should answer: is the fix "consolidate the
participants" (cost: reopens D84's SEDP-leak risk, may not even work per the paragraph
above) or "extend the existing single-participant health mechanism to also report
`team_wan`'s state as a data field" (cost: a `PresenceMonitor`/`RouterHealth` change, zero
participant-level/SEDP exposure change, since it's just adding a field to an existing
health payload published by the already-universally-visible `platform_wan`)? The second
option looks like it gets the SAME "one signal to check" diagnostic simplicity the
consolidation idea was chasing, without touching participant-level isolation at all — but
that's an untested hypothesis, not yet a decision, and is exactly what this task should
confirm or refute.

## Steps

### Part A — Does unique-ID matching change the SEDP-metadata-exposure math?

1. Build a minimal `rti.connextdds` repro: participant M has `participant_partition =
   ["PLATFORM_X_UNIQUE_ID"]` (a per-platform unique value, not a team name). Participant P
   (representing a consolidated platform_wan+team_wan) has `participant_partition =
   ["PLATFORM_X_UNIQUE_ID", "TEAM_A"]` (its own unique ID plus a team membership, per D83's
   multi-valued scheme) and creates endpoints on both a "platform-audience" topic and a
   "team-only" topic (distinct topic names, so they're separately identifiable in the
   builtin publication/subscription readers).
2. Confirm M and P participant-match (via the shared unique-ID value).
3. **The load-bearing check**: does M's builtin `DCPSPublication`/`DCPSSubscription` reader
   show P's **team-only** topic endpoint, not just the platform-audience one? If yes, unique
   -ID matching doesn't change anything — SEDP metadata visibility is confirmed all-or
   -nothing per matched participant pair, same conclusion as D85, just reached via a
   different partition value. If no (some other QoS/config gates this that this task hasn't
   accounted for), that changes the entire recommendation — chase down what specifically
   prevents it and whether it's a supported, reliable mechanism.
4. Ask the Connext MCP for confirmation/context on whether SEDP endpoint-discovery
   visibility can ever be scoped *within* a matched participant pair (i.e. is there any
   documented way to make some endpoints visible to a matched peer and others not, short of
   physically separate participants) — verify against the build per the standard protocol,
   log to `docs/connext-ai-issues/connext-ai-issues.md` if the build contradicts the MCP.

### Part B — Does extending RouterHealth solve the actual motivating problem?

5. Prototype adding `team_wan` liveliness/presence as a field (or a small nested struct) on
   the existing `RouterHealth` sample published via `platform_wan`'s `PresenceMonitor` — the
   router process already knows both its local participants' states in-process; this is
   aggregating already-known local state into an existing outgoing message, not a new
   discovery mechanism. Confirm: does this give an external observer, subscribing to exactly
   one topic on exactly one (already-universally-visible) participant, the SAME "one signal,
   fully healthy or not" diagnostic simplicity that consolidation was chasing?
6. Explicitly design for the asymmetric-failure case current docs don't address: what does
   the `RouterHealth` sample say when `team_wan` is down/partitioned but `platform_wan` is
   fine (or vice versa)? Pin a decision, don't leave it implicit.
7. Write up `spikes/wan_dp_consolidation/README.md` with a **recommendation**: consolidate
   (only if Part A found a real way to scope SEDP visibility within a match), extend
   `RouterHealth` (if Part A confirms all-or-nothing visibility, as expected, and Part B's
   prototype works), or something else Part A/B surfaced. Add a dated entry to
   `docs/cpp_router/design-decisions.md` referencing D84/D85, and update
   `docs/cpp_router/presence-and-health.md` to state the `team_wan` gap and its resolution
   either way (today it's silently undocumented).

## Deliverable / acceptance criteria

- `spikes/wan_dp_consolidation/` with `PLAN.md`, runner script(s), `README.md`.
- A definitive, empirically-checked answer to whether unique-ID matching changes SEDP
  -metadata exposure (expected: no, but verify, don't assume).
- A working prototype (or a confirmed non-viable path) for `RouterHealth` covering both
  local participants, with the asymmetric-failure case explicitly decided.
- A design-decisions.md entry stating the recommendation and why, referencing D84/D85.
