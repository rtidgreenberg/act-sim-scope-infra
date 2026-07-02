# Locked Decisions & Risks / Open Questions

> Part of the **ACT EMANE Simulation & Thesis-Validation** plan — start at the [overview & index](EMANE_SIMULATION_PLAN.md). Section numbers (§X.Y) are preserved from the original monolithic plan; the overview maps every section to its doc.

---

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
| Passive observation | **RTPS Analyzer** (existing tool, the **sniffer**) — Wireshark-based, detects DDS events off the wire. **Packet-level, not DDS-level** — zero observer effect. Generates the **node graph** and, via its own decode/normalize stage, **deserializes payloads** and publishes **deserialized events** on the bus (§2.9); Scope + harness are pure subscribers that never decode. |
| Base OS | **Ubuntu 22.04 (Jammy)** — EMANE `.deb`s + RTI Debian packages both target it |
| License | Internal tool — license covers **all RTI products** incl. **Observability Framework** (Monitoring Library 2.0 + Collector Service), Routing Service, Python, CDS, in containers |
| DDS Security | **Sim / integration / optimization run UNSECURED** — full sniffer fidelity (topology + flow + payload decode) is the critical path. Security is a **deployment-time switch** (off for dev/tuning, on for operational deployment). Under security the sniffer **cannot passively decrypt** (per-writer session keys reach only matched, authorized readers) and its visibility degrades along a **governance-set ladder** (§9). Deep inspection under security is an **explicit, non-passive opt-in** (authorized active observer, or an in-node LAN-side agent) — never the default. |
| Control plane / C2 | **Operational C2 rides the RF.** Commands and **team reassignment via RS remote admin** (`ServiceAdmin`, partition CRUD) traverse the *same* emulated RF as bulk traffic and are **subject to impairment** — realistic, and it's what the thesis validates. Survival under degradation is enforced by **DDS-layer prioritization** (TransportPriority + async flow controllers), since **RF Pipe won't prioritize on its own** (§6). **Only rig/experiment control** — docker lifecycle, EMANE event injection, sim process start/stop — is **out-of-band** on the management bridge (test apparatus, not system-under-test). |
| Relay control surface | **Custom DDS topics** for the **Python ISC relay** (not RS `ServiceAdmin` — we own the relay). Declarative **desired-state + reported-state** keyed topics for config (routes enable/disable, downsample, decode-set) — reported-state = the observable control-state inspector (answers "RS black box"); `TRANSIENT_LOCAL` for reconnect-safe reconcile. **Request/reply** for imperative one-shots needing an ack. **RS-managed functions stay on `ServiceAdmin`, proxied by the backend**, so GUI A sees one unified API. Authz of control topics = DDS Security permissions in deployment. *(Design in test-harness.md.)* |


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
- **In-band Observability competes for RF** — Monitoring Library 2.0 is a DDS *publisher*;
  if its telemetry crosses `emane0` it consumes the constrained RF we're measuring (skews S2).
  Pin the monitoring stream **off the RF** (LAN/management path) or explicitly measure its overhead.
  Same interface-pinning discipline as WAN/LAN (§ node-container anatomy).
- **Edge-rate source of truth** — sniffer packet counts (on `emane0`, post-loss) vs Prometheus
  rates (Monitoring Lib, offered) will diverge under loss. Decide per visual which drives it;
  the divergence is itself a thesis signal (offered vs delivered).

### DDS Security & Scope visibility (dev-off / deploy-on)

**Posture:** run the sim/integration **unsecured** for full-fidelity tuning and validation, then
**enable security for operational deployment**. Governance is a deployment-time switch, not a code
change. This keeps full Scope fidelity on the critical path while acknowledging the deployed
monitoring picture is lower-fidelity once security is on.

**Two hard facts:**
- **Passive decryption is impossible.** DDS Security distributes per-DataWriter session keys only to
  *matched, authenticated, authorized* DataReaders via the encrypted crypto handshake. A passive tap
  never receives them — so no identity cert alone unlocks decode; you'd have to *become a matched
  reader*, forfeiting zero-observer-effect.
- **IP/UDP + the RTPS header (source participant GUID prefix) and SPDP are always cleartext**
  (bootstrap requirement) — so a **coarse participant graph survives even under full encryption**.

**Visibility ladder** (what the sniffer yields per governance profile):

| Governance (common → hardened) | Node graph the sniffer can build |
|---|---|
| No security *(the sim)* | Full: participants, endpoints, topics, QoS, matches, flow, loss, **payload decode** |
| `data_protection=ENCRYPT`, discovery readable *(common)* | Full **topology + flow + loss**; **no payload decode** (inspector = metadata-only) |
| + metadata/discovery encrypted | **Participant graph** + traffic edges (RTPS-header GUIDs) + sizes/timing; no topics/endpoints/QoS/seq |
| Full `rtps_protection=ENCRYPT` | **Participant graph from SPDP** (nodes + IPs + who-talks-to-whom + volume) only |

**Fallback under security:** if the customer runs **RTI Observability**, take topology + rates from
the participants' own telemetry (no wire decryption) — complements the coarse sniffer graph.

**Open item — characterize before relying on it:** validate Scope against a *secured* config during
integration (not only unsecured) so the degradation tier is known before deployment; otherwise
"switch security on for deployment" can silently blind the monitoring we tuned with full visibility.

### Additional open concerns (architecture review)

- **WAN identity = Routing Service, not the app.** On `emane0` the writer of any sample is the **RS
  participant**, not the originating platform (app sims live on LAN domains 20/30, never crossing the
  RF). So the node graph / endpoint inspector show **RS WAN endpoints**, and app-origin attribution
  ("P30 published X") comes from the **RS↔node naming/config convention**, not the wire. Set the
  graph-semantics expectation accordingly; it's fine for a thesis about the RS mesh.
- **Sniffer late-join / restart resync.** Topology is rebuilt from observed SPDP/SEDP. SPDP
  re-announces periodically (participants recover), but **SEDP is event-driven** — a sniffer that
  starts late or restarts may show a **stale/partial endpoint graph** until entities churn. "Trust
  the picture" must-address: start the sniffer before the nodes and/or surface a **"topology may be
  incomplete since T"** indicator (distinct from "sniffer died").
- **Capture-pipeline throughput / backpressure.** The reference analyzer uses a tshark **display
  filter** (dissect-then-filter) and sharkd is **single-request-at-a-time**; under S2 video/detail-
  status rates — now with **per-node capture + per-node decode** under EMANE — it can fall behind and
  drop events, under-reporting flow/loss. Must **detect and signal drops**, not silently under-count.
  Load-test at S2 rates.
- **Three measurements cover three path segments.** App seq/ts = full LAN→WAN→LAN e2e; sniffer on
  `emane0` = WAN leg only; `emanesh` = PHY. Correlating app-loss vs PHY-drop needs an explicit
  per-segment model — RS retransmit can hide PHY loss from the app view.
- **Minor:** (a) don't use **sniffer timestamps** for latency (capture/parse jitter) — use only the
  app seq/ts; (b) **pin the `rti.connext` pip wheel to the base image's Routing Service version**
  (wire + Monitoring-Lib compat); (c) sequence **Grafana (Layer 2, ~free) before the flagship
  Cytoscape viz (Layer 1)** so the thesis has quantitative evidence even if the custom viz slips.

