# Three-topic WAN spike

Isolated transport prototype; **not deployed**. See [PLAN](PLAN.md).

| Physical topic | Data | Reliability | Writer/reader history |
|---|---|---|---|
| Status | Health | Best effort | KEEP_LAST(1) |
| Contacts | ContactReport | Reliable | KEEP_LAST(1) per contact |
| Event | ControlCommand, PlatformCommandAck | Reliable | KEEP_ALL |

Original topic -> physical topic is mapped in [topics.py](topics.py), enforced by
[ThreeTopicEndpoints](three_topic.py). All fixed QoS lives in [qos.xml](qos.xml)
and inherited XML profiles. This prototype reuses the full existing WanEvent
envelope on Contacts; it does not define the eventual production Contacts type.
Keys, payloads and audit metadata are unchanged.

Event/Contacts use independent writers and one shared highest-priority-first
flow controller per participant, nominal 2 Mbps. Per-write priority comes from
the existing original-topic map: commands/ACKs 100, contacts 10. **No MultiChannel.**
Status remains outside this budget; this is not a shared radio-capacity guarantee.
Reliable writers retain the preceding experiment's 512 KiB repair-response cap.
All endpoints are volatile: latest state is not permanent tombstone retention.

## Run

```bash
python3 -m unittest discover -s spikes/three_topic_wan -p 'test_*.py'
python3 spikes/three_topic_wan/run.py --ordering-only
python3 spikes/three_topic_wan/run.py --control-only --baseline 3 --recovery 3
python3 spikes/three_topic_wan/run.py --baseline 20 --outage-seconds 120 \
  --recovery 40 --drain-seconds 30 --capture-wire
```

The shared runner freezes/hashes source, XML, image and environment; owns a
`--network none` UDPv4 container; and removes it on exit. Working state is in
`/tmp`; reports/JUnit are in `debug/test_reports/three_topic_wan_*`, closed raw
captures in `debug/pcap/three_topic_wan/`. Never run it on the live mesh.

The workload and lifecycle are reused from
[instance_churn_reconnect](../instance_churn_reconnect/README.md).
Both participants have exactly one writer/reader per topic, with two-sided
self-ignore. Churn and health flow Source -> Receiver; commands/ACKs exercise
both Event directions. Reverse Contacts/Status endpoints verify topology but
do not carry the timed workload.

## Reading results

- `data_topic: Contacts`; `family: Event` is the reused workload's envelope label,
  **not** the physical contact topic.
- `write_history.required: false` applies only to intermediate Contacts writes.
  Expected/observed/missing sequences are still reported.
- Required Event delivery is checked separately: every sent command and ACK,
  missing/unexpected IDs and duplicate deliveries. All original latency and
  final-state gates remain failures in JUnit/exit status.
- `effective_qos.endpoint_inventory` records actual native topic names, policies,
  channel counts and remote matches. `metrics` separates contact, command and
  ACK backlog; aggregate `unacked` includes all three.
- Capture counters include **combined Source Event + Contacts DATA**, including
  repairs, excluding identified Status traffic. Historical `event_*` bandwidth
  field names are retained for report compatibility, not per-topic attribution.
  This excludes control/ACK/RF overhead and cannot establish total link usage.

### Ordering-control correction (2026-11-13)

The original cross-writer priority control concatenated Contacts and Event
reader polls, which could mistake polling order for reception order. Its
historical priority-reversal claims are superseded by the corrected control.
It now compares `SampleInfo.reception_timestamp` across both readers and rejects
cross-reader timestamp ties explicitly, rather than inventing an order.
This measures middleware commit order, not wire arrival or reliable repair.
See the [Connext SampleInfo reference](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/The_SampleInfo_Structure.htm).

All nine corrected native controls passed, including both priority directions:
[fresh results](../../debug/test_reports/three_topic_wan_20261113T175724-8653/results.json).
Offline regressions cover reversed polling order and ambiguous timestamps.
This correction does not change or rerun the historical recovery measurements.

## Measured results (2026-11-12)

Twenty host tests (18 existing + 2 new), all **9 three-topic native controls**,
and all **32 existing native controls** pass. Controls verify both participants'
six endpoints, actual policies, remote-only matching, queued replacement and
priority reversal without MultiChannel. A deliberately omitted ACK still fails
the new latest-state workload, and the old mixed KEEP_LAST outage still fails
its required-write/command/ACK gates. Intermediate-contact acceptance does not
relax Event delivery.

The full comparison uses 20 s baseline, 120 s blackout and 40 s continued
production, with up to 30 s final drain. The matched healthy reference is 60 s.

| Measurement | Healthy reference | Two-minute blackout |
|---|---:|---:|
| Commands received / sent | 60 / 60 | 180 / 180 |
| ACKs received / sent commands | 60 / 60 | 180 / 180 |
| Duplicate/unexpected command or ACK IDs | 0 | 0 |
| Worst fresh command RTT | 14.132 ms | 14.124 ms |
| First current-state catch-up after restoration | N/A | **19.110 s** |
| Valid contact writes observed / produced | 4,961 / 4,961 | 4,049 / 14,561 |
| Final state discrepancies / unacknowledged samples | 0 / 0 | 0 / 0 |

The 10,512 intermediate contact writes not observed during the outage are
allowed under the explicit latest-state contract. All 121 command probes
issued while loss rules were active were acknowledged within 0.673 s after
restoration. Producer lag peaked at 20.902 ms, below the unchanged 50 ms gate.
The first received post-restoration health sample arrived after 0.984 s; maximum
post-restoration health age was 3.106 ms. This measures sample receipt, not the
production presence state machine.

**The outage case fails only the unchanged 10 s contact-recovery gate.**
The split fixes required-event preservation in this workload; it does not yet
meet the state-recovery target. Do not claim a pure performance comparison with
the earlier lossy shared-writer experiment: queue layout, reader history and
endpoint inventory also differ.

Retained local evidence:

- [Full healthy/outage results](../../debug/test_reports/three_topic_wan_20261112T191709-882891/results.json)
  and [JUnit](../../debug/test_reports/three_topic_wan_20261112T191709-882891/junit.xml).
- [Nine native controls](../../debug/test_reports/three_topic_wan_20261112T192256-764586/results.json).
- [Existing native controls](../../debug/test_reports/instance_churn_reconnect_20261112T191330-716296/results.json).
- [Unchanged legacy negative gates](../../debug/test_reports/instance_churn_reconnect_20261112T192256-70929/results.json).
- [Closed captures](../../debug/pcap/three_topic_wan/20261112T191709-882891/).

Initial failed setup/control attempts are retained separately; the final negative
ACK control allows 5 s for native reliable acknowledgments to settle rather
than conflating a 0.5 s observation window with application ACK suppression.

No production router, IDL, generated XML, live configuration or running binary
is changed by this spike. Restart, late-join, ownership, bounded load and fleet
health qualification remain open.

## Why 12.9 s became 19.1 s: controlled investigation

The original two numbers were individual runs, not a controlled comparison.
Their manifests also contain different generated type XML and production QoS
XML hashes. The selected effective heartbeat, timeout and flow budgets match,
but the historical input files were not retained as full source archives.

Closed packet captures explain **5.891 s of the 6.181 s difference** as extra
time between contact repair requests:

- The 12.929 s run has one request gap: 7.852 -> 12.446 s, lasting 4.593 s.
- The 19.110 s run has gaps of 4.670 s and **5.815 s**. Its last contact repair
  request is at 18.775 s; current-state catch-up follows at 19.110 s.
- Normal Source application DATA continues during the gaps. This is a repair
  request stall, not evidence that the extra bytes from retaining commands
  necessarily require another six seconds of transmission.

[repair_trace.py](repair_trace.py) decodes actual set missing bits in standard
user-writer ACKNACKs, excludes empty ACKs and builtin discovery writers, and
records request times/bitmaps and Source DATA per second. It analyzes closed
captures only, not the live mesh.

### Frozen-input repeated trials

[compare.py](compare.py) freezes one source/XML snapshot and image digest for
each batch, verifies every child manifest, runs cases sequentially, then
reverses case order on the second repeat. All cases retain the same 20 s
baseline, 120 s blackout, 40 s continued production, priorities and pacing.
Each case keeps its original JSON/JUnit failures and exit status; the comparison
runner's success means measurements completed, **not** that acceptance passed.

| Case | Contact reader | MultiChannel | Recovery, two trials | Commands/ACKs received |
|---|---|---|---|---|
| `mixed_last` | KEEP_ALL | Original priority bands | 7.675 / 11.418 s | 60 / 58 of 180; **lossy** |
| `bands_all` | KEEP_ALL | Original priority bands | 14.655 / 16.310 s | 180/180 both trials |
| `none_all` | KEEP_ALL | None | 13.585 / 13.110 s | 180/180 both trials |
| `baseline` | KEEP_LAST(1) | None | **13.869 / 19.331 s** | 180/180 both trials |

Thus the identical original three-topic configuration itself varies by 5.462 s.
Removing MultiChannel is not demonstrated to be the cause of the historical
12-to-19 jump, and it is not required for the topic split. `bands_all` retains
the original bands and reader history while separating required Event retention.
Use it as the least-change MultiChannel comparison; `baseline` deliberately
keeps the initial three-topic prototype unchanged for comparison.

### One-field heartbeat-suppression trial

The installed 7.7 reader defaults resolve to heartbeat suppression 62.5 ms,
heartbeat response delay 0-500 ms and historical-data NACK period 5 s; writer
heartbeats remain 3 s. Similar numbers alone do **not** prove which timer
causes the observed multi-second stalls.

A separate paired batch retains MultiChannel and KEEP_ALL readers, changing
only the Contacts reader profile's `heartbeat_suppression_duration` to zero:

| Policy | Recovery, two trials | Worst fresh command RTT |
|---|---|---|
| Unchanged `bands_all` control | 12.646 / **28.210 s** | 14.022 / **2,253.636 ms** |
| `bands_no_suppression` | 12.227 / 11.781 s | 13.759 / 14.458 ms |

The candidate still has roughly three-second repair-request gaps and misses
the 10 s target. It does not establish a complete fix or prove suppression
alone caused every stall. The slow control has four long request gaps and
also fails the 2 s fresh-command gate. Short two-second MultiChannel smoke
tests also exposed command RTT above 2 s; do not hide those failures.

Across the twelve full outage trials, all ten three-topic cases preserve all
180 command/ACK pairs, correct final contact state and drained queues. All ten
still fail the 10 s state budget. The two batches overlap on this shared VM,
but use independent private network namespaces; cases within each batch are
sequential. Producer lag remains below 50 ms, but this is not a dedicated-host
statistical benchmark. Two repeats cannot establish a stable speed ranking.

**Conclusion:** retain MultiChannel as an explicit tested option. Focus the next
experiment on repair-request/heartbeat response pacing, not on discarding
commands or assuming reader history/channel removal explains a single timing.
The zero-suppression profile remains experimental and no default is retuned.
This investigation did not modify, rebuild or restart the live mesh.

```bash
python3 spikes/three_topic_wan/compare.py \
  --cases mixed_last bands_all none_all baseline --repeats 2 --capture-wire
python3 spikes/three_topic_wan/compare.py \
  --cases bands_all bands_no_suppression --repeats 2 --capture-wire
```

New batches also retain compressed `inputs.tar.gz`; the completed full studies
below retain their original per-file hashes rather than claiming archived input
bytes that no longer exist. Reports, manifests, JUnit and closed captures persist.

- [Main eight-trial comparison](../../debug/test_reports/three_topic_comparison_20261112T194923-228599/comparison.json),
  [historical input hashes](../../debug/test_reports/three_topic_comparison_20261112T194923-228599/historical_inputs.json)
  and [historical/main repair traces](../../debug/test_reports/three_topic_comparison_20261112T194923-228599/repair_trace.json).
- [Paired suppression results](../../debug/test_reports/three_topic_comparison_20261112T200938-260588/comparison.json)
  and [repair traces](../../debug/test_reports/three_topic_comparison_20261112T200938-260588/repair_trace.json).
- [Final nine native controls](../../debug/test_reports/three_topic_wan_20261112T202213-641497/results.json).
  Twenty-two host tests pass, including the bitmap decoder and one-field XML assertion.

## Lossless two-versus-three-topic tradeoff suite

The comparison now supports a symmetric two-topic baseline and a three-topic
KEEP_ALL control. Each synthetic node has one writer/reader per physical topic,
two-sided self-ignore and exactly one remote match. Historical `mixed_last`
captures used an asymmetric Status inventory; they are not reused as matched
endpoint-overhead measurements.

```bash
python3 spikes/three_topic_wan/compare.py \
  --tradeoffs --repeats 3 --include-healthy --capture-wire \
  --baseline 20 --outage-seconds 120 --recovery 40
```

The five cases separate three questions:

- `mixed_all` versus `split_keep_all`: additional Contacts endpoints/queues,
  holding KEEP_ALL, priority bands and reader protocol timing constant.
- `split_keep_all` versus `bands_all`: contact-writer latest-state coalescing,
  holding the three-topic topology and timing constant.
- `mixed_all`/`mixed_no_suppression` and `bands_all`/`bands_no_suppression`:
  zero suppression on the contact-carrying reader. In two topics that reader
  necessarily also carries commands/ACKs; three topics permit an independent
  Contacts setting. The report states this coupling explicitly.

Cases run sequentially with reverse ordering on alternate repetitions. Each
case gets a fresh private container for its healthy and outage trial. Raw
JSON/JUnit, captures, native policy/endpoint inventory, per-file hashes and
compressed exact inputs are retained. `summary.md` and `trials.csv` provide
median/range tables and per-trial data for later slides, including every failed
gate. The report generator can also save those deliverables outside the runtime
artifact tree:

```bash
python3 spikes/three_topic_wan/summarize.py --comparison <retained-comparison> \
  --output docs/wan-topic-tradeoffs-2026-11-12.md \
  --csv docs/wan-topic-tradeoffs-2026-11-12.csv
```

Generated reports list executed native writer policies and make matched-pair
claims only for pairs actually run. The default comparison matrix includes
unbanded controls and the lossy shared-Event `mixed_last` negative control;
it is not the lossless `--tradeoffs` matrix. Failed gates remain in both reports.
Repair analysis with `--comparison` skips healthy controls. An explicitly supplied
healthy-only report is rejected with a clear no-outage diagnostic.

Measurements include required-event correctness, latest contact/deletion state,
fresh command RTT, health arrival age/gaps, benchmark-process RSS, native repair
counters, recognized discovery bytes and both-node UDP traffic. Health remains
best effort outside the pacing budget; health-versus-routine-Status contention
and actual router STALE/DEAD transitions are **not** covered. Process RSS includes
the Python oracle and is not production-router/DDS-only memory. Loopback UDP
bytes are not EMANE/RF costs. No dev-router integration or live-mesh changes are
part of this suite.

### Retained execution: 2026-11-12 to 2026-11-13

The complete five-case suite ran three alternating-order repetitions: **15
healthy controls and 15 full 120-second outages**. Every row passed correctness
and the 50-ms scheduler validity gate; every outage preserved 180 command/ACK
pairs, correct final contact/deletion state and drained reliable queues.

Median recovery was **61.925 s** for lossless two-topic KEEP_ALL and **61.975 s**
for three-topic KEEP_ALL: adding a topic alone did not demonstrate faster
recovery. Three-topic contact KEEP_LAST(1) reduced the median to **19.119 s**.
Zero Contacts suppression reached **14.359 s** median but varied from **13.046
to 28.311 s**. All fifteen outage cases still failed the 10-second target; two
two-topic cases additionally exceeded 2-second fresh-command RTT. These results
do not confirm the earlier 11.8-second candidate as a stable optimum.

KEEP_ALL convergence happened in quiet drain, after the common 40-second
continued-production interval; the contact KEEP_LAST(1) cases converged during
production. Phase-aware tables and CSV make this distinction explicit.
Recognized startup discovery includes both standard and RTI builtin entity
flags; prefixless RTPS is recorded separately, and no startup packet bytes were
left in the unknown category. No health gap exceeded 2 seconds outside blackout,
but there was no routine periodic-Status competition or actual router presence
state machine.

The [durable report](../../docs/wan-topic-tradeoffs-2026-11-12.md) and
[per-trial CSV](../../docs/wan-topic-tradeoffs-2026-11-12.csv) are the reusable
slide inputs. [Frozen results and exact inputs](../../debug/test_reports/three_topic_comparison_20261112T231057-743299/)
retain raw JSON/JUnit, closed captures and all negative gates. Twenty-six host
checks and nine native controls passed before the frozen run; the final host
regression passed 34 checks with additive routed-presence and offline-selection
tests. Those later helpers are not part of the measured frozen workload. Report-phase
annotations are derived from original markers; measurement inputs and the live
mesh were not modified.
