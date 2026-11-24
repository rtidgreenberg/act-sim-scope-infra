# Routing Service with static WAN endpoint discovery

## Result (2026-11-20)

**Yes: native Connext 7.7.0 Routing Service forwards fixed binary envelopes
with LBED, including when participant discovery remains SPDP2.**

Two actual `rtiroutingserviceapp` processes each own one WAN writer/reader for
Event, State and Periodic, plus a separate dynamically discovered LAN.
No custom adapter or processor is needed for this discovery/forwarding proof.
The installed native DDS adapter honors explicit participant names and endpoint
RTPS object IDs in the Routing Service XML.

| Variant | Exact envelope deliveries | Fresh deliveries after B restart | Captured SEDP DATA |
|---|---:|---:|---:|
| SPDP2 + SEDP control | 18/18 | 18/18 | Present |
| Classic SPDP + LBED | 18/18 | 18/18 | 0 |
| SPDP2 + LBED | 18/18 | 18/18 | 0 |
| SPDP2 + LBED, A's Event writer omitted from catalog | 15/15 allowed; 3/3 expected non-deliveries | Not tested | 0 |

Across the matrix: **123 exact deliveries and three expected non-deliveries**.
The positive LBED captures contain no SEDP publication/subscription DATA,
HEARTBEAT or ACKNACK exchanges, while participant discovery and user DATA are
present. Classic LBED shows the classic SPDP writer; SPDP2 LBED shows SPDP2
bootstrap/configuration writers. All three explicitly assigned WAN writer
entity IDs appear on wire. There are zero capture drops and zero native
ERROR/FATAL lines in the final matrix.

These are functional/wire checks, **not an RF bandwidth or recovery-time
guarantee**. The baseline uses the same fixed envelope and workload, not the
production ACT union schema. Participant and ordinary user-reliability traffic
remain; static endpoint discovery does not mean discovery-free communication.

## Reproduce

```bash
bash spikes/routing_service_lbed/run.sh
```

The owned container uses `connext:7.7.0`, `--network none`, private IPC and
UDPv4-only participants. Domains are LAN A **110**, WAN **111**, LAN B **112**.
All generated XML and coordination state are under the container's disposable
`/tmp`. The checkout is mounted read-only and the license directory is mounted
read-only. Exact report/capture roots are cleared before each run:

- `debug/test_reports/routing_service_lbed/`: report JSON, logs, source hashes,
  image identity and generated configuration snapshots.
- `debug/pcap/routing_service_lbed/`: bounded WAN-only packet captures.

`report.json` and the `PASS` marker are written only after the entire matrix
passes. Failures write an explicit `failure.json` and return nonzero.
Every child is stopped/reaped; the owned container is removed on success/failure.
No production binaries, IDL, generated mesh configuration or mesh lifecycle
state are changed. The operator mesh remains stopped.

## Configuration that works

The external [QoS profiles](qos.xml) and generated DDS-XML catalog specify:

1. A fixed outer [SerializedWanEnvelope](../serialized_wan_envelope/envelope.xml)
   with keyed source/topic/instance metadata and an octet-sequence payload.
2. Static endpoint descriptions for both `GatewayA` and `GatewayB`, matching
   the explicit `participant_name` used by the real WAN participants.
3. Explicit writer object IDs **1001/1002/1003** and reader IDs
   **2001/2002/2003**, mirrored in the real Routing Service endpoint QoS.
4. An absolute `dds.discovery.endpoint.lbediscovery.config_file` path.
   This also avoids the documented QosProvider lookup issue COREPLG-741.
5. Explicit LBED loading with `rtilbedisc` and
   `DDS_LBEDiscoveryPlugin_create`.
6. Either `builtin_discovery_plugins=SPDP` or `SPDP2`, without SEDP.
   `DPSE` is the classic-SPDP convenience setting; it is not the SPDP2 variant.

Routing Service publisher/subscriber partitions are configured at the **session**
level, not inside individual input/output ports. A publishes on partition A and
subscribes on B; B does the reverse. LAN probes use the corresponding partitions.
Send/receive LAN topic names differ to prevent feedback. There is one shared WAN
topic name per family, not separate names per sender.

Event is RELIABLE/KEEP_ALL, State is RELIABLE with writer KEEP_LAST(1) and reader
KEEP_ALL, and Periodic is BEST_EFFORT/KEEP_LAST(1). All are VOLATILE. These are
minimal external spike profiles, **not the complete production WAN QoS**.

## What binary payloads do and do not prove

The rig compares every outer field and every payload octet at the receiving
LAN. It exercises empty payloads and all 256 octet values in 256-/512-byte
payloads, both directions, on each family. Routing Service forwards the fixed
envelope unchanged; it does not deserialize its embedded application bytes.

Static discovery needs only the fixed outer contract. The separate
[serialized-envelope spike](../serialized_wan_envelope/README.md) qualifies
Connext CDR serialization/deserialization with locally discovered original
application types. This spike **does not combine that codec with Routing
Service** or implement the binary ACT processor.

The gateway restart test sends fresh samples only after LAN rematching and
participant rediscovery. It does not claim replay of VOLATILE history, outage
retention, or end-to-end command execution/acknowledgment semantics.

### Negative-control refinement

An initial mismatched *reader* object-ID experiment still delivered data.
It was therefore rejected as a no-delivery oracle, not reported as a successful
negative control or as proof that endpoint identities are irrelevant.
The final control instead omits A's Event **writer** from the static catalog:
all three A-to-B Event samples remain absent, while B-to-A Event and both
directions of State/Periodic pass. This proves the final test depends on the
static database rather than silently falling back to SEDP.

## Dynamic CFT follow-up (2026-11-20)

**Runtime reader filtering works with SPDP2 + LBED, but dynamic writer-side
filtering does not work in this tested configuration.** Unwanted samples still
cross the WAN and are discarded at the receiving gateway.

The separate [CFT runner](cft.py) uses the same two real Routing Service
processes and three WAN topic pairs. It adds a content-filtered Event input on
Gateway B and changes its single expression parameter through Routing Service
remote administration, without restarting or recreating endpoints.

Each phase offers three fresh envelopes, with `original_topic` equal to
`alpha`, `beta` and `gamma`. The fixed expression is
`NOT (original_topic > %0)`. Parameter values select one topic, then two,
then all three, then one again. Filtering uses visible envelope metadata,
not the opaque payload.

| WAN discovery / static reader description | Correct LAN deliveries by phase | Distinct Event DATA writes crossing WAN by phase |
|---|---|---|
| SPDP2 + SEDP control | 1, 2, 3, 1 | **1, 2, 3, 1** |
| SPDP2 + LBED, catalog includes initial narrow CFT | 1, 2, 3, 1 | **3, 3, 3, 3** |
| SPDP2 + LBED, catalog has no CFT | 1, 2, 3, 1 | **3, 3, 3, 3** |

Both LBED variants accept newly permitted topics after expansion and reject
them after narrowing. However, even putting the initial filter in the static
DDS-XML reader description did not produce writer-side suppression. This
experiment did **not** observe a stale narrow writer filter blocking newly
permitted traffic; it observed transmission of everything followed by local
filtering.

Evidence and checks:

- **36 offered envelopes, 21 exact selected deliveries**, with no extra valid
  samples, across 12 phases.
- All nine parameter UPDATEs and 12 configuration GETs receive successful,
  request-correlated replies. GET confirms each updated parameter.
- Native logs show exactly one Event-input enable and three filter updates per
  variant. ACKNACKs identify the same receiving reader GUID in every phase;
  the transmitting Event writer prefix also remains unchanged.
- Counts come from independent WAN-only packet captures, deduplicated by writer
  sequence number, not just application receive counts. Both runners filter
  WAN **destination** ports: a regression rerun exposed unrelated LAN traffic
  whose ephemeral source port happened to fall in the WAN port range.
- SEDP is present in the control and absent from both LBED WAN captures.
  Administration uses a separate, dynamically discovered domain **113**, not
  the WAN.
- Zero capture drops, zero native Routing Service ERROR/FATAL lines and zero
  shared-memory remnants in the final matrix. An additional complete run
  reproduced the same delivery/transmission counts.

Reproduce without overwriting the original forwarding-matrix evidence:

```bash
bash spikes/routing_service_lbed/run.sh --cft
```

Reports are under `debug/test_reports/routing_service_lbed_cft/`; captures are
under `debug/pcap/routing_service_lbed_cft/`. A completed characterization writes
`report.json` with `experiment_checks_passed: true`, but returns **exit 1** and
`DYNAMIC_WRITER_FILTERING_NOT_OBSERVED` when the bandwidth-saving feature is
absent. It deliberately does not write `PASS`. Harness/API/control failures
instead write `failure.json`.

The runner generates administration DynamicTypes from the installed SDK IDL
into disposable `/tmp`, with `-unboundedSupport`; no vendor IDL is copied into
the repository. Runtime input updates use
`content_filter/expression_parameters/element`, verified against the actual
service and configuration readback.

**Consequence:** LBED plus dynamic local CFTs is viable for receiver selection,
but is not a drop-in replacement for our WAN-saving dynamic audience filters.
**Decision: do not adopt LBED for our current WAN.** Dynamic sender-side audience
filtering is required; receiver-only filtering does not meet that requirement.
The [discovery-strategies slides](../../docs/slides/dds-discovery-strategies.md)
record this decision and the measured comparison.
Retaining SEDP or adding a separately qualified sender-policy mechanism would
be necessary to preserve that behavior. This is a one-direction, reliable Event,
single-parameter test, not production ACT integration, multiple-parameter update
qualification, command execution/authorization, or a fleet/load/recovery gate.

## Before adding this to the production gateway

- Generate the static endpoint catalog from the same authoritative setup model
  as the native Routing Service XML; keep names, object IDs, partitions,
  types and matching QoS consistent.
- Decide which fleet identities are predeclared and how catalog updates are
  distributed. LAN payload changes can leave a binary outer envelope unchanged;
  new WAN participants/endpoints still require appropriate static definitions.
- Integrate with the **actual ACT processor** and its shared WAN endpoints.
  This native DDS-adapter proof does not qualify that processor combination.
- Resolve the dynamic writer-filtering limitation above before relying on LBED
  for WAN audience suppression. Qualify production SHARED partitions and actual
  team/mission filters, startup ordering and endpoint recreation separately;
  this rig uses directional fixed partitions.
- Qualify State dispose/unregister/revival, reliable loss repair, asymmetric
  outages, reader inactivity, multi-gateway load and cleanup.
- Measure discovery bytes and match latency on the actual radio profile.
  Removing SEDP is verified; a numerical whole-startup saving is not established.

## Product references

Connext AI was consulted; the native run and captures are the acceptance evidence.
Installed 7.7 documentation and examples were inspected before configuration:

- [LBED introduction](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/limited_bandwidth_endpoint_disc/users_manual/limited_bandwidth_endpoint_disc/users_manual/Intro.htm)
- [Plugin enablement](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/limited_bandwidth_endpoint_disc/users_manual/limited_bandwidth_endpoint_disc/users_manual/EnableLBEDPlugin.htm)
- [Plugin properties](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/limited_bandwidth_endpoint_disc/users_manual/limited_bandwidth_endpoint_disc/users_manual/Configuring_LBED_Plugin_.htm)
- [Discovery configuration](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/DISCOVERY_CONFIG_Qos.htm)
