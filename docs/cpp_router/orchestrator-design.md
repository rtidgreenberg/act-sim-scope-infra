# Mission Orchestrator — captured thinking (not yet a decided design)

> **Status: early concept capture, 2026-07-17.** This document exists so this conversation's
> reasoning doesn't have to be redone or drift. It records what was agreed and what's still
> open — it is **not** a D-numbered decision in [design-decisions.md](design-decisions.md),
> because the orchestrator is a separate process/component from the router itself, not a
> router implementation choice. Promote settled pieces of this into their own design doc
> (and, if they change router behavior, into `design-decisions.md`) once they firm up.

## What problem this solves

The router already gives a node local control (`ActRouterCommand`: `ENABLE_ROUTE`/
`DISABLE_ROUTE`/`SET_ROUTE_PARTITION`/`SET_PARTICIPANT_PARTITION`) and local observability
(`ActRouterLinkStats`, `ActRouterMeshStatus`, `RouterHealth`). Nothing today *decides* how
to use those levers based on mission context — that decision needs to live somewhere. The
question this doc captures: where does that logic live, and how does it get the
information and authority it needs.

## Core principle: single arbiter of control, and it's the platform itself

**C2 does not remotely control a router over the WAN.** Commanding a router directly from
C2 was considered and rejected: the WAN link is lossy/high-latency, and a remote command
model requires reliable exactly-once delivery, retry/timeout logic, and conflict resolution
against whatever the node has already decided locally in the meantime — exactly the kind of
fragility a contested link makes worse, not better. `ActRouterCommand` already needs
duplicate-command handling (D8) over the *reliable LAN*; extending that same imperative
model across the WAN multiplies the failure surface for no real gain.

Instead: **C2 publishes mission state, not commands.** State is a declarative, idempotent,
self-healing pattern over a lossy link — last-value-wins, naturally re-delivered on
reconnect, no ack/retry protocol needed. The **orchestrator** (per node) is the sole thing
that turns "current mission state" plus "current local mesh/link awareness" into an actual
decision, and it is the only thing that talks to its own router's `ActRouterCommand`. There
is exactly one arbiter of what a platform's router does: that platform's own orchestrator.

## Topology

- **The orchestrator is autonomous per node.** Each platform runs its own orchestrator
  process, deciding independently based on its own objectives — this is explicitly not a
  centralized mesh-wide controller. C2 has high-level awareness and expresses high-level
  mission state; each platform is responsible for completing its own mission against that
  state using its own local knowledge.
- **No new WAN participant, no new admin-plane topic.** Mission state from C2 to a node
  travels over a **regular route** — the same WAN→LAN data-plane mechanism that already
  carries `ControlCommand`/`PlatformData`/etc. The router doesn't need to know or care that
  a particular route happens to carry mission state instead of any other application
  payload; it's just another route. The orchestrator subscribes to that route's LAN-side
  output like any other LAN consumer — it does not need its own WAN-facing participant.
- **C2 is itself a router-role node in the mesh** — e.g. `c2_123`, running a real router
  instance (not a stub participant that exists only for presence). This means C2's
  liveliness is not a new problem: it already shows up in `RouterHealth`/`peers_seen`/
  `ActRouterMeshStatus` with the existing ALIVE/STALE/DEAD roster treatment (D74–D77). The
  orchestrator reads the *same* mesh-status view every node already gets and checks C2's
  entry like it would any peer's.
- **The orchestrator talks to its own router only over the LAN** — reading
  `ActRouterLinkStats`/`ActRouterMeshStatus` for local link/mesh awareness, and writing
  `ActRouterCommand` to act. This channel never crosses the WAN, so it doesn't inherit the
  lossy-link risk the C2-command-path idea was rejected for.

```text
        WAN (regular route: mission-state topic, same mechanism as ControlCommand)
         │
   ┌─────┴──────────────────────────────┐
   │  node's router (control-platform /  │
   │  platform-team instances)           │
   └─────┬──────────────────────────────┘
         │ LAN: mission-state route output, ActRouterLinkStats, ActRouterMeshStatus (read)
         │      ActRouterCommand (write)
   ┌─────┴──────────────────────────────┐
   │  orchestrator (this node, autonomous)│
   └──────────────────────────────────────┘
```

## Failure handling

C2 going stale/dead is not a special case — it's the existing presence model. If C2's
entry in mesh status is anything other than ALIVE, the orchestrator should fall back to a
defined safe behavior (last-known-good mission state, or a conservative default) rather than
acting on arbitrarily old state indefinitely or freezing. **Open:** the exact fallback
policy (how stale is "stale enough to distrust," what the conservative default actually is)
is not yet decided — it's a per-mission-profile question, not a router/orchestrator
mechanism question.

## Open questions (not yet decided)

- Exact shape/type of the mission-state route (topic name, payload schema, which route
  class it rides — likely closest to the existing `ControlCommand` pattern).
- Whether the orchestrator needs full `ActRouterStatus` visibility (this router's own
  detailed route table) in addition to `ActRouterLinkStats`/`ActRouterMeshStatus`, or
  whether the latter two are sufficient input for its decisions.
- The C2-stale fallback policy specifics (see above).
- Process/deployment model for the orchestrator itself (separate binary per node? library
  linked into something else? how it's started/supervised relative to the router
  instances).
- Trust/authorization model for the LAN command channel: today `ActRouterCommand` has no
  concept of "who is allowed to command me" beyond being a LAN participant — is the
  orchestrator just another equally-trusted LAN client, or does it need distinguishing from
  other things that might send commands on that same LAN?
