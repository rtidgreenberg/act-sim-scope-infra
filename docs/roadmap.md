# Roadmap — Phases, Feature-sets & Tests

> Part of the **ACT EMANE Simulation & Thesis-Validation** plan — start at the [overview & index](EMANE_SIMULATION_PLAN.md). Docs are organized by **process**; see [architecture.md](architecture.md), [decisions-and-risks.md](decisions-and-risks.md), [sniffer.md](sniffer.md), [scope.md](scope.md), [test-harness.md](test-harness.md). **RTI product gaps that gate later phases are tracked in [product-gaps.md](product-gaps.md).**

---

A **simple → complex** progression. Each phase lists the **feature-set** it turns on (by component:
harness / relay / sniffer+scope / DDS), the **tests/scenarios** that prove it, and an **exit criterion**.
Security is **off** through the build phases (full observation fidelity) and enabled only at the
deployment tier (Phase 9) — see the DDS-Security decision in [decisions-and-risks.md](decisions-and-risks.md).

Milestone tags (M0–M4) map to the original plan milestones.

## Phase 0 — Node stack in containers, plain bridge  · **[M0]**
- **Feature-set (harness):** one role-selected node image (`ROLE=platform|control`) = Routing Service + Python sim; `docker-compose` (1 control + N platforms) on a plain docker bridge; health-gated entrypoint (RS → sim); license mounted at runtime; env-parameterized (`ROLE`, `ID`, domains, peers).
- **Tests:** containers start; DDS discovery over the bridge; **all ACT channels flow** (commands / status / events / team); no EMANE.
- **Exit:** the ACT stack runs containerized end-to-end without EMANE. *(Foundational; can proceed in parallel with Phase 1.)*

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
- **Feature-set (DDS + sniffer + scope):** seq#/timestamp payloads (app e2e latency/loss); **RTI Observability** (Monitoring Lib 2.0 → `collector-service` → Prometheus/Loki/Grafana), telemetry **pinned off the RF**; `emanesh` PHY exporter; **sniffer decode stage** (command-gated, `act_types.xml`) → **endpoint inspector** live decoded samples.
- **Tests:** per-channel latency (p50/p95) + delivery ratio; **observer-off vs observer-on** credibility check; decode-set gating + rate-cap; **capture throughput** (signal drops, don't under-count); cross-layer loss (app seq-gaps vs `emanesh`).
- **Exit:** quantitative metrics + live decoded inspector, trustworthy under load.

## Phase 5 — Degradation & prioritization  · **[M3]**
- **Feature-set (DDS):** CommEffect / EEL impairment; **DDS-layer prioritization** (TransportPriority + **async flow controllers**) — *central, not optional* (§6, test-harness); reliable-vs-best-effort split.
- **Tests:** **S2 — degrade to 50 kbps** (reliable commands+events persist while best-effort status starves; SYS-REQ-02, partial SYS-REQ-11); prioritization keeps low-rate control alive as the pipe collapses.
- **Exit:** graceful degradation + prioritization demonstrated and measured.

## Phase 6 — Dynamic C2: teams, targeting, isolation  · **[M3]**
- **Feature-set (DDS control):** partition/**team CRUD via Python remote admin** (`rti.connext ServiceAdmin`) — **operational C2 over the RF**; content-filtered targeted commands; on-demand detail-status enable.
- **Tests:** **S3 — runtime team formation** (SYS-REQ-04/08, US-B4); **S7 — S3 under S2** (prioritized C2 + team CRUD survive degradation); targeted commands (DTR-05); inspector shows new endpoints on detail-status enable.
- **Exit:** runtime CRUD + isolation + targeting over RF, no restart. *(Gated by [product-gaps.md](product-gaps.md) LP-4 — Python remote-admin coverage.)*

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
4. **M3** — Degradation/prioritization + dynamic C2: S2, S3, S7 (Phases 5–6).
5. **M4** — Mesh/peer-loss + state convergence + discovery/security: S4–S6, S8 + writeup (Phases 7–9).
