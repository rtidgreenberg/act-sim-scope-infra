# Discovered command unions: State catalog or discovery-only

Isolated startup proof; no production schema/configuration or live-mesh changes.

```bash
bash spikes/discovered_union_catalog/run.sh
bash spikes/discovered_union_catalog/run.sh --discovery-only
```

Requires the existing `connext:7.7.0` image and the license directory selected by
`CONNEXT_SHARED_DIR` (default `/home/dgreenberg/rti_connext_dds-7.7.0`). If Docker
group membership needs activation, run with `sg docker -c 'bash .../run.sh'`.
No package downloads. The runner owns and removes its container; compilation
and process logs are transient under `/tmp`. A compact build/result report is
retained at `debug/test_reports/discovered_union_catalog.txt` or
`debug/test_reports/discovered_union_discovery_only.txt`, respectively.

## Original catalog-mode flow

```text
application -- original commands / LAN discovery --> gateway
gateway -- runtime Event union + retained State catalog --> platform
platform -- whole restored command / destination LAN --> application verifier
```

The application, gateway and platform are separate executables/processes.
The gateway/platform accept only the fixed bootstrap schema and external QoS
profiles; neither loads the application type XML. There is no union IDL/XML.
The gateway dynamically adds the complete discovered structures as branches.
The platform discovers that envelope and extracts the original types from its
branches to construct its output LAN writers.

State contains source, startup revision, WAN topic/envelope name, and each
original topic/type name, branch name and discriminator. This minimal catalog
does not carry serialized TypeObjects or cryptographic schema identifiers:
DDS discovery supplies the actual type graph, which is checked against the
catalog binding. It is not a production contract-negotiation protocol.

Only the fixture constructs/checks the concrete command contents. Gateway and
platform use common `destination` metadata as a configured routing convention;
the platform enumerates the other member names from discovery. Both copy each
complete payload without reconstructing its fields. User selection enters
through a test-control topic; catalog lookup translates it into a native CFT.
The platform recreates its filtered reader between selections, not its union
writer/type. Both gateways retain the same startup union throughout.

The fixture emits 36 commands: two families, two destinations, three commands
per combination, in three phases. Selection expects 3 Move, 3 Mode, then zero.
Native Event writer push deltas must equal those exact counts, not merely
receiver-side drop counts. Destination LAN verification checks complete
original DynamicData equality and absence of duplicates/other families.

## Discovery-only flow (no State topic)

```text
source LAN publication discovery --> runtime Event union with topic-named branches
WAN union discovery + destination LAN subscription discovery --> matched interests
matched labels + platform destination --> native WAN content-filtered reader
active discriminator --> discovered branch/topic/type --> original LAN command
```

In this mode neither gateway nor platform creates a State topic, writer or
reader. WAN builtin discovery assertions reject any State publication or
subscription. No catalog mapping is exchanged: the platform enumerates the
discovered union's members, names, complete types and discriminator labels.
Branches are named `Cmd_Mode` and `Cmd_Move`, with deliberately sparse labels
17 and 42, so ordinal position or a guessed 1/2 mapping cannot select correctly.
There is no `original_topic` field in this mode's Event envelope: output topic
selection comes from the discovered branch, not from per-sample topic metadata.

The platform inspects **alive local subscriptions**, not arbitrary local
publications or every known topic. A branch is selected only when its name
equals the local topic name and its complete `DynamicType` equals the local
reader's discovered type. This is intentionally strict equality, not general
XTypes assignability. A same-name/different-structure reader is explicitly
diagnosed and excluded before creating an output writer.

The fixture changes its local readers through five phases: Move, Mode, no
supported topic, an incompatible Mode type, and both supported types.
Every phase also has an unmapped local topic using the valid Move type; matching
by type alone must not select it. Destination participants are replaced between
phases to permit the same registered type name with incompatible definitions
in the negative case. This is controlled phase-boundary convergence, not a
production continuous discovery/rebinding loop under traffic.

The test-control topic remains for synchronization, source/expected counts and
verification. Its `selection` field is always empty and checked as such in this
mode. Local reader phase names and a raw reader count are test-only discovery
barriers, preventing stale/partial discovery snapshots from starting a phase.
They carry no topic/branch/type mapping and do not determine the selected
interests. The control schema is still preinstalled; application type XML is
loaded only by the fixture, never by either gateway.

## Qualification boundaries

- Fixed bootstrap type knowledge is unavoidable for reading the State catalog.
  This State is an isolated catalog fixture, not the production State union.
  The discovery-only path needs no State/catalog schema for routing; its test
  harness still loads the common XML for the separate progress topic.
- The runtime union is deployment-specific but automatically composed: this
  removes manual union authoring and generated application classes, not the
  existence of a deployment-specific wire type.
- One gateway builds one startup contract; labels follow sorted topics in this
  isolated run. Production needs stable explicit labels and coordinated complete
  manifests across senders. Sorting is not an evolution/compatibility scheme.
- LAN discovery selects the `Cmd_` prefix and waits for exactly two fixture
  publications. Production needs an explicit allowed-topic set and a startup
  completion policy; this is not an unbounded discover-everything service.
- This proves the configured 7.7 discovery path, not whether metadata traveled
  inline versus TypeLookup, startup wire cost, or large type-graph limits.
- No runtime branch addition, security policy, mission integration, command
  execution/application acknowledgment or outage/load/lifecycle qualification.
- The container is network-isolated and does not participate in or modify the
  existing live mesh. All fixed QoS comes from existing external XML profiles.

## Results (2026-11-16)

Fresh builds and complete three-process acceptance passed repeatedly against
Connext 7.7.0, GNU C++ 11.4.0, with `-Wall -Wextra -Werror`.

| Selection | Source commands | Native Event DATA pushes | Restored original LAN commands |
|---|---:|---:|---:|
| Move, Platform_30 | 12 | 3 | 3 |
| Mode, Platform_30 | 12 | 3 | 3 |
| None | 12 | 0 | 0 |

All six delivered commands match their independent original-type fixtures in
full, including nested/collection values, keys, timestamps and identity.
No duplicate, other-family or other-destination sample was accepted. Both
explicit rejection checks passed. The platform started after the catalog's
single publication, obtained its retained State sample, resolved the runtime
union and nested structures through discovery, and restored the original LAN
types without loading the application type XML.

The owned container exited and was removed; no host DDS/shared-memory artifacts
were created. The existing seven-node EMANE mesh was left running and unchanged.
This is spike evidence, not production integration or full router regression.

### Discovery-only results (2026-11-16)

Fresh three-process builds/runs passed repeatedly, with the original catalog
mode also rerun successfully after the extension.

| Discovered local interests | Source commands | Native Event DATA pushes | Restored original LAN commands |
|---|---:|---:|---:|
| Move | 12 | 3 | 3 |
| Mode | 12 | 3 | 3 |
| No supported reader | 12 | 0 | 0 |
| Same-named, structurally incompatible Mode reader | 12 | 0 | 0 |
| Move and Mode | 12 | 6 | 6 |

All twelve selected commands match the independent original-type fixtures in
full. Wrong destination, unselected family and duplicate delivery remain
negative gates. The platform reconstructs sparse labels and original topic
names from the discovered union, selects solely from real local readers, and
explicitly rejects `SpikeModeCommand` versus a different structure also named
`SpikeModeCommand`. No State endpoints or topic-selection messages are used.
The startup union remains unchanged while local interests change.

See [PLAN.md](PLAN.md) for acceptance criteria.
