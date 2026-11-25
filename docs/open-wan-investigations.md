# Open WAN investigations

Date: 2026-11-20.

This is the investigation backlog, not an approved configuration change or a
claim that the following capabilities are integrated. Binary payloads are the
likely direction; the existing ACT Routing Service processor still uses prepared
full-type unions.

## Summary

| ID | Investigation | Status |
|---|---|---|
| WAN-01 | Compression in the existing router with asynchronous publishing and its shared flow controller | Partial: full union comparison and effective post-compression pacing passed; binary, lifecycle/load/recovery gates remain |
| WAN-02 | Disable RTPX default-domain announcements throughout the runtime | Open audit: shared LAN/WAN profiles already disable them |
| WAN-03 | Disable the WAN built-in ServiceRequest channel when its dependencies are unnecessary | Open: establish dependencies and qualify a disabled-channel control |
| WAN-04 | Automatically derive envelope DDS keys from original topic key values | Open: limited string-key spike exists; generic key mapping unqualified |

## WAN-01: compression with asynchronous publishing and flow control

**Goal:** prove compression works in the existing Routing Service processor,
including its asynchronous writers, High/Normal priority channels and shared
`act_rs` flow controller. Do not treat the separate native-forwarding compression
spike as this integration gate.

**Existing evidence:** the [binary compression spike](../spikes/binary_wan_compression/README.md)
verified LZ4/zlib, exact original CDR round trips and sender-side metadata filtering
on actual Routing Service-owned endpoints. Bzip2 emitted native errors and was
rejected. The current [WAN QoS profiles](../harness_v2/qos/act_qos_profiles.xml)
assign Event, State and Periodic to the shared controller.

**2026-11-24 integration evidence:** the
[full union processor comparison](../spikes/full_union_compression/README.md)
uses the current six-topic IPv6 setup and shared nominal 4 Mbps controller,
complete monitoring and two priority bands. After user-authorized repairs to
the six-record RouterConfig/cache bounds and P1_Periodic health dispatch,
matched off/LZ4/zlib runs preserve all 1,536 command/ACK round trips.
Repeated-value 8 KiB command-family DATA shrinks 86.8%/90.9%; random command
DATA shrinks 0.0%/3.3%. Contact dispose/revival and final periodic markers pass.
This is an isolated typed-union processor gate, not binary-codec, arbitrary
filter-transition, unregister, sustained overload, loss/repair or radio
qualification. Source fixes and experimental compression are not deployed.

**2026-11-24 accounting follow-up:** a separate
[six-topic union accounting comparison](../spikes/full_union_compression/README.md#flow-controller-accounting-follow-up-2026-11-24)
passes all 16 trials at supported 2/4 Mbps allowances, with 2,048 exact command/ACK
exchanges and independently replayed captures. At 4 Mbps, the repeated-value
8 KiB burst completes in 5.162 s off versus 0.227 s zlib; random bytes remain
5.179/5.088 s. Halving the allowance approximately doubles the uncompressed
duration. Effective pacing thus tracks the compressed representation, not the
original size. Captures include two native WAN destinations; this is not a
single-destination result. Exact per-token overhead/rounding, packet repair,
sustained load and radio capacity remain unqualified. No deployment occurred.

**Investigation and acceptance checks:**

- Production source now enables the tested zlib/BEST_SPEED/threshold-zero
  settings on three-/six-topic Routing Service WAN writers only. This is not a
  deployment or closure of the remaining load/loss/radio investigations.
- Compare uncompressed, LZ4 and zlib using the actual processor and effective
  XML profiles. Verify compression, asynchronous publishing, controller
  assignment and priority bands together, not just XML intent.
- Exercise all three families, concurrent traffic, representative binary
  payload sizes/values and fragmented samples. Keep topology, offered load,
  audiences and controller budget identical between comparisons.
- Verify complete original payload and key equality, dynamic sender filtering,
  required Event coverage, State replacement and dispose/unregister/revival.
- Exercise overload, packet loss and recovery. Measure command round-trip time,
  queue/repair progress, CPU, user-DATA bytes and total WAN bytes.
- Effective post-compression pacing is now measured in the current union path.
  Quantify exact token rounding/header costs and retransmissions separately;
  do not extrapolate usable throughput directly from the compression ratio.
- Require zero unexpected native errors and capture drops. Retain explicit
  incompatible-reader controls. Select a threshold from representative data;
  the previous 8 KiB threshold missed useful savings on a 4 KiB fixture.

**Close when:** the current processor's combined configuration passes correctness,
filtering, lifecycle and load/recovery gates, with measured savings and costs.
Any binary-codec integration needed for this test remains an explicit prerequisite.

## WAN-02: disable RTPX default-domain announcements

**Goal:** eliminate unnecessary RTPX tool/default-domain announcements from the
runtime without disabling normal participant or endpoint discovery.

**Already configured:** `participant_discovery_base` sets
`default_domain_announcement_period` to infinity. Both the LAN base and WAN router
base inherit it in the [shared QoS source](../harness_v2/qos/act_qos_profiles.xml).
The historical [independent MAC/RTPS audit](../spikes/mac_rtps_accounting/README.md)
observed zero RTPX and other-domain RTPS frames in a 45-second seven-radio capture
after the isolation changes.

**Investigation and acceptance checks:**

- Audit effective native QoS and generated deployment XML, including gateways,
  applications, monitors and management participants. Identify any participant
  that bypasses the shared profile.
- Capture bounded startup, steady-state and restart windows. Require zero RTPX
  on the WAN-facing interface; report other-interface traffic separately.
- Confirm normal WAN discovery, LAN type discovery, dynamic filters, management
  and application delivery remain operational.
- Apply any missing suppression through external XML and the owned deployment
  lifecycle, not edits to running generated configuration.

**Close when:** actual runtime coverage is verified. Do not add redundant settings
or describe this already-configured policy as a newly demonstrated saving.

## WAN-03: disable the WAN built-in ServiceRequest channel

**Goal:** investigate disabling the built-in ServiceRequest channel on WAN
participants if the selected binary-envelope architecture does not require its
features.

**Working hypothesis, not a finding:** instance-state consistency (ISC) and
TypeObject v2 support may not be needed on this WAN path. Verify their actual
channel dependencies in Connext 7.7 before deciding what can be disabled.
Binary payloads alone do not settle that question.

**Investigation and acceptance checks:**

- Identify the precise built-in channel, supported XML setting and effective
  native behavior. Distinguish it from Routing Service remote-administration
  request/reply topics and other discovery/type-request mechanisms.
- Establish which features use it, including ISC and TypeObject/type resolution,
  and whether other required behavior depends on it. Check installed
  documentation/APIs and verify claims with a runnable control.
- Compare enabled/disabled WAN variants with fixed binary envelopes, compatible
  local schemas and no original application type graph distributed over WAN.
  Verify actual absence of the targeted requests, not only property readback.
- Preserve SEDP and dynamic writer-side audience filtering; the
  [LBED result](../spikes/routing_service_lbed/README.md) shows why removing
  discovery traffic must not sacrifice sender suppression.
- Keep destination-local type discovery and decoding operational. A binary WAN
  payload still needs compatible local types; do not disable required LAN
  type-learning channels as a side effect.
- Test startup, late joining, gateway/writer restart, loss, recovery and
  deletion/revival. If native ISC is intentionally unused, identify and qualify
  the mechanism preserving required instance/lifecycle behavior instead.
- Compare discovery/control bytes and useful delivery. Record explicit
  unsupported configurations or lost functionality rather than calling them
  successful bandwidth reductions.

**Close when:** dependencies are understood and the disabled WAN configuration
preserves required behavior with measurable benefit. TypeObject version and
payload CDR representation must not be conflated.

## WAN-04: automatically derive envelope keys from topic keys

**Goal:** discover a topic's DDS key members, read their values and automatically
populate the envelope's DDS-keyed identity, without manual per-topic key mappings.
Preserve the original keyed values, not merely the application payload bytes.

**Existing evidence:** the [serialized-envelope spike](../spikes/serialized_wan_envelope/README.md)
extracts one top-level string key into `instance_id`. Its
[fixed envelope](../spikes/serialized_wan_envelope/envelope.xml) keys on source,
original topic and instance ID. Compound, numeric, nested and unkeyed types are
not qualified by that result.

**Investigation and acceptance checks:**

- Obtain key annotations and values from discovered DynamicTypes/DynamicData.
  Establish support for compound keys, numeric/string/enum keys and nested keys;
  define explicit behavior for unkeyed or unsupported types.
- Decide whether to carry original keyed values in a fixed generic key
  representation or automatically mirror them as typed DDS-keyed members.
  Mirroring deployment-specific members changes the envelope schema; resolve
  that tradeoff against the likely fixed-binary WAN contract.
- Verify that the selected outer representation actually determines DDS
  instance identity. Keys inside opaque payload bytes alone are not an
  automatically keyed envelope.
- Define deterministic, unambiguous key encoding and ordering, including type
  information and bounds. Preserve source/topic isolation and exact values;
  do not silently truncate or concatenate ambiguous key tuples.
- Prove equal original keys map to the same outer instance across updates and
  restarts, while distinct keys/sources/topics remain independent.
- Restore exact original keys on the destination LAN. Test key-only
  dispose/unregister/NO_WRITERS and revival as well as valid-data writes;
  full payloads may be unavailable for lifecycle samples.
- Cover independent histories, State KEEP_LAST replacement, duplicate producer
  aliases and unsupported/oversized keys with explicit diagnostics.

**Close when:** generic key derivation and destination reconstruction pass native
DDS instance/history/lifecycle tests without hand-authored per-topic mappings.

## Execution and evidence

Start with isolated owned test environments. Keep all fixed DDS policies in
external XML; do not patch live generated configurations or overwrite running
binaries. A deployment, if requested later, must use the coordinated owned
mesh lifecycle.

For each investigation, retain the effective configuration, control comparison,
exact test results, measured byte/latency costs and unresolved limitations.
Use `debug/test_reports/<test_id>.*` for compact local evidence and `/tmp` for
disposable state. Do not combine separate spike results into a claimed integration
pass.

Related synthesis: [Bandwidth Optimization slides](slides/bandwidth-optimization.md)
and [current Routing Service guide](../router/routing_service/README.md).
