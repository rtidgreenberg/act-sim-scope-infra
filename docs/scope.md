# Scope — Passive Monitoring & Visualization (read-only)

> Part of the **ACT EMANE Simulation & Thesis-Validation** plan — start at the [overview & index](EMANE_SIMULATION_PLAN.md). Docs are organized by **process**; cross-cutting concerns live in [architecture.md](architecture.md), [decisions-and-risks.md](decisions-and-risks.md), [thesis-and-claims.md](thesis-and-claims.md), and [roadmap.md](roadmap.md). Original plan section numbers (§X.Y) are retained for traceability.

---

**Scope** (DDS Scope) is the passive, **read-only** monitoring & visualization product — deployable
standalone against any DDS network. It subscribes to the [sniffer](sniffer.md) bus, aggregates events
into a topology/state model (the **shared aggregation lib** the harness imports one-way), and serves
the network picture + endpoint inspector + dashboards. Backend/frontend tech and the end-state
component map are in [architecture.md](architecture.md); the viz-layer build + observability pipeline
are in [roadmap.md](roadmap.md) (Phase 2, Phase 4). This doc captures Scope's observation/UI
requirements.

## Endpoint Inspector / live sample subscriber (§2.6)

Select a node in the graph → panel lists the **DDS endpoints that node currently exposes**
(topics, readers/writers, type, QoS) → click an endpoint → backend **dynamically subscribes**
and **streams live samples** to the panel. Must be **live**: when detail-status is enabled
(or a team is assigned), new routes/endpoints appear and the panel updates automatically —
demonstrating "expose more topics on demand" visually.
- Requires **DDS discovery introspection** (builtin DCPSPublication/DCPSSubscription topics,
  or RTI Observability entity data) + on-demand **DynamicData** readers for sample content.
- This is essentially embedding **Admin Console / DDS Spy** behavior, driven by graph selection.

*(The on-demand decode mechanism behind the inspector is the [sniffer](sniffer.md) command-gated decode-set, §2.9.)*

## GUI B — Network State Visualization (§2.5)

- **Nodes:** role, identity, up/down, team, position.
- **Links:** who-hears-whom, link quality (pathloss/SNR/datarate), up/down.
- **Traffic (message transmission):** flow along edges — direction, per-channel,
  rate/intensity; highlight loss/retransmit/congestion.
- **Spatial/RF context:** positions on map/globe, ranges.
- **Temporal:** live + ideally scenario replay.  **Drill-down:** select node/link → stats.

**Does the EMANE panel (SDT3d) satisfy GUI B?** *Partially.* SDT3d natively shows the
**RF/PHY layer** — node positions, links, link state — from EMANE events. It does **not**
understand DDS **application traffic** (per-channel message flow, delivery, latency),
which lives above EMANE. So GUI B is really two complementary views:
- **RF-layer picture** (positions, connectivity, link quality) → **SDT3d** covers this.
- **Message-layer picture** (per-channel DDS flow animation) → custom Cytoscape.js view.

*(SDT3d, the RF/geo half of the network picture, is driven by EMANE and owned by the [harness](test-harness.md); Scope owns the Cytoscape.js DDS-flow view.)*

## Comms-network visualization gaps (§2.6)

- **Three connectivity layers, toggetable** — the same nodes carry three *different* graphs:
  (1) **RF connectivity** (who-can-hear-whom, from EMANE), (2) **DDS matches** (which
  readers/writers are matched, from discovery), (3) **live data flow** (actual traffic). A
  convincing viz lets you toggle/overlay these — they diverge exactly when the thesis is
  interesting (e.g. RF up but discovery not yet matched, or matched but partition-isolated).
- **Team/partition coloring** — make partition isolation visually obvious (color by team);
  central to the CRUD story.
- **Channel filter** — toggle edges by channel (commands / status / events / team) to declutter.
- **Health encoding** — distinguish delivered vs dropped vs retransmitted; link quality (SNR)
  as edge style.
- **Event overlay + scrubber** — scenario events (team-assign, link-cut, node-kill) on a
  timeline with replay scrubbing.

## GUI B — Network Picture (additions, §2.7)

- **Legend / encoding key** — channel colors, health styling, team colors, edge-width meaning.
- **Layout control** — logical vs geographic layout toggle, pin/save layouts, declutter
  filters (by node/team/channel), zoom/pan/focus.
- **Mobility trails** — node movement breadcrumbs over time.
- **Transcript usability** (the inspector firehose problem) — filter/search by topic/source/
  content, freeze/throttle/clear the stream, and a **single-message detail view** (full
  decoded fields + RTPS header + timing).

## Demo / reporting (§2.7)

- **Presentation mode** — clean full-screen, guided walkthrough, trigger scenario beats live.
- **Recorded-run playback** — play/pause/seek/speed over a captured run (Recording Service + scrubber).
- **A/B comparison view** — overlay two runs (e.g. multicast vs unicast) in viz/dashboards.
- **One-click capture** — screenshot current view + metrics window for the writeup.
