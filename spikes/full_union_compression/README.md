# Compression on the current full union processor

## Scope and setup

This is the actual ACT Routing Service processor, freshly compiled from current
source, with Control_20 and Platform_30/31. Current source uses full original
structures in the common schema-4 WanFrame type on the six IPv6 WAN topics,
complete native monitoring,
mission receive policy, two priority bands per writer and the shared nominal
4 Mbps `act_rs` asynchronous flow controller.

Only WAN writer compression changes: none, LZ4 or zlib, BEST_SPEED, threshold
zero, with compatible readers. Normalized generated XML is identical between
algorithms. A configured low-priority Debug route exercises P3_Periodic.
All six families appear in the capture. Domains 110-113 and loopback run inside
an owned container with no external network and private IPC.

**No mesh, persistent binary or deployed QoS was changed.**

The production source now defaults three-/six-topic Routing Service WAN writers
to zlib, BEST_SPEED, threshold zero. This runner still applies explicit
off/LZ4/zlib XML overrides for controlled comparisons. Historical reports retain
their original matched source/configuration hashes; they are not rewritten.
The numerical compression comparisons below used the earlier
WanEvent/WanStatus contract, not the new common type.

## Common-type production source gate (2026-11-25)

`--production-defaults` now also verifies the six native topic/type
advertisements, transient-local State writer/reader QoS, and live contact
unregister/revival. After capturing and analyzing the measured workload and live
lifecycle, it separately creates a filtered late State reader: two selected keys
each replay exactly their latest complete value, while a third key is excluded.
It then restarts the isolated source gateway, observes empty retained history
for 1.5 seconds after all State matches, republishes and verifies recovery.
The workload capture deliberately ends before these extra reader/restart checks;
its original 18-writer attribution remains exact.

Accepted local evidence:
`debug/test_reports/common_wan_20261125/attempt3/report.json` and
`debug/pcap/common_wan_20261125/attempt3-production.pcapng`.
All four stages pass: 256 exact command/ACK pairs, 983 exact original
deliveries, dispose/unregister/revival, final ALIVE peers, zero native errors and
zero capture drops. The State follow-up passes without refreshing the original
values before the late-reader check. The initial attempt exposed monitoring
constructing its own old codec; production monitoring now copies the service
codec. A second attempt passed the native assertions but incorrectly included
the extra-reader/restart traffic in the fixed-18-writer capture oracle; separating
the capture phases repairs that test boundary without weakening its checks.
Earlier failed-attempt evidence is retained separately, not counted as green.

This qualifies source only, not a deployment or new compression-performance
comparison. Earlier two-/three-topic modes keep schema 3 and their original QoS.

## Baseline repairs

The first uncompressed run exposed two pre-existing six-topic monitoring bugs:

- RouterConfig and the LAN health cache still bounded writer-filter records to
  three. Six records caused `router_config_publish_failed`.
- The monitoring receive callback recognized Status/Periodic, not P1_Periodic.
  Received health therefore did not populate the peer roster.

The user authorized fixing these in source before continuing. The bound is now
six, the generated XML is regenerated, and the callback recognizes the selected
health family. Native admin type round trips and full-type bindings pass.
This is a wire-type change: rebuild consumers together before deployment.
The passing comparisons use the **same repaired build** for every algorithm;
they are not a comparison between broken and repaired monitoring.

## Measured before/after

Primary evidence: `debug/test_reports/full_union_compression_20261124T174940Z/report.json`,
with native logs, generated XML, effective QoS, source/binary/capture hashes and
captures beside it. Two repetitions reverse order:
none/LZ4/zlib, then zlib/LZ4/none.

The command-family comparison has a fixed 64 commands plus 64 exact ACKs in
each workload. Values below are mean captured P2_Event DATA-frame bytes across
the two repetitions, including headers, fragments and retransmitted frames.

| Command workload | Off | LZ4 | zlib | LZ4 saving | zlib saving |
|---|---:|---:|---:|---:|---:|
| Small command | 153,088 | 153,047 | 101,964 | 0.0% | 33.4% |
| Repeated-value 8 KiB command | 1,219,702 | 160,703 | 110,807 | 86.8% | 90.9% |
| Random 8 KiB command | 1,219,840 | 1,219,702 | 1,180,076 | 0.0% | 3.3% |
| Burst of 64 repeated-value 8 KiB commands | 1,187,265 | 144,532 | 95,425 | 87.8% | 92.0% |

The random command octets are incompressible; the remaining zlib benefit comes
from the typed envelope/metadata and framing. This is not a claim of 3.3%
compression of random octets themselves.

The burst runs also carry contacts and periodic traffic. Worst observed command
RTT was **2,599 ms off, 181 ms LZ4 and 130 ms zlib**. Mean total native gateway
CPU time for that workload was **1,150 / 550 / 540 ms**. The measured drain
interval was **2.578 / 1.249 / 1.254 seconds**, including a fixed 1.2-second
periodic settling interval. Lower total CPU here includes shorter scheduling and
monitoring time; it does **not** prove compression has no CPU overhead.

Across all monitored WAN traffic, repeated-value command workload DATA savings
were 78.6% LZ4 and 87.6% zlib; burst DATA savings were 81.1% and 88.7%.
Those totals include changing monitor traffic and periodic delivery counts.
Use the fixed command-family table, not those mixed totals, to compare a fixed
number of required application events.

## Correctness and accounting

- Six algorithm/repetition cases and 24 measured stages completed.
- All **1,536 command/ACK round trips** passed with exact original structures.
- **6,110 delivered originals** passed full DynamicData equality. Every
  required Event, distinct contact and paced final periodic marker arrived.
- Each stage offers 647 original deliveries. Periodic intermediates are not
  required: KEEP_LAST and the existing one-second LAN time filters may omit
  them. Final markers use distinct sequences after a 1.2-second quiet window.
- Mission policy intentionally excludes ActPlatformStatusMode and peer
  PlatformData; the negative LAN checks preserve this behavior.
- Actual composite contact key materialization, disposal and revival pass.
- Both peers become ALIVE; RouterConfig publication failures are zero after
  repair. All six families retain their effective asynchronous/controller/
  reliability/history/priority settings. Native compression IDs are verified.
- All six capture interface drop counts are zero; native ERROR/FATAL lines
  are zero, not all warnings.
- Wire accounting includes DATA_SESSION, DATA_FRAG_SESSION (`0x81`) and native
  two-band channel writer identities. Physical wire sequence counts are not
  substituted for the application command/ACK equality oracle.
- The affected offline wire parser class passes 21 tests. An attempted broader
  host wire module run was not green: unrelated launcher fixtures failed and
  host pytest/Connext support was unavailable; it is not included in this gate.

Earlier exploratory captures were reanalyzed after fixing incomplete
fragmentation and channel attribution. The final fresh-build launcher run uses
the repaired accounting directly and records its source hashes. Do not use
the earlier partial wire totals.

## Repeat

From the repository root:

```bash
python3 spikes/full_union_compression/launch.py
```

The launcher requires the existing `connext:7.7.0` image and cached yaml-cpp
source under `router/build/_deps/yaml-cpp-src`. It builds privately under `/tmp`,
runs the two native targets, retains a new timestamped report directory, and
cleans its temporary build after the comparison. Set `CONNEXT_SHARED_DIR` only
to a readable license directory containing `rti_license.dat`.

## Flow-controller accounting follow-up (2026-11-24)

**Measured effective pacing follows the compressed representation, not the
original serialized size.** Compression therefore improves useful sample
throughput under the same controller allowance in this tested setup, rather
than only shrinking WAN packets.

Primary evidence:
`debug/test_reports/flow_token_accounting_20261124T191608Z/report.json`.
This is a separate matched test, not the earlier mixed-workload comparison.
Each cell below is the mean of two trials, with algorithm order reversed.
Every trial bursts 128 commands containing 8 KiB of repeated or seeded random
octets through the current typed unions and waits for 128 exact acknowledgements.

| Controller allowance | Command payload | Off: burst completion | zlib: burst completion | Command DATA-frame saving |
|---|---|---:|---:|---:|
| 2 Mbps | Repeated values | 10.278 s | 0.405 s | 95.51% |
| 2 Mbps | Random bytes | 10.272 s | 10.120 s | 1.35% |
| 4 Mbps | Repeated values | 5.162 s | 0.227 s | 95.42% |
| 4 Mbps | Random bytes | 5.179 s | 5.088 s | 1.35% |

The uncompressed control approximately doubles its duration when the allowance
halves, showing that the controller is limiting these bursts. Repeated-value
samples become dramatically faster with zlib at both allowances; random
samples do not. This distinguishes post-compression pacing from a model that
continues debiting the original sample size.

Important measurement details:

- Fresh native build; both affected native type/binding targets passed.
- Three fully monitored gateways, all six asynchronous WAN pairs and both
  priority channels. Effective composed participant QoS is verified, including
  2,500/5,000-byte tokens, one token per 10 ms and a one-token bucket.
- Compression is the only generated-XML difference within each allowance.
  The supported minimum is 2 Mbps; an initial 1 Mbps control was explicitly
  rejected without changing that production validation rule.
- One application target is addressed, but captures show **two physical WAN
  destinations** in this configuration. Byte totals include both; this is
  not a single-destination result or a new sender-filter qualification.
- Command DATA totals count Control_20's P2_Event frames, including native
  channel writers, fragments, headers and retransmissions. ACK bytes and
  other monitoring traffic are excluded from that column; burst completion
  still waits for every ACK. These percentages are not the earlier mixed
  command/ACK savings and are not RF-airtime savings.
- Captures continue for 0.75 s after ACK completion to include the other
  destination's tail. The measured completion time excludes that interval.
  The analyzer requires command DATA to settle before capture ends.
- **16/16 trials, 2,048 exact command/ACK exchanges and 4,096 original-payload
  comparisons passed.** Zero native ERROR/FATAL lines, capture drops or private
  shared-memory remnants. All sixteen captures and hashes were independently
  replayed and the comparison reproduced.
- Earlier attempts stopped on setup/readiness checks and are not passing
  evidence. The final harness initializes both discovery observers before
  gateways, waits for original LAN matches, and separates bounded startup
  probes from measured events. Every final warmup took one attempt; measured
  commands are each written once, with no application retries.

Repeat with the existing private-build launcher:

```bash
python3 spikes/full_union_compression/launch.py --token-accounting
```

The result establishes effective accounting behavior, not an exact internal
per-token debit formula. Fragment/header charging, rounding and coalescing,
different fan-out, repairs under packet loss, sustained load and radio operation
still require their own measurements. No production QoS, persistent binary or
live mesh was changed.

## Production-default six-topic baseline (2026-11-24)

```bash
python3 spikes/full_union_compression/launch.py --production-defaults
```

This separate mode leaves writer/reader data-representation settings untouched:
no off/LZ4/zlib comparison overrides are appended. It verifies effective
production zlib, asynchronous shared controller and priority channels using
the actual ACT processor, three gateways and the six-topic IPv6 contract.
It cannot be combined with `--token-accounting`.

[The retained report](../../debug/test_reports/production_six_topic_20261124T223259Z/report.json)
passed four workload stages, 256 exact command/ACK exchanges and 949 exact
original deliveries. All 18 WAN writers/readers, ALIVE peers and contact
dispose/revival passed; native errors and capture drops were zero.
Worst observed command RTT across the stages was about 157 ms.

This validates the current production-default configuration as a baseline for
subscription work. It does not implement or qualify six-topic manual selection,
EMANE/radio performance or the five live mission workflows. No persistent binary,
deployment or live mesh was changed.

## Remaining limits

This verifies compression working alongside the current asynchronous publisher,
flow controller and union codec. It does not close WAN-01: binary-codec
integration, arbitrary dynamic team/filter transitions, incompatible-reader
controls, unregister, same-key State churn, sustained overload, packet loss/
repair, fleet and EMANE/radio gates remain. Improved burst drain is empirical;
the accounting follow-up above now supports post-compression pacing. Exact
per-token charges, rounding and repair semantics remain unqualified.
Threshold zero is an experiment setting, not a deployment recommendation.
