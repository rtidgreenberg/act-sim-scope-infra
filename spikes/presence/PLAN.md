# Spike: presence — the Phase 8 presence mechanism + D74 identity

## Question

Phase 8 (Presence & Health) is designed but its mechanism has never been run:
[presence-and-health.md](../../docs/cpp_router/presence-and-health.md) pins `RouterHealth` as
the one liveliness-bearing WAN topic feeding a roster, with the D16 ordering constraint —
**WAN participant lease > `RouterHealth` liveliness window**, so the presence topic is always
the first and authoritative DEAD signal and participant purge trails as backstop. The
implementation plan (Phase 8, readiness item 2) requires this spike before any Phase 8 code,
and folds in the **D74 identity checks** (`participant_name` replaces the D15 `user_data` tag;
detection keys on `role_name == "act.router"` alone).

## Numbers under test (readiness item 3 — pinned by this spike if it passes)

| Knob | Value |
|---|---|
| heartbeat period | 1 s |
| `RouterHealth` DEADLINE | 2 s (2× period) |
| `RouterHealth` liveliness lease (`AUTOMATIC`) | 3 s (3× period) |
| WAN participant lease / assert period | 10 s / 3 s (D16: 10 > 3) |

## Approach

Python driver (`presence_spike.py`), per spike convention. "Routers" are modeled as processes:
peers are subprocesses of the same script (`peer` subcommand) so they can be SIGKILLed; the
surviving router (roster + LAN mesh aggregate publisher) runs in the driver. All participants
are UDPv4-only (SIGKILL leaks nothing in `/dev/shm`) and set the D74 `EntityName`
(`name = "<node>/<router>"`, `role_name = "act.router"`). Types are built programmatically
(`dds.StructType`, mirror of the design doc's IDL sketch) — no XML dependency.

- **Part A — D74 identity at discovery:** a spawned peer sets `EntityName`; the observer reads
  `participant_name.name/role_name` off the builtin `DCPSParticipant` sample. A plain app
  participant with an arbitrary display name (no `role_name`) is discovered but NOT classified
  as a router — detection keys on `role_name` alone.
- **Part B — DEAD before purge (D16 ordering, the money claim) + mesh aggregate:** three
  `RouterHealth` publishers (survivor + 2 peers). SIGKILL peer 1 at t0. The survivor must mark
  it DEAD (health-topic liveliness loss → `NOT_ALIVE_NO_WRITERS`) inside the liveliness window
  (~3 s), **while the peer's data writer is still matched** (participant not yet purged); the
  co-tested data topic (liveliness `AUTOMATIC`/infinite, per design) goes `NOT_ALIVE_NO_WRITERS`
  only at participant purge (~7–10 s) — the trailing backstop. The LAN `ActRouterMeshStatus`
  aggregate (separate LAN domain) updates ALIVE → DEAD; the second peer stays ALIVE throughout.
- **Part C — STALE, not DEAD:** a peer heartbeats 4× then withholds heartbeats while keeping
  liveliness asserted (explicit `assert_liveliness()`; `AUTOMATIC` liveliness also asserts as
  long as the process lives). The survivor marks it STALE via `REQUESTED_DEADLINE_MISSED`
  within ~2–4 s of the last heartbeat; over an 8 s hold (past the 3 s lease) it is never marked
  DEAD, liveliness is never lost, and nothing is torn down (data writer stays matched). STALE
  is a policy flag, not an action.
- **Part D — restart/GUID join (D74):** kill a peer, wait DEAD, restart it with the same
  identity. It re-enters the roster ALIVE **under the same `router_id`**; the builtin data
  shows the same `participant_name` under a **new participant GUID** (the old GUID may linger
  until purge — the roster join is by name/id, not GUID).

Out of scope (manual / later): Admin Console displaying the `EntityName` (manual check, noted
in README); the presence-driven reset action (Phase 12 by D72 scope split).

## Pass / fail

PASS iff all four parts hold. `spikes/presence/presence_spike.py` exits nonzero on any failed
assertion. Key ordering assertion: `t_dead < t_purge`, with `t_dead` ≈ liveliness window and
`t_purge` ≈ participant lease.

## Result — PASS (2026-07-16), stable 4/4

All four parts held on every run (base domains 71/81/91/101). DEAD at 2.6–5.3 s (3 s lease),
purge at 11.1–15.8 s (10 s lease) — the D16 ordering observed with 6–12 s of margin; STALE at
~2 s (deadline) and never escalated; mesh aggregate and all D74 identity checks green.
Observed latencies, findings (purge is lease-order not lease-exact; STALE→DEAD cascade on a
real crash), and the manual Admin Console residual are in [README.md](README.md).
