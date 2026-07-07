# Configuration

## QoS Model

QoS should be assigned primarily on the route **input** and **output** endpoints, not on the
route as a whole. Each endpoint either names a predefined QoS profile alias or uses `auto`
to match local applications.

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
- `team_wan` owns a participant-level partition for discovery and group-level scoping.
  Team route endpoints use `inherit_participant` so `SET_PARTICIPANT_PARTITION` updates the
  group in one place.
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
  discovery advertises type identifiers and Connext can fetch the full type definition when
  the router needs it.
- Once the router has a `DynamicType`, it can register/create the corresponding DynamicData
  topic on the WAN participant and write with that type.
- For `serialized_cdr` forwarding, the discovered/registered type is still needed for DDS
  topic creation and matching. The payload can remain serialized only when the input and
  output routes use compatible logical types and data representations.
- Static `act_types.xml` is still useful as a deterministic fallback for the ACT POC, but
  the architecture is pinned to Connext 7.7 wire-learned type resolution.

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
  default_forwarding_mode: serialized_cdr

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
    forwarding_mode: serialized_cdr
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
    forwarding_mode: serialized_cdr
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
    forwarding_mode: serialized_cdr
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
    forwarding_mode: serialized_cdr
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
  default_forwarding_mode: serialized_cdr

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
    forwarding_mode: serialized_cdr
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
    forwarding_mode: serialized_cdr
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
partition means `*`. Team routes use `inherit_participant` to resolve both endpoint
partitions from the current `team_wan.participant_partition` value.

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
  recovery strategy.

Treat runtime discovery/type/QoS misses as route errors or waiting states, not process-fatal
errors. The router should stay alive and publish status for the failed route.
