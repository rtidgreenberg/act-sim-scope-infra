# Native compression of binary WAN payloads

## Result (2026-11-20)

**Yes: native Connext compression works with our fixed binary-envelope approach,
including actual Routing Service-owned endpoints.** The receiver automatically
recovers the unchanged envelope; its original application CDR bytes deserialize
back to the complete original DynamicData sample.

**LZ4 is the best next candidate for radio qualification, not a deployed choice.**
It substantially reduces redundant binary records, while random/already-compressed
bytes do not benefit. zlib also works and saves some small-message overhead.
**Reject bzip2 in this tested configuration:** it preserves data but emits native
compression errors and has substantially higher measured CPU/latency.

### Actual user-data wire savings

Two repetitions per cell, against the same uncompressed envelope workload.
Counts include captured user-DATA packet headers, not just compressed payload size.

| Synthetic binary workload | Serialized telemetry envelope | LZ4, threshold 0 | zlib, threshold 0 | LZ4, threshold 8192 |
|---|---:|---:|---:|---:|
| Small nested status | 248 B | 0% | **14.0%** | 0% |
| Medium structured numeric record | 4,272 B | **78.8%** | **83.2%** | **0%** |
| Larger structured numeric record | 16,568 B | **93.9%** | **94.2%** | **93.9%** |
| High-entropy binary record | 32,940 B | 0% | 0.17% | 0% |
| Record containing already-compressed bytes | 32,972 B | 0% | 0.18% | 0% |

Each workload mixes 32 State writes, 32 Periodic writes and 32 bidirectional
command/echo exchanges per repetition. Percentages cover that complete mix,
including the small commands and echoes. Tiny per-repetition differences arise
from payload values and metadata. No JSON application payload crosses the WAN.

**Binary does not mean incompressible.** Repeated numeric values and CDR
structure can retain substantial redundancy. Conversely, compressing random or
already-compressed bytes adds work with essentially no bandwidth return.
zlib's tiny savings on those rows include envelope/command overhead, not a
meaningful reduction of the high-entropy blob.

The 8 KiB threshold is **not automatically the right choice**: it bypasses a
4 KiB record that compressed well. LZ4 threshold 0 skipped the small fixture
when it did not shrink, and skipped the high-entropy fixtures. Select any
production algorithm/threshold using representative payload sizes and values.

### CPU and command latency

Example: the larger structured workload, summed over two repetitions:

| Configuration | Combined gateway CPU | Application command RTT p95 |
|---|---:|---:|
| No compression | 40 ms | 2.18 ms |
| LZ4, threshold 0 | 40 ms | 1.89 ms |
| zlib, threshold 0 | 60 ms | 2.01 ms |
| bzip2, threshold 0 (**rejected**) | **370 ms** | **5.91 ms** |
| LZ4, threshold 8192 | 30 ms | 2.01 ms |

CPU is user+system time of the **two native Routing Service processes**, measured
through `/proc`, with 10 ms tick resolution. Each row has 256 exact deliveries,
including 64 command/echo round trips. These short batches are too coarse to
claim that LZ4 is free or that sub-millisecond timing differences are significant.
Algorithm order is fixed; workload order reverses in the second repetition.

RTT includes native forwarding/compression/decompression, Python application
echo handling, native sample copies and polling. It is not a codec-only timing or
a radio RTT. Fixture construction and exhaustive byte/CDR equality checks occur
outside the measured delivery interval. No loss, RF emulation, link-rate cap or
real radio was used.

On the high-entropy workload, zlib used 100 ms of gateway CPU for only 0.17%
wire savings; the uncompressed control used 60 ms. Bzip2 used 470 ms and had a
7.33 ms command RTT p95. Preserve the raw measurements and these limitations;
do not infer a production latency budget from loopback results.

## Correctness and filtering

The final matrix records:

- **6,403 exact envelope deliveries and complete original CDR round trips**.
- **1,600 application command/echo RTT observations**.
- **203 metadata exclusions**, with no extra selected-reader deliveries and
  independent WAN sequence counts proving unwanted data was not transmitted.
- A separate LZ4-writer / compression-disabled-reader control produces the
  expected non-delivery.
- A zlib-compressed Event reader changes its destination interest **B -> C -> B**
  through actual Routing Service administration. Both destinations are offered
  each time; exactly one selected sample crosses the WAN in every phase.
  All three transmitted samples carry the native zlib compression identifier.
  Successful UPDATE replies and configuration GET readback confirm changes.
- **Zero capture drops** in every variant.
- **Zero native ERROR/FATAL lines** for uncompressed, LZ4, zlib, threshold,
  compatibility-control and dynamic-filter variants.
- **256 native bzip2 errors**:
  `RTIOsapi_Bzip2_compress:The output buffer length is not enough`.
  The affected data still arrives unchanged, but this is explicitly a rejected
  variant, not a clean pass or an error silently treated as success.

Unlike the [LBED experiment](../routing_service_lbed/README.md), this retains
SPDP2 **plus SEDP** on the WAN. Native metadata filtering remains sender-side,
including after the tested runtime interest changes.

## What is actually tested

```text
Original DynamicData
  -> public Connext CDR serialization
  -> fixed SerializedWanEnvelope.payload octets
  -> LAN -> Routing Service A
  -> native metadata filter + built-in sample compression
  -> WAN Event / State / Periodic
  -> Routing Service B: automatic sample decompression
  -> LAN: unchanged envelope and inner CDR bytes
  -> original DynamicData deserialization + complete equality
```

Two actual `rtiroutingserviceapp` processes own the endpoints. Both LANs use
dynamic discovery and fixed envelope topics. Only the test application loads
the original [application types](../serialized_wan_envelope/application_types.xml)
and [binary telemetry fixture](types.xml). Routing Service loads only the
fixed [envelope type](../serialized_wan_envelope/envelope.xml).

Compression is the native `representation/compression_settings` policy on the
WAN writer, authored in [qos.xml](qos.xml), not application-level compression
of the inner payload. `zlib.compress` is used **only to manufacture the
already-compressed input fixture**, before the timed test.
All compressed variants use `BEST_SPEED`; this does not compare every
compression level. Readers accept `MASK_ALL`, except the explicit
compression-disabled control.

WAN captures are scoped to **destination** ports for domain 111. The parser
counts unique physical writer/sequence identities and reads compression IDs
from the CDR encapsulation options. Those IDs and bit placement were checked
against the installed 7.7 SDK headers. Packet-byte totals include retransmissions
if present, not discovery or acknowledgment-only packets; the report also
retains total WAN bytes within each measured interval.

Event is reliable KEEP_ALL; State has a reliable KEEP_LAST(1) writer and
KEEP_ALL reader; Periodic is best-effort KEEP_LAST(1). Outer instance IDs are
unique per test write so State replacement does not confound compression.
This is **not** qualification of latest-state replacement or lifecycle.

## Reproduce and inspect

```bash
bash spikes/binary_wan_compression/run.sh
```

The owned `connext:7.7.0` container has no external network, private IPC,
UDPv4-only participants, a read-only checkout/license mount and disposable
`/tmp` state. Domains are LAN A **110**, WAN **111**, LAN B **112**, plus
administration **113** in the dynamic-filter control.

- `debug/test_reports/binary_wan_compression/report.json`: source hashes,
  per-stage measurements, summaries, acceptance and rejection decisions.
- The same report directory contains exact generated XML, native logs,
  image identity and the complete console.
- `debug/pcap/binary_wan_compression/`: bounded captures, one per configuration.

Each run clears only direct files in those two explicitly owned artifact roots.
All children are stopped/reaped; the container is removed and private
shared-memory remnants are checked.

The current result is **`COMPLETED_WITH_REJECTED_VARIANTS`**, with
`experiment_checks_passed: true` and `rejected_variants: ["bzip2"]`.
The runner deliberately returns **exit 1**, writes no `PASS` marker and retains
the rejection. Unexpected harness/API/data/control failures write `failure.json`.
Do not call the entire algorithm matrix green.

## Limits and next gate

This qualifies native sample compression of a binary envelope, **not integration
of the binary codec into the ACT processor**. Original CDR construction and
decoding happen in the test application; learning original types from discovery
was independently tested in the [serialized-envelope spike](../serialized_wan_envelope/README.md).

The numeric fixtures intentionally contain repeated values; they are not a
measured production payload distribution. Real snapshots, contacts, commands,
images and encrypted bytes may have very different redundancy. No compression
of discovery, RTPS headers or whole RF packets is claimed.

Next: representative payload mixes and a rate-limited/radio run, preserving
required Event history and measuring command latency, State recovery, Periodic
freshness, loss repair and fleet CPU. Lifecycle, restart/retention, admission,
other vendors/versions and encrypted deployments remain separate gates.
Production binaries, XML/QoS and the stopped operator mesh remain unchanged.

Connext AI was consulted and the existing shared correction log cross-checked;
installed schema, headers, native execution and packet captures are the evidence.
Product reference:
[Connext 7.7 data representation and compression](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/DATAREPRESENTATION_Qos.htm).
