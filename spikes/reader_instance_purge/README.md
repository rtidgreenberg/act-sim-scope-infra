# Reader instance purge spike

Tests Connext Professional 7.7.0 native key availability for dispose/unregister
relay, including finite nonzero reader instance purge delays.

```bash
bash spikes/reader_instance_purge/run.sh
```

Requires the existing `connext:7.7.0` Docker image and the license-only directory
selected by `CONNEXT_SHARED_DIR` (default documented in the runner).
No Python test dependencies or host DDS SDK are needed.

See [PLAN.md](PLAN.md) for the matrix and isolation rules. Fixed QoS lives in
[qos.xml](qos.xml); the native probe is
[reader_instance_purge.cxx](reader_instance_purge.cxx). The structured report is
written to [reader_instance_purge.json](../../debug/test_reports/reader_instance_purge.json).

## Results

Verified 2026-11-10 against the installed Connext Professional 7.7.0 SDK
(`x64Linux4gcc8.5.0`) in `connext:7.7.0`. **21 cases, zero validation failures**:
12 native lifecycle/relay/revival cases, eight expected finite-instance-delay
rejections, and one finite-sample-delay enabled-reader control.

| Reader instance retention | Key during C++ take loan | After returning loan | Native relay |
|---|---|---|---|
| Default NO_WRITERS: 0 | Available | Unavailable immediately and through 5500 ms | Observed |
| Default DISPOSED: infinite | Available | Available through 5500 ms | Observed |
| Explicit DISPOSED: 0 | Available | Unavailable immediately and through 5500 ms | Observed |
| Explicit infinite, either state | Available | Available through 5500 ms | Observed |
| Either instance delay: 2 or 5 seconds | Reader creation rejected | Not applicable | Not attempted |

Both `take()` and `read()` followed by `take()` were exercised. Additional
zero-delay tests held the take loan for **2100 ms**: key lookup still succeeded
before returning it, then failed immediately after its destruction.

For finite values, XML/provider profile loading succeeds, but enabled reader
construction fails with the exact native consistency diagnostic:

```text
autopurge_nowriter_instances_delay (2s 0ns) must be DDS_DURATION_ZERO or DDS_DURATION_INFINITE.
autopurge_disposed_instances_delay (2s 0ns) must be DDS_DURATION_ZERO or DDS_DURATION_INFINITE.
```

The same diagnostics occur for 5 seconds. Finite **sample** delays of 2 seconds
are accepted by an enabled reader; this separate control does not measure sample
purge timing and does not provide finite **instance** retention.

Every successful relay used `reader.key_value()` during input processing,
`destination.lookup_instance(key)`, and native dispose/unregister. No fallback
key cache or blank valid write was used. The destination observed the expected
native state and subsequent ALIVE revival. The test key exceeds 16 bytes, so it
cannot simply be read out of a short inline key/handle representation.

## Consequence for the router plan

The installed 7.7.0 reader does **not** support the proposed 2/5-second instance
retention delay. The tested alternatives are:

1. Extract/translate the key and relay synchronously while the C++ input loan
   remains alive. These cases required no separate key cache or QoS change.
2. Select infinite instance retention in external XML if processing must outlive
   that loan, with explicit resource/cleanup qualification.

This is empirical evidence for the tested native C++ path, not a documented
guarantee under arbitrary resource replacement, concurrent consumers, or unseen
instances. It does not prove that payload-derived routing context can be recovered
from DDS keys. Do not copy the earlier Python observer failure into a claim that
C++ key lookup necessarily fails during its loan.

Following this spike, the user selected **infinite reader-instance retention**
for the lifecycle implementation, with automatic purging deferred. See the
[selected retention policy](../../docs/two-topic-native-lifecycle-plan.md#selected-reader-retention-policy).
This decision does not change the measured spike results. The subsequent
[adapter implementation checkpoint](../../docs/two-topic-wan-migration-plan.md#320-native-lifecycle-implementation-and-isolated-regression-gate-2026-11-10)
adds the XML retention profile and clears the four lifecycle acceptance failures.
This spike is not a live lifecycle acceptance gate.

Reference: [7.7 reader lifecycle policy](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/READER_DATA_LIFECYCLE_QoS_Policy.htm).
The Connext AI answer agreed that finite nonzero instance delays are unsupported;
the build, native consistency errors, and measured key lookups are the evidence.
