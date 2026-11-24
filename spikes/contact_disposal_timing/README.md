# ContactReport disposal: relay latency and reader retention

## Delayed/interrupted WAN follow-up

Run the user's requested **3-second one-way WAN delay, 2-second outage, and
10-second writer cleanup** experiment:

```bash
bash spikes/contact_disposal_timing/run_delayed.sh
```

**Executed 2026-11-19: all eight cases passed**, two repetitions of each row:

| Writer cleanup | WAN condition | Platform dispose -> C2 DISPOSED | WAN writer resources reclaimed |
|---|---|---|---|
| Zero | 3 s delay | 3.003 / 3.012 s | 7.506 / 8.057 s |
| Zero | 3 s delay + 2 s blackout | 11.469 / 13.564 s | 14.519 / 16.874 s |
| 10 s | 3 s delay | 3.005 / 3.009 s | 10.312 / 10.412 s |
| 10 s | 3 s delay + 2 s blackout | 11.361 / 14.125 s | 14.770 / 17.274 s |

Values within each table cell are sorted. All times start immediately before
the platform application's dispose. Cache observations run every 50 ms; the
report retains the last retained/first reclaimed bracket. They are not exact
internal cleanup timestamps or worst-case performance guarantees.

**The important result: ten seconds is not a deadline that discards an
unacknowledged dispose.** Native WAN writer resources remained until C2's
gateway acknowledged the dispose, including cases needing more than ten
seconds. Cleanup required both the configured interval and acknowledgment.
The zero-delay baseline also delivered every disposal: zero means cleanup
once acknowledgment permits it, not immediate abandonment of in-flight data.
A two-second outage can require substantially longer recovery because
discovery/heartbeat/repair traffic also traverses the delayed WAN.

### What was actually exercised

- The same deployed processor/helper hashes documented below; no production
  rebuild, live lifecycle action or application-data substitute for disposal.
- Native Routing Service platform/C2 gateways and original ContactReport
  payload/authoritative-key checks. Every C2 lifecycle sample was invalid
  `NOT_ALIVE_DISPOSED` for the previously received instance.
- Linux `tc netem` in a disposable `--network none --ipc private` container.
  Only domain-212 destination UDP ports 60400-60649 traverse the delayed
  queue, in both directions. LAN domains 211/213 are outside that range.
  WAN discovery and repair traffic are deliberately included.
- Independent UDP calibration measured 3.000054-3.000089 seconds on the WAN
  queue; an out-of-range control took at most 0.028 ms. During each blackout
  the control packet was dropped and native queue drop counters increased.
  Measured loss windows were 2.002-2.003 seconds and spanned the actual
  source-dispose timestamp. This is measured software impairment, not EMANE
  RF qualification.
- Writer cleanup is authored only in isolated XML for the application-event
  and WAN Event/State profiles, comparing zero against ten seconds.
  Actual native source and WAN writer QoS are checked. WAN writer durability
  is VOLATILE; finite delays are not qualified for durable writer queues.
- All relevant reader instance/sample delays remain INFINITE. The probe
  observes the disposal, confirms authoritative keys, retains it until its
  final take, then returns the loan. No unread-reader timeout was introduced.
- A test-only shared library enumerates the platform service's existing WAN
  State writer and reads native cache counters; it creates no participant or
  endpoint and changes no QoS. It records disposed/alive counts,
  unacknowledged sample counts and actual writer cleanup QoS. Polling stops
  before gateway teardown. The WAN cache had zero unacknowledged samples at
  cleanup in every case.
- The source-writer cleanup observations in the raw native probe start only
  after C2 receipt; they are upper bounds for that source writer. The table
  instead uses the continuously sampled **WAN writer**.

Evidence:
[`report.json`](../../debug/test_reports/contact_disposal_delayed_20261119/report.json)
and
[`inputs-and-logs.tar.gz`](../../debug/test_reports/contact_disposal_delayed_20261119/inputs-and-logs.tar.gz).
The archive retains prepared XML/YAML/types, actual isolated QoS, gateway/native
logs, 50 ms writer-cache observations and build diagnostics. All processes
exited zero; private scratch/queues disappear with the container.

### Compared with the linked UMAA cleanup profile

The [pinned UMAA reference](https://github.com/rticommunity/rticonnextdds-usecases-umaa/blob/ecb923646e7f3fe569b773c3946444e793d8a8be/qos/umaa_qos_lib.xml#L143)
combines several independent controls. Its writer ZERO setting is represented
by the baseline above. Its ten-second **reader sample** delay is not equivalent
to our ten-second **writer instance** delay: the installed 7.7 reader-lifecycle
manual says sample purge can drop unread samples lazily under pressure.
That is not a guarantee that a stalled relay/application processes a dispose.

The reference's max-instances limit and fully-processed-instance replacement
are a separate, plausible pressure-driven resource policy, not timed deletion.
They were not adopted or pressure-qualified here. No claim is made for
reader restarts, inactive-reader eviction, writer restart, long outages,
late-join replay, concurrent key churn, sustained load, or GUI cache expiry.
These active reliable-peer results are not persistence across process failure.
No new cleanup setting was applied to production.

## Reproduce

For the extended long-outage, reader-inactivity, restart, same-key ordering
and bounded-reader findings, see
[Instance State Through DDIL](../../docs/instance-state-through-ddil.md).
The extended WAN runner is `bash spikes/contact_disposal_timing/run_ddil.sh`;
native-only resource controls are in
[instance_state_resources](../instance_state_resources/README.md).
They remain isolated from the retained mesh.

From the repository root:

```bash
bash spikes/contact_disposal_timing/run.sh
```

The runner invokes Docker through `sg docker -c`, requires the existing
`connext:7.7.0` image and the license-only directory
`/home/dgreenberg/rti_connext_dds-7.7.0` (override with `CONNEXT_SHARED_DIR`).
It needs no pytest installation or network downloads. Only this spike's native
probe is built; **the deployed processor and helper are not rebuilt**.
The runner deliberately refuses a different deployed binary hash.

Evidence:

- [`report.json`](../../debug/test_reports/contact_disposal_20261119/report.json):
  all monotonic measurements, native instance-cache observations, effective
  lifecycle settings, assertion results, binary/input hashes, commands and errors.
- [`inputs-and-logs.tar.gz`](../../debug/test_reports/contact_disposal_20261119/inputs-and-logs.tar.gz):
  actual input YAML, prepared gateway XML and type registries, isolated QoS,
  native stdout/stderr, gateway logs and probe build log.

Only these two owned evidence files are overwritten on rerun.

## Executed original reader-retention result — 2026-11-19, 20:30:52 UTC

**PASS: nine real processor relay/retention trials and one expected finite-delay
reader-creation rejection.** The final runner exited zero.

Connext / Routing Service 7.7.0, `x64Linux4gcc8.5.0`, image
`sha256:cbabafb1dab80c66483f092e1770249a6196d974b01592fe3a52979613f5f7a0`.
Repository revision: `5f55ed4ca76db3ceb761bf851dd9861656a51e0e`.

Persistent binary SHA-256 values, checked before and after the experiment:

```text
router/build/routing_service/libact_gateway_processor.so
022941caaef2604dd41657da494b9144f17e5bbb7fd1a6c7b816e4dfd007f94b

router/build/routing_service/act_rs_config
406e734e0f713443fd4dd2a16bcbd6e4665d9eae583660211abb9fd1820ab6bf
```

This is the deployed processor, **not** a rebuild containing the source-only
diagnostics fix.

### 1. Writer dispose -> C2 invalid DISPOSED

Milliseconds from immediately before platform `writer.dispose_instance()` to
the first C2 `read()` observation of an invalid
`NOT_ALIVE_DISPOSED` sample:

| C2 reader profile | Trial 1 | Trial 2 | Trial 3 |
|---|---:|---:|---:|
| Inherited production `lan_event` | 11.829 | 11.784 | 9.684 |
| Isolated zero instance delay | 10.734 | 2.262 | 2.245 |
| Zero delay, minimum state disabled | 4.376 | 4.377 | 9.583 |

Across all nine: **2.245–11.829 ms**, median **9.583 ms**.
These are application-observation timings, including native dispose call
execution and approximately 1 ms polling, not exact network-arrival timestamps
or a production performance guarantee.

Every trial populates **all 18 original `contact_report` fields**. The C2 valid
payload must equal the entire original DynamicData value after replacing only
`source` with the authoritative platform identity, `Platform_30`. The producer
deliberately writes `source="deliberately-wrong-producer"`. Original
`source_node`, audit/timestamps and every other field must remain unchanged.

The subsequent invalid dispose must have the same instance handle as the
verified valid payload. Native `key_value()` must recover both original key
fields, `(source="Platform_30", contact_id="long-contact-key-N")`, while the
take loan is held. A lookup using the producer's incorrect source must fail.
No replacement valid “lost” report or reconstructed blank payload substitutes
for native disposal.

### 2. Retention and automatic instance purge

The effective inherited production reader settings were:

- `autopurge_disposed_instances_delay = INFINITE`;
- `autopurge_disposed_samples_delay = INFINITE`;
- `keep_minimum_state_for_instances = true`.

**No automatic disposed-instance purge was observed for that default.**
After taking the notification and returning its loan, the native cache still
reported one disposed instance, zero detached instances, and successful
authoritative-key lookup/recovery. Repeated inspections at 100, 1000, 2500 and
5500 ms after release retained that state.

Last observed retention since writer dispose was **5962.343, 6462.312 and
7210.269 ms** respectively. Each includes at least 5500 ms after loan return.
These are lower bounds (right-censored observations), **not finite
time-to-purge measurements**. Infinite configured retention does not promise
survival under arbitrary resource replacement or reader deletion.

The isolated zero-delay controls demonstrate actual purge ordering:

1. Leave the received notification untaken for 250/750/1500 ms respectively.
   The native disposed instance and authoritative keys remain present.
2. Take it, hold the native loan for another 200 ms, and inspect again.
   The disposed active instance and key still exist.
3. Return the loan. The first subsequent native cache inspection reports zero
   disposed active instances and failed per-key lookup/recovery.

| Zero-delay control | First active-state-purged observation since dispose, ms |
|---|---|
| Default minimum-state retention | 461.193 / 952.718 / 1702.725 |
| Minimum-state retention disabled | 454.872 / 954.867 / 1710.084 |

The last-retained to first-purged observation brackets span **0.124–0.175 ms**
and straddle loan return. They include instrumentation, native API calls and
scheduling; they are not exact purge execution durations. Purge was already
visible on the first post-return query.

With default minimum-state retention, **one detached instance remains**.
Therefore that control proves removal of active disposed state, not erasure
of all internal instance metadata. With minimum-state retention disabled,
disposed/alive/detached counts are all zero and the key is unresolvable.
The controls remain in that state at another inspection 500 ms after release.

An isolated XML setting of **two seconds** for disposed-*instance* delay loads
through QosProvider, but enabled reader creation rejects it with:

```text
autopurge_disposed_instances_delay (2s 0ns) must be DDS_DURATION_ZERO or DDS_DURATION_INFINITE.
```

The runner requires this exact native diagnostic, not just any failed reader.
Consequently, a finite nonzero instance-delay control is **not supported on
this installation**. Zero is the accepted control; no sample-delay timer is
misrepresented as an instance-retention timer.

### Three different meanings of “removed”

- **Sample consumption:** `take()` removes a queued notification; returning
  its loan releases native references. Neither alone proves instance purge.
  In this run the cache's `sample_count` was even zero while an invalid
  lifecycle notification was still available: this counter is not an
  instance-existence test or a count of all possible notifications.
- **DDS reader instance purge:** measured here using native
  `datareader_cache_status()` disposed/detached counts together with per-key
  `lookup_instance()` and handle-based `key_value()`. Default infinite
  retention remains after the sample has been consumed.
- **GUI/cache expiry:** separate application policy. No dashboard browser,
  target-cache expiry, or live reader state was measured or modified.

## Exact scope, assertions and safety

Two native Routing Service processes carry
`Platform_30 LAN (211) -> WAN State (212) -> Control_20 LAN (213)`.
The original ContactReport is packed/unpacked by the deployed processor.
The three WAN families are Event/State/Periodic. Full gateway monitoring is
enabled, but only ContactReport application routes are enabled; receive policy
is static, rather than exercising mission/team changes.

The development config is derived from `router/config/control-platform.yaml`.
Prepared XML preserves independent native LAN auto-QoS routes and the shared
WAN hub. A spike-only QoS copy replaces every authored transport mask with
UDPv4; all peer addresses are loopback. The report hashes every actual YAML,
prepared XML, types registry and merged probe QoS, and the archive retains them.
No shared source/configuration file is edited.

Startup/matching/materialization must finish within 20 seconds, native disposal
within five seconds, and zero-delay active-state purge within two seconds.
Unexpected payload/key/state/retention failures are explicit JSON `FAIL`
records with a nonzero runner exit. Actual gateway log tails and native errors
are retained even when a run fails.

The container uses `--network none --ipc private`, never host DDS/network/IPC.
The repository and license are read-only. Scratch/build/compiler temporary
files use an owned directory under the private container's `/tmp` tmpfs.
The runner sets `TMPDIR` and `HOME` to `/tmp`; removing the container removes
all transient state.

After the final run both owned gateways exited zero; the container and scratch
were removed, private DDS shared-memory artifacts were absent, and the
persistent binary hashes were unchanged. Gateway logs had zero error lines;
Periodic best-effort loss warnings are present, so the logs are not
warning-free. Expected invalid-handle diagnostics in zero controls and the
finite-delay rejection are retained separately in native probe stderr.
The canonical six-platform live mesh was never accessed or restarted.
No full regression, RF/EMANE, outage, load, or live mission claim is made.

## API evidence

The native probe was compiled and run against the installed headers; its
`ReaderDataLifecycle` extension getter uses `policy->...`. The Connext MCP
answer was checked against the existing corrections log and the installed
cache-status declarations, then tested rather than assumed. It agreed that
nonzero finite instance delays are unsupported and that detached state differs
from an active disposed instance. This contact API answer required no correction.

Prior art:
[`reader_instance_purge`](../reader_instance_purge/README.md),
[`test_rs_native_lifecycle`](../../router/test_e2e/test_routing_service.py),
and the [native processor guide](../../router/routing_service/README.md).

RTI references:
[reader lifecycle](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/READER_DATA_LIFECYCLE_QoS_Policy.htm),
[instance management](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/AccessingManagingInstances.htm).
