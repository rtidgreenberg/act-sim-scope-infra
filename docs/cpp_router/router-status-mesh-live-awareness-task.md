# Task 4: Live, discovery-grounded mesh-participant awareness in `RouterStatus` — the D88/D89 readiness pass

Self-contained enough to hand to a fresh session with no prior conversation context. Read
first: `.github/copilot-instructions.md` (repo guardrails, MCP-verification protocol,
wire-capture conventions), `docs/product-gaps.md` **LP-5**, and `docs/cpp_router/
design-decisions.md` entries **D74, D79, D80, D81, D83, D85, D87, D88, D89**.

## Context

D88 decided *what* `RouterStatus` should carry instead of today's static participant-config
echo: retire `RouterParticipantStatus`/`RouterStatus.participants` (pure echo of the shared
config file every node, including C2, already loads verbatim per D80 — no new information to
C2, and config-drift already has its own dedicated mechanism, `RouterHealth.config_hash`).
Replace it with a live field — `RouterStatus.mesh_participants: sequence<RouterMeshParticipant>`,
`RouterMeshParticipant = { string name; string guid; sequence<string, 16> partition;
RouterParticipantSource source; int64 last_seen; }` (the `guid` field added by D89),
`RouterParticipantSource` a two-value enum (`PARTICIPANT_MATCHED`, `PARTICIPANT_LOG_DERIVED`)
— sourced for every participant including the router's own `team_wan`/`platform_wan` (via an
in-process `DomainParticipantQos.partition` read, not self-discovery).

**D89 then fixed the deployment constraint this task must design against: zero new
`DomainParticipant`s, of any kind, for this feature.** Not a separate process, not even one
more local participant co-located inside `router_main`. The mechanism must reuse `team_wan`'s
existing participant exclusively. This retires D88's original process-bridge design (a
separate monitor process publishing onto a LAN topic `router_main` subscribes to) — there is
no bridge, because there is no second participant or process to bridge from. Everything
happens in-process, on `team_wan` itself.

**The resulting hard limitation (D89):** a peer outside your own team can only ever be
identified by GUID + partition string (via `UNMATCH` log-parsing), never by name — receiving
a peer's `ParticipantBuiltinTopicData`/`participant_name()` requires a partition match, and
`team_wan` structurally never gets one with an out-of-team peer while holding its real
team-scoped partition. Adding `"*"` to `team_wan`'s own partition set to get free name
resolution was rejected (again) because `team_wan`/`platform_wan`/`control_wan` share one
domain (200) — a standing wildcard reopens D84/D85's exact SEDP-merge exposure to C2. The one
accepted mitigation, `spdp2-and-boot-init-tasks.md` Task 2 (wildcard-boot-then-revert, run
once before any routes/sessions exist), gives a name-resolved census at boot only — it is a
separate, not-yet-scheduled task; this task must not depend on it landing first, and should
design `mesh_participants` so log-derived (GUID-only) and boot-census (named) entries can
coexist correctly once Task 2 does land.

Today's static-echo code that D88/D89 leave untouched pending this task:
`RouterController::build_snapshot()` (`router/src/core/RouterController.cxx:1033-1051`),
`state_.participants` (seeded from `RouteConfigParser` in `router_main.cxx`, mutated by
`RouterController::handle_add_participant_partition`/`handle_remove_participant_partition`),
`ParticipantState` (`router/src/core/RouterState.hpp`), and the consumers that assert
today's shape: `router/test/test_admin_types.cxx`, `router/test/test_controller_phase1.cxx`,
`router/test/test_route_config.cxx`.

## Steps

### Part A — Is the log-parsing hook reachable from the C++11 modern API, attached to a live, already-in-use participant?

1. Build a minimal `rti::core`/modern-C++11 repro mirroring
   `spikes/dp_partition_monitor/`'s Python one, but structured the way `team_wan` will
   actually be used: **one** participant, holding a real (non-wildcard) team-scoped
   partition, alongside several peer participants representing a mix of same-team and
   out-of-team WAN members. Find and register the C++ equivalent of
   `dds.Logger.instance.output_handler(...)` + `verbosity_by_category(LogCategory.entities,
   STATUS_REMOTE)` — check `$NDDSHOME` headers directly, do not assume parity with the
   Python binding.
2. Ask the Connext MCP for the C++11 API surface corresponding to Python's
   `dds.Logger`/`LogCategory`/`Verbosity` — **verify against the actual headers/build**
   before trusting it (standing rule: MCP is a hint, the build is the arbiter). Log a
   `docs/connext-ai-issues/connext-ai-issues.md` entry if the build contradicts the MCP
   answer, following the two-part submodule-then-parent sync in
   `.github/copilot-instructions.md`.
3. If found: confirm the `UNMATCH` line's content and the known field-swap bug
   (`docs/product-gaps.md` LP-5) reproduce identically from C++.
4. **Multi-participant contamination check (LP-5 residual 4, now unavoidable):** repeat the
   repro with the single team-scoped participant co-located in the same process as two or
   three *other*, unrelated local participants (mirroring `router_main`'s real shape —
   `platform_wan`, `platform_lan`). Confirm the log handler receives `UNMATCH` lines for
   mismatches not involving the team-scoped participant at all, and that filtering strictly
   by that participant's own instance-handle-derived GUID correctly discards them.
5. If the C++ hook is **not** reachable (no equivalent, or it requires something this
   codebase's build doesn't link): this is a hard blocker for the whole feature under the
   D89 constraint — there is no fallback process to lean on this time. Document exactly
   what's missing and surface it as a fresh gap (`docs/product-gaps.md` and/or
   `connext-ai-issues.md` as appropriate) rather than quietly downgrading scope.

### Part B — Attach in-process, without disturbing `team_wan`'s existing role

6. Confirm attaching the log handler doesn't interfere with `team_wan`'s existing
   responsibilities on that same participant — the `platform_team_to_wan`/
   `wan_team_to_platform` routes' `PlatformData` topic, and (if `team_wan` is ever the
   `presence_participant` for a role) `PresenceMonitor`'s own reader/writer pair. Confirm
   route forwarding and any presence heartbeat continue unaffected with the handler active.
7. Design the callback-threading discipline per D89 (the D81 app-ack pattern): the log
   callback fires on an arbitrary middleware thread and must do only GUID + partition-string
   extraction into a mutex-guarded accumulator; `RouterController` drains it on its own
   strand/tick, never touching controller state from the callback thread directly.
8. Design and verify the shutdown discipline (LP-5 residual 3, now `router_main`'s own
   responsibility): the log handler and raised verbosity must be reset on **every**
   `router_main` exit path — normal shutdown, an uncaught exception, and a signal — tied
   into whatever teardown sequence already gates `PresenceMonitor::shutdown()`/the D31/D32
   blocking-barrier convention. Prove this with a kill-based test (repo convention), not
   just a clean-exit one.
9. Confirm the router's own self-entry (in-process `DomainParticipantQos.partition` read,
   per D88) and `team_wan`'s matched-half peers (`discovered_participants()`, real names)
   and the log-derived (GUID-only) peers all compose correctly into one
   `mesh_participants` sequence with no double-counting and no ordering assumption the real
   `build_snapshot()` would violate.

### Part C — Implement (only after Part A/B produce a working, verified design)

10. Land the `RouterAdminTypes.idl` changes (`RouterParticipantSource`,
    `RouterMeshParticipant` incl. the D89 `guid` field, `RouterStatus.mesh_participants`);
    remove `RouterParticipantStatus`/`RouterStatus.participants`.
11. Update `RouterController::build_snapshot()`, `RouterState.hpp`/`ParticipantState`,
    `router_main.cxx`, `RouteConfigParser.cxx` accordingly; fix the now-broken
    `test_admin_types.cxx`/`test_controller_phase1.cxx`/`test_route_config.cxx` assertions
    to the new shape.
12. Extend `router/test_e2e/` (a new `test_mesh_participants.py`, or extend
    `test_team_partition.py`) to assert `mesh_participants` correctness for: a same-team
    peer (named, `PARTICIPANT_MATCHED`), a cross-team peer (`guid`-only,
    `PARTICIPANT_LOG_DERIVED`, empty `name`), and the router's own self-entry.
13. Write `spikes/<name>/README.md` with the final recommendation and add a dated
    design-decisions.md entry amending D88/D89 with what Part A/B actually found (same
    pattern D87 used to amend D83/D78 with real implementation findings).
14. **Not part of this task's gate, but flag it in the writeup:** once
    `spdp2-and-boot-init-tasks.md` Task 2 lands, revisit whether its boot-census output
    should feed `mesh_participants`' `name` field for entries this task would otherwise
    have reported GUID-only.

## Deliverable / acceptance criteria

- A definitive, empirically-checked answer to whether the `UNMATCH` log-parsing hook is
  reachable from the C++11 modern API on a single, already-in-service participant
  (expected: unverified going in, don't assume either way) — including the multi-participant
  contamination check, since that's no longer optional under the D89 constraint.
- A verified design for attaching the handler to `team_wan` without disturbing its existing
  route/presence responsibilities, with the D81-pattern threading discipline and a
  kill-tested shutdown discipline.
- `RouterStatus.mesh_participants` implemented, replacing `RouterParticipantStatus`, with
  green `ctest` + the full e2e suite including new/updated mesh-participant coverage —
  explicitly covering the GUID-only cross-team case, not just the named same-team case.
- A design-decisions.md entry amending D88/D89 with the implementation findings, referencing
  D73/D74/D79/D80/D81/D83/D85/D87/D88/D89 and LP-5.
