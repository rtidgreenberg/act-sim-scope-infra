# Roadmap — Phases, Feature-sets & Tests

> Part of the **ACT EMANE Simulation & Thesis-Validation** plan — start at the [overview & index](EMANE_SIMULATION_PLAN.md). Docs are organized by **process**; see [architecture.md](architecture.md), [decisions-and-risks.md](decisions-and-risks.md), [sniffer.md](sniffer.md), [scope.md](scope.md), [test-harness.md](test-harness.md). **RTI product gaps that gate later phases are tracked in [product-gaps.md](product-gaps.md).**

---

A **simple → complex** progression. Each phase lists the **feature-set** it turns on (by component:
harness / relay / sniffer+scope / DDS), the **tests/scenarios** that prove it, and an **exit criterion**.
Security is **off** through the build phases (full observation fidelity) and enabled only at the
deployment tier (Phase 9) — see the DDS-Security decision in [decisions-and-risks.md](decisions-and-risks.md).

Milestone tags (M0–M4) map to the original plan milestones.

## Phase 0 — Node stack in containers, plain bridge  · **[M0]**
- **Status:** implemented as a Docker-bridge delivery-audit baseline for one control plus
  selected platforms. The validated proof currently covers `ControlCommand` and
  `PlatformInitStatus`; it does not yet demonstrate every ACT channel.
- **Feature-set (harness):** one role-selected node image (`ROLE=platform|control`) = Routing Service + Python sim; `docker-compose` (1 control + N platforms) on a plain docker bridge; health-gated entrypoint (RS → sim); license mounted at runtime; env-parameterized (`ROLE`, `ID`, domains, peers).
- **Tests:** containers start; DDS discovery over the bridge; **all ACT channels flow** (commands / status / events / team); no EMANE.
- **Exit:** the ACT stack runs containerized end-to-end without EMANE. *(Foundational; can proceed in parallel with Phase 1.)*

## Phase 0.5 — Container isolation baseline & scenario control contract  · **[M0]**
Bridge the working host-process harness and implemented Docker-bridge delivery baseline to
the EMANE architecture before introducing impairment. The current DDS-domain isolation and
container delivery proof remain useful, but neither proves network isolation.

- **Feature-set (harness):** replace host-launched node processes with Compose-managed
  control and platform containers. Create an `emane_ctrl` Docker bridge used only for
  EMANE OTA/Event Service traffic and management. Define each node's future WAN contract
  as `emane0`; do **not** carry direct WAN DDS on the Docker bridge, because that would
  bypass EMANE in Phase 3. Preserve node-local LAN traffic on loopback until LAN UDP-only
  pinning lands with EMANE. A two-NEM RF Pipe feasibility fixture now proves container
  privileges, `emane0`, OTA multicast, nominal pathloss events, and bounded UDP transport;
  it is a prerequisite proof. The container baseline's `--emane` mode now proves the
  control-plus-two-platform command/status audit through nominal RF Pipe (`9/9` expected
  deliveries) and bounded per-node captures proving that only WAN domain `200` RTPS appears
  on `emane0`. Each EMANE node now records passive domain-200 `emane0` interval counters and
  sends compact control-bridge summaries to the control dashboard, which publishes only complete
  all-node aggregate RF-load samples.
- **Feature-set (control):** introduce a centralized scenario controller with lifecycle
  primitives (`up`, `down`, `reset`, node status) and an append-only run event log. It owns
  Compose/Docker lifecycle and later EMANE events and faults. It is not the per-node
  mission orchestrator: per-node policy continues to command only its local router.
- **Tests:** start one control plus two platforms; after `emane0` exists, prove WAN DDS has
  no direct Docker-bridge path; prove container lifecycle actions
  are recorded with run id, timestamp, target, request, and result; reset returns to a
  clean baseline with no residual containers, ports, or DDS shared-memory entries.
- **Exit:** a repeatable container baseline and a narrow controller contract exist without
  claiming RF impairment. Detailed topology and controller ownership: [test-harness.md](test-harness.md).

## Phase 1 — Python ISC relay proof-of-concept + ISC test  · **[M0 · go/no-go gate]**
De-risk the linchpin of the transparent-relay strategy **before** building the environment around it: *does a pure-Python, ISC-enabled DP-to-DP relay preserve true DDS `instance_state` across a disconnection?* Needs only `rti.connext` + a plain network — **no containers/EMANE/ACT stack.**
- **Feature-set (relay):** rough Python relay = **ISC-enabled `DataReader` (leg 1) + ISC-enabled `DataWriter` (leg 2)**, forwarding samples and **mirroring instance lifecycle** (reader instance-state change → writer `dispose`/`unregister`). Concrete keyed `@idl.struct` type first; ISC via QoS API or **XML QoS profile** fallback.
- **Tests — 3 paths × fault cases, one assertion:**
  - Paths: **(A) direct** writer→reader (ISC baseline, converges), **(B) through Routing Service** (does *not* converge — demonstrates the gap), **(C) through the Python ISC relay** (converges — the proof).
  - Faults: reader disconnect/reconnect over a DISPOSE; writer disconnect/reconnect; **NO_WRITERS** (writer unregisters/dies during outage); **multi-transition replay**.
  - Assert: after reconnect, each path's downstream `SampleInfo.instance_state` per key == ground truth (== path A).
- **Exit (go/no-go):** ✅ C converges like A incl. NO_WRITERS + replay → transparent-relay strategy validated, proceed. ❌ → mirroring insufficient; fall back (RTI dependency / shadow / narrower scope), documented. **Bonus artifact:** the B-vs-C result ("RS defeats ISC; our relay restores it").

## Phase 2 — Passive observation MVP (sniffer + Scope on the bridge)
- **Feature-set (sniffer + scope):** RTPS Analyzer capturing on the bridge → normalized JSON event bus (discovered-entity events); Scope backend aggregation → topology/state model; **node graph** (participants + endpoints from SPDP/SEDP) + basic `flow_stats`. No payload decode yet.
- **Tests:** sniffer detects **every** participant/endpoint vs. ground truth; node graph correct; **late-join / restart resync** behavior characterized (§ decisions #10); self-health distinguishes "quiet" from "sniffer died".
- **Exit:** a live, correct node graph with **zero observer effect**.

## Phase 3 — EMANE RF fabric  · **[M1]**
- **Feature-set (harness):** node stack over **EMANE RF Pipe**; `emane_ctrl` bridge (OTA + Event Service); per-node **`emane0`** capture; **interface pinning** (WAN→`emane0`, LAN→loopback); **SDT3d** RF/geo view; discovery over RF (multicast; CDS/unicast fallback ready).
- **Tests:** **S1 — baseline nominal** (all channels flow over RF; US-A2); discovery over RF; **domain isolation** — LAN never crosses `emane0` (SYS-REQ-13).
- **Exit:** full stack over emulated RF, observed on `emane0`.

## Phase 4 — Instrumentation, decode & metrics  · **[M2]**
- **Status:** partial baseline landed: sequenced audit fields, node-owned JSONL logs,
  manifest-declared topic minima/thresholds, immutable expectations for the two baseline
  routes, and compact per-test JSON/HTML reports. Generic route-derived expectations,
  latency/order/ack analysis, packet accounting, and observability remain Phase 4 work.
- **Feature-set (DDS + sniffer + scope):** seq#/timestamp payloads (app e2e latency/loss); one read-write, container-mounted node directory per control/platform under `debug/<node>_debug`, cleared before every run and containing only current JSONL send/receive audit events and node logs; immutable per-run expectation snapshots derived from routes, filters, partitions, endpoint matches, and lifecycle; compact latest-per-test HTML/JSON completion reports retained as `debug/test_reports/<test_id>.*`; **RTI Observability** (Monitoring Lib 2.0 → `collector-service` → Prometheus/Loki/Grafana), telemetry **pinned off the RF**; `emanesh` PHY exporter; packet accounting on `emane0` for payload, application/router control, DDS discovery/reliability, and unresolved traffic, with per-node interval JSONL logs; **sniffer decode stage** (command-gated, `act_types.xml`) → **endpoint inspector** live decoded samples. Add bounded control-side replay for the existing `ActRouterControllerJournal` ledger: an analysis client requests the last $K$ records, or records after an event sequence, from one target router and receives a correlated finite reply stream. The delivery-audit contract is in [delivery-audit-framework.md](delivery-audit-framework.md); WAN accounting is in [traffic-accounting-design.md](traffic-accounting-design.md).
- **Tests:** per-channel latency (p50/p95) + delivery ratio; completion percentage by route/topic/receiver from expected-recipient rules; targeted-command filtering, team isolation, disabled-route, duplicate, and missing-sequence audit cases; bytes/s and bytes-per-delivered-payload-byte by RTPS class; **observer-off vs observer-on** credibility check; decode-set gating + rate-cap; **capture throughput** (signal drops, don't under-count); cross-layer loss (app seq-gaps vs `emanesh`); time-aligned raw per-peer DDS protocol counters against a labeled fault schedule; a late control-side analysis client requests the last $K$ ledger records and receives ordered, gap-explicit results without enabling durable replay on the WAN.
- **Exit:** quantitative metrics, route-derived message completion report, and live decoded inspector, trustworthy under load, with enough raw evidence to begin the Phase 5 impairment-correlation experiment.

**Journal replay contract.** The live controller journal remains a LAN-local analysis stream.
Replay is an explicit, bounded request/reply operation over the target router's local control
interface, not a change to WAN durability: request fields include target router, correlation
id, and either `last_k` or `after_event_sequence`; replies carry records in increasing event
sequence plus the oldest/newest retained sequence and a truncation indicator. The target keeps
only a fixed in-memory ring, rejects invalid or unbounded requests, and never blocks the
controller event strand while a control-side reader is slow. This allows late analysis clients
to recover enough decision history to align fault windows and protocol counters while retaining
the current bounded-overhead posture.

## Phase 5 — Degradation & prioritization  · **[M3]**
- **Feature-set (DDS):** CommEffect / EEL impairment; **DDS-layer prioritization** (TransportPriority + **async flow controllers**) — *central, not optional* (§6, test-harness); reliable-vs-best-effort split.
- **Tests:** **S2 — degrade to 50 kbps** (reliable commands+events persist while best-effort status starves; SYS-REQ-02, partial SYS-REQ-11); intermittent-loss, cutout, recovery, and congestion windows are controller-labeled; prioritization keeps low-rate control alive as the pipe collapses; correlate per-peer reliable-protocol counters with those windows before evaluating a link-impairment detector.
- **Exit:** graceful degradation + prioritization demonstrated and measured, with detection latency, recovery latency, and false-alarm evidence for any proposed protocol-statistics detector.

## Phase 6 — Dynamic C2: teams, targeting, isolation  · **[M3]**
- **Feature-set (DDS control):** partition/**team CRUD via Python remote admin** (`rti.connext ServiceAdmin`) — **operational C2 over the RF**; content-filtered targeted commands; on-demand detail-status enable.
- **Tests:** **S3 — runtime team formation** (SYS-REQ-04/08, US-B4); **S7 — S3 under S2** (prioritized C2 + team CRUD survive degradation); targeted commands (DTR-05); inspector shows new endpoints on detail-status enable.
- **Exit:** runtime CRUD + isolation + targeting over RF, no restart. *(Gated by [product-gaps.md](product-gaps.md) LP-4 — Python remote-admin coverage.)*

## Phase 6.5 — GUI configuration and deployment operations  · **[M3 · deployment track]**
- **Feature-set (Scope GUI):** add a **Config** tab to the mesh dashboard for viewing the effective router/mesh configuration, editing supported route and participant settings, validating a candidate configuration, showing a before/after diff, and applying or rolling back an explicit configuration revision. The GUI must use a versioned control API, not edit YAML files directly; every apply/reject/rollback is audited with operator, timestamp, revision, and result.
- **Safety boundary:** separate read-only diagnostics from mutating operations; require an explicit apply step; reject malformed or unsafe domains/QoS/partitions before touching a live router; make restart-required changes visible instead of pretending they are live-applied. Preserve a last-known-good configuration and expose validation/apply status in the GUI.
- **Tests:** load the effective config into the tab; schema and semantic validation failures leave the running mesh unchanged; route-only changes apply without losing unrelated routes; restart-required changes are staged and clearly reported; concurrent edits detect a stale revision; rollback restores the previous revision; audit records survive dashboard reconnect; permissions and failed-apply paths are covered.
- **Exit:** an operator can inspect, validate, diff, apply, and roll back supported deployment configuration from the GUI with no direct filesystem editing and with an auditable, recoverable control path.

## Phase 7 — Mesh survival & peer-loss  · **[M4]**
- **Feature-set (DDS + harness):** finite **Liveliness/Deadline** QoS on status endpoints; EEL **mobility**; mesh peer-to-peer.
- **Tests:** **S4 — control-link loss / mesh survival** (US-C6); **S5 — peer-loss detection** (`docker kill` a platform; SYS-REQ-10).
- **Exit:** mesh resilience + peer-loss detection.

## Phase 8 — DDIL state convergence — integrate the (proven) ISC relay  · **[M4+]**
- **Feature-set:** take the **Phase 1 Python ISC relay** (now validated), harden it (DynamicData for the keyed state topics, matched QoS), and deploy it over EMANE **for the keyed state topics** (team membership, track lifecycle, dispositions) — **RS still carries bulk traffic**. Pure Python ⇒ **user-inspectable/modifiable** (addresses the "RS is a black box" feedback).
- **Tests:** **S8 — state-convergence-under-outage** — dispose/change a keyed instance *during* an S4/S5 outage → peers converge (true `instance_state`) **via the ISC relay vs. stay stale through plain RS**; validate **NO_WRITERS mirroring under partition** at RF scale.
- **Exit:** true instance-state convergence on state-critical topics via the ISC relay; RS retained for bulk. *(RTI integrating ISC into RS — LP-1 — would remove the need to bypass RS for these topics.)*

## Phase 9 — Discovery scaling & deployment-mode security  · **[M4]**
- **Feature-set:** **CDS/unicast** discovery path; **DDS Security enabled** (deployment posture) → validate the Scope **visibility-degradation ladder**.
- **Tests:** **S6 — discovery scaling** (multicast vs unicast/CDS; SYS-REQ-01/06 — *a true storm needs scale beyond 2–5 nodes*); **secured-mode visibility** — confirm which ladder rung deployment lands on.
- **Exit:** discovery-scaling story + known secured-deployment observation fidelity + thesis writeup/visuals.

## Phase 10 — v2 / future
- **PRS-sync durable-state gossip overlay** (WAN-peer topology, parallel to RS; transitive/anti-entropy) for durable convergence + relay (SYS-REQ-09/16); leader-follower / multi-hop live relay; A/B comparison; record & replay; presentation mode; **Scope productization** (gated by [product-gaps.md](product-gaps.md) LP-2, LP-3); **C++ port of the relay** *only if* a C++-only deployment/audience requires it.

---

## Scenario → thesis-claim reference
| # | Scenario | Phase | Validates |
|---|---|---|---|
| — | **ISC relay PoC** (direct vs RS vs relay) | 1 | Python ISC relay preserves true `instance_state` across reconnect; RS does not (go/no-go) |
| S1 | Baseline nominal | 3 | Everything flows; baseline latency/bw (US-A2) |
| S2 | Degrade to 50 kbps | 5 | Reliable persists, best-effort starves (SYS-REQ-02; partial SYS-REQ-11) |
| S3 | Runtime team formation | 6 | Dynamic team CRUD, no restart (SYS-REQ-04/08, US-B4) |
| S4 | Control-link loss / mesh survival | 7 | Team channel survives hub-link drop (US-C6) |
| S5 | Peer-loss detection | 7 | Liveliness/lease detection (SYS-REQ-10) |
| S6 | Discovery scaling | 9 | Discovery-storm avoidance (SYS-REQ-01/06/C2) |
| S7 | **C2 survives degradation (S3 under S2)** | 6 | Prioritized operational C2 keeps working as the RF collapses (SYS-REQ-02/11 + SYS-REQ-04) |
| S8 | **State convergence under outage** | 8 | Keyed-instance state converges via the ISC relay; RS-ISC gap characterized at RF scale |

## Milestones
1. **M0** — Node stack in containers (Phase 0) **+ ISC relay PoC go/no-go (Phase 1)** — foundations proven, incl. the transparent-relay bet.
2. **M1** — Stack over EMANE RF Pipe; S1 baseline (Phase 3). *(Passive observation MVP, Phase 2, lands alongside.)*
3. **M2** — Instrumentation + Prometheus/Grafana + decoded inspector (Phase 4).
4. **M3** — Degradation/prioritization + dynamic C2 + GUI configuration: S2, S3, S7 (Phases 5–6.5).
5. **M4** — Mesh/peer-loss + state convergence + discovery/security: S4–S6, S8 + writeup (Phases 7–9).
