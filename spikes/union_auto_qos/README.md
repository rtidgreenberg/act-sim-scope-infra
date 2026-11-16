# Typed WAN unions + native Routing Service LAN auto-QoS

**Status: isolated Connext 7.7.0 integration spike passes. Not deployed.**

```bash
sg docker -c 'bash spikes/union_auto_qos/run.sh'
```

Needs the existing `connext:7.7.0` image and readable
`/home/dgreenberg/rti_connext_dds-7.7.0/rti_license.dat`
(`CONNEXT_SHARED_DIR` overrides that directory).

## Architecture actually tested

```text
LAN publisher -> native per-topic input [automatic QoS] --+
                                                        |
                       processor-plugin shared WAN owner |
                       Event / State / Periodic          |
                       one reader + writer per family    |
                                                        |
LAN subscriber <- native per-topic output [automatic QoS]+
```

Each configured LAN input **and** output has an independent native topic route.
There is no combined all-topics startup barrier and no extra DDS intermediate bus.
All processor instances share one owner through the single plugin instance.
The owner creates the WAN endpoints directly from XML QoS; they are **not native
Routing Service route input/output ports**. Endpoint sharing is implemented here,
not inferred from a claim that native routes automatically share writers.

This is the principal tradeoff: native LAN discovery/auto-QoS and independent
activation coexist with precisely three physical WAN pairs, but native Routing
Service route monitoring/administration does not own those WAN entities. A
production implementation would need monitoring, shutdown/reconfiguration barriers,
bounded admission and ownership/lifecycle policy for this shared owner.

## Discovery, generation and deployment

`application.xml` is the fixture known only by the original producers.
The isolated acquisition stage advertises `UaqPoint`, `UaqCommand` and `UaqStatus`
on discovery-only export topics. A separate observer obtains their full
DynamicTypes from native discovery, exports IDL, and invokes installed `rtiddsgen`
4.7.0 to convert it to XML. Exact native type equality is checked for all three.
`request_types_filter=*` in XML is needed to resolve the discovered TypeObjects.

The generator adds statically selected typed union branches and keyed envelopes to
that exported catalog, producing `generated_types.xml`. The native processor,
native Routing Service configuration, and remote peer all load that **same frozen
schema before deployment**. Union branches contain the original structures, not
octet serialization or field-by-field operation translations. Packing/unpacking
uses a whole `DynamicData` structure assignment.
Event carries two different active branches in the integration test: the high
channel carries commands, and the normal channel carries nested status structures.
Both use the same physical writer/reader pair.

The WAN schema is generated XML with strongly typed DDS union branches, accessed
through the DynamicData API. This spike does **not** compile a generated
C++ application-type API. It does **not** generate/change unions while the routing
service is running, transmit a schema catalog over WAN, or discover new branches
at runtime.

The collector is intentionally bounded: the three export topics and dependency
order are known. `UaqPoint` is explicitly advertised as an export dependency.
Arbitrary dependency traversal, vendor annotations and arbitrary discovered schemas
are **not qualified**. Some XML-legal field names may not round-trip through IDL:
for example, an unescaped member named `sequence` is an IDL reserved word.
Never deploy an exported catalog without the exact-type equality check.

## XML and channel policy

`config.xml` is the native Routing Service template. `qualify.py` inserts the
frozen type catalog, fixed XML QoS and independent per-topic port definitions;
it changes only the LAN `auto_configure_qos` toggle between controls.
LAN ports intentionally have **no endpoint QoS profile**, allowing native defaults
to be inferred. No Python or C++ code copies discovered QoS onto LAN ports.

WAN QoS is always explicit in `qos.xml` and never automatic:

| Family | Writer policy | High / Normal priority |
|---|---|---|
| Event | Reliable, transient-local, KEEP_ALL | 600 / 300 |
| State | Reliable, transient-local, KEEP_LAST(1) | 400 / 200 |
| Periodic | Best effort, volatile, KEEP_LAST(1) | 500 / 100 |

Event/State readers retain KEEP_ALL; Periodic readers use KEEP_LAST(1).
The six logical channel names are `event_high`, `periodic_high`, `state_high`,
`event_normal`, `state_normal`, `periodic_normal`.
Each WAN writer has exactly two MultiChannel bands, divided at its High priority,
and uses asynchronous publishing with the same participant-owned `uaq`
highest-priority-first flow controller. The installed C++ API checks effective
asynchronous mode, two channels and shared controller name. Each send supplies
the corresponding priority through native `WriteParams`.

The test exercises all six priorities; it **does not** prove saturation ordering,
fairness, latency bounds, end-to-end acknowledgments or required-event retention.
This is not a new production queue/resource budget.

## Measured results

Final run, 2026-11-16, `connext:7.7.0`:

* **6/6 cases pass**, no skips: automatic false/true with best-effort producers
  created before service startup, false/true created afterwards, and false/true
  with reliable transient-local producers.
* Before startup there are zero native service LAN endpoints. In delayed cases
  they remain absent until matching applications appear.
* Afterwards every case discovers **6 native LAN readers + 6 native LAN writers**;
  a thirteenth configured input, `AbsentTopic`, remains pending, without blocking
  those twelve ports.
* In all three automatic cases, starting the missing publisher activates just its
  route and forwards its complete command. **All six WAN endpoint identities
  remain unchanged**.
* Discovery reports **one Event writer/reader, one State writer/reader and one
  Periodic writer/reader** for the gateway, in every case. The remote test peer
  separately has the same six endpoints; it is not included in gateway counts.
* **39 unique automatic full-payload equalities** pass: six outbound and six
  inbound comparisons per automatic case, plus one late command per case.
  False controls add **18 outbound full-payload equalities**.
* Both commands and nested status survive whole-structure conversion, including
  original keys, 64-bit timestamps, audit sequences, arrays, nested structs,
  sequence lengths/values and strings.
* Three typed CFT scenarios accept commands for `Platform_30` and exclude repeated
  commands for `Platform_99`, using
  `(payload._d = 1 AND payload.command.destination = 'Platform_30') OR payload._d = 2`
  inside the originating-source predicate. The second branch remains deliverable.
  This is receive-side exclusion evidence, not a measured writer push-count gate.

### Native auto-QoS, not equivalent explicit QoS

| LAN observation | Auto false | Auto true |
|---|---|---|
| Input, BE/volatile producer | BE / volatile | BE / volatile |
| Input, reliable/TL producer | BE / volatile | Reliable / transient-local |
| Output, reliable/TL subscriber | Reliable / volatile | Reliable / transient-local |
| Outbound whole-payload delivery | 6/6 per case | 6/6 per case |
| Inbound whole-payload delivery | 0/6 per case | 6/6 per case |

The false input remains compatible, so its successful forwarding is expected.
The false output cannot satisfy the subscriber's transient-local durability;
native inference changes the effective output durability and restores delivery.
Observed QoS comes from builtin discovery, not guessed profile intentions.
WAN reliability/durability remain the explicit per-family values in every case.

Only **LAN input reliability/durability kind and LAN output durability kind** are
proven automatic here. Finite deadline inference, explicit-policy precedence,
heterogeneous/multiple endpoints, live QoS changes, ownership strength,
partitions, and other policies are not qualified by this spike. See the existing
[native qualification](../../router/routing_service/README.md) for separate
deadline/precedence results. The supplied
[feature note](../../docs/routing-service-qos-auto-configuration-routing-1309.md)
also discusses future K Release proposals; those are not implemented claims.

## Files, evidence and isolation

* `application.xml`: source application fixture.
* `qos.xml`: every fixed DDS policy, including UDPv4-only transport.
* `config.xml`: native Routing Service configuration template.
* `Processor.cxx`: generic whole-structure adapters and plugin-owned shared endpoints.
* `qualify.py`: discovery/export, frozen union generation, XML rendering and native
  false/true integration controls.
* `CMakeLists.txt`, `run.sh`: fresh build and owned lifecycle.
* `PLAN.md`: bounded objective and acceptance gate.

Compact ignored evidence: `debug/test_reports/union_auto_qos.txt`.
Build products, exported/generated schemas, per-case rendered configurations and
logs exist only in the container-owned `/work`; they are regenerated every run
and deleted with the container. `config.xml` and the schema generator are the
reproducible source, not a copied host deployment configuration.

The runner uses `--network none --ipc private`, loopback UDPv4, and domains
87 and 91–102 (all <=232). The checkout and license are read-only. Source is not
built on the host. There is no Docker socket mount, host network, live mesh
inspection probe, or live configuration/binary update. Owned Routing Service
process groups are stopped, private shared memory is checked, and the owned
container is removed by a trap, including failed runs.

The seven running mesh containers were left untouched. No core router edits,
commits or pushes were made.
