# Six topics versus three topics with multiple writers

## Result

**Reducing topic names alone did not materially reduce measured discovery.
Reducing DataReaders did.** Keeping six separate filtered readers on three
shared topics doubled the matched writer-reader associations compared with six
separate topics.

The final Connext 7.7.0 experiment ran four repetitions, reversing case order,
with both two and seven participants. Each participant always had six writers
with six different transport-priority/DSCP markings. Every endpoint used the
same union-containing `Frame` type. No production code, QoS, persistent binary
or live mesh was changed.

### Seven-participant comparison

These are **fleet totals**, not per-participant costs. Decimal KB means 1,000
IP bytes. Startup is a fixed four-second window. Primary results use the
production-style reader property that disables preemptive NACKs:

| Layout per participant | Startup discovery KB, mean | Endpoint discovery records | Matched writer-reader associations |
| --- | ---: | ---: | ---: |
| 6 topics / 6 writers / 6 filtered readers | 410.154 | 42 publications + 42 subscriptions | 252 |
| 3 topics / 6 writers / 6 filtered readers | 412.264 | 42 publications + 42 subscriptions | 504 |
| 3 topics / 6 writers / 3 filtered readers | 340.982 | 42 publications + 21 subscriptions | 252 |

The six-reader cases differed by about **0.5%** in mean discovery bytes, within
their observed run ranges: 408.548-413.724 KB versus 410.864-416.184 KB.
The three-reader case reduced startup discovery by **16.9%** relative to the
six-topic case; endpoint-discovery (SEDP) packet bytes alone fell **27.2%**.
Participant discovery is still present in the total.

| Change on one participant | 6 topics / 6 readers | 3 topics / 6 readers | 3 topics / 3 readers |
| --- | ---: | ---: | ---: |
| One reader filter update: median discovery IP bytes | 3,576 | 3,576 | 3,576 |
| All its reader filters updated: mean discovery IP bytes | 22,680 | 22,680 | 11,904 |
| Unique subscription records for one update | 1 | 1 | 1 |
| Unique subscription records for all-reader update | 6 | 6 | 3 |

One-reader updates produced six discovery packets across the six remote
participants in the ordinary observation; two trials included another 188-byte
discovery packet, making the corresponding means 3,623 bytes. No writer-count
multiplier appeared in the number of unique subscription records. Updating all
three rather than all six readers reduced update bytes by **47.5%**.

The three-reader case gives up six independent receive queues. A reader covers
both priority classes in its family. Consequently, the one-reader update blocks
one class in the six-reader cases but both classes in that family in the
three-reader case; the all-reader update covers all six classes in every layout.

### Two-participant comparison

| Layout | Mean startup discovery IP bytes | One-reader update | All-reader update |
| --- | ---: | ---: | ---: |
| 6 topics / 6 readers | 46,706 | 536 | 3,420 |
| 3 topics / 6 readers | 46,611 | 536 | 3,420 |
| 3 topics / 3 readers | 43,812 | 536 | 1,804 |

The corresponding matched-association counts were 12, 24 and 12.

### Reliability is a separate cost

The first full matrix left preemptive NACKs enabled. With seven participants,
the six-topic and shared-topic/six-reader cases averaged about 424 and 853
startup user ACKNACK submessages respectively. That should **not** be presented
as the current production startup cost.

Repeating the complete matrix with
`dds.data_reader.protocol.disable_preemptive_nack=true` verified zero user
base-zero ACKNACKs and zero startup user ACKNACKs in all 24 trials. The number of
matched associations still doubled in the shared-topic/six-reader case.
Other user reliability traffic was not eliminated: mean total startup RTPS IP
bytes, including discovery, were 445,434 / 471,736 / 376,262 for the three layouts.
The test does not measure long-running reliability CPU/memory or overload.

## What this means for the design

- Six differently marked writers are needed in all three arrangements.
- Sharing topic names does not remove those writer discovery records.
- Keeping six readers preserves six subscription records per participant.
- A filter update advertises the changed reader once logically, rather than
  sending one distinct subscription record for each matching writer.
- With shared topics and six readers, each reader matches both class writers
  from every remote participant, even though its content filter selects one
  class. Separate topics restrict these matches by name.
- Fewer readers save discovery and update bytes, but combine receive queues,
  limits and processing unless separated again above DDS.

**Recommendation:** if six independent receive classes are useful, this test
provides no discovery-bandwidth reason to replace six topics with three shared
topics and six filtered readers. Three shared topics become a discovery saving
when the application can actually use fewer readers.

## Method and verification

```bash
# Original default-protocol controls:
bash spikes/topic_layout/run.sh

# Primary production-style preemptive-NACK sensitivity matrix:
SUPPRESS_PREEMPTIVE=1 bash spikes/topic_layout/run.sh

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s spikes/topic_layout -p 'test_*.py' -v
```

Requires the existing `connext:7.7.0` image and license-only directory. The runner
does not download dependencies. `REPETITIONS=1` runs a shorter pilot; the default
is four. It uses a repository-specific nonblocking lock and a named owned
container with no external network, private IPC, and bounded loopback captures.
Fixed DDS policies are authored in [qos.xml](qos.xml); participant-specific
partitions exclude only self and are identical across layouts.

Each trial runs in a fresh process. Per participant, six or three native
ContentFilteredTopic readers select class ranges and a sequence threshold.
Startup, idle, one-reader update, restoration, all-reader update and restoration
have explicit timestamps. Each update is followed by offered-data checks.
All six writers are RELIABLE/VOLATILE/KEEP_ALL in every case to isolate topology
effects; production Event/State/Periodic policy differences are not reproduced.
Each writer's effective transport-priority value is checked, and six DS fields
0/32/64/96/128/160 are verified in actual application packets. Those correspond
to DSCP 0/8/16/24/32/40 and are test classes, not a recommended network policy.

Across the two full matrices:

- **48/48 trials passed.**
- **1,152 original writes and 3,680 selected deliveries** passed complete
  DynamicData equality and duplicate/exclusion checks.
- Every startup had the exact expected distinct publication/subscription
  discovery record count and writer/reader match count.
- Each reader update changed exactly the expected number of subscription
  records without changing the match counts.
- All 48 captures had zero reported drops and no unexpected SDK ERROR/FATAL
  lines; both focused packet-accounting tests passed.
- All capture hashes and every phase metric were independently replayed.
- All owned DDS/container resources were closed; no shared-memory remnants.

Discovery bytes count whole IP packets addressed to the standard metatraffic
ports, once per packet. SEDP is a subset, not an additional byte total.
Distinct SEDP DATA records are deduplicated by source prefix/sequence separately
for publications and subscriptions; retransmitted or multi-destination copies
still count toward wire bytes. RTPS submessage counts are not packet counts.
Idle windows contained no observed traffic in either final matrix.

## Retained evidence and limits

- [Default-protocol full matrix](../../debug/test_reports/topic_layout_20261125T171156Z/report.json)
  and [captures](../../debug/pcap/topic_layout_20261125T171156Z/).
- [Suppressed-preemptive full matrix](../../debug/test_reports/topic_layout_20261125T172048Z/report.json)
  and [captures](../../debug/pcap/topic_layout_20261125T172048Z/).
- Reports retain per-trial XML, source hashes, endpoints, match counts,
  phase timestamps, delivery checks and capture hashes. These local ignored
  files can later be cleared; the measured findings are recorded here.

Earlier pilots are not part of the final counts. One failed capture-file
permissions; two exposed an incorrect fixture expectation that a filtered reader
would match only one of two same-topic writers. Native readback showed both
matches, and the final oracle explicitly checks that topology.

This is native DDS over private loopback UDPv4 with participants in one process
per trial, not a deployed Routing Service or EMANE/RF measurement. Application
DATA on UDP is verified, but process placement can affect timing. No general
latency, memory, CPU, packet-loss, sustained-load or fleet-scale guarantee is
claimed. Transport-class mapping, multicast, different union/type graphs and
discovery settings can change absolute bytes. Shared-key moves between writers,
transient-local cache/lifecycle semantics and production subscription integration
require separate qualification.
