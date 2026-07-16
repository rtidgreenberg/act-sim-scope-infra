# Configuration

> See [Thesis & Tenets](thesis-and-tenets.md) for the decisions this config model encodes.
> Key rules baked in below: **LAN endpoints adapt** to discovered app QoS (`auto`), **WAN
> endpoints impose** explicit aliases (Tenet 6); **WAN data topics carry no liveliness**
> (Tenet 4); **`dynamic_data` is the default forwarding mode**, `serialized_cdr` is opt-in and
> eligibility-gated (Tenet 7).

## QoS Model

QoS should be assigned primarily on the route **input** and **output** endpoints, not on the
route as a whole. Each endpoint either names a predefined QoS profile alias or uses `auto`
to match local applications.

The LAN/WAN split is deliberate (Tenet 6): on the **LAN** the router is a peer to the ACT apps,
so its endpoint QoS is **constrained by / derived from the discovered app writers** (RxO
compatibility) — the router adapts. On the **WAN** both endpoints are routers we control, so we
**impose** explicit QoS. App-dictated LAN policies (including liveliness) **terminate at the
relay** and are re-shaped, not propagated, across the link. WAN data-topic QoS sets liveliness
to `AUTOMATIC` with an effectively infinite lease (no per-topic liveliness across the link);
router/link presence is carried by the separate `RouterHealth` topic
(see [Presence & Health](presence-and-health.md)).

That mirrors the ACT design: commands/events are reliable and prioritized differently from
periodic status and detail status. Team traffic reuses the status-style QoS profile and
gets its team behavior from participant-level partition updates, not from a dedicated QoS
profile.

The router should support four QoS selection mechanisms:

1. **QoS profile alias**: named policies such as `platform_wan_udpv4_qos`, `wan_event`,
  `wan_status`, `wan_detail_status`, and LAN downsample aliases such as
  `lan_status_1hz`, each mapped directly to a concrete profile such as
  `WAN_QOS_LIB::event_qos`.
2. **Endpoint assignment**: `input.reader_qos` and `output.writer_qos` select the QoS for
  each leg of the route independently.
3. **Topic override**: a topic entry may override reader or writer QoS when one topic in
  the list is special.
4. **LAN auto-match**: ordinary LAN-side readers/writers use `auto`, meaning the router
  creates a compatible local endpoint from discovered endpoint QoS.

For the POC, keep the rule simple:

- WAN participants and WAN endpoints use explicit QoS profile aliases such as
  `platform_wan_udpv4_qos`, `wan_event`, and `wan_status`.
- LAN participants use the router's built-in participant defaults. LAN endpoints default to
  `reader_qos: auto` or `writer_qos: auto` and partition `*` when omitted.
- The only explicit LAN QoS aliases are downsample profiles, for example `lan_status_1hz`,
  used when the router intentionally samples local status before forwarding it.
- `control_lan` follows the same LAN rule as `platform_lan`: auto-match local app QoS.
- `control_wan` follows the same WAN rule as `platform_wan`: explicit aliases for command,
  event, and status traffic.
- Platform/control WAN routes set publisher and subscriber partitions independently. For
  example, platform command readers subscribe on `CONTROL`, while platform status writers
  publish on `PLATFORM`.
- `team_wan` owns a participant-level partition for discovery and group-level scoping —
  and per **D73 (2026-07-16)** it is the **only** team-scope mechanism: team route
  endpoints use the default partition, and the `inherit_participant` sentinel is
  **retired** (non-overlapping participant partitions already make the participants
  mutually invisible and suppress WAN endpoint discovery; a second endpoint-level gate
  added nothing). `SET_PARTICIPANT_PARTITION` updates the group in one place via
  participant `set_qos`. The example below predates D73 and its `inherit_participant`
  lines drop out when Phase 10 lands.
- **WAN participants are never multi-homed (D18).** When a node has multiple physical
  networks (e.g. mesh radio + SATCOM), the config defines **one WAN participant per unique
  network**, each pinned to its interface via the UDPv4 builtin transport
  `allow_interfaces_list` (a multi-homed participant would receive redundant DATA on every
  announced locator and defeat per-path link metrics). The network-definition YAML shape
  (name → interface allowlist) lands when a second network reaches the rig; today's
  single-network config is the `N = 1` case.
- DDS partition names are uppercase in config, commands, and status samples: `CONTROL`,
  `PLATFORM`, `PLATFORM_30`, `TEAM_A`.
- If auto-match cannot find a compatible discovered endpoint within a short startup window,
  fail the route loudly and publish a command/status error.

This keeps LAN app-facing routes terse, makes downsampling visible where it matters, and
preserves explicit control over constrained WAN behavior.

## YAML Route Config

The YAML is intentionally flatter than Routing Service XML. It should be easy to read in a
demo and easy for the control plane to patch.

Route configs name topics only. The router resolves each topic's type from discovered local
endpoints, loaded type XML, or generated type support; the route YAML should not repeat type
names.

Type resolution rule for topic-only routes:

- Discovery provides the topic name and the remote endpoint's registered type name. The
  router uses that type name internally when creating the output `Topic`.
- In Connext 7.7, TypeObject v2 plus on-demand TypeLookup is the target mechanism:
  discovery advertises small type-identifier hashes and Connext fetches the full type
  definition (once, then caches it) only when the router needs it.
- Once the router has a `DynamicType`, it can register/create the corresponding DynamicData
  topic on the WAN participant and write with that type.
- For `serialized_cdr` forwarding, the discovered/registered type is still needed for DDS
  topic creation and matching. The payload can remain serialized only when the input and
  output routes use compatible logical types and data representations.
- Static `act_types.xml` is still useful as a deterministic fallback for the ACT POC, but
  the architecture is pinned to Connext 7.7 wire-learned type resolution.

**Type discovery is enabled on the WAN (why it is now cheap).** The router creates the same
topics/types on both ends of the link, so in steady state discovery carries only the small
per-endpoint TypeIdentifier hashes — a full TypeObject crosses the WAN only when a peer that
has never seen the type joins, served once by the relay and then cached (full behavior and
validation in [Connext Investigation Review](connext-investigation-review.md#wan-type-exchange-behavior-typeobject-v2)).
Config implications:

- The WAN participant QoS must set `resource_limits.type_object_max_serialized_length` to
  `LENGTH_AUTO` (not `0` — `0` disables TypeObject v2 entirely) and keep
  `TYPE_LOOKUP_SERVICE_CHANNEL` in `discovery_config.enabled_builtin_channels`. The relay
  ships this in **its own** WAN QoS library rather than editing ACT: the ACT submodule's
  `wan_qos_lib.xml` (which sets the value to `0`) is left untouched for now, and reconciling
  it is deferred to a single later pass. The relay QoS library takes precedence for the
  relay's own participants.
- Leave `type_code_max_serialized_length = 0` (legacy TypeCode, not used by v2).
- Do **not** set `request_types_filter` to `*` on the WAN participant — the router is a type
  *source*, not a learner, so proactive fetching is unnecessary and only adds WAN traffic.
- The builtin TypeLookup replier endpoints must be reachable inbound across the WAN
  transport/NAT/firewall so remote peers that lack a type can resolve it from the relay.
- Keep both ends' type definitions synchronized (shared IDL, same extensibility and
  `rtiddsgen` version). If the structural equivalence hashes drift, Connext silently falls
  back to fetching full TypeObjects per affected topic — the WAN cost this design avoids.

Use two config sets:

- **control-platform YAML**: loaded by the `control-platform` router instance on the control
  node and on each platform node. It defines each logical control/platform route once. The
  local `node.role` chooses whether this process runs the route's source side or
  destination side.
- **platform-team YAML**: loaded by the `platform-team` router instance on platform nodes.
  It defines concrete platform-to-platform team routes through `team_wan` and uses
  `team_wan.participant_partition` for discovery and group scoping.

Shared QoS aliases stay in the config set. WAN participants and WAN route legs are explicit.
LAN participants use built-in defaults plus auto-match behavior, so the YAML only names LAN
QoS when it is intentionally changing the app-facing traffic shape, such as `lan_status_1hz`.

```yaml
qos_profiles:
  control_wan_udpv4_qos: WAN_QOS_LIB::control_participant_udpv4_qos
  platform_wan_udpv4_qos: WAN_QOS_LIB::platform_participant_udpv4_qos
  wan_event: WAN_QOS_LIB::event_qos
  wan_status: WAN_QOS_LIB::status_qos
  lan_status_1hz: LAN_QOS_LIB::status_1hz_qos
```

## Control/Platform Config Example

```yaml
node:
  name: Platform_30
  role: platform   # control or platform

router:
  id: 30
  name: platform-30-control-platform
  config_set: control-platform
  default_forwarding_mode: dynamic_data

control:
  domain: 100
  command_topic: ActRouterCommand
  status_topic: ActRouterStatus
  target_node: Platform_30
  target_router: platform-30-control-platform

types:
  xml: harness/act/node_sim/datamodel/act_types.xml

qos_libraries:
  - harness/act/config/qos/lan_qos_lib.xml
  - harness/act/config/qos/wan_qos_lib.xml
  - relay/qos_isc.xml

participants:
  control_lan:
    role: control
    domain: 20
  control_wan:
    role: control
    domain: 200
    qos: control_wan_udpv4_qos
  platform_lan:
    role: platform
    domain: 30
  platform_wan:
    role: platform
    domain: 200
    qos: platform_wan_udpv4_qos

routes:
  - name: control_command
    enabled: true
    forwarding_mode: dynamic_data
    source: control
    destination: platform
    topics:
      - name: ControlCommand
    source_side:
      input:
        participant: control_lan
      output:
        participant: control_wan
        writer_qos: wan_event
        publisher_partition: CONTROL
    destination_side:
      input:
        participant: platform_wan
        reader_qos: wan_event
        subscriber_partition: CONTROL
        filter:
          expression: "msg.destination = %0"
          parameters: ["${node.name}"]
      output:
        participant: platform_lan

  - name: platform_primary_status
    enabled: true
    forwarding_mode: dynamic_data
    source: platform
    destination: control
    topics:
      - name: PlatformStatus
    source_side:
      input:
        participant: platform_lan
        reader_qos: lan_status_1hz
      output:
        participant: platform_wan
        writer_qos: wan_status
        publisher_partition: PLATFORM
    destination_side:
      input:
        participant: control_wan
        reader_qos: wan_status
        subscriber_partition: PLATFORM
      output:
        participant: control_lan

  - name: platform_detail_status
    enabled: false
    forwarding_mode: dynamic_data
    source: platform
    destination: control
    topics:
      - name: PlatformDetailStatus
    source_side:
      input:
        participant: platform_lan
      output:
        participant: platform_wan
        writer_qos: wan_status
        publisher_partition: PLATFORM
    destination_side:
      input:
        participant: control_wan
        reader_qos: wan_status
        subscriber_partition: PLATFORM
      output:
        participant: control_lan

  - name: platform_events
    enabled: true
    forwarding_mode: dynamic_data
    source: platform
    destination: control
    topics:
      - name: PlatformCommandAck
      - name: ContactReport
    source_side:
      input:
        participant: platform_lan
      output:
        participant: platform_wan
        writer_qos: wan_event
        publisher_partition: PLATFORM
    destination_side:
      input:
        participant: control_wan
        reader_qos: wan_event
        subscriber_partition: PLATFORM
      output:
        participant: control_lan
```

For a role-aware route, the router compares `node.role` with `source` and `destination`. If
the local role is `source`, it selects `source_side` as this router instance's active side.
If the local role is `destination`, it selects `destination_side`. For example,
`control_command` becomes `control_lan -> control_wan` on the control router and
`platform_wan -> platform_lan` on a platform router. This lets one control/platform YAML
define each logical route once while preserving correct direction on both sides. Disabled
configured routes, such as `platform_detail_status`, are still selected and reported in
`RouterStatus`; they just do not create readers/writers until enabled and discovered. The
concrete detail-status topic name can come from the ACT route override or be filled in
before the POC run.

## Platform/Team Config Example

```yaml
node:
  name: Platform_30
  role: platform

router:
  id: 30
  name: platform-30-team
  config_set: platform-team
  default_forwarding_mode: dynamic_data

participants:
  platform_lan:
    role: platform
    domain: 30
  team_wan:
    role: platform
    domain: 200
    qos: platform_wan_udpv4_qos
    participant_partition: PLATFORM_30

routes:
  - name: platform_team_to_wan
    enabled: true
    forwarding_mode: dynamic_data
    input:
      participant: platform_lan
    output:
      participant: team_wan
      writer_qos: wan_status
      publisher_partition: inherit_participant
    topics:
      - name: PlatformData

  - name: wan_team_to_platform
    enabled: true
    forwarding_mode: dynamic_data
    input:
      participant: team_wan
      reader_qos: wan_status
      subscriber_partition: inherit_participant
    output:
      participant: platform_lan
    topics:
      - name: PlatformData
```

For `input` endpoints, `subscriber_partition` configures the route reader's Subscriber
Partition QoS. For `output` endpoints, `publisher_partition` configures the route writer's
Publisher Partition QoS. Omitted LAN endpoint QoS means `auto`, and omitted LAN endpoint
partition means `*`. ~~Team routes use `inherit_participant` to resolve both endpoint
partitions from the current `team_wan.participant_partition` value.~~ **Retired (D73):**
team scope is carried by `team_wan.participant_partition` alone; team route endpoints use
the default partition, and unknown partition sentinels are a parse error once Phase 10
lands.

## Config Validation Rules

Fail the process at startup for structural config errors:

- duplicate participant role names or duplicate route names;
- route references to unknown participant roles or unknown QoS aliases;
- route with no topics;
- role-aware route where local `node.role` matches neither `source` nor `destination`, unless
  the route is explicitly marked external to this router instance;
- explicit WAN endpoint without partition where the route type requires `CONTROL`,
  `PLATFORM`, or a team participant partition;
- lifecycle mirroring enabled without `dynamic_data` or `generated_type` mode and no key
  recovery strategy;
- `serialized_cdr` forwarding mode combined with a **content filter** on the route (the filter
  needs field access; only safe if writer-side filtering is guaranteed — reject otherwise);
- `serialized_cdr` forwarding mode combined with lifecycle/meta-mirroring (needs key + field
  access).

Forwarding-mode default: when `forwarding_mode` is omitted, the route uses `dynamic_data`.
`serialized_cdr` must be requested explicitly and passes the eligibility checks above (Tenet 7).

Treat runtime discovery/type/QoS misses as route errors or waiting states, not process-fatal
errors. The router should stay alive and publish status for the failed route.
