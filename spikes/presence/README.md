# presence spike

Validates the Phase 8 presence mechanism before any Phase 8 code (implementation-plan
readiness item 2): `RouterHealth` liveliness declares a killed peer **DEAD before participant
purge** (the D16 ordering, observed not assumed), STALE is a deadline-driven policy flag that
never escalates, the LAN `ActRouterMeshStatus` aggregate tracks the roster, and the **D74
`participant_name` identity** is readable at discovery, detected on `role_name` alone, and
joins the roster across a restart/GUID change. See [PLAN.md](PLAN.md).

## Run

From the repo root:

```bash
python3 spikes/presence/presence_spike.py [base_domain_id]
```

- `base_domain_id` defaults to `71`; the four parts use `base+0 … base+4` (Part B uses two:
  WAN `base+1`, LAN `base+2`).
- Exit `0` = all parts pass; nonzero = a failed assertion.
- Peers are subprocesses of the same script (`peer` subcommand) so the SIGKILL is real;
  UDPv4-only, leaves no `/dev/shm` segments and no stray processes.

## What each part proves

- **A** — D74 identity: `participant_name.name = "nodeA/router1"` /
  `role_name = "act.router"` read off the builtin `DCPSParticipant` sample; a plain app with
  a display name but no sentinel `role_name` is NOT classified (detection keys on `role_name`
  alone).
- **B** — the money claim: SIGKILL a peer → survivor marks it DEAD via health-topic
  liveliness loss **while the peer's data writer is still matched** (participant not yet
  purged); purge trails and only then drives `NOT_ALIVE_NO_WRITERS` on the co-tested data
  topic; the LAN mesh aggregate updates; the second peer stays ALIVE.
- **C** — STALE: withheld heartbeats + asserted liveliness → `REQUESTED_DEADLINE_MISSED` →
  STALE; over a hold well past the liveliness lease it never becomes DEAD and nothing is
  torn down.
- **D** — restart: same identity, new process → same `router_id` rejoins ALIVE under a
  **new** participant GUID (roster join by `participant_name`, not GUID).

## Result — PASS (2026-07-16, Connext 7.7.0, x64Linux4gcc7.3.0, rti.connextdds), stable 4/4

With the pinned numbers (heartbeat 1 s, DEADLINE 2 s, liveliness lease 3 s, WAN participant
lease 10 s / assert 3 s):

| Signal | Observed latency after SIGKILL | Governing knob |
|---|---|---|
| STALE (deadline missed) | 1.3–2.0 s | DEADLINE 2 s (from last heartbeat) |
| **DEAD (liveliness lost)** | **2.6–5.3 s** | lease 3 s + up to one assert interval + jitter |
| participant purge (data-topic `NO_WRITERS` backstop) | 11.1–15.8 s | participant lease 10 s |

- The D16 ordering held in every run with a wide margin: DEAD preceded purge by 6–12 s, and
  at the moment of DEAD the dead peer's data writer was still matched (participant still in
  the discovery DB).
- **Purge latency is lease-ORDER, not lease-exact**: 11.1–15.8 s against a 10 s lease —
  expiry counts from the *last SPDP announce before the kill* (assert period 3 s) and the
  purge check has its own granularity. Treat the participant lease as an order-of-magnitude
  backstop knob, never a precise timeout.
- The STALE→DEAD cascade on a real crash (deadline fires before the lease) is expected and
  benign — the roster passes through STALE on the way to DEAD.
- Part C's liveliness stayed asserted purely via `AUTOMATIC` liveliness (the process was
  alive); the explicit `assert_liveliness()` call is exercised but redundant for
  `AUTOMATIC` — a hung-but-scheduled process is ALIVE-by-liveliness, silent-by-deadline,
  which is exactly the STALE semantics the design wants.
- D74 identity: reading `participant_name` off the builtin sample needed no new machinery
  (`discovered_participants()` / `discovered_participant_data()`), and the roster join across
  a GUID change is trivially by-name.

## Manual residual

Admin Console displaying `name = "<node>/<router>"` for the spike participants is a
**manual check** (launch Admin Console, join the spike domain while Part B runs) — not
automated here. Everything else about D74 is machine-verified.

## Requirements

`rti.connextdds` (Connext 7.7). No XML/data-model dependency — all types are built
programmatically (`dds.StructType`).
