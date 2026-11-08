# Whole-structure DynamicData union adapter

This isolated spike tests the proposed legacy-topic adapter. It does not modify
or migrate the production router. **Field mapping is intentionally absent.**

## Run

```bash
bash spikes/two_topic_union_adapter/run.sh
```

Requires the existing `connext:7.7.0` Docker image and readable license directory
specified by `CONNEXT_SHARED_DIR` (default
`/home/dgreenberg/rti_connext_dds-7.7.0`). No dependency downloads, host-network
access, shared-memory transport, or packet impairment privileges are required.
The runner owns its container and teardown. Build/generated files disappear with
that container; the compact result is
[two_topic_union_adapter.txt](../../debug/test_reports/two_topic_union_adapter.txt).

## Mapping and copy

[mapping.tsv](mapping.tsv) is a whitespace-separated, predefined mapping table:
topic pattern, input type, WAN topic, union branch, discriminator, XML profile,
and original key field. Patterns use POSIX `fnmatch`, without first-match
precedence: unmapped or multiply matched topics fail explicitly. Startup checks
the actual branch schema and discriminator against each mapping.

The current production C++ router selects explicit topic names. This small
pattern-to-union selector is **spike logic**, not a claim that production already
has wildcard conversion. The alias check proves that a wildcard can select a
branch without losing the original topic name at the destination.

Packing copies one entire original structure:

```cpp
auto payload = envelope.loan_value("payload");
payload.get().value<DynamicData>(mapping.branch, incoming);
```

Unpacking checks the envelope type and active discriminator, then extracts that
entire branch. No payload fields are renamed, flattened, or reconstructed.
This is a structured copy, **not a zero-copy promise**.

[adapter.idl](adapter.idl) reuses the existing qualification unions, which in turn
reuse production structures. It adds `original_topic` to the outer metadata.
The stable outer key is source node + source router + variant/topic/local-key
instance ID. The topic is length-prefixed to prevent concatenation collisions;
aliases get separate DDS instances. Session remains metadata, not an extra key.
These are prototype bounds/identity rules, not a final production schema.

## Validation

The executable checks full payload/envelope equality over three isolated domains,
exact forwarding counts, ten explicit rejection cases, sender protocol counters,
and dispose/revive/unregister through both adapters. The health fixture includes
nonempty nested peer/route sequences and sixteen team entries; commands use the
full 8192-byte octet bound, navigation a 64-character mission ID, and acknowledgments
a 256-character message.

### Results (2026-11-08)

**All checks passed in two consecutive fresh builds/runs** against Connext 7.7.0,
rtiddsgen 4.7.0, and Modern C++ with `-Wall -Wextra -Werror`. The second complete
build/run took 8.68 seconds; that is harness runtime, not message latency.

| Check | Observed result |
|---|---|
| Whole structures through both adapters | All four payload types plus a wildcard navigation alias arrived on their original topic/type, with full payload and envelope equality |
| Mapping/schema errors | Ten explicit rejections: unknown topic, wrong input type, ambiguous patterns, branch/type mismatch, discriminator mismatch, wrong envelope family, inactive branch, unmapped schema case, inconsistent instance ID, and oversized topic |
| Selected delivery | Five original inputs produced exactly five valid WAN receives, five sender pushes, and five original-topic outputs |
| Sender-side filtering | Twenty unwanted low-speed/wrong-audience unions produced zero additional pushed DATA and no destination output; one selected union produced exactly one |
| Lifecycle | Five disposals and five explicit NO_WRITERS states propagated through both adapters using recovered keys and previously decoded output bindings |
| Stable identity | Five independent variant/topic instances; payload updates and dispose/revive retained original, WAN-reader, and restored-writer handles |
| Cleanup/counting | Ten original inputs plus one selected filter probe produced eleven valid WAN receives/outputs; all five output bindings were removed by unregister |

The final sender `pushed_sample_count` was **21**, not 11: eleven valid
publications plus ten lifecycle notifications. The isolated data/filter checks
measure valid DATA before lifecycle operations; do not treat that aggregate
counter as a valid-payload-only delivery metric.

Initial failures were resolved in the spike: participant-local Topic ownership,
portable generated XML include paths, non-ordered InstanceHandle lookup, string
sequence fixture construction, and lifecycle-inclusive sender accounting.
An out-of-range enum discriminator (`99`) was rejected by the SDK during sample
construction; the adapter rejection test instead uses the schema-valid debug
case (`3`) for which no mapping exists. This is not wire-level malformed-data
or schema-evolution qualification.

## Limitations

- One process drives six real DDS participants over private loopback UDP. This is
  real topic/type conversion, but not a deployment/process-failure test.
- Source input topics and allowed destination topics are predefined. Dynamic
  discovery/configuration and production factory wiring remain migration work.
- These four original types have single string keys (`source` or `router`).
  Composite/non-string local keys need an explicit encoding contract.
- The receive adapter retains a binding for each previously decoded WAN instance.
  First-observed invalid states without that binding fail explicitly; restart
  reconstruction and bounded cache/resource policy remain unqualified.
- Disposal and explicit unregister are not writer-crash or session-restart tests.
- Source/destination audience and routing context are test fixtures, not a complete
  team-selection or scheduling implementation.
- Status uses application and health writers but a common receive filter. The
  production PresenceMonitor/RTT path and dedicated health receive endpoint classes
  are not integrated here; see the separate [SDK qualification](../../router/qualification/test_two_topic_qualification.py).
- No radio-byte savings, congested fleet throughput, latency budget, event execution,
  or transformed-schema support is claimed.
- Legacy command octets are copied unchanged inside the command structure. This
  does not implement the migration plan's final typed command-operation cases.
