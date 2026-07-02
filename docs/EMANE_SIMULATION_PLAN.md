# ACT — Containerized EMANE Simulation & Thesis-Validation Plan

> **Status:** Draft v1 · Owner: dgreenberg · Last updated: 2026-07-01
>
> Goal: containerize the ACT node stack, run it over an **EMANE**-emulated mesh-radio
> WAN, execute scripted scenarios, and produce visualizations that **validate the
> architectural thesis** of this repo.

---

## 1. The thesis we are validating

> RTI Connext DDS + a per-node Routing Service gateway can serve as the resilient
> "connective tissue" for **Autonomous Collaborative Teaming** over **DDIL**
> (Denied, Degraded, Intermittent, Limited) networks — enabling 1:N control,
> domain isolation, QoS-based bandwidth prioritization, dynamic runtime team CRUD,
> and peer-to-peer mesh coordination.

Source of truth: [SYSTEM_ARCH.md](../SYSTEM_ARCH.md). Testable claims and their
current status are in [§7 Thesis-claim traceability](#7-thesis-claim-traceability).

## 2. Locked decisions

| Decision | Choice |
|---|---|
| RTI packaging | **Node image:** `FROM rticom/routing-service` + **PyPI** `rti.connext`; license mounted at runtime. **No RTI Debian packages / apt token anywhere** — remote admin is done in Python (`rti.connext` ServiceAdmin request/reply). The C++ `remote_admin` tool + `Dockerfile.remote-admin` are **reference-only, off the critical path**. |
| EMANE source | Upstream **[adjacentlink/emane](https://github.com/adjacentlink/emane)** — prebuilt `.deb` packages on GitHub Releases (+ RF Pipe model, `emane-model-rfpipe`) install cleanly on the Debian/Ubuntu node image |
| EMANE RF model | **CommEffect + RF Pipe** — CommEffect for precise manual/scenario impairment (exact per-link loss / latency / jitter / bandwidth directives); RF Pipe for realistic range / pathloss / mobility. Pick per scenario. |
| Initial scale | **2–5 nodes** (e.g. 1 Control + 2–4 Platforms) |
| Orchestration | **docker-compose**, single host |
| Metrics pipeline | **RTI Observability Framework** (supported): Monitoring Library 2.0 on sims + Routing Services → **`rticom/collector-service`** → **Prometheus** (metrics) + **Grafana Loki** (logs) → **RTI-provided Grafana dashboards**. Our custom collector stops reimplementing DDS metrics and just adds topology/stream + EMANE + app-level e2e latency. |
| Naming | The passive monitoring/viz product = **Scope** (a.k.a. **DDS Scope**) — chosen over "Observer" to avoid collision with RTI **Observability**. Its packet-capture engine = the **sniffer** (the RTPS Analyzer). |
| Viz | **Scope** app (Cytoscape.js: node graph + flow + endpoint inspector) · **Grafana** (RTI dashboards + custom) · **EMANE SDT3d** (RF globe) |
| Passive observation | **RTPS Analyzer** (existing tool, the **sniffer**) — Wireshark-based, detects DDS events off the wire + **publishes socket events**. **Packet-level, not DDS-level** — zero observer effect. Generates the **node graph** and handles **payload introspection**; the Scope backend just consumes its socket events. |
| Base OS | **Ubuntu 22.04 (Jammy)** — EMANE `.deb`s + RTI Debian packages both target it |
| License | Internal tool — license covers **all RTI products** incl. **Observability Framework** (Monitoring Library 2.0 + Collector Service), Routing Service, Python, CDS, in containers |

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

### GUI A — Test Control Console — requirements
- **Environment lifecycle:** bring topology up/down/reset; add/remove Platform/Control
  nodes at runtime; per-node health.
- **Scenario mode:** load/select predefined scenario; run / pause / step / stop; progress
  on a timeline; reset to baseline.
- **Manual RF/link control:** degrade a link (set datarate → e.g. 50 kbps / raise
  pathloss), sever & restore, set loss %, reposition a node (changes RF).
- **Manual DDS/app stimulus:** send a message on a topic (e.g. ControlCommand → a
  specific platform); enable/disable detail status; assign/reassign/disband teams
  (partition CRUD); enable/disable a channel/session.
- **Fault injection:** kill / restart a node (peer-loss test).
- **At-a-glance monitoring:** current per-channel traffic, delivery/latency/loss, link +
  team state — enough to act on (deep dives live in GUI B / Grafana).
- **Repeatability & capture:** define/save/replay a scenario (timed action sequence);
  action+response event log; export artifacts.

### GUI B — Network State Visualization — requirements
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

**Locked scope:**
- GUI A and GUI B are **separate apps**.
- GUI B = **two separate views**: SDT3d (RF/geo) **+** Cytoscape.js DDS flow view (must-have).
- Scenarios: **predefined files + live manual controls**; no in-GUI scenario authoring.

### Working-backwards implication: a control-plane API (the backbone)
Both GUIs, the scenario runner, and scripted/CI tests all sit on one **backend control
service** exposing primitives across:
- **EMANE:** set pathloss/location/RF-Pipe params; emit location events (link + mobility).
- **Orchestration:** start/stop/kill/add containers (node lifecycle, fault injection).
- **DDS:** publish messages; drive Routing Service remote admin (partition/session);
  **introspect endpoints** (discovery) + **subscribe on demand** and stream samples (§2.6).
- **State/query:** read topology + metrics (Prometheus / EMANE events).

**Define this control surface before choosing GUI tech.**

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
(harness → the shared aggregation lib in the Scope package). Since the sniffer deserializes,
there is **no CDR-decoder work-item**.*

### Tech stack (locked)
| Component | Tech |
|---|---|
| **Scope backend** (collector + data/query API) — *shippable* | **Python FastAPI** — consumes RTPS Analyzer socket events, reads Observability metrics; serves WebSocket (`/stream`, `/nodes-edges`) + query API (GUIDs/topology/endpoints). **No harness deps.** |
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

## 2.6 Requirements review — gaps & additions

Review of the GUI + control-plane plan for missing high-level requirements.

### NEW — Endpoint Inspector / live sample subscriber (GUI B + control plane)
Select a node in the graph → panel lists the **DDS endpoints that node currently exposes**
(topics, readers/writers, type, QoS) → click an endpoint → backend **dynamically subscribes**
and **streams live samples** to the panel. Must be **live**: when detail-status is enabled
(or a team is assigned), new routes/endpoints appear and the panel updates automatically —
demonstrating "expose more topics on demand" visually.
- Requires **DDS discovery introspection** (builtin DCPSPublication/DCPSSubscription topics,
  or RTI Observability entity data) + on-demand **DynamicData** readers for sample content.
- This is essentially embedding **Admin Console / DDS Spy** behavior, driven by graph selection.

**Inspector + node graph — RESOLVED via the RTPS Analyzer (packet-level, zero DDS impact).**
The existing **RTPS Analyzer** (Wireshark-based) is the single passive observation source.
No DDS participant anywhere:
- **Node graph** ← the Analyzer detects DDS entities/events from RTPS on the wire (SPDP/SEDP
  discovery → participants/endpoints/topics; DATA submessages → live flow) and **publishes
  socket events**. The backend consumes these → `/nodes-edges` + `/stream` → Cytoscape.
- **Endpoint list** ← same socket-event stream (discovery-derived), mapped to nodes via
  participant name / GUID. Newly exposed routes (detail-status/team enable) show up as new
  discovery events → panel updates live.
- **Sample content** ← the Analyzer's **packet introspection** (decodes payloads).
- Subsumes the earlier "custom CDR decoder" work-item and removes any need for a DDS-level
  subscriber or type propagation — **WAN QoS unchanged**.

**Integration unknowns (need from the RTPS Analyzer):** (1) socket-event **transport +
schema** (so the backend can consume); (2) does it **decode payload fields**, and does it need
type/IDL input (we can supply `act_types.xml`); (3) does it derive topology from **discovery
(SEDP)** or only from data traffic.

**Capture considerations:** RTPS must be on a **sniffable transport** — UDP is visible,
**SHMEM is not**. ✅ **Decision: LAN QoS → UDP-only** (disable SHMEM) so LAN-local traffic is
also fully sniffable — consistent with SYSTEM_ARCH ("SHMEM *or* UDP loopback"). Config change
to `config/qos/lan_qos_lib.xml` (transport mask UDPv4, drop shmem) + `params/system_params.sh`
peers. **Capture point:** per-node **`emane0`** (post-EMANE-decap RTPS) once EMANE is in; the
shared **bridge** for the M0 plain-bridge phase.

*Pending:* user will provide the **RTPS Analyzer repo** → introspect it to infer the socket
event transport/schema, payload-decode capability, and topology source (the integration
unknowns above), and derive its container/tech requirements.

*Connext note:* 6.0+ propagates TypeObject in discovery (7.7+ = TypeObject v2 + on-demand
TypeLookup); propagation only matters for apps that must *learn* a type from the wire — not
for one that loads it locally. *Caveat:* a WAN monitor sees only WAN-crossing traffic; purely
LAN-local topics would need an optional in-node agent later (out of scope for v1).

**Observe without impacting the system (observer effect).** DDS has no promiscuous listen —
a DataReader is a real protocol participant: it forces discovery on every matching writer,
and on **reliable** topics obligates writers to retain/heartbeat/repair for it (extra traffic
+ altered timing) and consumes **RF bandwidth** on the unicast WAN. So a naive "subscribe to
inspect" can skew the very latency/loss we measure. Design split:
- **Measurement (loss/latency/rate) = zero-impact by construction** — receiving sims
  self-report from seq#/timestamp, and **Observability** reports per-endpoint counts/rates.
  The measurement path never adds a subscriber.
- **Node graph + live transcription = the RTPS Analyzer** (Wireshark-based, packet-level,
  publishes socket events). ✅ **DECIDED.** **Zero DDS perturbation** — no entity, no
  discovery, no ACKs. No DDS Security here, so RTPS is plaintext/decodable. See the
  RTPS-Analyzer block above for capture points + integration unknowns.
- **Fallback** if payload decode gaps remain: a **best-effort** observer reader (matches
  reliable writers without imposing repair burden) attached **LAN-side inside the node**
  (off the constrained RF link) — bounded, characterizable impact.
- **Credibility check:** run scenarios **observer-off vs observer-on** to quantify that the
  tooling doesn't skew results.

### Test-operation gaps (GUI A / scenario runner)
- **Result capture & verdict** — per-scenario metric windows, **pass/fail vs thresholds**,
  and an **artifact bundle** (metrics CSV, event log, screenshots, optional pcap). This is the
  actual thesis *evidence* — currently under-specified.
- **Configurable traffic profiles** — sims today emit tiny random payloads at 1 Hz. To validate
  bandwidth claims (50 kbps, video vs status) we need **per-channel size + rate** parameters
  (e.g. detail-status = large/fast, primary = small/1 Hz). Test-operation requirement.
- **Record & replay** — leverage **`rticom/recording-service`** to capture a run and replay it
  into the viz for offline analysis / repeatable demos.
- **A/B comparison** — run the same scenario under two configs (multicast vs unicast discovery,
  reliable vs best-effort) and overlay results. Thesis validation is often comparative.
- **Cross-layer loss correlation** — line up app-level loss (seq gaps) with EMANE PHY drops to
  show *why* delivery fell. Requires joining app seq/timestamp with `emanesh` stats.
- **Time/pause semantics** — define what "pause" means across containers (freeze mobility +
  stop stimulus injection; DDS itself can't truly pause). Needed for step/replay.

### Comms-network visualization gaps (GUI B)
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

### Control-plane gaps
- **RF impairment model** — ✅ decided: **CommEffect + RF Pipe** (CommEffect for precise
  manual/scenario impairment; RF Pipe for realistic range/mobility; pick per scenario).
- **Guardrails** — confirm destructive manual actions (kill node); prevent invalid states.

## 2.7 Additional GUI requirements

### Cross-cutting (highest value — emerge from "two separate apps" + passive observation)
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

### GUI A — Control Console (additions)
- **Presets / quick-actions** ("jam Control↔P30", "form Team A", "enable detail on selected").
- **Multi-select / bulk actions** (assign N platforms to a team, kill several).
- **Running-script view** — while a predefined scenario runs, show its steps + current/next
  (viewing, not authoring).
- **Run/session history** — name runs, browse past runs, load their artifacts for review.

### GUI B — Network Picture (additions)
- **Legend / encoding key** — channel colors, health styling, team colors, edge-width meaning.
- **Layout control** — logical vs geographic layout toggle, pin/save layouts, declutter
  filters (by node/team/channel), zoom/pan/focus.
- **Mobility trails** — node movement breadcrumbs over time.
- **Transcript usability** (the inspector firehose problem) — filter/search by topic/source/
  content, freeze/throttle/clear the stream, and a **single-message detail view** (full
  decoded fields + RTPS header + timing).

### Demo / reporting (the thesis is partly a story)
- **Presentation mode** — clean full-screen, guided walkthrough, trigger scenario beats live.
- **Recorded-run playback** — play/pause/seek/speed over a captured run (Recording Service + scrubber).
- **A/B comparison view** — overlay two runs (e.g. multicast vs unicast) in viz/dashboards.
- **One-click capture** — screenshot current view + metrics window for the writeup.

## 2.8 Application roles & the sniffer event contract

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

**Sniffer event contract — make it a generic producer "anyone can subscribe to":**
Three orthogonal choices:
- **Schema** — the sniffer publishes **two categories**, documented via **JSON Schema**:
  - **Discovered entities** — `participant_discovered/lost`, `endpoint_discovered/lost`
    (topic/type/QoS/partition/GUID), `match`, `liveliness/link`.
  - **Deserialized messages** — `data_sample` (topic, writer GUID, size, seq, **decoded
    fields** — the sniffer deserializes, so **no CDR-decoder work-item**), and derived
    `flow_stats` (per-topic/writer rate/interval).

  Optional **CloudEvents** envelope (`id/source/type/time/data`) so it's a recognized standard.
- **Encoding** — **JSON** (human-readable, universal; the user-friendly/generic goal).
- **Transport** — pub/sub so N independent subscribers attach without the sniffer knowing them:
  - **MQTT** (recommended) — ubiquitous clients, topic hierarchy `dds/<domain>/<topic>/<event>`
    for filtered subscription, lightweight broker (mosquitto), Grafana/Node-RED friendly.
  - **NATS** — very simple/fast subjects; fewer built-in integrations.
  - **WebSocket / SSE** — browser-native (SSE consumable by `curl`); great for the frontend.
  - **Raw NDJSON over TCP** — dead simple, any language; no fan-out/filtering built in.

**Decisions:** ✅ **Encoding = JSON, documented via JSON Schema** (the contract). ⏳
**Transport = deferred** until we review what the Analyzer already emits, then pick
(MQTT / NATS / WS-SSE) and add a **thin adapter** if its native socket output differs from the
target. The JSON-Schema event contract is transport-independent, so it's safe to define now and
bind to a transport later — the sniffer stays a generic producer; the Scope backend is just one
subscriber.

## 3. Target topology (initial)

```
                 Control_20                Platform_30           Platform_31
             ┌───────────────┐        ┌───────────────┐    ┌───────────────┐
  app LAN →  │ control_sim.py │        │ platform_sim  │    │ platform_sim  │
 (dom 20/30) │ routing (ctrl) │        │ routing (plat)│    │ routing (plat)│
             │ emane + emane0 │        │ emane + emane0│    │ emane + emane0│
             └───────┬────────┘        └───────┬───────┘    └───────┬───────┘
   emane0 (RF IP) ───┤                         │                    │
                     └──────────── EMANE OTA + Event bridge ─────────┘
                        (docker bridge; carries over-the-air frames
                         + location/pathloss events between nodes)

   WAN DDS (domain 200) rides emane0  →  EMANE RF Pipe applies bw/delay/loss/pathloss
   LAN DDS (domain 20/30) stays inside the container (loopback/shmem) — never crosses RF
```

## 4. Node container anatomy

One image, role-selected at runtime (`ROLE=platform|control`). Each node container
runs **three** cooperating processes via a supervisor/entrypoint:

1. **`emane`** — one instance per container; creates the `emane0` TAP, assigns the
   node's *RF IP*, exchanges OTA + events over the control bridge.
2. **Routing Service** — the domain gateway (`-cfgName platform|control`), bridging
   the node-local LAN domain ↔ WAN domain 200.
3. **Simulator** — `platform_sim.py` / `control_sim.py` traffic gen/sink.

**Critical interface pinning (new QoS work — see §6):**
- **WAN** participant (domain 200) must bind **only to `emane0`** so its traffic is
  subject to the emulated RF.
- **LAN** participant (domain 20/30) must bind to **loopback/shmem only** so
  high-rate local traffic never leaks to the RF (proves domain isolation, SYS-REQ-13).

**Container requirements:** `--cap-add=NET_ADMIN`, `--device=/dev/net/tun`,
`sysctl` for multicast; the control bridge is a dedicated compose network.

## 5. Phased execution

### Phase 0 — Containerize the node stack
- [x] Base image: **`FROM rticom/routing-service`** + `pip install rti.connext` for the
      sims. EMANE added as a later layer from [adjacentlink/emane](https://github.com/adjacentlink/emane)
      jammy release `.deb`s (core + `emane-model-rfpipe` + transport daemon).
      *(Confirm the base tag's OS is Jammy so EMANE debs match.)*
      → scaffolded in [sim/docker/Dockerfile.node](../sim/docker/Dockerfile.node)
      *(Alternative: `FROM rticom/routing-service` and layer EMANE + Python on top —
      evaluate which base is smaller/cleaner.)*
- [ ] Entrypoint/supervisor launches emane → routing service → sim, in order, with
      health gating (wait for `emane0` up before routing service binds WAN).
- [ ] Parameterize by env: `ROLE`, node `ID`, domains, RF IP, initial peers.
      Reuse existing [params/system_params.sh](../params/system_params.sh) logic.
- [ ] Mount `rti_license.dat`; confirm `NDDSHOME`/workspace paths inside the image.
- [ ] Smoke test **without EMANE first** (plain bridge) to isolate DDS-in-container issues.

### Phase 1 — EMANE fabric
- [ ] compose network `emane_ctrl` (OTA + Event Service multicast backhaul).
- [ ] Per-node EMANE platform XML + RF Pipe NEM (transport `transportdaemon` or
      raw TAP). Start with a static pathloss so all nodes hear each other.
- [ ] Event Service driven by an **EEL file** (`*.eel`) for location + pathloss →
      this is how scenarios inject mobility / link degradation.
- [ ] Validate DDS discovery over `emane0`: WAN multicast (239.255.0.2) is carried by
      RF Pipe broadcast for the small case — confirm participants discover.
- [ ] Add an optional **unicast/CDS** path using `rticom/cloud-discovery-service`
      (needed for the discovery-storm scenario, §Phase 3).

### Phase 2 — Instrumentation & metrics pipeline

**Supported path: RTI Observability Framework** (replaces hand-rolled RS-monitoring subscribers)
- [ ] Enable **Monitoring Library 2.0** on the sims (`participant_factory_qos.monitoring.enable = true`
      via MONITORING QosPolicy) and on both **Routing Services** (QoS profile snippet).
- [ ] ⚠️ **Gotcha:** Connext *host* install packages omit the Monitoring Library 2.0
      distribution — the node image must install the monitoring lib (target package) and
      set the library search path. Verify it's present in / addable to `rticom/routing-service`.
- [ ] Run **`rticom/collector-service`** in compose → **Prometheus** (metrics) + **Loki** (logs).
- [ ] Import **RTI Observability Grafana dashboards** (participant/reader/writer/RS-route
      telemetry) — most quantitative panels come for free.
- [x] **License:** ✅ covered (internal tool — all RTI products incl. Observability).

**App-level end-to-end (still needed — Observability gives DDS-level, not routed e2e):**
- [ ] **Payload instrumentation (code change):** embed monotonic `seq` + `send_ns`
      timestamp in `base_type.payload` so receivers compute per-topic **latency** and
      **loss** across the full LAN→WAN→LAN routed path. (Today payloads are random bytes.)

**PHY layer:**
- [ ] EMANE stats (`emanesh`) → small exporter for RF offered/delivered/lost frames.

### Phase 3 — Scenarios (each maps to a thesis claim)
| # | Scenario | Method | Validates |
|---|---|---|---|
| S1 | **Baseline nominal** | Good RF, all nodes in range | Everything flows; baseline latency/bw (US-A2) |
| S2 | **Degrade to 50 kbps** | Lower RF Pipe datarate / raise pathloss via EEL | Reliable commands+events persist while best-effort status starves (SYS-REQ-02; partial SYS-REQ-11) |
| S3 | **Runtime team formation** | Backend flips partitions to "A" mid-run via **Python remote admin** (`rti.connext` ServiceAdmin) — triggered from GUI A or scenario file | Mesh isolation + dynamic CRUD, no restart (SYS-REQ-04/08, US-B4) |
| S4 | **Control-link loss / mesh survival** | Move a platform out of LOS to Control but in range of a teammate (EEL) | Team channel keeps working when hub link drops (US-C6) |
| S5 | **Peer-loss detection** | `docker kill` a platform | Teammates detect loss via liveliness/lease (SYS-REQ-10 — needs QoS change, §6) |
| S6 | **Discovery scaling** | Multicast vs unicast/CDS at N nodes | Discovery-storm avoidance (SYS-REQ-01/06/C2) |

### Phase 4 — Visualization

**Two collectors, clear split:**
- **RTI Observability Collector Service** (supported) owns DDS/RS telemetry →
  Prometheus + Loki → Grafana (Layer 2). *No custom code.*
- **Custom Python collector** (FastAPI) owns only what Observability doesn't: the
  **topology graph** + the **live animation stream** + **EMANE** ingestion. It *reads
  rates from Prometheus* (already populated by Observability) rather than re-subscribing
  to DDS. Exposes:
  - **`/nodes-edges`** (topology JSON snapshot) — nodes (routers, role, position) + edges
    (per channel: rate/latency/loss). → web app initial load.
  - **`/stream`** (**WebSocket/SSE**) — live per-channel rate + link-state + node-position
    deltas at ~1–2 Hz. → drives web-app animation & node mobility. *(Driven by the
    Cytoscape.js decision — Prometheus scraping alone can't animate.)*
  - Sources: **Prometheus** (Observability metrics) + app seq/timestamp e2e latency +
    **EMANE** location/pathloss events + EMANE PHY stats (`emanesh`).

**Layer 1 (PRIMARY / flagship) — Custom web app (Cytoscape.js).**
- [ ] SPA served by the collector (or thin nginx), connects to `/stream`.
- [ ] Nodes = Control/Platform routers; **edges = message traffic**, styled per channel
      (color), width ∝ rate, dashed/red when reliable retransmits/loss climb.
- [ ] **Flow animation is rate-driven, not per-packet** — animate dot density/speed
      along an edge ∝ measured channel rate (efficient; scales; looks live).
- [ ] **Node mobility** — positions updated live from EMANE location deltas (preset
      layout, live reposition) so the graph reflects platforms moving in/out of range.
- [ ] Click an edge → per-topic breakdown; scenario event markers (team-assign, link-drop).

**Layer 2 — Quantitative dashboards (Grafana time-series) [sweet spot].**
- [ ] Per-channel latency (p50/p95), delivery ratio, offered vs delivered throughput.
- [ ] Reliable-vs-best-effort behavior under degradation (the S2 story).
- [ ] Scenario timeline annotations (team-assign, link-drop, node-kill).
- *Optional quick-look:* Grafana **Canvas** or **Node Graph** panel off the same
  collector data if we want a topology glance without opening the web app.
  (⚠️ not FlowCharting — deprecated/Angular, off in Grafana 11+.)

**Layer 3 — RF / mobility globe: EMANE SDT3d.**
- [ ] Run **SDT3d** subscribing to the EMANE **event channel** (location/pathloss) on the
      control bridge → nodes + link state on a 3D globe. Native to EMANE, no synthesis.
- [ ] Note: SDT3d is a GUI app (NASA WorldWind) — needs an X11/display; run on the host
      pointed at the event multicast group, or in a container with X forwarding.

- [ ] One "money shot" per scenario for the blog/thesis narrative.

## 6. Required code / config changes (gaps)

These are prerequisites for *measuring* or *proving* specific claims — the repo is a
reference demo and does not yet include them:

- **Seq# + timestamp in payloads** (Phase 2) — blocks all latency/loss metrics. *Highest priority.*
- **WAN/LAN interface pinning** in QoS (`allow_interfaces_list`) — pin WAN to `emane0`,
  LAN to loopback. Needed for both isolation proof and correct RF routing.
- **LAN transport → UDP-only** (disable SHMEM in `lan_qos_lib.xml`) — so the RTPS Analyzer
  can sniff LAN-local traffic, not just WAN. Consistent with the architecture's stated options.
- **Finite Liveliness/Deadline QoS** on status readers/writers — required for S5 peer-loss.
  (Current `status_qos` uses INF.)
- **TransportPriority + async flow controllers** — for true bandwidth prioritization
  (SYS-REQ-11). Optional stretch; S2 can partially demonstrate with existing
  reliable-vs-best-effort split.
- **Leader-follower relay / gossip / persistence** (SYS-REQ-09/16) — net-new, out of
  scope for v1; note as future work.

## 7. Thesis-claim traceability

| Claim (req) | Demonstrable now? | Notes |
|---|---|---|
| Domain isolation (SYS-REQ-13) | ✅ with interface pinning | LAN never crosses `emane0` |
| Content-filtered targeted cmds (DTR-05) | ✅ | Already in routing config |
| Dynamic team CRUD (SYS-REQ-04) | ✅ | `remote_admin` partition switch |
| Graceful degradation (SYS-REQ-02) | ◑ partial | best-effort vs reliable split; no true priority yet |
| Bandwidth prioritization (SYS-REQ-11) | ✗ needs QoS | TransportPriority/flow control |
| Peer-loss detection (SYS-REQ-10) | ✗ needs QoS | finite liveliness/deadline |
| Discovery-storm avoidance (SYS-REQ-01/06) | ◑ | multicast now; add CDS/unicast |
| Mesh resilience / relay (US-C6, SYS-REQ-16) | ◑ / ✗ | S4 shows peer survival; gossip relay is net-new |

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

## 9. Risks & open questions

- **RTI license in containers** — ✅ covered for all products (internal tool): Routing
  Service, Python, CDS, **Observability Framework** (Monitoring Library 2.0 + Collector).
- **Monitoring Library 2.0 not in host package** — RTI host installers omit the lib;
  the node image must install the monitoring target package + set the lib search path.
- **RTI apt repo is license-gated** — the Debian repo URL embeds a per-user access
  token. Passed to the build as `--build-arg RTI_APT_TOKEN=...` (never baked into the
  image or committed). Same for the license file (mounted at runtime, not built in).
- **EMANE + RTI version alignment** — ✅ both target **Ubuntu 22.04 (Jammy)**; single base image.
- **Multicast over RF Pipe** — validate broadcast forwarding for DDS discovery early;
  fall back to unicast/CDS if flaky.
- **Time sync for latency** — single host = shared clock (fine). Multi-host later needs PTP/NTP.
- **Remote admin** — ✅ decided: **Python** via `rti.connext` ServiceAdmin request/reply
  in the backend (token-free). Implementation note: load the RTI Service Admin command
  types and issue CommandRequest/CommandReply against each router's admin interface
  (ADMIN_DOMAIN 100). C++ tool kept only as a reference/CLI.
- **Base image choice** — `rticom/routing-service` (add EMANE+py) vs Debian-from-scratch;
  decide in Phase 0.

## 10. Milestones

1. **M0** DDS node stack runs in containers over a plain bridge (no EMANE). 
2. **M1** Same stack over EMANE RF Pipe; discovery + all topics flow (S1 baseline).
3. **M2** Instrumentation + Prometheus/Grafana showing live per-channel metrics.
4. **M3** Scenarios S2–S4 scripted and captured.
5. **M4** QoS gap changes in; S5 + S6 captured; thesis-validation writeup + visuals.
