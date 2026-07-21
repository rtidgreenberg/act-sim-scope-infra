# POC User Stories & Validation Plan (Phases 0–10)

> Companion to [Implementation Plan](implementation-plan.md) and [Thesis & Tenets](thesis-and-tenets.md).
> Scope: capture the router POC's requirements as user stories, independent of the
> phase-numbered implementation slices, then map each story to the test(s) that validate
> it. Intended to seed a separate testing/baselining pass once Phase 10 (team partition
> route) lands — see [implementation-plan.md Phase 10](implementation-plan.md#phase-10-team-partition-route)
> for its in-progress status. Drafted 2026-07-20 from the current repo state (router/,
> docs/cpp_router/) plus the design/implementation conversation; not itself a
> design-decisions.md entry.

## Personas

| Persona | Role |
|---|---|
| **C2 operator** | Runs the control node; sends commands (enable/disable route, team add/remove), watches status and mesh presence. |
| **Platform operator** | Runs a platform node's router; cares that app traffic keeps flowing and that team scoping behaves predictably. |
| **Mission commander** | Decides team membership (who shares `PlatformData`) — the human behind team-assignment commands. |
| **Integrator/deployer** | Writes/maintains the YAML config, wires QoS profiles, stands up the router in place of Routing Service. |
| **Network engineer** | Diagnoses link quality using the router's telemetry instead of a separate probe tool. |

## Master story

> As the ACT program, I want the control and platform simulators to run **without Routing
> Service**, moving the same command, status, event, and team topics through the router
> gateway, so I can retire the opaque XML-driven relay for these flows.
> — [README.md "Goal"](README.md), the POC's own stated win condition.

Validated today for control+platform (not yet team) by
`router/test_e2e/test_control_platform_full.py` — a real control-node/platform-node pair
running the verbatim production `control-platform.yaml`.

## Functional user stories

| # | Story | Acceptance criteria (condensed) | Phase | Validating test(s) | Status |
|---|---|---|---|---|---|
| US-1 | As an integrator, I want `router_main` to validate its config and report its identity at startup, so I can catch a bad deploy before any DDS traffic happens. | Executable starts, validates config path, prints identity, exits cleanly on error. | 0 | `test_admin_types.cxx`, `test_config_identity.cxx` | Done |
| US-2 | As a C2 operator, I want `ENABLE_ROUTE`/`DISABLE_ROUTE` commands to be idempotent and acked, with route state visible in status, so retries over an unreliable channel are safe. | Duplicate `command_id` returns the original ack, no revision bump; unknown-route command rejected; disabled route visible with no entities. | 1, 6a | `test_controller_phase1.cxx`, `test_router_admin_commands.py` | Done |
| US-3 | As a platform operator, I want routes to activate automatically once a matching endpoint is discovered on either side, with no manual peer wiring. | Route transitions `WAITING_FOR_DISCOVERY → RESOLVING → ENABLED`/`TOPIC_FORWARDING` as peers appear; zero-matched-count is a visible status, not a false green. | 2, 3, 7m | `test_discovery_smoke.py`, `test_discovery_startup.py`, `test_create_and_observe.py` | Done |
| US-4 | As an integrator, I want one route config file to work on both control and platform nodes, each side picking its own role automatically. | Control-side selects `control_lan→control_wan`; platform-side selects `platform_wan→platform_lan`; a command addressed to another node's `target_node` never reaches this one. | 4, 6a | `test_route_config.cxx`, `test_control_command_route.py` | Done |
| US-5 | As an app developer on the LAN, I want the router to match my app's QoS automatically (or warn loudly if incompatible), so I don't hand-author a router-side profile per app. | `BEST_EFFORT`/`VOLATILE` app writers still match and forward; an `EXCLUSIVE`-ownership or `TRANSIENT`-durability mismatch produces a named incompatible-QoS warning, not silence. | 5 | `test_auto_qos.py` | Done |
| US-6 | As a C2 operator, I want every controller decision (command in, state change out) journaled, so I can audit what happened without instrumenting the app layer. | With a journal reader matched, every processed event records input/decision/outcome/revision delta; with none matched, behavior is unchanged (no forced traffic). | 6b | `test_controller_journal.py` | Done |
| US-7 | As a C2 operator, I want commands to cross the WAN as one control/status/event channel using named QoS profiles, so the same profile vocabulary from the production config just works. | A route using `wan_status`/`wan_event` aliases forwards; declared aliases are preflighted at startup (fail fast on a bad profile name). | 7a | `test_qos_alias_route.py` | Done |
| US-8 | As a mission commander, I want CONTROL/PLATFORM audience partitions enforced at the DDS level (not app logic), and retargetable at runtime without a rebuild. | A PLATFORM-partitioned route matches only PLATFORM readers; `SET_ROUTE_PARTITION` changes it in place via `set_qos`, QoS summary byte-identical across the change. | 7b | `test_wan_partition.py` | Done |
| US-9 | As a platform operator, I want status and event topics (including two distinct payload types from one process) to forward without per-type router config, so adding an ACT event type doesn't require new router code. | `PlatformCommandAck` and `ContactReport` both forward from one `router_main`; a type built only in Python (absent from any XML) still routes. | 7c | `test_platform_events.py` | Done |
| US-10 | As a platform operator, I want the full production `control-platform.yaml` (commands, primary status, events) plus opt-in detail status to run end to end as a two-process pair, replacing Routing Service. | All three always-on routes cross the WAN; `ENABLE_ROUTE platform_detail_status` starts that flow only for the targeted node; `samples_forwarded` advances in status. | 7d | `test_control_platform_full.py`, `test_detail_status_toggle.py`, `test_platform_status_route.py` | Done |
| US-11 | As a C2 operator, I want to see which routers are ALIVE/STALE/DEAD in near-real-time, so a quiet topic reads as "nothing to report" instead of "is anyone even there." | Two routers see each other as ALIVE; a SIGKILLed peer is marked DEAD within the liveliness window (not waiting on participant-lease purge); a peer that stops heartbeating but still asserts liveliness is STALE, not DEAD, and nothing is torn down; a restarted peer re-enters as ALIVE. | 8 | `test_presence_roster.py`, `test_same_node_ignore.py` | Done |
| US-12 | As a network engineer, I want per-peer link-quality counters and RTT exposed on the LAN, so I can diagnose a degraded link without a separate probe tool or touching the app. | `ActRouterLinkStats` advances per peer, attributed by router **name**; a peer (re)match in the interval is flagged `rediscovery_in_interval`, not misread as repair traffic; RTT populates at ~1 Hz; polling never bumps `state_revision`. | 9 | `test_link_stats.py` (incl. `test_link_stats_wire_frugal` — confirms the probe pair is the only new WAN traffic) | Done |
| US-13 | As a mission commander, I want platforms with no shared team to exchange **zero** `PlatformData` and **zero** discovery metadata about each other — isolation that's structural, not a filter that could be misconfigured. | Disjoint `team_wan.participant_partition` ⇒ held-zero matched count *and* no SEDP endpoint discovery of the other's identity in either process's discovery log. | 10 | `test_team_partition.py` (`E-disabled`) | Test drafted; **implementation in progress** (uncommitted `RouteConfigParser`/`RouterController`/`ParticipantRegistry` changes) |
| US-14 | As a mission commander, I want to add or remove a platform's team membership at runtime, acked as soon as it's applied (not once rematch completes), so reassignment never requires a restart or hangs on WAN discovery timing. | `ADD_PARTICIPANT_PARTITION`/`REMOVE_PARTICIPANT_PARTITION` ack on apply; matched counts/data flow follow within the SPDP2 settle window; removing membership stops delivery without tearing down entities (`topic_state` stays `TOPIC_FORWARDING`). | 10 | `test_team_partition.py` (`E-team`, `E-remove`) | Test drafted; implementation in progress |
| US-15 | As an operator, I want to grant one platform visibility into a specific other platform's data without building a shared team, so ad-hoc coordination doesn't force a team restructure. | Adding the peer's own protected identity name to my `team_wan` partition set makes data cross between exactly those two nodes; removing it reverses it. | 10 | `test_team_partition.py` (`E-direct`) | Test drafted; implementation in progress |
| US-16 | As a mission commander, I want a duplicate team command to be a safe no-op and an attempt to remove my own protected identity to be rejected outright, so the command channel can't be used to accidentally self-isolate a platform. | Duplicate `ADD` (name already present): idempotent accept, no revision bump. `REMOVE` of the protected `"${node.name}"` entry, or of a name not currently present: rejected, not silently accepted. | 10 | `test_team_partition.py` (`E-idempotent`) | Test drafted; implementation in progress |

## Cross-cutting / non-functional stories (from the Tenets)

These aren't tied to one phase — they're why a custom relay exists at all
([thesis-and-tenets.md](thesis-and-tenets.md)). Several have **no automated test today**;
that's a real gap for a "testing and baselining" pass, not an oversight in this table.

| # | Story | Validating today | Status |
|---|---|---|---|
| US-17 | As an integrator, I want every forwarding decision to be something I can read in code and logs, not an opaque XML engine's behavior. | One structured log stream (Connext logger bridged in); controller journal (US-6). | Done — logging/journal exist; no dedicated "no black box" test, this is inherently a code-review property. |
| US-18 | As an integrator, I want route/partition config changes to be YAML edits + a runtime command, not a redeploy. | Every US-7/8/13-16 test exercises a runtime command path. | Done |
| US-19 | As a network engineer, I want to capture DDS wire traffic (incl. shared memory) at the relay for offline analysis, since the Python binding I'd otherwise use can't do this. | `rti::util::network_capture` — **not called anywhere in `router/src`, no test exercises it.** | **Gap — now scoped as [Phase 15: Network Capture & Debug Mode](implementation-plan.md#phase-15-network-capture--debug-mode)** (proposed, not yet scheduled). |
| US-20 | As a C2 operator, I want the router's steady-state resource/latency cost to be no worse than the Routing Service hop it replaces (Tenet 7). | Nothing yet — no RS-vs-router comparison exists in the repo (`connext-investigation-review.md` recommends this as a "behavioral conformance suite," scoped to **Phase 13**). | **Gap — this is the "baselining" the current conversation flagged.** See below. |

## Explicit non-stories (out of scope — don't chase these)

Per [README.md "POC Boundary"](README.md#poc-boundary): Instance State Consistency /
cross-relay recovery, link impairment (that's EMANE/netem's job), per-topic liveliness
across the WAN, Routing Service auto-topic-route parity, regex route expansion,
multi-output fanout, transform plugins, durable persistence/replay, full flow control,
security, full remote-admin compatibility. Also not yet in scope for *this* validation
pass (later phases, not yet built): meta-sample lifecycle mirroring and presence-driven
reset (**Phase 12**), the `serialized_cdr` fast path (**Phase 11**), Routing-Service
removal from the container/startup path (**Phase 13**).

## Recommendation for the new POC's first slice

Given the gaps above, and that US-13–16 (Phase 10) already have a drafted but
not-yet-green test:

1. **Finish Phase 10** — get `test_team_partition.py` green against the in-progress
   `RouteConfigParser`/`RouterController`/`ParticipantRegistry` changes. This closes the
   last functional story in this doc.
2. **US-20 (RS-vs-router baseline)** is the highest-value net-new work: nothing in the
   repo compares the two today, and it's explicitly called for (Phase 13,
   `connext-investigation-review.md`). A first cut doesn't need the full harness
   replacement — a narrow spike (`spikes/rs_baseline/`, per repo convention) running the
   same route through Routing Service and through `router_main` and diffing latency/
   resource cost would validate US-20 well ahead of Phase 13.
3. **US-19 (network capture)** is now [Phase 15](implementation-plan.md#phase-15-network-capture--debug-mode)
   — a `debug_mode` knob wiring `rti::util::network_capture` behind an explicit flag (a pcap
   has no DDS-native matched-reader signal to piggyback on the way the journal recorder
   does). Cheap to close: a small spike, then the knob itself.
