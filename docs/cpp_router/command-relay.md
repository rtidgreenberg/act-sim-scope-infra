# Command Relay: Multi-Hop Command Delivery Over the Existing Mesh Graph

> Design proposal for delivering `RouterCommand`s to a target router that the sender has no
> direct/healthy link to, by relaying through intermediate routers. Status: **proposed,
> not yet scheduled** — captured for a future phase (see
> [implementation-plan.md](implementation-plan.md) Phase 14). Pinned stance: D86 in
> [design-decisions.md](design-decisions.md). Builds on [Command And
> Status](command-status.md) and [Presence & Health](presence-and-health.md).

## Problem

`RouterCommand` addressing (`target_node`/`target_router`, D74/D79 name-only identity) assumes
the sender has a working link straight to the target. On a lossy/partitioned WAN that isn't
always true — a sender (e.g. C2) may be connected to router A but not router C, while A and C
can hear each other fine. Today there is no way for A to help get a command from the sender to
C.

## Why today's delivery can't relay as-is

The command reader on each router is built on a **ContentFilteredTopic**
(`target_node = %0 AND target_router = %1`, [command-status.md](command-status.md)). That
filtering happens in the middleware: a router whose identity doesn't match the command's
target never receives the sample at all. A relay can't forward something the middleware
already discarded for it — any relay mechanism needs a delivery path that *isn't* filtered out
before application code sees it.

## Design principle: use the graph that already exists, flood only as fallback

The obvious alternative to filtered unicast is flooding every command to every peer
(gossip) and letting non-matching routers re-broadcast it. That works with zero topology
knowledge, but it is not the simplest available option here, because the router **already
maintains a live mesh graph**: every `RouterHealth` sample carries `peers_seen`
(`sequence<RouterPeerRef>`, D77) — each router's own roster edges — heartbeated at 1 s
(D75). Any single WAN observer (the sender included) can already assemble the full
who-sees-who graph from these samples, and that graph is *continuously* refreshed, not a
one-off snapshot.

That rules out a dedicated route-discovery protocol (flood a small probe, record the path,
then send the real command along it — AODV/DSR-style). A probe's discovered path is itself
only as fresh as the round trip it took to discover it; the standing `RouterHealth` graph is
already at least that fresh, continuously, for free. Building a second discovery mechanism on
top of a mechanism that already answers the same question violates Tenet 9 (prefer DDS-native
/ already-paid-for mechanisms over new app-level machinery,
[thesis-and-tenets.md](thesis-and-tenets.md)).

**Decision (see D86 for the full record):** compute the relay path from the existing
`RouterHealth`-derived graph at send time; fall back to TTL-bounded flood only when the graph
doesn't show a usable path, or when a graph-directed attempt times out unacked.

## Graph caveat: edges are directed, not guaranteed symmetric

`peers_seen` presence is one-directional — it records that a router *heard* a peer, not that
the peer can hear it back. [presence-and-health.md](presence-and-health.md) already calls out
that one-way visibility shows up as an asymmetric edge. Path computation must treat the graph
as a directed graph and must not assume that "A sees B" implies "B can send to A" — picking a
next hop that can receive a heartbeat but can't actually forward is a real failure mode, not a
theoretical one.

## What relaying requires, concretely

1. **Graph assembly at the sender.** A reader on `RouterHealth` (already a WAN topic every
   router publishes) plus an in-memory directed edge map built from `peers_seen`, kept live as
   heartbeats arrive. New application code — nothing today aggregates `RouterHealth` for
   routing purposes.
2. **Path computation.** Given the sender's position in the graph and the target
   `target_node`/`target_router`, compute a path (plain reachability/BFS is enough to start;
   there's no weighted link-cost signal wired in yet, though `RouterLinkStats`/app-ack RTT
   ([link-health.md](link-health.md)) could later feed edge weights for a best-path rather than
   any-path choice — not in scope for the first version).
3. **New `RouterCommand` fields** to carry the computed path explicitly: an ordered
   `sequence<string> relay_path` of router names, plus a hop position marker (e.g.
   `uint32 relay_hop_index`) so a relay knows whether it's the addressed next hop. Alongside
   this, the flood-fallback mode needs its own minimal fields: a `relay_requested` flag (or
   equivalently, `relay_path` empty means "flood") and a `hop_count`/TTL to bound propagation.
4. **Relay-forward logic in `RouterController`.** Net new — nothing forwards commands today.
   On receipt of a command not addressed to self: if `relay_path[relay_hop_index] == self`,
   re-publish with `relay_hop_index + 1`; if `relay_path` is empty (flood mode) and
   `hop_count > 0`, re-publish to all peers with `hop_count - 1`. Either way, a
   `command_id`-keyed dedupe cache prevents re-forwarding a command already seen (needed for
   loop safety in flood mode, and cheap insurance against duplicate delivery in graph-directed
   mode too).
5. **Sender-side retry/fallback ladder**, reusing the existing `RouterCommandAck`
   (keyed by `command_id`) unchanged: send direct (today's behavior) → on N unacked retries (or
   immediately, if the graph already shows the target as unreachable direct), send
   graph-directed via `relay_path` → on further unacked retries, drop to flood mode. The ack
   contract itself never changes.

## Ack and diagnostics scope

**Only the target router (`target_node`/`target_router`) acks.** Relay hops are transparent —
they do not emit their own ack, and the sender's retry state machine only ever waits on the one
ack from the actual target, identical to direct delivery today. Relay hops are traceable after
the fact via logging, not via the ack protocol: each relay hop logs the `command_id`, the
router it forwarded from/to, and the hop index/path position to the same
`ActRouterControllerJournal` recorder already used for other command/status events
([command-status.md](command-status.md)). This keeps the protocol simple (no new ack
semantics to design or test) while still making the path a command took reconstructable from
logs.

## Group/team targeting (open question, deferred)

Everything above targets one specific `target_node`/`target_router`. A "send to my team"
mode would need a separate `target_team` field (distinct from `target_node`, which stays
unicast) that a receiving router checks against its own team membership
(the D83 participant-partition team value) before deciding to execute — the relay/flood
mechanics above are unaffected either way. Not designed further here; call out as a residual
if/when a use case for it appears.

## Non-goals for the first version

- No dedicated route-discovery/probe protocol (see "Design principle" above — rejected in favor
  of the standing `RouterHealth` graph).
- No link-cost-weighted path selection — plain reachability only, to start.
- No relay-level acknowledgment — only the target router's ack matters; relay hops are
  logged, not acked.
- No `target_team`/group addressing — open question above, not designed.

## Related

- [Command And Status](command-status.md) — today's `RouterCommand`/`RouterCommandAck`
  addressing and delivery, unchanged by this proposal except for the new fields above.
- [Presence & Health](presence-and-health.md) — `RouterHealth`/`peers_seen`, the graph this
  design reuses rather than duplicating.
- [Link Metrics](link-health.md) — the future source of edge-weight data if plain reachability
  ever needs to become best-path.
- [design-decisions.md](design-decisions.md) — D86 pins the "graph-first, flood-fallback,
  no probe protocol" stance recorded here.
