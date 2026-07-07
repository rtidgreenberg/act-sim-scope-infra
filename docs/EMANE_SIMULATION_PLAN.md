# ACT — Containerized EMANE Simulation & Thesis-Validation Plan

> **Status:** Draft v1 · Owner: dgreenberg · Last updated: 2026-07-02
>
> Goal: containerize the ACT node stack, run it over an **EMANE**-emulated mesh-radio
> WAN, execute scripted scenarios, and produce visualizations that **validate the
> architectural thesis** of this repo.
>
> **This file is the overview & index.** The plan was split out of a single monolithic
> doc into the targeted docs mapped below; section numbers (§X.Y) are preserved across the
> split so existing cross-references stay valid.

---

## The thesis (in one line)

> RTI Connext DDS + a per-node Routing Service gateway can serve as the resilient
> "connective tissue" for **Autonomous Collaborative Teaming** over **DDIL**
> (Denied, Degraded, Intermittent, Limited) networks — enabling 1:N control, domain
> isolation, QoS-based bandwidth prioritization, dynamic runtime team CRUD, and
> peer-to-peer mesh coordination.

Full statement + testable-claim traceability → **[thesis-and-claims.md](thesis-and-claims.md)**.

## Two products, one clean interface

The work splits into two **independently deployable** products (a hard boundary, not just modules),
coupled only through the **sniffer event bus** — no runtime dependency between them:

| Product | Purpose | Distribution |
|---|---|---|
| **Scope** (DDS Scope) | Passive, **read-only** monitoring/viz of any DDS system — node graph, message flow, endpoint inspector, dashboards | Deployable at a customer site |
| **Test/Sim Harness** | Stand up + perturb an emulated network: EMANE, orchestration, scenario runner, fault injection, manual control | Internal test tooling only |

One-way code dependency (**harness → Scope's shared aggregation lib, never the reverse**) keeps
Scope liftable into its own product later. The end-state component map (sniffer bus at the center)
lives in **[architecture.md](architecture.md)**; the event contract + the decode-placement decision
that binds it all together are in **[sniffer.md](sniffer.md)**.

## Doc map

<a name="doc-map"></a>

The plan is organized **by process** (sniffer / scope / test-harness) plus shared cross-cutting docs.
Original plan section numbers (§X.Y) are retained for traceability; sections §2.5–§2.7 were split
across the process docs by ownership.

**Process docs:**

| Doc | What's in it |
|---|---|
| **[sniffer.md](sniffer.md)** | The RTPS Analyzer / sniffer: passive observation model, capture points, the JSON event contract (§2.8), reference-repo findings + **the decode-placement decision** (§2.9), sniffer work-items |
| **[scope.md](scope.md)** | The **Scope** product (read-only monitoring/viz): endpoint inspector, GUI B network picture, comms-viz gaps, demo/reporting |
| **[test-harness.md](test-harness.md)** | The **harness** (internal): target topology (§3), node-container anatomy (§4), control-plane API, GUI A control console, test-op/control-plane gaps, required QoS/code changes (§6) |

**Shared / cross-cutting docs:**

| Doc | Sections | What's in it |
|---|---|---|
| **[architecture.md](architecture.md)** | §2.4, §2.5, §2.8 roles, §8 | Two-product model + sniffer-bus coupling; run-modes; app roles; component map; locked tech stack; cross-cutting requirements; repo strategy + layout |
| **[thesis-and-claims.md](thesis-and-claims.md)** | §1, §7 | The thesis we validate; per-claim traceability |
| **[decisions-and-risks.md](decisions-and-risks.md)** | §2, §9 | Locked technology/packaging decisions; risks & open questions |
| **[roadmap.md](roadmap.md)** | §5, §10 | **Capability-phased roadmap (Phase 0–10): feature-sets + tests per phase**, incl. the **Phase 1 Python ISC-relay PoC (go/no-go)**; scenarios S1–S8; milestones M0–M4 |
| **[product-gaps.md](product-gaps.md)** | — | **Long poles for RTI product** — gaps we can't work around that gate later phases (LP-1 ISC-through-RS is the headline). Feed to RTI so long-lead items start early. |
| **[cpp_router/README.md](cpp_router/README.md)** | alternate exercise | Bare-minimum scope for a YAML-driven C++ DynamicData router that replaces Routing Service in the ACT POC node stack |

## Milestones (summary)

1. **M0** — DDS node stack in containers + **Python ISC-relay PoC go/no-go**. *(Phases 0–1)*
2. **M1** — Same stack over EMANE RF Pipe; discovery + all topics flow (S1 baseline). *(Phase 3; Scope MVP Phase 2 alongside)*
3. **M2** — Instrumentation + Prometheus/Grafana + decoded inspector, live per-channel metrics. *(Phase 4)*
4. **M3** — Degradation/prioritization + dynamic C2: S2, S3, S7 captured. *(Phases 5–6)*
5. **M4** — Mesh/peer-loss + DDIL state convergence + discovery/security: S4–S6, S8 + writeup. *(Phases 7–9)*

> Detailed feature-sets, tests, and exit criteria per phase → **[roadmap.md](roadmap.md)**.
> RTI product gaps that gate Phases 6/8/10 → **[product-gaps.md](product-gaps.md)**.

Details + the phased task lists behind each milestone → **[roadmap.md](roadmap.md)**.
