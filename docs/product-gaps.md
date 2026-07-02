# Product / Feature Gaps — Long Poles for RTI

> Part of the **ACT EMANE Simulation & Thesis-Validation** plan — start at the [overview & index](EMANE_SIMULATION_PLAN.md). These are gaps in **RTI products** (not our tooling) that **gate later roadmap phases**. Feed to RTI product/engineering so long-lead items start **before** we reach them. Phase gating is in [roadmap.md](roadmap.md); rationale in [decisions-and-risks.md](decisions-and-risks.md).

---

**Legend — severity:**
- 🔴 **HARD** — no real workaround; blocks the capability; long product lead → start now.
- 🟠 **CONSTRAINT** — a workaround exists but it shapes/limits the architecture.
- 🟡 **VERIFY-FIRST** — may or may not be a gap; confirm before committing.
- ⚪ **KNOWN CONSTRAINT** — packaging/inherent; informs, doesn't block.

---

## 🟠 LP-1 — Instance State Consistency not integrated with Routing Service (or Persistence Service)
- **What it is:** ISC (`instance_state_consistency_kind`, RELIABILITY QoS; production in **Connext 7.3 LTS**) recovers a reader's instance-state transitions (DISPOSED / NOT_ALIVE_NO_WRITERS) after a disconnection, reconciling over an **internal builtin request/response channel** on rediscovery. Per RTI docs it is **"not fully integrated with the Infrastructure Services (Routing Service, Persistence Service, Web Integration, Recording)"**, and **"DataWriters and DataReaders communicating through Routing Service cannot communicate instance state consistency updates"** (an RS output writer can only *respond to* requests).
- **What does NOT work (and why):** anything that isn't a pair of genuine ISC-enabled matched endpoints. ✗ **RS** — its internal input-reader→output-writer forwarding doesn't carry ISC across. ✗ **RS Processor plugin** — runs inside that same RS data path, inherits the limitation. ✗ **Sidecar** (separate app publishing shadow state) — can't set a reader's real `instance_state`, only an app-level shadow.
- **What DOES work (our workaround):** a **standalone, ISC-enabled DP-to-DP relay** — standard `rti.connext` DomainParticipants with `instance_state_consistency_kind` **on both legs**, mirroring its reader's instance lifecycle (DISPOSED/NO_WRITERS → writer `dispose`/`unregister`). Native ISC runs **per leg** (source-writer↔relay-reader, relay-writer↔dest-reader), so true DDS `instance_state` converges end-to-end. No middleware-private state is touched externally — the relay only uses its **own** ISC endpoints. **Tradeoff:** state-critical topics **bypass RS** (you run this relay for them; RS still carries bulk traffic). Proven in **Roadmap Phase 1 (PoC)**, integrated in **Phase 8**.
- **Residual / to validate:** faithful **NO_WRITERS mirroring under partition** (relay-reader loses the source vs. source-writer truly gone); relay throughput for high-rate topics (scope it to low-rate keyed state topics).
- **Blocks:** nothing hard — **Phase 8 is achievable via the ISC relay** (validated first by the Phase 1 PoC). Cost is architectural (a parallel relay + bypassing RS for those topics) + the perf/scope caveat above.
- **Ask to RTI (softened but still valid):** integrate ISC with **Routing Service** (and PRS) so instance-state transitions propagate across the gateway — this would **remove the need to bypass RS with a custom relay**. Core-protocol + IS work → long lead; worth starting.
- **Source:** [What's New 7.3.0 — Instance State Consistency + Infrastructure Services limitation](https://community.rti.com/static/documentation/connext-dds/current/doc/manuals/connext_dds_professional/whats_new/whats_new/WhatsNew730.htm)

## 🟠 LP-2 — Persistence Service synchronization does not work through Routing Service
- **What it is:** PRS data-synchronization (redundant/transitive, anti-entropy, eventual consistency) is our intended **durable-state relay / "gossip overlay."** But: **"the data synchronization protocol does not work when there are Routing Service instances between the Persistence Service instances."**
- **Why it matters:** in the RS-per-node topology, a naive "PRS behind each RS" design is broken. Workaround: deploy each PRS as a **direct WAN-domain peer** (parallel to the RS plane, not routed through it) — viable, but it forces a parallel participant set and extra RF traffic, and isn't a clean routed-PRS story.
- **Blocks:** clean PRS integration in an RS-routed mesh — **Roadmap Phase 10 (v2 relay/durability)**. Not a hard blocker (overlay works).
- **Ask to RTI:** PRS-sync interoperation across RS-routed topologies. Medium lead.
- **Source:** [Synchronizing of Persistence Service Instances](https://community.rti.com/static/documentation/connext-dds/current/doc/manuals/connext_dds_professional/users_manual/users_manual/Synchronizing_of_Persistence_Service_Ins.htm)

## 🟠 LP-3 — RTPS Analyzer as a shippable, supported component (stable contract + robust decode)
- **What it is:** Scope depends on the **internal** RTPS Analyzer for the node graph + payload decode. To ship **Scope as a customer deliverable**, the Analyzer needs: a **stable, documented event/socket contract**; robust decode (**RTPS fragment reassembly** + XCDR1/2 from `act_types.xml` / TypeObject); and **productization/licensing**.
- **Why we can't work around it:** we can normalize/extend and build the decode stage, but **productizing, licensing, and supporting** the Analyzer is RTI's call; the `ui/`-tier decoder's liftability is unknown (not in our snapshot).
- **Blocks:** Scope as a **deliverable** (not the internal sim — the sim is fine without this). Business + engineering lead.
- **Ask to RTI:** decide Analyzer productization; publish a stable event contract; support the decode path (fragmentation + XTypes).
- **Source:** internal RTPS Analyzer repo review (this project, `references/`).

## 🟡 LP-4 — Python remote-admin (`ServiceAdmin`) command coverage
- **What it is:** we drive RS remote admin (partition/team CRUD, session/route enable, detail-status) via **pure-Python `rti.connext` ServiceAdmin** to avoid the apt-token/C++ path. Need to confirm the **Python API covers every command** the C++ `remote_admin` tool does.
- **Why it matters:** if any needed command is missing from the Python surface, we either need RTI to fill it or fall back to the (off-critical-path) C++ tool.
- **Blocks:** dynamic C2 — **Roadmap Phase 6 (S3/S7)** — only if a required command is absent.
- **Ask to RTI:** confirm/complete Python `ServiceAdmin` coverage for the ACT command set. **Verify first — may be a non-issue.**

---

## Known constraints (workarounds exist / inherent — inform, don't block)

- ⚪ **C-1 — Monitoring Library 2.0 not in the host installer.** The node image must install the **target** monitoring package + set the lib search path. Packaging only; workaround known. *(Phase 4.)*
- ⚪ **C-2 — DDS Security defeats passive observation (inherent, not a bug).** Per-writer session keys reach only matched authorized readers, so a passive sniffer **cannot decrypt**; visibility degrades along the governance ladder. Not something RTI "fixes" — but **secured-network observability strategy** (an authorized-observer mode? Observability-based topology under security?) is a **product-strategy question** if Scope must work against secured customer networks. *(Phase 9.)*
- ⚪ **C-3 — RF-layer prioritization is ours, not RTI's.** EMANE RF Pipe is a flat single-queue pipe; prioritization must be enforced at the **DDS layer** (TransportPriority + flow controllers) or via `tc`/qdisc. No RTI ask. *(Phase 5.)*

---

## TL;DR for product
1. **Start LP-1 now** — ISC-through-Routing-Service is the long pole; it gates transition-level DDIL state consistency and we can only partially work around it (last-value). *(You suspected this — confirmed by RTI's own docs.)*
2. **LP-3** if Scope is to be a **deliverable** (business + engineering lead).
3. **LP-2** for the v2 PRS relay/gossip story.
4. **LP-4** is a quick confirmation, not necessarily a gap.
