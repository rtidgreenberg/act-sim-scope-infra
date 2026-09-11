# Test/Sim Harness — Control, EMANE & Scenarios (internal)

> Part of the **ACT EMANE Simulation & Thesis-Validation** plan — start at the [overview & index](EMANE_SIMULATION_PLAN.md). Docs are organized by **process**; cross-cutting concerns live in [architecture.md](architecture.md), [decisions-and-risks.md](decisions-and-risks.md), [thesis-and-claims.md](thesis-and-claims.md), and [roadmap.md](roadmap.md). Original plan section numbers (§X.Y) are retained for traceability.

---

The **Test/Sim Harness** is the internal tooling that stands up and perturbs the EMANE-emulated ACT
network: node containers, EMANE RF, container orchestration, the control-plane backend + scenario
runner, and GUI A. It consumes the [sniffer](sniffer.md) bus (topology/GUIDs + scenario triggers)
and imports [Scope](scope.md)'s shared aggregation lib one-way. Phased build + scenarios S1–S6 +
SDT3d live in [roadmap.md](roadmap.md); repo/override strategy in [architecture.md](architecture.md).
This doc covers the harness's topology, node anatomy, control surface, and required QoS/code changes.

## 3. Target EMANE topology

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

### Current baseline

`harness_v2/scripts/run_mesh.sh --emane` is the canonical nominal-RF mesh entry point. It starts
a control node and selected platforms from a versioned topology JSON file. Each node record
declares its role, LAN domain, NEM ID, RF address, and EMANE-control-network address. The runner
uses those records to render Compose and router configuration, establish RF links, wait for
interfaces, capture `emane0`, and compile delivery expectations. WAN participants are pinned to
`emane0`; LAN participants remain on UDP loopback. The dashboard runs with the control node's
loopback LAN participant QoS and is exposed through the mesh-owned HTTP port.

The supplied three-node topology passed delivery audit and bounded per-node captures requiring
decoded RTPS on `emane0` to contain only WAN domain `200`. A non-sequential `Platform_32` topology
also passed the same dashboard, isolation, audit, and cleanup lifecycle. The old host-process
behavior remains available without `--emane` as a local diagnostic mode. The Compose engine lives
in `run_container_baseline.sh` as an internal implementation, not a separate harness.

`harness_v2/scripts/run_emane_rfpipe_feasibility.sh run` is a separate, disposable
two-NEM prerequisite proof. It creates `emane0` in each container, injects a nominal RF
Pipe pathloss matrix, and sends one UDP datagram between the two RF addresses. It proves
the image, container privileges, virtual transport, OTA bridge, and RF Pipe path, but it
does not run Routing Service, DDS, or any ACT simulator.

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

### Migration checkpoint: container baseline before RF

The working `harness_v2/scripts/run_mesh.sh` harness remains the host-process diagnostic
tool until the Compose baseline is proven. Its DDS domains isolate simulated LANs, but it
does not create network namespaces or demonstrate that WAN traffic crosses an impaired
link. The next implementation phase is therefore a **container isolation baseline**, not
an ad-hoc Docker bridge WAN:

1. Compose creates one container per control/platform node and an `emane_ctrl` bridge for
  EMANE OTA/Event Service plus management traffic only.
2. WAN DDS has no direct Docker bridge route. Once EMANE is added, each WAN participant is
  pinned to `emane0`; any alternative path is a test failure because it bypasses RF Pipe
  and CommEffect.
3. LAN DDS remains node-local. The EMANE slice adds UDP-only LAN QoS plus explicit
  loopback pinning, allowing packet capture to prove LAN traffic never crosses `emane0`.
4. The container lifecycle is owned by the harness controller, not shell scripts launched
  outside it. It creates an isolated run directory on local storage and records all
  actions there.

This yields a meaningful progression: domain IDs allocate DDS test space; containers
create network isolation; EMANE owns WAN transport and impairment. The Phase 0.5 exit
criteria and baseline tests are in [roadmap.md](roadmap.md).

## Control-plane API (the backbone, §2.5)

Both GUIs, the scenario runner, and scripted/CI tests all sit on one **backend control
service** exposing primitives across:
- **EMANE:** set pathloss/location/RF-Pipe params; emit location events (link + mobility).
- **Orchestration:** start/stop/kill/add containers (node lifecycle, fault injection).
- **DDS:** publish messages; drive Routing Service remote admin (partition/session);
  **introspect endpoints** (discovery) + **subscribe on demand** and stream samples (§2.6).
- **State/query:** read topology + metrics (Prometheus / EMANE events).

**Define this control surface before choosing GUI tech.**

### Scenario controller boundary

The scenario controller is the harness's centralized experiment authority. Its initial
API should be deliberately small: create/reset a run, bring the topology up/down, query
node health, execute a timed action, and append a durable action/result event to the run
log. A scenario is a declarative sequence of those actions with stable ids and relative
times; failures stop the run unless the scenario marks them as expected.

Later adapters implement EMANE link/mobility/CommEffect events, Docker node
kill/restart, DDS stimulus, and remote-admin actions. Each adapter reports a correlated
result to the controller so scenarios can make a verdict and export artifacts. Do not put
this experiment lifecycle or fault policy into `router_main` or a per-node orchestrator:
those processes make local mission/router decisions, while the controller deliberately
causes and records faults across the whole topology.

### Python-relay control surface — custom DDS topics ✅ decided
The **Python ISC relay's** control surface is **custom DDS topics** (not RS `ServiceAdmin` — we own the relay, so we give it a clean, typed, observable contract). Two patterns, by action type:
- **Declarative *desired-state + reported-state* topics** for config-like control (routes enable/disable, downsample rate, decode-set). Keyed by target, e.g.:
  - `RouteSpec { key route_id; bool enabled; <params> }` — **desired** (operator/backend publishes).
  - `RouteStatus { key route_id; State state; string detail }` — **reported** (relay publishes; == the §2.7 *current-control-state inspector*, and observable by Scope → answers the "black box" complaint).
  - Enable/disable = publish/adjust the keyed instance (disable = `enabled=false` or dispose the key). Idempotent.
- **Request/reply** (custom `Requester/Replier`, clean command type) for **imperative one-shots** needing a correlated ack (inject a message, step a scenario beat) → GUI A shows pending → success/failure (+latency), §2.7.

Design rules: **desired + reported topics are `TRANSIENT_LOCAL`/KEEP_LAST** so a rejoining node re-reads current config and reconciles (DDIL-safe); **authorization** of control topics is a **DDS Security permissions** concern in deployment (only the C2 identity may write them — open in the unsecured sim); stamp a `command_id`/`revision` for convergence tracking.

**RS-managed functions stay on `ServiceAdmin`** (partition/team CRUD, sessions — RS won't subscribe to our topics); the **backend proxies** those, so GUI A speaks **one** unified API while the backend fans out (relay control topics + RS ServiceAdmin + EMANE + docker-py).

## GUI A — Test Control Console (§2.5)

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

## GUI A — Control Console (additions, §2.7)

- **Presets / quick-actions** ("jam Control↔P30", "form Team A", "enable detail on selected").
- **Multi-select / bulk actions** (assign N platforms to a team, kill several).
- **Running-script view** — while a predefined scenario runs, show its steps + current/next
  (viewing, not authoring).
- **Run/session history** — name runs, browse past runs, load their artifacts for review.

## Test-operation gaps (scenario runner, §2.6)

### Measurement objectives and correlation method

The harness exists to produce defensible evidence under controlled conditions, not merely
to demonstrate that messages can flow. Every baseline and fault scenario records a shared
scenario clock, the requested fault schedule, and measurements from the three distinct
path segments:

| Question | Ground truth / measurement | Required output |
|---|---|---|
| What is DDS network overhead? | Packet capture on `emane0`, classified into user DATA and RTPS discovery/reliability/control traffic; offered application payload bytes from the sims | bytes/s and bytes per delivered application byte, by topic, node pair, and RTPS class |
| How does DDS react to impairment? | EMANE CommEffect/RF Pipe parameters and events; per-peer `LinkStatsCollector` reliable-protocol totals and interval deltas; app seq/timestamp delivery and RTT | time-aligned traces of NACKs, repair bytes, heartbeats, send-window/backlog, inactive readers, `lost_by_writer`, app delivery, and RTT |
| Can protocol statistics detect a bad link? | A scenario's labeled transient loss, outage, recovery, delay, and congestion windows | detection latency, recovery latency, precision/recall, and false alarms during a nominal baseline |

Detection begins as an offline correlation exercise. The controller must retain raw counters
and fault labels before selecting thresholds or a classifier. Candidate signals include
NACK/repair-byte rate, unacknowledged backlog, send-window contraction, reader inactivity,
application-ack RTT, and app sequence gaps. A detector may report `nominal`, `degraded`,
`congested`, or `unreachable` only after the experiment distinguishes network impairment
from local resource limits, endpoint rediscovery, and quiet traffic. Until then, the UI
shows the raw measurements and scenario ground truth rather than an unsupported health
judgment.

The initial fault matrix is: nominal baseline; fixed bandwidth congestion; bounded loss;
intermittent loss; complete cutout; recovery; and a node kill/restart control case. Each
fault action is emitted by the scenario controller with a stable id and expected duration,
so a run can be replayed and metrics windows can be compared across configurations.

### Application delivery audit

The harness must also produce a message-level completion verdict from simulator send/receive
logs. Expected receivers are compiled from the effective route, content-filter, partition/team,
endpoint-match, and lifecycle state at each send timestamp; they are not inferred from a raw
receive count. The full event contract, expectation rules, artifact layout, JSON output, and
HTML report requirements are defined in [delivery-audit-framework.md](delivery-audit-framework.md).

The audit harness must isolate each active run with an owned Compose project, work directory,
and node-artifact directories; it must refuse a conflicting active run before clearing any
evidence. Before stimulus begins, the controller waits for every selected node's simulator
endpoints and required route output entities, then waits for their required DDS endpoint
matches to stabilize. A versioned run manifest declares every required topic, its priority,
minimum sent sample count, completion threshold, and latency policy. The report shows a verdict
and denominator for each declared topic; an absent required topic is `inconclusive` or `fail`,
never hidden by an aggregate percentage. Malformed/truncated JSONL is reported as an
evidence-quality failure and makes the result `inconclusive`.

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

## Control-plane gaps (§2.6)

- **RF impairment model** — ✅ decided: **CommEffect + RF Pipe** (CommEffect for precise
  manual/scenario impairment; RF Pipe for realistic range/mobility; pick per scenario).
- **Guardrails** — confirm destructive manual actions (kill node); prevent invalid states.

## 6. Required code / config changes (gaps)

These are prerequisites for *measuring* or *proving* specific claims — the repo is a
reference demo and does not yet include them:

- **Seq# + timestamp in payloads** (Phase 4) — blocks all latency/loss metrics. *Highest priority.*
- **WAN/LAN interface pinning** in QoS (`allow_interfaces_list`) — pin WAN to `emane0`,
  LAN to loopback. Needed for both isolation proof and correct RF routing.
- **LAN transport → UDP-only** (disable SHMEM in `lan_qos_lib.xml`) — so the RTPS Analyzer
  can sniff LAN-local traffic, not just WAN. Consistent with the architecture's stated options.
- **Finite Liveliness/Deadline QoS** on status readers/writers — required for S5 peer-loss.
  (Current `status_qos` uses INF.)
- **TransportPriority + async flow controllers** — for true bandwidth prioritization
  (SYS-REQ-11). **CENTRAL, not optional:** operational C2 (commands, team reassignment) rides the
  *same* RF as bulk status/video (see the control-plane decision in decisions-and-risks §2), so
  keeping low-rate control alive when the pipe collapses **is** the SYS-REQ-02/11 result. Enforce it
  at the **DDS layer** (TransportPriority + async flow controllers shaping best-effort status while
  reliable commands get the queue/retransmit budget) — **RF Pipe is a flat single-queue pipe and
  will not prioritize on its own.** (A `tc`/qdisc priority band on `emane0` is a link-layer
  alternative, but DDS-layer prioritization is the thesis point.)
- **Leader-follower relay / gossip / persistence** (SYS-REQ-09/16) — net-new, out of
  scope for v1; note as future work.
