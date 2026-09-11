# act-sim-scope-infra

Private sim/test infrastructure and the **Scope** live-monitoring tool for the
[RTI ACT](https://github.com/rticommunity/rticonnextdds-usecases-act) (Autonomous
Collaborative Teaming) reference architecture.

> **Status:** Early implementation. The authoritative design is
> **[docs/EMANE_SIMULATION_PLAN.md](docs/EMANE_SIMULATION_PLAN.md)** — read it first.
> The current container baseline is under `harness_v2/`; the EMANE image package is
> prepared, but the RF topology has not yet been validated.

## What this is

Containerize the ACT node stack, run it over an **EMANE**-emulated mesh-radio network,
drive scripted + manual scenarios, and **validate the architecture's thesis** (resilient
DDS + Routing Service C2 over DDIL networks) — with live visualization and metrics.

## Two products, one repo (one-way dependency)

| Package | Product | Nature |
|---|---|---|
| **`scope/`** | **Scope** — passive, **read-only** live monitoring & visualization (node graph, message flow, endpoint inspector, dashboards) fed by the RTPS Analyzer "sniffer" | Deployable standalone — even against a customer's live DDS network |
| **`harness_v2/`** | **Test/Sim Harness** — container orchestration, EMANE, scenario runner, fault injection, manual RF/DDS control, and the control console | Internal test tooling only |

**Rule:** `scope/` imports nothing from `harness_v2/`. The harness may import the shared
aggregation library from `scope/` (one-way), and both subscribe independently to the
sniffer's event bus. This keeps the Scope liftable into its own product later.

## Layout

```
docs/EMANE_SIMULATION_PLAN.md        # architecture and roadmap
docker/connext-7.7/                  # Ubuntu 22.04 Connext/EMANE node image
harness_v2/                          # active harness, data model, QoS, simulators, scripts
  scripts/run_mesh.sh                # host-process diagnostic mesh
  scripts/run_container_baseline.sh  # Docker-bridge delivery-audit baseline
gui/mesh_dashboard/                  # Scope dashboard implementation
router/                              # C++ DynamicData router and tests
```

## Fresh VM setup

For a reproducible Ubuntu 22.04 or 24.04 host, Docker data-root sizing, license handling, image
verification, and router tests, follow [docs/fresh-instance.md](docs/fresh-instance.md).
The bootstrap entry point is `scripts/setup_instance.sh`.
