# C2-to-platform router setup: current procedure and simplified target

Date: 2026-11-13.

Latest qualification: **2026-11-14**. XML-only ACT-catalog startup, shared
production monitoring and the six-platform processor mesh are implemented.
All five live browser workflows passed, with explicit always-on reporting
assertions. See [migration checkpoint 3.51](two-topic-wan-migration-plan.md#351-xml-only-processor-deployed-and-mesh-qualified-2026-11-14)
for exact gates, retained runtime ownership and remaining generic-registry limits.

The subsequent [mission receive-policy increment](platform-mission-cft-plan.md)
renames the LAN topics to Mission, Team and PlatformStatus, without renaming
their payload types. Production platforms start with Mission/Team only, then
select fleet navigation positions and same-team mission/contact data while active.
That increment is now reference-mesh-qualified; see
[checkpoint 3.52](two-topic-wan-migration-plan.md#352-platform-mission-cfts-deployed-and-qualified-2026-11-16).
The checkpoint above records the preceding gate.

## Agreed release split

### V1: prepared XML bundle and manual setup

Ship a configured C2/platform pair, not a setup application:

- The tested processor plugin, supported Routing Service/runtime versions and
  installation/license requirements.
- Shared original/WAN type XML, external QoS profiles and per-node Routing
  Service XML, with full-type bindings supplied through XML processor properties.
- Per-node environment files, documented configuration-variable defaults and
  start/stop scripts with bounded owned-process cleanup.
- A route/mapping reference, step-by-step installation and manual connectivity
  checks, plus a manifest identifying the matching schema/configuration set.

The user simplified v1 to **a union branch containing the full original type**.
XML binds an original LAN topic/type to a logical channel/union branch and assigns generic
instance identity/routing metadata. Copy the complete DynamicData value,
including all nested structures, sequences, arrays, keys and optional fields,
then restore that same full type on receive. Preserve the existing agreed
authoritative source restoration. Payload field selection, renaming, partial
reconstruction, default rules and arbitrary transformations are out of v1.
The channel is one of `event_high`, `event_normal`, `state_high`, `state_normal`,
`periodic_high` or `periodic_normal`; it determines the physical WAN topic and
scheduling priority together. It does not create another topic or endpoint.

Keep source identity authoritative from the configured originating node.
Validate branch/full-type equality, required keys and metadata paths before
forwarding. V1 selects one existing string instance-ID field; no numeric or
composite encoding. All LAN keys must remain recoverable for key-only lifecycle
samples, including approved source restoration. Reject unsupported key layouts.

The latest user decision is **define everything now; discover and set it up
later**. V1 explicitly supplies the application type definitions, shared full-type
union/type XML, bindings and QoS, distributing the matching contract to both ends.
Reuse existing IDL/XML definitions where available.
Bindings and schemas stay fixed for the run; incompatible changes require a
coordinated stop/start and, if required, a matching plugin rebuild.
Discovery capture/export and automatic union generation are deferred, not v1
prerequisites or available commands.
No mapping GUI or automatic ping/pong app is required for v1.
Engineering qualification and safe manual delivery checks are still required.

**The generic type registry is a release target, not a current capability.**
XML-only startup and explicit full-type bindings now work for the fixed ACT
catalog; arbitrary new types still require codec changes. Health/monitoring and other retained-scope gates in the
[processor plan](routing-service-three-topic-plan.md) are not waived.

#### XML mappings and reuse of RTI transformations

Mappings belong in XML, following Routing Service's transformation/property
conventions where practical. The shipped Assign transformation uses destination
member paths as property names and source member paths as values. Its 7.7.0
documentation supports primitive fields, including strings and nested primitive
leaves, but explicitly excludes arrays/sequences; it is not a whole-structure
or collection copier.

The installed 7.7.0 SDK contains `librtirsassigntransf.so` and the matching
`shapes/topic_bridge_w_transf2.xml` example. The example verifies the XML
registration and destination/source convention; the
[7.7 transformation tutorials](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/services/routing_service/tutorials.html#example-changing-some-values-in-data)
state the primitive-only restriction. This investigation did not run native
mapping/collection tests.

Reuse the standard extension/property framework and existing validated
DynamicData copy helpers, rather than inventing a separate configuration
language. V1 reuses whole-value copying, envelope/union selection and full-type
restoration; it does not need a selected-field mapping engine. The current
processor remains responsible for routing, keys, audiences and lifecycle.
The selected v1 direction is to extend that processor with XML-authored
mappings, not add another Transformation plugin. Native integration still
requires qualification; the stock Assign library is not a drop-in replacement.
See the [processor-only XML mapping plan](routing-service-xml-mapping-plan.md)
for the research, proposed grammar, implementation phases and acceptance gates.

Do not add unrecognized mapping tags to Routing Service XML or claim that a
proposed custom property schema already works. Define/validate the XML mapping
contract and resulting processor-visible types before freezing v1 packaging.

### V2: setup and test application

Add a Python-backed setup/test application that produces and validates the
**same v1 deployment bundle**, rather than a second runtime configuration format.
It adds discovery/capture, XML import and bundle generation, captures endpoint QoS,
and presents the field tree
for mapping, prepares shared WAN schemas and per-node configuration, and tests
the actual configured routes using owned isolated endpoints.

The v1 full-type binding contract must not depend on the app existing. V2
automates its authoring and testing. Selected-field/subtree mapping is deferred
optional future work, not a requirement for the initial setup/test app.
Remote provisioning and GUI design remain implementation decisions.

## V2 recommendation

Give the user one setup application with this workflow:

**Connect to both LANs -> scan or import XML -> select full types and recipients ->
generate a deployment bundle -> validate it in isolation -> install and start.**

Use Python for discovery, validation, configuration generation and owned-process
testing. A web GUI should consume the saved type catalog and call the same
Python engine. Do not build separate GUI and command-line mapping implementations.

The sections below distinguish current capability from that target:

- [The current executable procedure](#current-executable-procedure) connects the
  existing ACT application types using the Routing Service data-plane prototype.
- [The simplified target](#simplified-target-user-workflow) describes the setup
  v2 assistant to build. The headless capture/export/union generation engine,
  editor and standalone ping/pong app are **not implemented** and are deferred.
  V1 explicitly supplies types, union branches, bindings and QoS profiles.

## What exists today

| Surface | Current implementation | Consequence for setup |
| --- | --- | --- |
| Custom router | Event / State / Telemetry, schema 3 | Retained as the default backend and rollback option; inspect actual lifecycle ownership before replacing a mesh. |
| Routing Service processor | Opt-in Event / State / Periodic; retained Event / Status mode; fixed application catalog, whole-structure copying | Event and State reuse WanEvent; Periodic reuses WanStatus. Shared-endpoint health, RTT and link statistics are available. |
| Routing Service generator/launcher | Development YAML preparation or direct `--xml` launch | Runtime reads explicit XML properties, not YAML. Retain referenced type/QoS/plugin assets at valid paths. |
| Three-topic Routing Service mesh | Owned lifecycle accepts `--router-backend routing_service --wan-contract three_topic_union` | Uses Periodic consistently; reports are always on and obsolete status-mode changes reject explicitly. |
| Type discovery | Existing custom-router discovery and a small [type-discovery spike](../spikes/type_discovery/README.md) | These are building blocks, not a complete catalog-exporting setup scanner. |
| Mapping | Exact whole-payload catalog in [WanCodec](../router/src/core/WanCodec.cxx) | Arbitrary new types and selected-field mappings do not work just by adding YAML. |
| LAN QoS in this processor | Explicit profile selection; renderer sets `auto_configure_qos` to false | Do not assume discovered application QoS is automatically applied. |
| Dashboard Config tab | Read-only configuration inspection | It is not the proposed scan-and-map editor. |
| Health and monitoring | Processor parity remains incomplete | Service startup is not proof of mesh/dashboard compatibility or full router readiness. |

### QoS correction and investigation gate

The installed Connext 7.7.0 `resource/schema/rti_routing_service.xsd` includes
`auto_configure_qos` for route ports and AutoTopicRoute endpoints. Our
[renderer](../router/routing_service/run.py) explicitly disables it.
The earlier conclusion that no such setting exists was too broad.

The [2026-11-16 native qualification](../router/routing_service/README.md#native-lan-auto-qos-qualification)
now proves reliability/durability inference and exact-payload forwarding with
our custom processor under `ON_DOMAIN_MATCH`. Finite deadline requirements
needed explicit configuration, and policy-specific profile interactions were
observed. Enabled `IMMEDIATE` cases did not create the LAN endpoint within
10 seconds. With discovery-triggered creation, an absent configured LAN input
prevented the shared processor route from starting and forwarding other data.
The renderer remains unchanged pending resolution of that startup barrier.
Conflicting endpoints, replacement and complete policy coverage remain open;
the procedure below retains the processor's explicit XML profiles.
See [LP-9](product-gaps.md#-lp-9--routing-service-discovery-driven-qos-selection-needs-qualification).

### Confirmed v1 binding choices

Use native Routing Service properties grouped per binding, with logical fields
`channel`, `topic_name` and `instance_id`. The last names one existing string
field, not a literal identifier or a numeric/composite encoding. Keep the same
LAN topic name/full type at both ends and preserve existing recipient rules.
Reference manually authored reader/writer QoS directly on LAN port declarations;
shared WAN profiles are determined by channel family.

Start with the current reference workload, then agree measured release limits
before shipping. Discovery supplies type definitions, not automatic LAN QoS
selection or new routing semantics.

## Current executable procedure

This is the smallest supported **Routing Service data-plane** setup for the
current ACT types. It is not the future arbitrary-schema mapping workflow.

After the 2026-11-16 review fixes, regenerate prepared XML before using the new
plugin. LAN DDS participant names identify the node, router and declared
LAN participant (`node/router/participant`, with a deterministic SHA-256 form
when the full name exceeds 255 bytes); WAN names retain `node/router`.
Preflight checks XML port references, domain and identity, and native startup
checks the actual reader/writer participant. Old prepared LAN identities are
rejected rather than silently accepting a port on the wrong participant.
Do not edit generated XML or replace binaries beneath a running mesh.

### 0. Choose an isolated deployment and a minimal contract

Use two dedicated test hosts/VMs, with each DDS application co-located with its
router. Use a directly routed IPv4 test network without NAT for the first run.
This procedure uses the normal non-EMANE profiles.

| Item | C2 host | Platform host |
| --- | --- | --- |
| Example WAN address | `10.77.0.10` | `10.77.0.30` |
| Node name | `Control_20` | `Platform_30` |
| Role | `control` | `platform` |
| Local application DDS domain | 20 | 30 |
| Shared WAN DDS domain | 200 | 200 |
| Initial routes | Mission/Team out; platform reports in | Mission/Team in; platform reports out |

The addresses are examples; substitute reachable addresses. Configure firewalls
for DDS discovery and data between the nominated peers. This is not a single
TCP listening port. Initial peers identify discovery destinations; they do not
pin transport to one interface. Multi-network deployments need an explicit
XML transport-interface policy before use.

All DDS domain IDs must be between 0 and 232. These example domains are already
used by the retained mesh on the development VM: **do not run this recipe in
that VM's host network or attach tests to its running containers**. Separate
owned network namespaces are required for co-located experiments.

Start with these existing routes:

- `Mission`: C2 to the addressed platform (payload type `control_command`).
- `Team`: C2 team assignment (payload type `TeamAssignment`).
- `PlatformCommandAck`: platform to C2.
- `ContactReport`: platform to C2 on State.
- `PlatformStatus`: platform to C2 (payload type `platform_init_status`).

The router does not execute commands or manufacture application ACKs. Use an
approved test application/fixture that implements the command/response exchange.
Do not inject synthetic commands into a real vehicle or mission system.

### 1. Prepare the same checkout and runtime on both hosts

Use the same source revision, application type XML and QoS library at both ends.
Install Connext DDS Professional **and Routing Service 7.7.0**, with a suitable
license. Development YAML preparation needs PyYAML; direct XML launch does not.
DDS inspection/testing needs the 7.7.0 `rti.connextdds` binding.

Run the following from the repository root in one shell per host. The SDK,
architecture and license paths below match the supported repository image;
replace them for a different installation. The license directory is not an SDK.

```bash
export ACT_REPO="$PWD"
export NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0
export ACT_ARCH=x64Linux4gcc8.5.0
export RTI_LICENSE_FILE=/shared/rti_license.dat
export PATH="$NDDSHOME/bin:$PATH"
export LD_LIBRARY_PATH="$NDDSHOME/resource/app/lib/$ACT_ARCH:$NDDSHOME/lib/$ACT_ARCH${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

test -r "$RTI_LICENSE_FILE"
test -x "$NDDSHOME/resource/app/bin/$ACT_ARCH/rtiroutingserviceapp"
test -d "$NDDSHOME/resource/cmake"
python3 -c 'import yaml; import rti.connextdds; print("Python dependencies available")'

export ACT_WORKDIR="$(mktemp -d /tmp/act-rs-setup.XXXXXX)"
export ACT_BUILD="$ACT_WORKDIR/build"
printf 'Keep this setup directory: %s\n' "$ACT_WORKDIR"
```

Treat a failed check as a stop condition. Install missing components before
continuing; do not mistake the Python binding for the Routing Service install.
The current launcher also expects exactly one native Routing Service architecture
under `NDDSHOME/resource/app/bin`.

### 2. Build only the Routing Service variant

```bash
cmake -S "$ACT_REPO/router" -B "$ACT_BUILD" \
  -DBUILD_ROUTING_SERVICE_VARIANT=ON \
  -DCONNEXTDDS_ARCH="$ACT_ARCH"
cmake --build "$ACT_BUILD" \
  --target act_gateway_processor act_rs_config -j2
export ACT_RS_BUILD_DIR="$ACT_BUILD/routing_service"
```

The first configure fetches yaml-cpp 0.8.0 unless an approved local source is
provided through `FETCHCONTENT_SOURCE_DIR_YAML-CPP`. Do this during packaging,
not on a disconnected operational host. Production packaging should ship the
tested plugin/config helper and matching RTI runtime instead of asking operators
to compile.

This fresh build directory does not overwrite the live custom-router binary.

### 3. Supply the only host-specific choices

On C2:

```bash
export ACT_ROLE=control
export ACT_NODE=Control_20
export ACT_PEER_IP=10.77.0.30
```

On the platform:

```bash
export ACT_ROLE=platform
export ACT_NODE=Platform_30
export ACT_PEER_IP=10.77.0.10
```

The command's `destination` must equal `Platform_30`, exactly. Node identity is
not the router process name.

### 4. Generate the minimal current input config and environment file

Run this unchanged on both hosts, from the repository root. It copies and trims
the committed configuration; it does not edit the source template. It deliberately
uses the local loopback LAN profile for both roles, so applications must be on
the same host/network namespace as their router.

```bash
python3 - <<'PY'
import ipaddress
import os
from pathlib import Path
import shlex
import yaml

repo = Path(os.environ["ACT_REPO"]).resolve()
work = Path(os.environ["ACT_WORKDIR"]).resolve()
role = os.environ["ACT_ROLE"]
node = os.environ["ACT_NODE"]
peer = str(ipaddress.IPv4Address(os.environ["ACT_PEER_IP"]))
if role not in ("control", "platform"):
    raise SystemExit("ACT_ROLE must be control or platform")
if not node:
    raise SystemExit("ACT_NODE must not be empty")
config = yaml.safe_load((repo / "router/config/control-platform.yaml").read_text())
config["node"] = {"name": node, "role": role}
config["router"]["name"] = "gateway"
config["router"]["wan_contract"] = "three_topic_union"
config["routing_service"] = {"receive_policy": "static"}
config["router"]["admin_participant"] = role + "_lan"
config["router"].pop("presence_participant", None)
config["types"]["xml"] = str(repo / "harness_v2/datamodel/gen/ActTypes.xml")
config["qos_libraries"] = [str(repo / "harness_v2/qos/act_qos_profiles.xml")]
config["qos_profiles"]["wan_participant"] = "ACT_QOS_LIB::wan_three_topic_participant"
config["qos_profiles"]["wan_event"] = "ACT_QOS_LIB::wan_three_topic_event"
config["qos_profiles"]["wan_contacts"] = "ACT_QOS_LIB::wan_union_contacts"
config["qos_profiles"]["wan_status"] = "ACT_QOS_LIB::wan_union_status"
config["qos_profiles"]["control_router_lan_participant"] = "ACT_QOS_LIB::lan_router_participant"
wanted = {"control_command", "platform_command_ack", "platform_events", "platform_init_status"}
config["routes"] = [route for route in config["routes"] if route["name"] in wanted]
if {route["name"] for route in config["routes"]} != wanted:
    raise SystemExit("Required template routes changed; review the setup recipe")
for route in config["routes"]:
    route["enabled"] = True
path = work / (role + ".yaml")
with path.open("x") as stream:
    yaml.safe_dump(config, stream, sort_keys=False)

keys = ("ACT_REPO", "ACT_WORKDIR", "ACT_RS_BUILD_DIR", "ACT_ARCH",
        "ACT_ROLE", "ACT_NODE", "ACT_PEER_IP", "NDDSHOME", "RTI_LICENSE_FILE",
        "LD_LIBRARY_PATH")
environment = {key: os.environ[key] for key in keys}
for index in range(1, 12):
    name = "WAN_PEER" if index == 1 else "WAN_PEER" + str(index)
    environment[name] = "[0-15]@builtin.udpv4://" + peer
environment["WAN_RECEIVE_MULTICAST"] = "0"
environment["WAN_TIMEOUT_SEC"] = "300"
environment["WAN_HB_PERIOD_SEC"] = "3"
environment["WAN_HB_RETRIES"] = "100"
with (work / "node.env").open("x") as stream:
    for key, value in environment.items():
        stream.write("export " + key + "=" + shlex.quote(value) + "\n")
print("Generated:", path, "and", work / "node.env")
PY

source "$ACT_WORKDIR/node.env"
```

All eleven existing peer slots are assigned explicitly to avoid inherited
loopback/old deployment peers. These duplicate locators do not imply eleven
remote nodes. The participant-ID range covers the small test setup; it is not a
fleet-scale discovery-budget claim.

The timeout/heartbeat/retry values retain the current defaults; do not tune
them for initial connectivity. Retry count is derived as
`ceil(WAN_TIMEOUT_SEC / WAN_HB_PERIOD_SEC)`. These reliable-protocol settings are
not a completed router presence/health policy.

### 5. Render and inspect the generated XML before starting DDS

```bash
python3 router/routing_service/run.py \
  --config "$ACT_WORKDIR/$ACT_ROLE.yaml" \
  --role "$ACT_ROLE" --node-name "$ACT_NODE" --name gateway \
  --build-dir "$ACT_RS_BUILD_DIR" \
  --data-plane-only --render "$ACT_WORKDIR/$ACT_ROLE.xml"
```

This resolves configured paths, aliases and catalog restrictions through the
native configuration helper, and writes the XML without starting participants.
It is **not** a substitute for the Routing Service runtime parser or matching
tests. Review these specific outputs:

```bash
python3 - <<'PY'
import os
from pathlib import Path
import xml.etree.ElementTree as ET

path = Path(os.environ["ACT_WORKDIR"]) / (os.environ["ACT_ROLE"] + ".xml")
root = ET.parse(path).getroot()
routes = root.findall(".//topic_route")
if len(routes) != 1:
    raise SystemExit("Expected one multi-port TopicRoute")
route = routes[0]
for participant in root.findall(".//domain_route/participant"):
    print("participant:", participant.attrib["name"],
          "domain:", participant.findtext("domain_id"))
for kind in ("input", "output"):
    for port in route.findall(kind):
        qos = port.find("datareader_qos" if kind == "input" else "datawriter_qos")
        print(kind, port.attrib["name"], port.findtext("topic_name"),
              port.findtext("registered_type_name"), qos.attrib["base_name"])
print("Rendered only; endpoint matching and data delivery are not yet verified.")
PY
```

Expected: two local-role participants, one TopicRoute and exactly one Event,
State and Periodic input/output pair (ten total LAN/WAN ports for these four
original topics). Event and State register WanEvent; Periodic registers WanStatus.
C2 has a command LAN input and ACK/contact/navigation LAN outputs; the platform
has the reverse. No Status or Telemetry WAN endpoint should appear in this mode.

The rendered deployment has explicit binding properties, embedded QoS and a
sibling `.types.xml` asset; it does not reference a runtime YAML model.
Keep referenced assets and the plugin at valid paths when distributing it.
This minimal four-topic example explicitly selects static receive bindings
(`routing_service.receive_policy: static`) and data-plane-only operation.
`--data-plane-only` disables monitoring; it does not select static receive policy.
For production mission selection, use the complete template and the default
`mission` receive policy so Team and local PlatformMissionStatus are present.
Omit the data-plane-only flag to include production health and statistics endpoints.

### 6. Start the gateways and application endpoints

Start approved C2/platform application endpoints on their nominated local
domains, with the original ACT topic names/types and unpartitioned LAN endpoints.
These four topics are already in the current codec catalog; a new arbitrary
application type is not supported by this procedure.

Run in the foreground on each host:

```bash
cd "$ACT_REPO"
source "$ACT_WORKDIR/node.env"
python3 router/routing_service/run.py \
  --xml "$ACT_WORKDIR/$ACT_ROLE.xml" \
  --build-dir "$ACT_RS_BUILD_DIR"
```

The launcher starts the saved XML directly and owns the service process group.
It adds the installed service/DDS library paths and chooses the native
`rtiroutingserviceapp` executable. Use `--xml ... --validate-only` for native
preflight without starting DDS. Deployment does not invoke the YAML helper.

Look for `rs.gateway.started` at both ends and inspect errors. That marker proves
processor startup only. Do not interpret it as application matching, successful
delivery, ALIVE health, dashboard parity or deployment acceptance.

### 7. Prove connectivity in stages

1. Verify the intended source writer/router reader and router writer/destination
   reader actually match, including topic, type, partition and requested/offered
   QoS. The current launcher is not a QoS-scanning assistant.
2. Observe an approved platform `PlatformStatus` at the C2 application reader.
   Also verify ContactReport on State; its LAN source is restored from the
   authoritative originating node identity.
3. Use a safe command fixture addressed to `Platform_30`; verify its original
   values at the platform reader.
4. Have the fixture publish a corresponding `PlatformCommandAck`; verify it at
   C2 and correlate it to the request.
5. Repeat with unique identifiers and bounded waits. Missing samples, mismatched
   values or schema/QoS errors are failures, not successful connectivity.

There is no standalone automatic ping/pong command yet. The existing
[two-topic acceptance tests](../router/test_e2e/test_routing_service.py) and
[three-topic acceptance tests](../router/test_e2e/test_three_topic_routing_service.py)
exercise forwarding, equality, lifecycle and shared endpoints in isolated test
fixtures. They are developer qualification, not an installed two-host operator
validator and not evidence that an application executed a command.

Current Status LAN inputs always use `lan_status_1s`: at most one accepted update
per keyed instance per second. It is not a guarantee of periodic snapshots or
preservation of every status update. The planned optional per-topic policy is
not implemented in this prototype.

### 8. Stop and retain the evidence

Press Ctrl+C in each launcher terminal. The current launcher allows ten seconds
for graceful shutdown before reporting and terminating its owned process group.
Verify that both owned Routing Service instances exited before changing their
configuration. Never stop unrelated services or restart one retained-mesh node.

Keep the generated YAML/XML, environment file, exact revision/runtime versions,
application fixture details and validation results together. Do not delete the
setup directory while processes use it. It is disposable `/tmp` state; export
the completed bundle to the user's chosen deployment location if it must survive
reboots, then regenerate paths as necessary.

## Simplified target user workflow

This is the v2 product experience to implement, not an available CLI or a v1
installation requirement.
Do not invent executable commands for it in customer instructions.

### A. Connect

Install one setup agent alongside each application LAN. The C2 wizard receives
the two agent addresses, node names, roles, local DDS domains and WAN peer
addresses. The agent connection is a separate provisioning channel, not the
WAN data contract. Transport/authentication remain implementation decisions;
authenticate and authorize provisioning before writing remote configurations.

Prefer one packaged, supported runtime per architecture. The agent checks
license access, SDK/service compatibility, plugin ABI, network/interface
selection and absence of conflicting owned deployments. Users should not
manually set a dozen environment variables.

### B. Scan or import

Capture each side separately; C2 cannot discover a remote LAN without an agent
or supplied artifacts. Save complete type definitions and an endpoint inventory:
topic, registered type, definition identity, keys, nested types, collections,
optional/union metadata, partitions and discovered endpoint QoS with provenance.

XML import feeds the same catalog. Type XML does not supply endpoint QoS; use
discovery, an exported endpoint manifest or explicit profiles for that part.
Unresolved types and incomplete discovery remain visible; a timed scan does not
prove no other endpoint exists. Do not infer schemas from sample values.
Preserve catalog snapshots so offline setup is reproducible.

### C. Map using the captured model

The GUI loads the catalog and shows the original field tree. Users:

1. Select the original LAN topic/type; both ends retain that same name and full type.
2. Assign generic instance identity and addressing fields.
3. Bind the entire original type to a union branch. Nested arrays/sequences
   retain their types, bounds, dimensions and values.
4. Confirm the full-type/branch match; there is no payload field selection or
   omitted-field reconstruction in the v1 contract.
5. Choose one logical channel and the intended recipients. The channel determines
   Event, State or Periodic and its High/Normal scheduling class.

Keep authoritative source identity router-configured and use the v1 string
instance-ID field with explicit size validation. Recover identity for key-only
lifecycle notifications, not just full samples. Numeric/composite encodings
would require a separately agreed reversible codec in a later extension.

The setup compiler creates shared union/type artifacts containing the complete
original types, plus explicit local port bindings. Generated branch
IDs must not depend on route ordering. Both nodes use the same schema generation;
their local enabled routes may differ. No user-maintained WAN payload catalog.

### D. Resolve QoS without presenting every DDS policy

Show source-LAN and destination-LAN compatibility separately from WAN delivery
policy. Reuse qualified Routing Service auto-configuration if it meets the
requirements; otherwise select/generate external XML profiles. Missing
information and conflicting endpoints must block the affected route or require
an explicit decision. Do not silently downgrade reliability or invent defaults.

Fix Event / State / Periodic policies through qualified profile references.
Use the approved six-class priority order and explicit sampling choices from
the three-topic plan. No continuous QoS negotiation is required.

### E. Generate one deployment bundle

The generator should write:

- Saved intent and captured catalogs with source/revision identifiers.
- Original and generated WAN type XML.
- External QoS profiles and per-node Routing Service XML.
- Processor mappings/properties consumed directly from XML, not runtime YAML.
- Per-node environment files and launch/stop entry points.
- A manifest of schema identities, generated-file hashes, runtime/plugin
  requirements and startup configuration-variable values.
- A readable route table and unresolved/unsupported-item report.

Use local qualified paths or supported configuration variables with documented
defaults; generate per-node values automatically. Regenerate for the destination
installation root rather than shipping stale absolute paths. Never edit running
generated configuration as a substitute for deployment.

### F. Validate the generated routes before deployment

The Python validator launches **those generated configurations**, with owned
endpoints in isolated domains/network namespaces. Preserve their delivery
policies, and record every test-only substitution.

Check native XML load/plugin startup, endpoint matching, whole-payload equality,
keys, arrays, sequences, optional presence and application union discriminators.
Use explicit forward/return mappings for repeated correlated ping/pong and report
round-trip timing, misses, errors and untested routes.

A one-way route needs an explicit test return path; do not claim it was
bidirectional. A test reader receiving an ACK-shaped sample does not prove a real
application executed a command. Keep lifecycle, restart, required-event retention,
outage recovery and load as separate qualification gates.

### G. Install, preflight, activate

Each agent stages the bundle, confirms local type/QoS compatibility and reports
readiness. Both sides must agree on schema and physical topic names before
activation. Activation is an explicit user action, separate from generation.
For incompatible changes, perform a coordinated owned stop/start with rollback
to the previous complete bundle; rolling schema changes are not assumed safe.

After isolated validation, perform a separately approved application-level check
on the actual link. Show the distinct results: **configured**, **started**,
**matched**, **data received**, **round trip verified**, and **application
confirmed**. Do not collapse these into one green "connected" badge.

## Implementation order and boundaries

1. **V1:** finish XML-only configuration/startup and hand-authored full-type bindings.
   Extend or replace the current `WanCodec` catalog/type/schema checks with
   validated full-type union bindings; do not bypass those checks or build a
   selected-field mapping engine for v1.
2. **V1:** complete retained health/monitoring and other processor gates; qualify
   the configured C2/platform pair, including mapping and lifecycle behavior.
3. **V1:** package explicitly defined type/union XML, QoS and bindings,
   environment files, tested plugin, start/stop scripts,
   manifest and manual setup/check procedure. Prove operation without YAML,
   the native YAML helper or the future setup app.
4. **V2:** implement discovery/capture, XML import and full-type union/bundle
   generation with a binding editor; qualify automatic LAN QoS selection.
5. **V2:** automate the workflow and isolated mapped-route ping/pong testing,
   with reproducible reports.
6. Add authenticated two-node provisioning if selected for the setup app;
   keep generation, testing and activation separate.

The first current C2/platform connection is achievable with the bounded
three-topic recipe above. The full "scan -> map -> generate -> prove -> connect"
experience is v2. Neither documenting that experience nor the current three-topic
forwarding milestone makes the v1 XML-only/mapping release complete.

## Evidence and validation scope

This guide is based on the current launcher, model/config helper, processor,
production route/QoS/type files and existing Routing Service tests, rather than
the older handoff alone. The key source paths are linked above and in the
[processor README](../router/routing_service/README.md).

The three-topic generation/render steps executed for both roles against a fresh
processor/helper build: each produced ten ports and passed the installed
Routing Service XSD after configuration-variable expansion. All eight Bash
blocks passed syntax validation and local documentation links resolved.
These render-only checks started no DDS entities. Separately, 36 processor
acceptance tests across both modes and 18 configuration/launcher checks passed
in owned network-isolated containers. Three opt-in time-filter checks were
skipped. This is not a fresh two-host or complete integration gate.

Earlier two-topic guide validation was performed on 2026-11-13 in a disposable `connext:7.7.0` container with
networking disabled and the checkout mounted read-only:

- Fresh processor/config-helper build passed using the cached yaml-cpp source.
- All **9 renderer/native-configuration tests passed**, with no skips.
- All **8 Bash blocks** passed syntax checks.
- The documented generation/render steps executed for both roles; each produced
  the expected seven ports and passed the installed Routing Service XSD after
  configuration-variable expansion.
- Native QosProvider comparison confirmed that adding explicit empty
  `multicast_settings` to the two three-topic priority channels preserves both
  affected writer profiles' effective QoS. Routing Service embeds those profiles
  even in the two-topic setup, so their previously omitted schema-required
  elements blocked XML validation; the source XML and a regression check now
  cover that setup dependency.

The initial retained helper failed native default-alias checks; rebuilding from
current source resolved that mismatch. The current guide requires a fresh build
rather than relying on retained binaries.

In that earlier guide-only validation, no DDS participants or Routing Service
processes were started. The temporary
build/configurations were removed, and the retained mesh was not reconfigured.
These results establish build/configuration validity, **not** a fresh two-host
connection, runtime native-parser acceptance or the unimplemented wizard
workflow. Do not run DDS e2e tests against the retained mesh.
