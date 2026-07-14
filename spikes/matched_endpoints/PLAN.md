# Spike: matched-endpoints — the create-and-observe matching authority (D64)

## Question

The D64 pivot makes **DDS the matching authority**: the router creates a route entity, then
reads that entity's own `matched_publications()` / `matched_subscriptions()` as the truth about
connectivity — instead of the controller re-deriving matching by topic name (the source of the
D61 partition false-green: a cross-partition writer is counted as matched, the route reaches
`ENABLED`, DDS never associates, zero samples flow, and the incompatible-QoS path is silent).
Before retiring the controller matching and the D39/D51 readiness gate — a change to shipped
Phase 1–5 code — the `matched_publications` surface for **DynamicData** route entities must be
proven. D64 explicitly listed this as the "not yet proven" residual.

## Approach

Model the router's entities (a "route" reader/writer) and the peer's (an "app" writer/reader,
built exactly as the ACT sim builds them: `rti.connextdds` + DynamicData from `act_types.xml`),
and read connectivity purely from `matched_publications` / `matched_subscriptions`. Connext
7.7.0; each part on its own domain with fresh participants.

- **Part A — match truth (both directions):** a route reader's `matched_publications` lists a
  peer app writer; a route writer's `matched_subscriptions` lists a peer app reader; both go
  non-empty within a short create-then-observe window.
- **Part B — the money claim (false-green dissolved):** the route input reader is a
  `ContentFilteredTopic` on partition `PLATFORM` (like `control_command`'s destination side).
  (b1) an app writer on partition `CONTROL` must **never** appear in `matched_publications`
  (held zero over a window); (b2) an app writer on `PLATFORM` **does** appear. Covers partition
  **and** CFT together.
- **Part C — created-but-unmatched → observe:** a route reader created with no peer reports
  zero matches (the honest "built but not connected" signal — a status reason, not a false
  `ENABLED`), then transitions to matched when the peer appears.

## Pass / fail

PASS iff all three parts hold. `spikes/matched_endpoints/matched_endpoints_spike.py` exits
nonzero on any failed assertion.

## Result — PASS (2026-07-14, Connext 7.7.0, x64Linux4gcc7.3.0, rti.connextdds py38), stable 4/4

- **A:** route reader matched the app writer (~0.6–1.0 s to first match); route writer matched
  the app reader (often immediately). `matched_publications`/`matched_subscriptions` are the
  authority, both directions.
- **B (decisive):** a cross-partition (`CONTROL`) writer **never** entered the CFT route
  reader's `matched_publications` (held 0 over the hold window); a same-partition (`PLATFORM`)
  writer matched within ~0.1 s. **The partition false-green is dissolved, not merely
  diagnosed** — a create-and-observe router reading `matched_publications` sees zero and
  correctly reports "not connected." Partition + CFT compose exactly as `control_command`
  needs.
- **C:** a reader with no peer holds `matched_publications = 0` (the honest signal), then
  observes the writer within ~0.1 s of it appearing.

## Design implications

- **The create-and-observe matching authority is viable and proven** for DynamicData route
  entities. DDS gates on partition/QoS/type; the router reads `matched_publications` /
  `matched_subscriptions` and never re-implements matching. This closes D64's "not yet proven"
  residual on the *behavior/API* axis.
- The **created-but-unmatched** state is a real, observable zero — wire it as a **status
  reason** on the route/topic, not a new operational state (do not overload `DEGRADED`; keep
  the D2/D11 contract).
- **Still to do (the readiness pass, not a spike):** integrate this into the shipped controller
  — replace the topic-name matching + matched-endpoint sets (D12/D20/D22) and the D39/D51 gate
  with entity-`matched_*`-driven discovery state, decide the poll-vs-`SUBSCRIPTION_MATCHED`-
  StatusCondition mechanism (this spike polled; the router already has a StatusCondition/
  AsyncWaitSet pattern from Phase 5 it can reuse for the matched-status callback), and define
  the created-but-unmatched status-reason field. That is a change to tested code and needs its
  own D54/D59-style slicing.
- **C++ residual:** `matched_publications()`/`matched_subscriptions()` and the
  `SUBSCRIPTION_MATCHED`/`PUBLICATION_MATCHED` StatusConditions exist in the C++ API too; the
  exact call surface is a compile-check at implementation time (the MCP is not trusted).
