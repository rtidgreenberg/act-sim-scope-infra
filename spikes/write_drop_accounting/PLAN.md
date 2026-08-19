# PLAN — write-drop accounting

## Question

Review item **M6** says `RouteTopicRuntime::pump()` swallows every `write()` exception
with no counter, no log, and no status field, so a route whose writer rejects every
sample is indistinguishable from an idle one. The proposed fix is a router-side
`dropped_` counter.

Before adding router-side accounting, settle empirically: **does Connext already count
this?** If a native counter covers it, the router should read that counter rather than
maintain a parallel one.

Explicit constraint on this spike: **do not replicate any internal Connext mechanic or
counter.** The only numbers the spike keeps of its own are a ledger of its own `write()`
calls (attempted / returned / threw, by exception type) — the independent variable the
native counters are measured against.

## Sub-questions

| ID | Question |
|----|----------|
| Q1 | KEEP_ALL + RELIABLE, `max_blocking_time` exhausted → `write()` throws. Does **any** native writer counter move for that sample? |
| Q2 | KEEP_LAST + RELIABLE, reader stalled → writer overwrites unacked samples. Does `replaced_unacknowledged_sample_count` account for the loss exactly? |
| Q3 | Is per-matched-subscription `rejected_sample_count` populated at all? `dds_c_publication.h:460` annotates it `/* Only available for local DW status */`, and the router reads **only** the per-subscription variant (`WanStatsPoll.hpp:85` → `samples_rejected_remote`, `ActTypes.idl:460`). |
| Q4 | Does `unacknowledged_sample_count` rise measurably **before** the first throw — is it usable as a leading indicator at a threshold, like `ControllerJournalPublisher`'s `kBacklogThreshold`? |

Q2/Q4 exist because the Phase 9 note at `spikes/matched_endpoints/cpp_compile_check.cxx:155-159`
dismissed `unacknowledged_sample_count` as un-attributable **per peer**. M6 needs
per-`(route, topic)` attribution, and a route topic has exactly one output writer — so the
writer-global status is the right granularity and that dismissal does not apply here.

## Rig

Two participants per scenario on a dedicated domain id (181-184, within the repo
guardrails), **UDPv4-only** so a killed process cannot leak `/dev/shm` segments. The
reader is stalled by giving it RELIABLE + KEEP_ALL + a small `max_samples` and never
calling `take()`: once its cache fills it stops ACKing and the writer's cache backs up.

Payload is unkeyed (`DropProbe.idl`) so every sample lands on one instance and a
KEEP_LAST depth-N writer cache is guaranteed to replace rather than spread across
instances.

| Scenario | Writer history | Reader | Purpose |
|----------|----------------|--------|---------|
| A | KEEP_ALL, `max_samples=10`, `max_blocking_time=100ms` | stalled (`max_samples=5`) | Q1, Q4 — force `write()` to throw |
| B | KEEP_LAST depth 5 | stalled (`max_samples=5`) | Q2 — force unacked replacement **with real loss** |
| C | KEEP_LAST depth 5 | drained every write (`max_samples=50`) | control — **zero** loss, same writer QoS as B |
| D | KEEP_LAST depth 5 | drained, writes paced 20ms | discriminator — zero loss *and* slower than the ACK round trip |

C is the control that proves the rig does not manufacture drops. D exists to separate
"loss" from "wrote faster than the ACK came back": if `replaced_unacknowledged_sample_count`
is a loss counter, C and D should read the same as each other and differently from B.

## Guardrails observed

- Repo working dir is local ext4 (`/dev/sda5`), not the vboxsf share — no SQLite/FIFO
  concern, and the spike writes only stdout captures under `results/`.
- Domain ids 181-184 (within the port-mapping ceiling and outside the live bands).
- UDPv4-only; `/dev/shm` checked clean after every run.
- No `pkill`: the spike is a single short-lived foreground process.
