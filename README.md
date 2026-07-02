# act-sim-scope-infra

Private sim/test infrastructure and the **Scope** live-monitoring tool for the
[RTI ACT](https://github.com/rticommunity/rticonnextdds-usecases-act) (Autonomous
Collaborative Teaming) reference architecture.

> **Status:** Planning / early scaffold. The authoritative design is
> **[docs/EMANE_SIMULATION_PLAN.md](docs/EMANE_SIMULATION_PLAN.md)** — read it first.
> The `harness/docker` + `harness/compose` files are early drafts and are **not yet
> buildable** (they predate the ACT-submodule + overrides layout; see plan §2.4).

## What this is

Containerize the ACT node stack, run it over an **EMANE**-emulated mesh-radio network,
drive scripted + manual scenarios, and **validate the architecture's thesis** (resilient
DDS + Routing Service C2 over DDIL networks) — with live visualization and metrics.

## Two products, one repo (one-way dependency)

| Package | Product | Nature |
|---|---|---|
| **`scope/`** | **Scope** — passive, **read-only** live monitoring & visualization (node graph, message flow, endpoint inspector, dashboards) fed by the RTPS Analyzer "sniffer" | Deployable standalone — even against a customer's live DDS network |
| **`harness/`** | **Test/Sim Harness** — EMANE, container orchestration, scenario runner, fault injection, manual RF/DDS control, NiceGUI control console | Internal test tooling only |

**Rule:** `scope/` imports nothing from `harness/`. The harness may import the shared
aggregation library from `scope/` (one-way), and both subscribe independently to the
sniffer's event bus. This keeps the Scope liftable into its own product later.

## Layout

```
docs/EMANE_SIMULATION_PLAN.md   # the plan (source of truth)
scope/                          # Scope backend + Cytoscape frontend + Analyzer integration + observability
harness/
  act/                          # (TODO) git submodule → public rticonnextdds-usecases-act
  overrides/                    # (TODO) private QoS/sim overrides (UDP-only, seq/ts, emane0 pinning)
  docker/                       # node image, entrypoint, remote-admin (reference)
  compose/                      # docker-compose (M0 plain bridge; EMANE later)
  emane/ backend/ gui/ scenarios/   # (TODO)
```

## Next steps (from the plan)

1. Add public ACT as a submodule: `git submodule add https://github.com/rticommunity/rticonnextdds-usecases-act harness/act`
2. Wire `harness/docker/Dockerfile.node` COPY paths to `act/…` + `overrides/…`.
3. M0: bring the DDS node stack up in containers over a plain bridge (no EMANE).
4. See the plan's milestones M0–M4.
