# Write-drop accounting — results

Verified on this VM against Connext **7.7.0** (`x64Linux4gcc7.3.0`), 2026-08-19.
See [PLAN.md](PLAN.md) for the questions and rig.

Feeds review item **M6** in
[`docs/review-2026-08-11-harness-v2-and-router.md`](../../docs/review-2026-08-11-harness-v2-and-router.md),
and produced a new item (**M15**, `samples_rejected_remote` is a dead field) — see Q3.

**Reproducibility.** Four valid-domain runs. A, B and C are bit-identical across all four.
D's `replaced_unacked` varies 24-30, as expected for a timing-dependent measurement — the
finding it supports (nonzero on a lossless link) holds in every run.

## Build & run

```sh
export NDDSHOME=/home/rti/rti_connext_dds-7.7.0
cmake -B build -DCONNEXTDDS_ARCH=x64Linux4gcc7.3.0
cmake --build build
export LD_LIBRARY_PATH=$NDDSHOME/lib/x64Linux4gcc7.3.0:$LD_LIBRARY_PATH
./build/write_drop_accounting
```

## Results

Our `write()` ledger is the ground truth; every other column is read off a native status
getter. "reader rejected" is `DataReaderProtocolStatus::rejected_sample_count` on the
*peer's* reader — the only place actual loss shows up.

| Scenario | attempted | ok | threw | pushed | writer-global `rejected` | per-sub `rejected` | `replaced_unacked` | unacked peak | **reader `rejected` (real loss)** |
|----------|-----------|-----|-------|--------|--------------------------|--------------------|--------------------|--------------|-----------------------------------|
| A KEEP_ALL, stalled | 40 | 15 | **25** | 15 | 0 | 0 | 0 | 10 | 10 |
| B KEEP_LAST, stalled | 40 | 40 | 0 | 40 | 0 | 0 | **35** | 5 | **35** |
| C KEEP_LAST, drained (control) | 40 | 40 | 0 | 40 | 0 | 0 | **35** | 5 | **0** |
| D KEEP_LAST, drained + paced | 40 | 40 | 0 | 40 | 0 | 0 | **24-30** | 5 | **0** |

## Findings

### Q1 — Nothing counts a thrown `write()`. M6's local counter is the only possible source.

Scenario A: 40 attempts, 15 returned normally, **25 threw `dds::core::TimeoutError`**.
`pushed_sample_count` reads exactly **15** — the count of writes that returned. The 25
thrown samples appear in **no** writer-side counter: writer-global `rejected_sample_count`
= 0, per-subscription `rejected_sample_count` = 0, `replaced_unacknowledged_sample_count`
= 0.

This is expected in hindsight — the sample never entered the writer's cache, so there is
nothing for the middleware to account. It means a router-side counter **duplicates no
Connext counter**: it counts the outcome of our own API calls, which the middleware does
not observe. M6's fix stands as originally written.

### Q2 — `replaced_unacknowledged_sample_count` is NOT a loss counter. Do not use it.

Compare B and C. Same writer QoS, same sample count, same write loop. The only difference
is whether the reader is drained:

- **B** lost 35 samples for real (reader `rejected` = 35) → `replaced_unacked` = **35**
- **C** lost **nothing** (reader `rejected` = 0) → `replaced_unacked` = **35**

Identical. The counter cannot distinguish total loss from perfect delivery. D pins down
why: pacing writes at 20ms (well above loopback ACK latency) only brings it down to
24-30, still on a link that lost nothing. It counts samples overwritten in the writer
cache before the **heartbeat-driven** ACK returned — and since piggyback heartbeats are
periodic/every-N-samples, that is routine on a *healthy* reliable KEEP_LAST writer.

Wiring this to a route-health field would light up permanently on every healthy KEEP_LAST
route. This retires the idea that it is a better metric than a local counter.

### Q3 — Writer-side `rejected_sample_count` never moves, even when the peer rejects 35 samples.

Worse than the header annotation implied. In scenario B the remote reader demonstrably
rejected 35 samples, and **both** the per-subscription **and** the writer-global
`rejected_sample_count` read 0. So this is not merely the
`/* Only available for local DW status */` restriction on the per-subscription variant
(`dds_c_publication.h:460`) — nothing on the writer side observes peer-side rejection at
all.

Consequence for the router: **`samples_rejected_remote`
([`WanStatsPoll.hpp:85`](../../router/src/core/WanStatsPoll.hpp#L85) →
[`ActTypes.idl:460`](../../harness_v2/datamodel/ActTypes.idl#L460)) does not measure what
its name and IDL comment claim.** It is 0 in every scenario here, including the one with
35 confirmed remote rejections. Either drop the field or re-source it; it cannot be fixed
by switching to the writer-global getter.

The corresponding reader-side field, `samples_rejected_local`, **does** work — 35 vs 0
cleanly separates B from C/D. But it lives on the *receiving* router's reader; a
forwarding router cannot see it from its own writer.

### Q4 — `unacknowledged_sample_count` is a saturating gauge, not an early warning.

In A it read exactly **10** at the first throw — precisely the writer's `max_samples` —
and peaked there. It hits its ceiling *simultaneously with* the first failure rather than
climbing ahead of it, so it provides no lead time at this write rate, and being a gauge it
cannot count what was lost. In B vs C/D it peaked at 5 (= KEEP_LAST depth) in both the
lossy and the lossless case — indistinguishable, same failure as Q2.

It remains legitimate for what `ControllerJournalPublisher` already uses it for: an
edge-triggered "the cache is full right now" warning. It is not a substitute for a drop
count.

Note the granularity question is *not* what rules it out. The Phase 9 note at
`spikes/matched_endpoints/cpp_compile_check.cxx:155-159` set this gauge aside because it
cannot be attributed **per peer**, which was correct for link stats. M6 needs per-`(route,
topic)` attribution and a route topic has exactly one output writer, so the writer-global
status *is* the right granularity here. The gauge is unusable for M6 on its own merits —
saturation and no count — not because of where it lives.

## Also considered and rejected: `full_reliable_writer_cache`

The one remaining native candidate, an `EventCount32` of "the reliable writer cache went
full". It does discriminate: **2** in scenario A, **0** in B, C and D. But it counts
*cache-full events*, not samples — 2 events for 25 lost samples — so it can report that a
writer is in trouble and never how much was lost. It is also blind to the KEEP_LAST path
(0 in B, where 35 samples were genuinely lost), because replacing an unacked sample means
the cache never reports full.

Usable as a supporting signal for the KEEP_ALL blocking case. Not a drop count, and not
sufficient alone.

## A route topic can lose samples two independent ways at once

Scenario A is the clearest illustration and worth keeping in mind when reading route
status. Of 40 samples offered to the route:

- **25** never entered the writer (`write()` threw) — invisible to every native counter
- **10** reached the reader and were rejected there (reader `rejected` = 10)
- **5** actually survived

So 35 of 40 were lost through **two different mechanisms**, and the writer-side status
shows `pushed_sample_count = 15` with every rejection counter at 0. A single
`samples_dropped` field on the forwarding side captures the first mechanism only; the
second is only ever visible on the receiving router's reader
(`samples_rejected_local`). Neither number alone describes the route.

## The failure is cleanly typed

Across every run, all 25 failures in scenario A were `dds::core::TimeoutError` and
`threw_other` was **0**. `max_blocking_time` exhaustion is therefore distinguishable from
other write faults by exception type at the catch site, so a `last_write_error` /
error-class field on the topic status can be populated meaningfully rather than reduced to
a generic "write failed".

Also worth recording: `pushed_sample_count` equalled our `returned_ok` **exactly** in all
four scenarios. The router's own `count_` (incremented only on a write that returns) and
the middleware's `pushed_sample_count` should therefore agree on a healthy route, which
makes a divergence between `samples_forwarded` and `pushed_sample_count` a signal in its
own right if we ever want a cross-check.

## Incidental finding

A RELIABLE writer's `resource_limits.max_samples` must stay above
`protocol.rtps_reliable_writer.heartbeats_per_max_samples` (default 8) or writer creation
fails with `DDS_DataWriterQos_is_consistentI: inconsistent QoS policies:
protocol.rtps_reliable_writer.heartbeats_per_max_samples and resource_limits.max_samples`.
Hit at `max_samples=5`; scenarios B–D bound the cache with KEEP_LAST depth instead.

## Bottom line for M6

Implement the local counter. Every native writer-side counter reads **identically** in the
healthy and the broken case for the KEEP_LAST scenarios, and the KEEP_ALL throw path is
invisible to all of them. Only the router's own record of its own `write()` outcomes can
distinguish "forwarding fine" from "dropping everything".

Separately, `samples_rejected_remote` is a dead field and deserves its own review item.

## Not a `connext-ai-issues` entry

The repo rule is to append there when empirical evidence contradicts an **MCP** claim. The
`connext` MCP was not consulted for this spike (it is unauthorized in this session), and
the claims tested came from the shipped C headers and this repo's own Phase 9 notes. The
findings belong in the review doc and here.
