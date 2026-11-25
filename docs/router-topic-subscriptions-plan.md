# Router topic inventory and graph subscription controls

Date: 2026-11-24.

Status: draft implementation plan. No runtime or deployment changes are authorized
by this document.

## Goal and agreed behavior

Selecting a boat in the mesh graph should show:

- The topics its WAN reader filters currently select.
- Available topics advertised by other routers, with source and availability.
- Whether selection comes from mission policy or a user override.
- Controls to apply a user selection or return to mission-driven selection.

User selections override mission-driven selection. This changes receive interests,
not outgoing reporting levels. Producers continue reporting; reader interests
continue to drive native sender-side filtering through the current SEDP path.
Do not adopt the receiver-only LBED configuration.

Initial scope is platform receivers in the graph. Every router, including C2,
advertises its inventory. Editing C2's static receive policy, arbitrary SQL,
new WAN bindings, per-producer checkbox rules and automatic schema expansion are
outside this first implementation.

## Existing foundations and gaps

- [GatewayModel](../router/routing_service/GatewayModel.hpp) already holds prepared
  source/destination bindings and generates family-specific receive filters.
- [Mission policy](../router/routing_service/README.md#platform-mission-receive-policy)
  selects Init or ActiveMission from local mission state and team membership.
  Whole-platform restart currently resets to Init.
- [GatewayModel::apply_filters](../router/routing_service/GatewayModel.cxx)
  stages model changes and rolls back all families if a native filter update fails.
  [GatewayMonitor](../router/routing_service/GatewayMonitor.cxx) applies these
  filters to Routing Service-owned content-filtered topics.
- [RouterConfig and RouterHealth](../harness_v2/datamodel/ActTypes.idl) expose
  writer-observed remote reader filters. This is useful propagation evidence,
  but not a complete authoritative snapshot of a boat's own applied subscriptions.
- [The graph](../gui/mesh_dashboard/static/mesh_graph.js) currently displays
  reporting policy and receive profile without subscription editing.
- [The bridge](../gui/mesh_dashboard/server/mesh_bridge.py) has no subscription
  request API. Existing reporting-mode requests are fire-and-forget and must not
  be reused as proof of successful filter application.
- The native processor's local admin handler currently supports team membership,
  not topic selection. Command handling must be extended explicitly.

## 1. Selection semantics

Use two explicit policy sources:

| Source | Effective topic selection |
| --- | --- |
| Mission / Auto | Existing Init/ActiveMission selection |
| User override | Protected interests plus the user's selected application topics |

Applying checkboxes switches to User override. Subsequent mission changes must
not replace the user's optional selection. They still update the recorded mission
state and any required safety interests. Team changes always recompute audiences:
selecting ContactReport does not grant access to another team's reports.

**Return to mission selection** clears the override and applies the policy for
the current local phase immediately; it must not wait for another mission sample.

Keep health/configuration, inventory/subscription control traffic, C2 Mission
and Team interests protected. Do not remove self-ignore, source validation,
configured audience restrictions, original-type checks or fixed QoS.

Proposed safety rule to confirm before implementation: fleet navigation
PlatformStatus remains protected while a mission is active. This prevents a
checkbox from silently breaking navigation/collision inputs. Show protected
topics as locked, with the reason.

Proposed first-release lifetime: overrides are router-session scoped. A whole
platform restart returns to Auto/Init, matching current restart behavior. The UI
must show the reset, not silently resend an old override. Disk persistence is
separate work unless explicitly approved.

Selection is by original application topic, not Event/State/Periodic transport
name. The same original topic may have multiple eligible producers; the existing
audience rules still determine whose data is accepted.

## 2. Automatically advertised topic inventory

Publish a latest-state inventory per router in the existing State family:
State in three-topic mode and P2_State in six-topic mode. Add internal metadata
branches/mappings; do not create another physical WAN topic or participant.

Inventory contents:

- Authoritative node/router identity, current session, revision and timestamp.
- Prepared schema/bundle identity.
- For each exportable original topic: topic name, type identity, effective WAN
  family, configured audience classification and source binding status.
- Separate availability facts: configured, native LAN input exists, compatible
  application producer matched, and optionally last observed sample time.

Derive entries from prepared export bindings and their native LAN inputs.
Update availability from native matching/discovery, excluding the router's own
forwarded LAN publications. A matched endpoint is not proof that samples are
currently being produced; label those facts separately.

Do not advertise arbitrary discovered LAN topics as selectable. A topic needs a
prepared WAN mapping and a compatible destination LAN output. Type-name equality
alone is insufficient. An inventory is selection metadata, not a replacement for
the existing union schema or a mechanism for inserting new union branches.

Publish first, on change, and periodically for late join/recovery. Initially
reuse the existing configuration refresh cadence rather than increasing heartbeat
size or retuning WAN QoS. Volatile WAN history means latest-state publication
alone is not a late-join guarantee.

The opt-in transient-local WAN State spike subsequently verified no-refresh
inventory replay and latest-per-key delivery to new matching WAN readers.
It did not replay cached contacts on in-place filter widening, and source writer
restart cleared the tested history. Production profiles remain unchanged pending
review. If adopted, retain recovery publication for restarted writers/volatile
LAN legs, define replay freshness and disposal/cache bounds, and separately
decide how a new manual selection obtains pre-existing state.

Cache snapshots by router/session/revision, preserving current-session replay
protection. Mark stale inventory and unavailable sources explicitly. Establish
bounded entry counts, strings, cached routers/sessions and refresh work before
coding; validate limits against supported prepared bundles and reject overflow
explicitly rather than silently truncating selectable topics.

Inventory reception must be independent of optional subscriptions so the user
can discover topics after deselecting all optional application data.

## 3. Authoritative applied subscription state

Publish a separate latest-state snapshot in the same State family, keyed by
router identity. Include:

- Session and committed subscription revision.
- Independent monotonic snapshot sequence within the session, so an older
  confirmation cannot overwrite newer degraded state at the same policy revision.
- Selection source: Mission/Auto or User override.
- Current mission phase, teams and stored user topic selection.
- Effective selected topics and which are protected.
- Actual native filter expressions/parameters per family, readback status and
  snapshot freshness.
- Last request identifier and explicit application/readback error information.

Use structured model selections for the checklist; do not reverse-engineer
arbitrary SQL into an editable topic list. Keep raw native filter readback for
verification and the existing SQL inspector.

The graph's **Subscribed topics** means topics selected by the applied receive
filters, not topics producing samples at that moment. Distinguish:

1. Requested selection.
2. Committed/read-back local filters.
3. Remote writers' observed filters and propagation freshness.
4. Actual observed deliveries.

A published request, HTTP success, or local model intention must not masquerade
as applied native state. If readback is unavailable or differs, show that error.
Already accepted samples may drain after narrowing; no atomic fleet-wide cutover
or automatic replay of previously excluded data is promised.

## 4. Reliable selection requests and transactional application

Add a dedicated subscription request/result contract. Carry addressed requests
and results through the existing reliable Event family and extend the local
router command/ack seam explicitly, not Routing Service remote administration.
Finalize the LAN-to-Event and local dispatch
mapping in the contract phase; do not add an intermediate bus or extra WAN pair.
Do not repurpose reporting-mode messages or redesign mission command octets.

A request contains target router/node, target session, request ID, expected
subscription revision, policy source and a bounded topic-name set.

At the receiving router:

1. Validate target/session, revision, permitted requester, supported topics,
   destination type/bindings, audience rules and bounds.
2. Build all next-family filters without mutating committed state.
3. Apply through the existing serialized transactional filter path, validating
   native readback inside the apply callback before committing the next model.
   A readback mismatch/failure triggers the same rollback path.
4. Publish the resulting committed applied state.
5. Return a correlated result distinguishing committed, rejected, stale/conflict
   and degraded/readback failure. Never report success for a rollback failure.

Define bounded duplicate-request handling. A retry of a committed request must
not apply it twice or overwrite a newer user choice. Reject stale requests from
previous router sessions; do not replay them after restart.

Retain explicit rollback/error reporting and heartbeat scheduling isolation.
Interests are not authorization: topic selection must not widen who is permitted
to request changes or which source audiences the router is allowed to receive.

## 5. Graph UI and bridge

Extend the selected-boat detail panel rather than adding another dashboard tab:

- **Selection:** Mission/Auto or User override, with current mission phase.
- **Subscribed topics:** effective applied names, protected markers, audience,
  family and freshness.
- **Available topics:** inventory-backed checkbox list with producer routers,
  configured/matched/stale status and incompatibility reasons.
- **Apply selection**, **Discard edits**, and **Return to mission selection**.
- **View SQL filter** remains available for exact native expressions/parameters.

Show desired edits separately from applied state. Disable Apply when nothing
changed, the target session/state is unknown, a request is pending, or a topic
is unsupported. Preserve a selected topic when its producer temporarily becomes
unavailable, but mark it unavailable; do not silently unsubscribe or broaden it.
An available topic's appearance must not automatically enable it in User mode.

The bridge needs inventory/state caches and a correlated request/result API,
plus REST/WebSocket updates using existing transport patterns. Handle timeout,
rejection, restart, conflicting edits and lost connections visibly. A pending
request may have applied remotely even if its result was lost: reconcile against
authoritative state instead of declaring rollback or retrying blindly.

Bump the graph asset revision in
[index.html](../gui/mesh_dashboard/static/index.html). Preserve graph/detail
layout, mission controls, team assignment and reporting-policy labels.

## 6. Dependent diagnostics and delivery accounting

Extend committed receive-policy audit records with selection source, effective
topics, user override and revision. Keep local mission/team history and explicit
logging on audit failures.

Update [delivery recipient resolution](../gui/mesh_dashboard/server/mesh_bridge.py)
and its history parser to use the effective subscription at the relevant event
time. Today they assume Init/ActiveMission; treating a new User profile as static
would incorrectly count deliberately filtered samples as lost.

Preserve existing reliable/periodic grace-window semantics and historical Auto
records. Test filter-transition boundaries and report missing history explicitly.
Do not claim sender-filter propagation is instantaneous merely because local
policy committed.

## Implementation phases and acceptance gates

| Phase | Deliverable | Required gate |
| --- | --- | --- |
| 1 | Confirm protected interests, override lifetime, request authority, schema fields/mappings and resource bounds | Reviewed contract; no existing branch labels/keys/QoS silently changed |
| 2 | Inventory and authoritative subscription snapshots, cache/refresh and native readback | Three-/six-topic native publication, late join, restart, stale snapshots, bounded overflow, producer appearance/disappearance; unchanged WAN endpoint counts |
| 3 | User/Auto policy and reliable request/result path | Override survives mission changes; Auto restoration; team audience enforcement; duplicate/conflict/session handling; every-family rollback and readback/error cases |
| 4 | Bridge, graph checklist and applied-policy audit/delivery accounting | Browser interaction tests, loaded asset revision, pending/error/freshness states, exact SQL, no false Delivery losses |
| 5 | End-to-end qualification and deployment preparation | Actual sender suppression, exact original payloads, lifecycle, existing mission regression, bundle compatibility and coordinated rollback |

### Focused validation

- Extend existing model/configuration tests in
  [test_config.py](../router/routing_service/test_config.py).
- Extend native processor coverage in
  [test_routing_service.py](../router/test_e2e/test_routing_service.py),
  [test_three_topic_routing_service.py](../router/test_e2e/test_three_topic_routing_service.py)
  and [test_routing_service_mesh.py](../router/test_e2e/test_routing_service_mesh.py).
  Include six-topic native execution; configuration-only tests do not qualify it.
- Extend [graph filter browser tests](../gui/mesh_dashboard/tests/test_wan_filters.py)
  and the existing policy-history/delivery tests.
- Use offered samples with known identities/sequences and native writer counters
  or bounded captures to prove excluded samples are not sent to the receiver.
  Application non-delivery alone is insufficient.
- Verify narrowing, widening, publisher churn and team/mission changes during
  traffic. Preserve selected payload/key equality and dispose/revival behavior.
- Test late-join/restart recovery, explicit empty optional selection, protected
  interests, unknown topics and incompatible types.
- Run the existing live mission workflows only after separate approval, through
  the owned harness; no current mesh restart is implied by this plan.

## Compatibility and rollout

Adding inventory/state/request/result payloads changes IDL, union mappings,
generated XML and prepared bundles. Preserve existing numeric branch labels and
stable instance-key semantics; explicitly define keys for new metadata snapshots.
Regenerate types/configuration and qualify matching binaries/bundles together.
Do not assume mixed old/new participants are safe.

Keep fixed QoS in external production XML. Preserve compression, priorities,
controllers, reliability/history policies and three-/six-topic endpoint counts.
Do not enable the new control path on legacy/two-topic/custom-router modes without
separate support and qualification.

Use owned private network/IPC test containers and disposable local workdirs.
Retain compact evidence, stop owned processes, and clean their artifacts. A later
deployment requires explicit approval, coordinated owned down/build/up and a
matching-bundle rollback check.

## Completion definition

A boat's graph panel lists available routable topics and its actual applied
subscriptions. The user can override optional mission selection, change that
selection and return to Auto, with correlated applied-state confirmation. Mission
and team behavior remain correct; sender suppression is proven on the WAN;
restart, errors and delivery accounting are honest. No additional physical WAN
topic, reporting-level toggle or unqualified schema expansion is introduced.

## Isolated mechanism spike

The [topic subscription spike](../spikes/topic_subscriptions/README.md) tests
typed State inventory/applied snapshots, reliable Event requests/results,
mission/User precedence, three-family native CFT updates, WAN sender suppression
and a standalone graph. The current spike uses only receiver-owned native CFT
changes and automatic DDS propagation to writers, without administration or
direct writer configuration. The earlier administration exploration is historical,
not a dependency of this design. It also demonstrates rollback divergence,
unconfirmed UI state, producer churn, late-LAN-reader refresh and restart reset.

The expanded isolated gate passed continuous traffic with timestamped WAN
evidence during each update, a two-second outage, competing revisions,
every-family apply/rollback faults, native type bounds, typed WAN frame lifecycle,
source restart and receiver-WAN late joining. It repaired five spike-only
request/cache/UI defects; these were not demonstrated production defects.

Two additional observations matter for integration: ten old-policy reports
drained after a team change, and an early source-restart report after LAN matching
was missed before fresh WAN inventory established readiness. Neither local commit
nor LAN matching proves fleet-wide convergence. Refresh recovered late-WAN
inventory; session reset remains explicit fixture behavior, not persistence.

This is not production processor/bridge subscription integration, six-topic manual
selection, sustained fleet load/long-outage recovery or deployment approval.
A separate actual ACT six-topic production-default baseline passed forwarding,
compression and lifecycle checks, without testing manual selection. The
[spike README](../spikes/topic_subscriptions/README.md#expanded-qualification-and-spike-repairs)
records exact results, evidence and remaining boundaries.

The [transient-local follow-up](../spikes/topic_subscriptions/README.md#transient-local-wan-state-experiment-2026-11-25)
passed the full isolated gate and an unchanged volatile-mode rerun. It supports
last-value replay for new WAN readers, not automatic history retrieval after
an existing reader changes selection. No production QoS change or deployment
is implied by that experiment.
