# Routing Service processor: Event / State / Periodic

Date: 2026-11-13

Status: implementation plan only. No code change, deployment or new test gate
is implied by this document.

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

### Remaining decision before priority implementation

Choose State/High and State/Normal positions in the global ordering and the
initial ContactReport assignment. ContactReport was Event/Normal before this
split; its new State priority must be explicit, not silently inferred.

Strict priority does not guarantee health progress under an unbounded
Event/High load. Qualification must distinguish configured preference from
capacity and starvation guarantees.

## Current starting point

The current dev/custom router already selects Event / State / Telemetry in
three-topic mode. Its selected policy and qualification history are recorded in
[migration-plan sections 3.40-3.42](two-topic-wan-migration-plan.md).

The Routing Service variant still explicitly requires `two_topic_union`,
enumerates Event/Status, constructs WAN type names from physical family names,
and unconditionally selects `lan_status_1s` for Periodic-equivalent inputs.
It is a data-plane milestone, not complete health/monitoring parity.

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
- Resolve State priority placement before implementing the new scheduling map.
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
