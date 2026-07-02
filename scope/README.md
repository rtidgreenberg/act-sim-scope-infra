# scope/ — the Scope (live monitoring & visualization)

Passive, **read-only** monitoring of *any* DDS system. Deployable standalone — including
against a customer's live network. Imports **nothing** from `harness/`.

Fed by the **sniffer** (RTPS Analyzer), which publishes JSON events — **discovered entities +
deserialized messages** — on a pub/sub bus. The Scope backend aggregates these into a
topology/state model and serves the frontend + a data/query API.

Planned contents:
- `backend/` — FastAPI: bus subscriber, topology/state aggregation, `/stream` + `/nodes-edges`
  + query API. Houses the **shared aggregation library** (the harness imports this, one-way).
- `web/` — Cytoscape.js node graph + message-flow view + endpoint inspector.
- `analyzer/` — RTPS Analyzer (sniffer) integration + the JSON-Schema event contract.
- `observability/` — RTI Observability collector-service + Grafana dashboards config.

See the plan [overview](../docs/EMANE_SIMULATION_PLAN.md) — most relevant here:
[scope.md](../docs/scope.md) (this product: inspector, GUI B, viz),
[sniffer.md](../docs/sniffer.md) (event contract + decode placement), and
[architecture.md](../docs/architecture.md) (component map, tech stack, repo layout).
