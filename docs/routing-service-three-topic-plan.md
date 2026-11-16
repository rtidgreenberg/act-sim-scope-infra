# Routing Service processor: Event / State / Periodic

Date: 2026-11-13

Latest checkpoint (2026-11-14): native XML-only ACT bindings and monitoring are
deployed on the owned six-platform mesh; **5/5 live browser workflows passed**.
[Migration checkpoint 3.51](two-topic-wan-migration-plan.md#351-xml-only-processor-deployed-and-mesh-qualified-2026-11-14)
records the current evidence and the remaining release boundaries.

Current entry point: [processor architecture and development plan](routing-service-processor-architecture-and-plan.md).
Next agreed direction: [platform mission-state receive filters](platform-mission-cft-plan.md).
Whole-platform restart starts in Init with minimum C2 receive interests;
local mission-state changes update platform CFTs, not routes or outgoing reports.
That focused policy is planned, not implemented by the baseline above.

Status: three-topic data-plane implementation is qualified in this checkout.
The physical topic/type mapping and approved six-class priority configuration
are implemented; 36 acceptance tests across both modes and 18
configuration/launcher checks passed in an isolated Connext 7.7.0 environment.
Both documented role renders passed the installed Routing Service XSD.
XML-only configuration and shared-endpoint production health/control integration
are now implemented for the fixed ACT catalog. See the
[processor README](../router/routing_service/README.md) for executable native
properties and launch commands. The phased text below retains the original
acceptance plan; full mesh qualification and wider release gates must be recorded
separately. Live deployment requires lifecycle ownership and explicit authorization.

## Agreed target

Modify the parallel [Routing Service processor](../router/routing_service/README.md)
in this repository before moving it into the destination submodule.

Keep one always-running multi-input/multi-output TopicRoute per node. Routing
Service owns the static DDS endpoints; the processor owns conversion, audience
selection and application-specific behavior.

| Physical WAN topic | Initial contents | Delivery policy |
| --- | --- | --- |
| Event | Commands, ACKs, assignments and reliable router configuration | Reliable KEEP_ALL |
| State | ContactReport | Reliable writer KEEP_LAST(1), reader KEEP_ALL, matching the current dev baseline |
| Periodic | The current Status/Telemetry contents, including health | Best effort KEEP_LAST(1) |

Each node has exactly one WAN writer/reader pair per family, including State
when no local contact binding is active. State retains latest updates per full
DDS instance, not one sample for the whole topic. It does not promise every
contact observation or every intermediate lifecycle transition.

The only naming difference from the dev router's semantic three-topic contract
is Telemetry -> Periodic. This does not imply a new periodic publishing loop,
payload model or cadence. Original application LAN topics/types remain unchanged.

Preserve the current envelope identity, authoritative source restoration,
whole-structure copying, native lifecycle handling, source alias deduplication,
feedback protection and audience rules. Do not add a generic telemetry schema,
Persistence Service, journals, normal remote administration or processor
execution-priority scheduling.

## Confirmed configuration choices

- Deployment uses Routing Service XML only, including processor properties.
  Use qualified XML QoS profile references directly; do not carry the prototype's
  YAML QoS-alias lookup into the deployed configuration.
- Express deployment-specific variation through XML configuration variables
  with defaults and values supplied at startup. Do not add a separate runtime
  configuration source or imply live QoS retuning; normal application/team
  commands remain distinct from startup configuration.
- The deployed processor and launcher must not load YAML or invoke the YAML
  configuration helper. The launcher accepts XML directly and retains generic
  owned-process lifecycle handling. YAML rendering may remain a development-only
  convenience, not a deployment dependency or second configuration authority.
- Keep the processor's shared asynchronous flow controller and High/Normal
  channel approach; do not copy the dev router's choice to leave Telemetry
  outside the Event/State controller.
- Preserve the established relative order:
  Event/High > Periodic/High > Event/Normal > Periodic/Normal.
- RouterHealth remains Periodic/High.
- Keep the nominal 2 Mbps shared budget as the starting configuration.
  Treat it as a system-level DDS flow-controller budget, not radio capacity.
- Make time-based filtering optional on original-topic Periodic LAN input
  readers. It is **off by default** and enabled only for explicitly selected
  original topics, using a supported XML-authored interval such as one second.
  This supersedes the earlier one-second-by-default decision.
- Input filtering limits acceptance per keyed instance. It neither generates
  periodic snapshots nor throttles the WAN reader. Event/State lifecycle and
  command paths must not accidentally inherit the Periodic input policy.
  Reject time-filter configuration for Event or State inputs explicitly; do
  not apply a route-wide or participant-wide sampling default.

## Release scope: v1 manual bundle, v2 setup/test app

The user confirmed this split on 2026-11-13:

| Release | Deliverable | Mapping/setup responsibility |
| --- | --- | --- |
| V1 | Explicit type/union XML, manual QoS and binding XML, per-node environment files, tested plugin, start/stop scripts and guide | Define and validate the configured deployment now; discovery-driven setup is deferred |
| V2 | Setup/test app producing the same bundle | Discovery or XML import, catalog/field-tree editor, schema/config generation and isolated route tests |

The latest user decision simplifies v1 to a union containing the **full original
type**, not selected fields/subtrees. XML describes the LAN topic/type, logical channel,
branch and generic identity/routing metadata. Preserve all nested structures,
sequences, arrays and keys, and restore the same complete type on receive,
including existing agreed authoritative source restoration. Field selection,
renaming, partial reconstruction and default rules are deferred.
Configurable full-type bindings are not limited to the current ACT catalog.

Both ends receive the same prepared union/type and mapping contract before
startup; each node's local bindings may differ. Schemas/mappings are static for
the run. XML-only configuration is implemented, but this configurable full-type
registry remains implementation work: adding XML files cannot bypass the current fixed catalog.
Define and test the XML mapping contract before building a GUI over it.

The latest decision is to define types, full-type union branches, bindings and
QoS explicitly now, then add discovery/capture and setup generation later.
The preparation helper is not a v1 prerequisite. Router runtime must run
without that helper, YAML, the YAML helper, a setup app or an
automated operator ping/pong tool. Use explicit qualified XML QoS profiles and
safe manual verification; engineering acceptance tests remain mandatory.
V2 automates authoring/testing, not a different forwarding runtime or a second
configuration authority. Existing health/monitoring/parity gates below remain
v1 obligations; a new setup GUI must not be confused with retained dashboard
status/configuration integration.

The user also confirmed native grouped properties, identical LAN topic names
and full types at both ends, one existing string instance-ID field, and
preservation of existing control/addressed/team/global rules. Keep LAN QoS
references on the native port declarations. Start qualification with the
reference workload and agree measured release limits before shipping.

See the [release/setup procedure](routing-service-c2-platform-setup.md#agreed-release-split)
for bundle contents and the staged implementation order.

### XML mapping reuse investigation

Mappings must be XML-authored. Follow Routing Service's transformation property
conventions rather than adding a separate runtime mapping language. The shipped
7.7.0 Assign library and example were inspected: its property convention is
destination member path -> source member path. The
[7.7 tutorials](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/services/routing_service/tutorials.html#example-changing-some-values-in-data)
limit Assign to primitive fields, including strings/nested primitive leaves,
not arrays/sequences. V1 therefore reuses the processor's whole-value union
copy pattern, not Assign or a new general field-mapping engine.

Reuse the standard property framework and existing copy helpers; retain
custom envelope/union, identity, audience and lifecycle behavior. The user
selected extending the existing processor, with XML syntax/conventions, rather
than adding a Transformation plugin. The
[processor-only XML mapping plan](routing-service-xml-mapping-plan.md) records
the native copy research, proposed full-type binding properties, affected
surfaces and staged gates. Four entity-free whole-member copy cases passed;
XML-only startup is implemented for ACT; arbitrary full-type registry integration
remains unfinished.

### Approved priority order (2026-11-13)

For the planned XML bindings, call the user-facing selection **channel**:
`event_high`, `periodic_high`, `state_high`, `event_normal`, `state_normal`,
`periodic_normal`. Each value determines both the WAN family and High/Normal
priority; users do not configure those independently. These are six logical
scheduling channels on the same three physical topics, not additional endpoints
or payload branches. The current prototype's YAML settings are unchanged.

The user selected Event/High > Periodic/High > State/High > Event/Normal >
State/Normal > Periodic/Normal, with ContactReport initially State/Normal.
The new profiles use sample priorities 600, 500, 400, 300, 200 and 100
respectively, sharing the existing `act_rs` controller. The retained two-topic
mode keeps its previous priorities and profiles.

Strict priority does not guarantee health progress under an unbounded
Event/High load. Qualification must distinguish configured preference from
capacity and starvation guarantees.

## Current starting point

The current dev/custom router already selects Event / State / Telemetry in
three-topic mode. Its selected policy and qualification history are recorded in
[migration-plan sections 3.40-3.42](two-topic-wan-migration-plan.md).

The Routing Service variant now also accepts `three_topic_union` and enumerates
Event/State/Periodic with explicit WanEvent/WanEvent/WanStatus type selection.
It still supports the earlier two-topic mode, reads YAML and unconditionally
selects `lan_status_1s` for Periodic-equivalent inputs. This is a data-plane
milestone, not XML-only deployment or complete health/monitoring parity.

The shared [codec catalog](../router/src/core/WanCodec.cxx) already separates
physical selection from payload types: State carries the ContactReport branch
of WanEvent; Telemetry currently carries WanStatus. Reuse that separation.
There is no need to invent WanState or WanPeriodic solely to rename topics.
The current catalog also includes RouterConfig; do not lose that newer branch
when porting the older processor.

The live mesh currently runs the custom router, not this processor. Keep it
immutable while developing and qualifying the replacement.

## Implementation sequence

### 1. Pin the contract and mapping

- Record the current dev mapping, QoS, lifecycle policy, health/config behavior
  and relevant test expectations as the comparison baseline.
- Use the approved State priority placement recorded above.
- Define the three-topic processor selection explicitly. Preserve existing
  legacy and two-topic behavior rather than globally replacing strings.
- Use a physical-family mapping that produces Event / State / Periodic while
  retaining the existing catalog discriminators, envelope types and schema.
- Reject unsupported family/branch combinations and unknown configuration.

Gate: mapping and configuration tests cover every catalog entry and clearly
distinguish physical topic names from union type/branch names.

### 2. Wire all static endpoints and filters

- Update [GatewayModel](../router/routing_service/GatewayModel.cxx),
  [ConfigMain](../router/routing_service/ConfigMain.cxx),
  the [renderer](../router/routing_service/run.py) and
  [processor](../router/routing_service/GatewayProcessor.cxx) together.
- Replace hard-coded two-family loops and `Wan` + physical-name type lookup.
- Replace runtime YAML model loading with validated processor properties supplied
  by Routing Service XML, retaining the shared codec/catalog and audience helpers
  without depending on the custom router's YAML parser. Define each physical
  input/output once and keep logical audience selection separate.
- Add the v1 XML full-type binding contract. Reuse whole-value union packing and
  unpacking; reject branch/type mismatches, missing required keys and invalid
  identity/routing metadata before forwarding. Qualify complete original types
  containing nested structures, sequences and arrays without a selected-field
  engine, the v2 app or silent fallback to the ACT catalog.
- State accepts only ContactReport; Event excludes it; Periodic retains the
  existing periodic/health interests and source suppression.
- Keep one intended logical output, two-sided local endpoint isolation and
  unchanged application LAN restoration.
- Preserve lifecycle key recovery while input loans remain valid.
- Wire live team/scope changes into outgoing audience metadata and receive
  interests without endpoint restarts; preserve rejected-update rollback and
  safe callback/lifecycle ownership.

Gate: real three-node Routing Service tests discover exactly three WAN
writer/reader pairs per node and verify whole-payload LAN equality, wrong-family
rejection, recipient filtering and no forwarding loops.

### 3. Author QoS and apply priorities

- Author all fixed policies in the existing external
  [QoS library](../harness_v2/qos/act_qos_profiles.xml).
- Use independent Event and State retention policies; do not apply State's
  latest-value policy to Event commands.
- Extend the existing two-band channels to the State writer. Attach all three
  asynchronous writers to the one selected flow controller.
- Stamp the agreed per-original-topic priority on data, dispose and unregister.
- Preserve the selected dev baseline's other relevant reliable-reader/repair
  settings; identify any intentional deviation instead of importing experimental
  candidates implicitly.

Gate: native effective-QoS assertions; known unsent backlogs proving the exact
global class ordering and within-class FIFO; real processor command/ACK progress
under contact and Periodic traffic. Validate XML with installed Connext and
fresh-build the processor.

### 4. Make Periodic input filtering configurable

- Replace unconditional `lan_status_1s` selection with validated per-original-topic
  input choices, backed by external XML profiles.
- Default every input to no time filtering. Enable it only on an explicitly
  selected Periodic original-topic input; support turning it off again and
  reject unsupported intervals rather than silently approximating them.
- Reject Event/State time-filter settings. A selected Periodic input must not
  change any other input or the independently generated health cadence.
- Do not use a hidden WAN filter or a producer-mode command to implement it.
- Keep existing producers and their publishing loops unchanged.

Gate: actual processor tests prove unconfigured inputs remain unfiltered,
compare explicitly selected intervals across multiple keys/report types, and
reject Event/State filter settings. Verify every Event update and rapid
State updates/lifecycle reach the processor without time-filter suppression,
while retaining State's separately specified latest-value WAN semantics.
Check interval/deadline consistency for each supported profile.

### 5. Integrate retained-scope health and monitoring

- Reuse current dev compact health, presence/restart handling and reliable
  RouterConfig behavior through service-owned shared endpoints.
- Maintain health publication cadence independently of optional LAN input
  sampling. Do not create an extra health WAN endpoint.
- Publish compatible existing LAN status, mesh and link-statistics topics.
  Collect processor per-original-topic forwarding counts separately from
  shared-endpoint protocol facts.
- Add a cumulative output-write timeout counter per original topic to compatible
  LAN status, resetting on processor restart. Count actual write timeouts, not
  other forwarding errors. No new timeout timestamp or timeout-driven health
  policy is required for now.
- Leave queuing, blocking limits, flow control and reliable delivery to Connext;
  do not add a processor retry queue.
- Add explicit physical-family and configured-priority fields to topic status
  as already requested. Preserve existing fields/topic names; coordinate any
  necessary IDL/XML regeneration and dependent rebuilds.
- Keep the existing web-server/API contract wherever possible. Inventory the
  specific bridge/UI additions needed to expose new status fields rather than
  claiming they render automatically. Journals remain out of scope.

Gate: unchanged existing consumers receive truthful status, presence and link
information, with Event/State/Periodic names. Per-branch status must not invent
independent physical match counts. Timeout-counter tests distinguish write
timeouts from successful writes and other errors.

### 6. Expose system configuration

- Show configured/effective DDS budget, units and restart requirements in the
  Config screen, separately from radio/link bandwidth.
- Expose the selected family, priority and Periodic input-filter choices.
- Keep one validated source of configuration; avoid UI-only copies.
- Add an environment override later through the same resolver, with explicit
  precedence. Do not introduce live retuning by editing running generated XML.

Gate: configuration round-trip/error tests and browser checks of the specific
new controls and labels.

### 7. Qualify, then deploy only with authorization

- Verify XML-only startup with no YAML configuration present and no YAML helper
  invocation, including processor mappings and explicit QoS profile references.
- Install and run the complete v1 bundle without development/build tools or the
  v2 app. Verify documented environment substitutions, type/mapping compatibility,
  manual safe connectivity checks and rollback to the previous complete bundle.
- Keep launcher shutdown bounded: allow ten seconds after the first stop signal,
  then report and force termination of the owned process group if needed.
  Verify graceful exit, stuck-child cleanup and signal-handler restoration
  without DDS or live-mesh changes.
- Fresh isolated build and existing C++ regressions.
- Extend the Routing Service tests to three-topic mappings, priorities and
  input-filter choices; preserve retained application-behavior assertions.
- Test repeated updates, dispose/unregister/revive, writer/router restart,
  multiple producers, unseen keys and late observers against the dev contract.
  Distinguish final-state convergence from delivery of every update.
- Measure bounded-rate overload and outage recovery, command/ACK latency,
  health freshness, queue growth and memory. Do not relabel old failed capacity
  gates as passed because the transport split changed.
- Run unchanged application mission workflows and targeted dashboard tests.
  Explicitly account for retired route-management/mode/journal behavior.
- Publish the measured results and remaining gaps. Historical test counts are
  not a current gate.
- Only then perform an authorized, owned all-node stop/build/up cutover, with
  rollback to a compatible configuration/binary set. Mixed Telemetry/Periodic
  participants will not exchange that topic.
- Migrate into the destination submodule only after retained-scope parity.

## Completion criteria

Three-topic forwarding alone is one milestone. Full retained-scope parity also
requires health, monitoring compatibility, live team/scope updates,
configuration integration and the recovery/live-mission gates. No live
deployment, permanent compatibility topic or scope expansion is authorized by
this plan.
