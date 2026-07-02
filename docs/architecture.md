# System Architecture & Repository Strategy

> Part of the **ACT EMANE Simulation & Thesis-Validation** plan — start at the [overview & index](EMANE_SIMULATION_PLAN.md). Docs are organized by **process**; cross-cutting concerns live in [architecture.md](architecture.md), [decisions-and-risks.md](decisions-and-risks.md), [thesis-and-claims.md](thesis-and-claims.md), and [roadmap.md](roadmap.md). Original plan section numbers (§X.Y) are retained for traceability.

---

System-level architecture shared by all three processes ([sniffer](sniffer.md), [scope](scope.md),
[test-harness](test-harness.md)): the two-product model, the sniffer-bus coupling, the locked tech
stack + component map, cross-cutting requirements, and the repository strategy. **GUI A/B and
control-plane requirements have moved to their owning process docs** (GUI B + inspector →
[scope.md](scope.md); GUI A + control-plane API → [test-harness.md](test-harness.md)).

## 2.5 End-state vision & GUI requirements (working backwards)

**The experience we're building toward.** An engineer opens a control console, picks a
scenario (or goes manual), hits run, and watches — on a separate network picture —
platforms move, links degrade, and DDS messages flow (or fail) in real time, while
Grafana records the quantitative story. They can pause, reach in (sever a link, send a
command, disband a team), and see the network react immediately. Everything is
repeatable and captured for the thesis writeup.

**TWO PRODUCTS, one clean interface (key architecture).** The work splits into two
**independently deployable** products — this is a hard boundary, not just modules:

| Product | Purpose | Depends on | Distribution |
|---|---|---|---|
| **Scope** (DDS Scope — monitoring & observation) | Passive, **read-only** view of *any* DDS system — node graph, message flow, endpoint inspector/transcription, metrics dashboards | Only passive RTPS capture (RTPS Analyzer) + optional Observability. **No EMANE, no orchestration, no control backend.** | **Deployable at a customer site** against their live network (proprietary due to the Analyzer, but a deliverable) |
| **Test/Sim Harness** (control & simulation) | Stand up + perturb an emulated network: EMANE, container orchestration, scenario runner, fault injection, manual RF/link/DDS control | The emulation environment | **Internal test tooling only** |

**Coupling via the sniffer bus (not point-to-point).** The sniffer is a **generic pub/sub
producer** — it publishes **discovered entities + deserialized messages** as JSON events, and
**both the Scope and the harness subscribe to it independently.** The bus is the shared
integration point; there is **no runtime dependency between Scope and harness**. The harness
reads discovered GUIDs/endpoints/topology + message traffic straight off the bus (for node
lists, **remote-admin targeting**, scenario **triggers** like "when P30 publishes ContactReport,
cut the link"). The Scope has no knowledge the harness exists → deploys standalone.

**Shared aggregation, not duplicated.** Both need to fold raw events into a topology/state
model — so that logic is a **shared library in the Scope/observer package that the harness
imports**: a *code* dependency in the allowed direction only (**harness → observer, never the
reverse**). No runtime coupling; Scope still lifts out cleanly.

**Two run-modes, same code:**
- **Deployment mode** — sniffer + Scope only, on the customer's live DDS traffic.
- **Test/sim mode** — sniffer + Scope + harness, all on the same bus; harness adds control +
  scenario logic and shares selection/clock with the Scope frontend.

The Scope being **100% passive / read-only** is what makes it safe to deploy live.

**Surfaces, mapped to products:**
| Surface | Product | Question it answers |
|---|---|---|
| **Network Picture** (GUI B: Cytoscape graph + flow + endpoint inspector) | **Scope** | "What is the network doing now?" |
| **Grafana** dashboards | **Scope** | "What happened over time, quantitatively?" |
| **Control Console** (GUI A) | **Harness** | "What do I want the environment to do now?" |
| **SDT3d** RF/geo globe | **Harness/sim** (driven by EMANE events) | "Where are the nodes + RF links?" — in a *real* deployment this view would instead come from platform **position telemetry** (a DDS topic), observed passively |

*(GUI A requirements → [test-harness.md](test-harness.md); GUI B requirements → [scope.md](scope.md); the control-plane API backbone → [test-harness.md](test-harness.md).)*

**Locked scope:**
- GUI A and GUI B are **separate apps**.
- GUI B = **two separate views**: SDT3d (RF/geo) **+** Cytoscape.js DDS flow view (must-have).
- Scenarios: **predefined files + live manual controls**; no in-GUI scenario authoring.

## Application roles (§2.8)

**Roles — one responsibility per app:**
| App | Sole job | Consumes | Produces | Product |
|---|---|---|---|---|
| **Sniffer** (RTPS Analyzer) | Passively capture RTPS; publish **discovered entities + deserialized messages** | the wire (pcap/iface) | **JSON event bus** (contract below) | shared producer |
| **Scope backend** | Aggregate events → topology/state model; serve API | **sniffer bus** + Observability | `/stream`, `/nodes-edges`, query API | Scope |
| **Scope frontend** | Render graph + flow + endpoint inspector | Scope backend | UI | Scope |
| **Grafana + Obs. collector** | Metrics dashboards | Observability | dashboards | Scope |
| **Control backend** | Orchestrate/EMANE/remote-admin/scenarios; scenario triggers off bus traffic | **sniffer bus** (+ shared agg. lib) | control REST/WS | Harness |
| **GUI A** (NiceGUI) | Drive the environment | control backend | UI | Harness |
| **SDT3d** | RF/geo globe | EMANE events | UI | Harness/sim |

### End-state component map (sniffer bus at the center)
```
   node containers (routing service + sim + emane) ── RTPS on emane0/bridge
                                   │ (sniff, passive)
                       ┌───────────▼──────────────────────┐
                       │ SNIFFER (RTPS Analyzer)           │  publishes JSON events:
                       │ passive, packet-level             │  • discovered entities
                       └───────────┬──────────────────────┘  • deserialized messages
                                   │  pub/sub bus (JSON events)
                 ┌─────────────────┴───────────────────────┐
        subscribe▼                                          ▼subscribe
  ══ SCOPE (standalone, passive, read-only) ══   ══ HARNESS (internal test/sim only) ══
  ┌───────────────────────────┐                  ┌───────────────────────────────┐
  │ Scope backend             │  shared agg. lib │ Control backend               │
  │ (aggregate→topology,      │  (harness imports│ (orchestrate/EMANE/           │
  │  serve /stream+/query API)│───imports from──▶│  remote-admin/scenarios;      │
  └──────┬────────────────────┘   scope, one-way)│  triggers off bus traffic)    │
         ▼                                        └──────┬────────────────────────┘
  ┌──────────────┐ ┌──────────────────────┐             ▼
  │ Scope        │ │ Grafana ◀ Obs.        │      ┌─────────────┐  ┌────────────┐
  │ frontend     │ │ collector→Prom+Loki   │      │ GUI A       │  │ SDT3d      │
  │ (Cytoscape)  │ │ (in-band telemetry)   │      │ (NiceGUI)   │  │ (EMANE geo)│
  └──────────────┘ └──────────────────────┘      └─────────────┘  └────────────┘
```
*Two passive observation planes: the **sniffer** (out-of-band packet capture → discovered
entities + deserialized messages on the bus) and **Observability** (in-band telemetry →
Prometheus/Grafana). Neither adds a DDS subscriber. Scope and harness are **independent bus
subscribers** — no runtime dependency between them; the only link is a one-way **code** import
(harness → the shared aggregation lib in the Scope package). **Payload decode happens inside the
sniffer** (its decode/normalize stage, §2.9), so the bus already carries **deserialized** messages
— no subscriber decodes, and it's still packet-level (no DDS subscriber, measurement path unaffected).*

### Tech stack (locked)
| Component | Tech |
|---|---|
| **Sniffer** (analyzer core + decode/normalize stage) — *shared producer* | **RTPS Analyzer** (Node core, pristine) + a **Python decode/normalize stage** (XCDR + fragment reassembly + `act_types.xml`, command-gated). Publishes **deserialized** events on the bus. |
| **Scope backend** (collector + data/query API) — *shippable* | **Python FastAPI** — consumes the **sniffer's deserialized bus** (no decode here), reads Observability metrics; serves WebSocket (`/stream`, `/nodes-edges`) + query API (GUIDs/topology/endpoints). **No harness deps.** |
| **Control backend** (control-plane + scenario runner) — *internal* | **Python FastAPI** — REST (control) + WebSocket; **subscribes to the sniffer bus** + imports the shared aggregation lib from the Scope package (one-way) |
| ↳ orchestration | **docker-py** — start/stop/kill/add node containers |
| ↳ RF control | **EMANE Python event API** — Location/Pathloss/CommEffect events |
| ↳ DDS control / remote admin | **`rti.connext`** — publish messages + RS remote admin via `ServiceAdmin` request/reply (**pure Python; C++ tool now reference-only, off critical path**) |
| ↳ metrics read | **Prometheus HTTP API** |
| **GUI A: Control Console** | **NiceGUI** (pure Python, live via WebSocket) |
| **GUI B-1: DDS flow view** | **Cytoscape.js** SPA served by backend (WebSocket `/stream`) |
| **GUI B-2: RF/geo view** | **SDT3d** (EMANE-native; Java/WorldWind, needs X11) |
| **Metrics/dashboards** | RTI Observability → `rticom/collector-service` → Prometheus + Loki → Grafana |

**Consequence:** with Python remote admin, **no RTI Debian packages / apt token are
needed anywhere** — the whole stack builds from `rticom/*` images + PyPI + EMANE `.deb`s.

## Cross-cutting requirements (§2.7)

Highest value — these emerge from "two separate apps" + passive observation:
- **Shared selection across apps** *(test/sim mode)* — select a node/edge in GUI B → the
  control console focuses that node's actions. Enabled by the harness consuming the Scope's
  live GUIDs/topology (one-way dependency). The Scope works alone without it.
- **Shared scenario clock** *(test/sim mode)* — one `T+mm:ss` since scenario start, shared by
  GUI A timeline, GUI B scrubber, Grafana, SDT3d, and the transcript. A sim-mode integration;
  the standalone Scope just shows wall-clock/live.
- **Tooling self-health** — is the backend up? is the RTPS Analyzer feeding events? EMANE
  up? Observability collecting? Must distinguish **"no traffic" from "sniffer died"** — a
  passive tool that silently stops looks identical to a quiet network. Trust the picture.
- **Async command feedback** — control actions are async (remote-admin request/reply,
  container ops take time): show pending → success/failure (+ latency), never leave the
  operator guessing.
- **Current control-state inspector** — "what state is the system in *now*?": enabled
  sessions, current partitions/teams, per-link RF params, QoS in effect. Distinct from
  traffic; manual + scenario actions mutate it and the operator must see the result.
- **Alerts / event feed** — surface liveliness loss, link drop, delivery-ratio threshold
  breach, reliable-queue backup, discovery storm — so the operator isn't forced to stare.
- **Reset to baseline** — one-click return to known-good after manual fiddling; guardrails
  on destructive actions; optional **read-only/view-only mode** (safe demos / multiple viewers).

## 2.4 Repository strategy

This sim/test infra lives in a **new private repo** (the ACT repo is public, and the **RTPS
Analyzer is internal** — it must not appear in a public repo). ⚠️ The current `docs/` +
`sim/` scaffold is **uncommitted** in the public ACT repo — relocate it; do not commit here.

**Recommended layout — split by product (Scope = deployable, harness = internal):**
```
scope/   (private, but a deployable DELIVERABLE — self-contained, no harness deps)
├── backend/                  # FastAPI: collector + data/query API
├── viz/web/                  # Cytoscape.js graph + flow + endpoint inspector
├── analyzer/                 # RTPS Analyzer integration (INTERNAL)
└── observability/            # collector-service + Grafana dashboards config

harness/    (private, INTERNAL test/sim only — client of the Scope)
├── act/                      # git submodule → public rticonnextdds-usecases-act (pinned)
├── overrides/                # private QoS/sim overrides (UDP-only, seq/ts, emane0 pinning)
├── docker/ compose/ emane/   # node image, compose, EMANE configs
├── backend/                  # FastAPI: control-plane + scenario runner (consumes Scope API)
├── gui/                      # NiceGUI control console
└── scenarios/
docs/EMANE_SIMULATION_PLAN.md
```
ACT stays clean/public. ✅ **Decided: ONE private repo** with `scope/` and `harness/` as
internal packages (not separate repos) for now. Keep the **one-way dependency a hard package
boundary** (`scope/` imports nothing from `harness/`) so that if this gains traction, the
the Scope lifts out into its own productized/deliverable repo cheaply. RTI already uses
submodules, so ACT-as-submodule under `harness/` is idiomatic; build context = repo root,
`Dockerfile.node` COPYs `act/…` + `overrides/…`.

**Decisions:** ✅ ACT as **git submodule**. ✅ ACT stays **pristine** — the sim applies
**private overrides**, never edits the submodule.

**Override mechanism (keep ACT untouched):**
- `Dockerfile.node`: COPY the ACT submodule, then **layer private overrides on top** —
  - **QoS/config** (LAN→UDP-only, `emane0`/loopback interface pinning, Monitoring Library 2.0):
    private override XML in the image; `NDDS_QOS_PROFILES` / env point at the private copies
    (or bind-mount over the ACT QoS files at run).
  - **Instrumented sims** (seq#/timestamp payloads): private modified `platform_sim.py` /
    `control_sim.py` copied over the ACT versions in the image (or bind-mounted at run).
- Result: bump the submodule to track upstream ACT; overrides stay isolated and reviewable.
- Build context moves to the private repo root; `Dockerfile.node` COPY paths shift to `act/…`
  + `overrides/…`.

## 8. Proposed repo layout additions

```
sim/
  docker/
    Dockerfile.node            # rticom/routing-service + python sim, role-selected
    Dockerfile.remote-admin    # reference-only (C++ tool); control plane uses Python remote admin
    entrypoint.sh              # (emane →) routing → sim, health-gated
  compose/
    docker-compose.yml         # 1 control + N platforms + control bridge
    prometheus.yml
    grafana/ (dashboards, provisioning)
  emane/
    platform.xml, transportdaemon.xml, rfpipe-nem.xml
    scenarios/*.eel            # mobility + pathloss timelines per scenario
  observability/
    monitoring_qos.xml         # Monitoring Library 2.0 enable snippet (sims + RS)
    collector-service.xml      # rticom/collector-service config (→ Prometheus + Loki)
    prometheus.yml, loki.yml
    grafana/                   # RTI Observability dashboards + provisioning
  collector/
    collector.py               # FastAPI: reads Prometheus + EMANE events →
                               #   /nodes-edges (topology JSON) + /stream (WebSocket)
  viz/
    web/                       # PRIMARY: custom Cytoscape.js graph (animated flow + mobility)
  scenarios/
    run_S1_baseline.sh ... run_S6_discovery.sh
```
