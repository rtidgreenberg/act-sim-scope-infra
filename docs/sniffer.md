# Sniffer (RTPS Analyzer) — Observation, Event Contract & Decode

> Part of the **ACT EMANE Simulation & Thesis-Validation** plan — start at the [overview & index](EMANE_SIMULATION_PLAN.md). Docs are organized by **process**; cross-cutting concerns live in [architecture.md](architecture.md), [decisions-and-risks.md](decisions-and-risks.md), [thesis-and-claims.md](thesis-and-claims.md), and [roadmap.md](roadmap.md). Original plan section numbers (§X.Y) are retained for traceability.

---

The **sniffer** is the passive observation *producer* for the whole system — the RTI **RTPS
Analyzer** (Wireshark/tshark-based) capturing RTPS off the wire and publishing JSON events on a bus
that **both** [Scope](scope.md) and the [harness](test-harness.md) subscribe to independently. It
adds **no DDS participant** (zero observer effect). This doc covers the observation model, the event
contract, the reference-repo findings, and the decode-placement decision.

## Passive observation via the RTPS Analyzer (§2.6)

**Inspector + node graph — RESOLVED via the RTPS Analyzer (packet-level, zero DDS impact).**
The existing **RTPS Analyzer** (Wireshark-based) is the single passive observation source.
No DDS participant anywhere:
- **Node graph** ← the Analyzer detects DDS entities/events from RTPS on the wire (SPDP/SEDP
  discovery → participants/endpoints/topics; DATA submessages → live flow) and **publishes
  socket events**. The backend consumes these → `/nodes-edges` + `/stream` → Cytoscape.
- **Endpoint list** ← same socket-event stream (discovery-derived), mapped to nodes via
  participant name / GUID. Newly exposed routes (detail-status/team enable) show up as new
  discovery events → panel updates live.
- **Sample content** ← payload decode. ⚠️ **Nuance (see §2.9):** the Analyzer is **two-tier**.
  The **core CLI analyzer** (the repo we reference) decodes **RTPS headers + discovery metadata
  only** — it captures the user payload bytes but *drops* them. **Payload-field decode lives in
  a separate `ui/` tier** (not in our snapshot). ✅ **Decision (§2.9): decode inside the sniffer's
  own decode/normalize stage** (bytes are already captured; topic/type are known from discovery
  *before* decode) rather than depend on the UI — so **the sniffer emits deserialized messages**,
  one unified `data_sample` carrying RTPS metadata **and** decoded fields.
- Removes any need for a DDS-level subscriber or type propagation — **WAN QoS unchanged**. The
  decoder is a small, type-table-driven (`act_types.xml`) module **within the sniffer** (not "free"
  from the analyzer core as first assumed, but still packet-level / zero observer effect).

**Integration findings — RESOLVED (both reference repos reviewed; details in §2.9):**
1. **Transport + schema** — native transport is **socket.io**, and the Analyzer **dials *out* as
   a client** (core CLI → collector `:8888`, msg `'message'`/`rtpsEvent`; UI → server `:3000`,
   msg `'cdr-deserialized'`). → the sniffer's decode/normalize stage **consumes the core's socket.io
   feed** and re-publishes on the bus. Event schema = the enumerated `rtpsEvent` types (normalize
   before adopting — §2.9).
2. **Payload decode** — the core analyzer does **not** decode user payloads (no CDR/XCDR, no
   TypeObject handling); the UI tier does full nested decode from a **configured type set**
   (confirmed via sbir's `rtps_analyzer_processor.py` consuming `cdr-deserialized`). We build/lift
   the decoder into the sniffer's decode stage, fed by `act_types.xml`.
3. **Topology source** — **both** SEDP/SPDP discovery *and* DATA/HB/ACKNACK traffic; entities are
   keyed by GUID (`guid_prefix`, `guid_prefix+entity_id`) — we key strictly on binary GUID.

**Capture considerations:** RTPS must be on a **sniffable transport** — UDP is visible,
**SHMEM is not**. ✅ **Decision: LAN QoS → UDP-only** (disable SHMEM) so LAN-local traffic is
also fully sniffable — consistent with SYSTEM_ARCH ("SHMEM *or* UDP loopback"). Config change
to `config/qos/lan_qos_lib.xml` (transport mask UDPv4, drop shmem) + `params/system_params.sh`
peers. **Capture point:** per-node **`emane0`** (post-EMANE-decap RTPS) once EMANE is in; the
shared **bridge** for the M0 plain-bridge phase.

*Resolved:* both reference repos are now in `references/` and have been reviewed — the RTPS
Analyzer (`rticonnextdds-rtpsanalyzer`) and the sbir64_65 DDS track-fusion system (working prior
art for sims + socket.io dashboards + an analyzer-event consumer). Findings, the decode-placement
decision, the event contract, and reusable patterns are in **§2.9**. (Still outstanding: the
Analyzer's **`ui/`** tree is *not* in our snapshot — needed only to decide whether its XCDR
decoder is a liftable headless module vs. reimplementing decode in the adapter.)

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

## Sniffer event contract (§2.8)

**Sniffer event contract — make it a generic producer "anyone can subscribe to":**
Three orthogonal choices:
- **Schema** — the sniffer publishes **two categories**, documented via **JSON Schema**:
  - **Discovered entities** — `participant_discovered/lost`, `endpoint_discovered/lost`
    (topic/type/QoS/partition/GUID), `match`, `liveliness/link`.
  - **Deserialized messages** — `data_sample` (topic, writer GUID, size, seq, + **decoded
    fields** produced by the sniffer's own decode stage, §2.9 — *not* free from the core Analyzer),
    and derived `flow_stats` (per-topic/writer rate/interval). **The sniffer emits deserialized
    messages** — decode happens *inside* the sniffer, upstream of the bus; **raw payload bytes never
    cross the bus** and no subscriber ever decodes. Decode is **command-gated** (§2.9): always-on
    lightweight detection for *all* traffic; full payload decode only for an on-demand decode-set.

  Optional **CloudEvents** envelope (`id/source/type/time/data`) so it's a recognized standard.
- **Encoding** — **JSON** (human-readable, universal; the user-friendly/generic goal).
- **Transport** — pub/sub so N independent subscribers attach without the sniffer knowing them:
  - **MQTT** (recommended) — ubiquitous clients, topic hierarchy `dds/<domain>/<topic>/<event>`
    for filtered subscription, lightweight broker (mosquitto), Grafana/Node-RED friendly.
  - **NATS** — very simple/fast subjects; fewer built-in integrations.
  - **WebSocket / SSE** — browser-native (SSE consumable by `curl`); great for the frontend.
  - **Raw NDJSON over TCP** — dead simple, any language; no fan-out/filtering built in.

**Decisions:** ✅ **Encoding = JSON, documented via JSON Schema** (the contract). ✅
**Transport = DECIDED (§2.9):** the analyzer core's native output is **socket.io, dialing outward as
a client**, so the sniffer's **decode/normalize stage** consumes the core's `rtpsEvent` messages,
runs the **command-gated decoder**, **normalizes** to the JSON-Schema contract, and publishes
**deserialized events on the bus**. That decode/normalize stage is **part of the sniffer** (upstream
of the bus) — **Scope and the harness are pure subscribers; neither decodes.** Whether the bus is
MQTT / NATS / WS-SSE for downstream fan-out is a **secondary** choice (the stage decouples it from
the core); the JSON-Schema contract stays transport-independent so N subscribers attach without the
sniffer knowing them.

## 2.9 Reference-repo findings & the decode-placement decision

Both reference repos in `references/` were reviewed to inform our technical choices:
- **`rticonnextdds-rtpsanalyzer`** (Node.js/TS) — the sniffer we reference for DDS entity
  detection: tshark/sharkd → RTPS parse → runtime model → socket.io events.
- **`sbir64_65`** (Python, RTI Connext + Flask-SocketIO) — working prior art: DDS sims +
  subscribers + socket.io dashboards + `rtps_analyzer_processor.py` (an analyzer-event consumer).

### 2.9.1 The Analyzer is two-tier — decode is NOT in the tier we have
| Tier | What it is | Transport (dials **out**) | Gives us |
|---|---|---|---|
| **Core CLI analyzer** (our snapshot) | tshark/sharkd → RTPS parse → model builder | socket.io **client → collector `:8888`**, msg `'message'`/`rtpsEvent` | Entity detection + topology + discovery metadata (topic/type/QoS/GUID) + traffic events — **headers only; user payload bytes are captured then dropped** (`handleUserData` never reads `serializedData`) |
| **Analyzer `ui/`** (*not* in snapshot) | separate npm app | socket.io **client → our server `:3000`**, msg `'cdr-deserialized'` | **Fully nested XCDR-decoded** payload fields (`{name, topicName, entityType, src_timestamp, data{…}}`) |

Confirmed by sbir: `rtps_analyzer_processor.py` **hosts** the `:3000` socket.io server and receives
`cdr-deserialized` with fully decoded `data{}` — it imports no `rti.connextdds` and does no decode
itself. The UI is started *"with update and delete topics"* → its decoder is **type/topic-table
driven** (path A below), *not* wire-learned.

### 2.9.2 Decision: decode INSIDE the sniffer — it emits deserialized messages
Decode is a pure function of **(raw CDR bytes + type schema + XCDR1/2 + endianness)**. The sniffer
already has the bytes (in the PDML/sharkd frame) and already resolves **topic/type/writer from
discovery *before* decode** — so it can decide *whether* to decode using metadata it already holds,
before paying the cost. ✅ **Decode lives inside the sniffer**, upstream of the bus, so **the
sniffer's output is deserialized messages** (one `data_sample` carrying BOTH the RTPS/transport
metadata AND the decoded fields — strictly more than either existing feed). This resolves the
product-boundary question: **Scope and the harness are pure subscribers to a bus that is already
decoded; neither carries decode logic, and the harness has no runtime dependency on Scope.** (sbir
is the cautionary case: consuming the decoded-but-metadata-poor UI feed alone forces a weak string
key `topic_source_system_timestamp`, plus hand-rolled dedup and lossy attribute-merge heuristics —
machinery that mostly vanishes when decode sits next to GUID identity.)

- **Type sourcing — ✅ path A (static `act_types.xml`):** load the ACT types locally; map
  topic→type→schema. Deterministic; matches how the UI itself is configured; we own the types.
  *Path B (wire-learned TypeObject, Connext 6.0+/7.7+)* is optional future work — only if the
  standalone-Scope "decode any customer system with no type input" story demands it.
- **Packaging — the sniffer = analyzer core + a decode/normalize stage**, deployed as one
  observation-plane producer. The pristine analyzer core hands raw `serializedData` to the decode
  stage **internally** (one small core override to surface the bytes); the decode stage does XCDR +
  fragment reassembly + `act_types.xml` lookup and publishes deserialized events. Recommended: the
  decode stage is a **bundled Python component** (easier XCDR/fragmentation work, keeps the Node core
  pristine). *(Alternative — decode in the Node core itself; truer to "in the analyzer" but a large
  fork of an external tool. Reconsider only if the UI's decoder proves a liftable headless module.)*
  Either way the decoder, gate, and decode-set control surface are **part of the sniffer**, not Scope.

### 2.9.3 Command-gated, two-tier emission (blanket decode is the risk)
Decoding **every** detected sample is a CPU + firehose hazard. Because the sniffer knows
topic/writer pre-decode, decode is **gated**, not blanket:
- **Always-on (all traffic, header-only, cheap):** discovery + `sample_sent`/`flow_stats`. This
  feeds the node graph and flow animation — which are **rate-driven, not per-packet** (Scope viz), so
  they never needed decoded payloads.
- **Gated (opt-in, expensive):** full decode → `data_sample` with fields, emitted **only** for a
  sample whose topic/writer is in the active **decode-set**. **Default: decode nothing.**
- **Control command** mutates the decode-set: `decode.enable/disable {topic | writer_guid}`. This
  *is* the §2.6 Endpoint Inspector "click endpoint → stream samples" action — an on-demand
  **decode** toggle (not a DDS subscribe), so zero observer effect holds. It also throttles the
  §2.7 "inspector firehose" **at the source** instead of in the browser.
- **Granularity:** per-**topic** (a channel) and per-**writer-GUID** (an endpoint / inspector
  selection). **Rate-cap** per decode-set entry (max-Hz / 1-in-N) so "enable topic X" ≠ "decode all
  of a high-rate topic."
- The active decode-set is **control state** (surfaces in §2.7 "current control-state inspector");
  enabling gets **async ack**. In deployment mode the Scope inspector issues the command; in
  test/sim mode the harness can also drive it from a scenario trigger — same one-way control surface.

### 2.9.4 Event contract to mirror — and normalize
Mirror the enumerated `rtpsEvent` types (discriminated on `event_type`, tagged `category` ∈
{`model`,`data`,`validation`}): `new_participant`, `new_reader`, `new_writer`, `removed_reader`,
`removed_writer`, (`removed_participant`), `sample_sent`, `HB`, `ACKNACK`. **Fix the reference's
hazards** when we author the JSON-Schema: inconsistent field names (`entityType`↔`entity_type`,
`entity_id`↔`reader_id`/`writer_id`, `part_entity`↔`fully_qualified_part_name`), `removed_participant`
/`sample_received` defined-but-unemitted, a `Boolean("false")`-is-truthy bug, and an inconsistent
`qos_values` shape. **Key strictly on binary GUID**, not the reference's string-concat / FQ-name
correlation (which also breaks under its default `--use_guid`). Draft **unified `data_sample`**
(the payoff of §2.9.2):
```jsonc
{ "event_type": "data_sample", "category": "data",
  "domain_id": 200, "topic_name": "...", "type_name": "...",
  "writer_guid": "<guid_prefix+entity_id>",          // stable binary identity
  "seq": 12345, "size": 512, "src_ts": "...", "recv_ts": "...",
  "ip_src": "...", "port_src": 0, "ip_dst": "...", "port_dst": 0,  // from core analyzer
  "decoded": true, "data": { /* ...fields, only when in decode-set... */ } }
```

### 2.9.5 Reusable patterns (adopt) & anti-patterns (avoid) from sbir
- **Adopt:** Python `@idl.struct` dynamic types with keys via `member_annotations={'f':[idl.key]}`
  (no IDL toolchain — good for our seq#/timestamp payload instrumentation); config-driven
  **hot-reloaded control file** (mtime-poll, `_active` flags) for live fault injection + the
  **CLI-overrides-config** precedence rule + **deterministic seed/fixed-epoch** sim mode for
  repeatable CI; dashboard **connect-then-`request_*` handshake** (don't dump full state on connect)
  + parallel REST snapshot endpoints; temporary→persistent **mismatch state machine** for alerting.
- **Avoid:** Flask-SocketIO on the Werkzeug dev server (`allow_unsafe_werkzeug=True`) → keep the
  interaction patterns but use our **FastAPI + async WS**; `take()`+`sleep()` polling → use
  waitsets/listeners; **no-QoS** everywhere → set explicit reliability/durability/liveliness (this
  *is* our §6 QoS-gap work); bash orchestration with no readiness gating → compose health checks.
- **Free surface to imitate:** the core analyzer's **MCP server** (`get_model` returns live
  topology) — a low-effort pattern to expose Scope's topology to agents; extend with query + streaming.

## Sniffer-integration work-items (from §2.9)

- **Analyzer-core override — surface raw `serializedData` to the decode stage** (internal to the
  sniffer, not on the bus): small, isolated change; bytes are already in `pkt_data`, just dropped today.
- **Sniffer decode/normalize stage (Python, bundled with the analyzer core)** — consumes the core's
  `rtpsEvent` socket.io feed, does **XCDR + RTPS fragment reassembly** + `act_types.xml` lookup,
  normalizes to the JSON-Schema contract, owns the **command-gated decode-set** control surface, and
  publishes **deserialized events on the bus**. This is part of the sniffer, not Scope.
- **JSON-Schema event contract** — author from the normalized `rtpsEvent` types + unified
  `data_sample` (§2.9.4); key on binary GUID.
