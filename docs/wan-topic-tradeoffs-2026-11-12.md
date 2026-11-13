# WAN topic tradeoffs: retained comparison

**Transport spike only. No dev-router integration or live-mesh changes.**

- Source commit: `520c264e2f72422dfceea0b579a1d1607039fba0`; dirty inputs are archived and individually hashed.
- Derived report generator SHA-256: `c4ebc6f890f69087ac0afa33530251f9324e028b913a86c76449b93a9cf1ea59`. Convergence-phase labels are derived from original trial markers; frozen measurement inputs are not changed by report regeneration.
- Image: `sha256:cbabafb1dab80c66483f092e1770249a6196d974b01592fe3a52979613f5f7a0`.
- Started: 2026-11-12T23:10:57Z; completed: 2026-11-13T00:05:52Z.
- Host: 16 CPUs; shared VM; private networks, not dedicated CPU or memory. Host load averages at each trial boundary are retained in the CSV.
- Execution: sequential; reverse order on alternate repeats. Cases within this suite never overlap.
- Requested repetitions: 3; completed trial rows: 30.
- Outage: 120.0 s; baseline: 20.0 s; continued production: 40.0 s; then up to 30 s drain.
- Workload: 50-ms ticks, one create, three updates and one dispose per tick; commands/ACKs and health once per second; one never-disposed stationary contact.
- Two synthetic nodes each own one writer and reader per topic, with two-sided self-ignore and exactly one remote match. Reverse Contacts/Status endpoints are idle.
- Scheduling and retention vary by case. The native writer inventory below records the executed histories, channel counts and flow controllers; original trial JSON retains repair limits and reader timing.
- Status remains best effort and outside that controller. Routine periodic Status competition and actual router STALE/DEAD transitions are **not tested**.

## Comparisons and causal limits

- `mixed_all` versus `split_keep_all`: same KEEP_ALL retention and reader timing; isolates adding the Contacts pair and splitting transmission queues.
- `split_keep_all` versus `bands_all`: same three-topic topology and timing; isolates contact-writer KEEP_LAST(1) coalescing.
- `mixed_all` versus `mixed_no_suppression`: suppression treatment within two topics.
- `bands_all` versus `bands_no_suppression`: suppression treatment within three topics.
- Single-variable causal claims above apply only to those executed case pairs. Other cases are scheduling/retention controls, not a matched lossless-baseline matrix.
- In two topics, the contact-carrying reader also carries commands/ACKs; zero suppression there applies to the whole Event reader. Three topics can change Contacts independently. That coupling is documented, not hidden as a perfectly identical per-reader treatment.
- A lossy shared Event KEEP_LAST(1) writer is a negative control, not an eligible lossless two-topic baseline. Its failed gates remain visible.

## Executed native writer policies

Each distinct recorded policy is shown, including changes across repetitions.

| Case | Native writer | History | Channels | Flow controller |
|---|---|---|---:|---|
| `bands_all` | Receiver/Contacts | HistoryKind.KEEP_LAST | 2 | dds.flow_controller.token_bucket.churn |
| `bands_all` | Receiver/Event | HistoryKind.KEEP_ALL | 2 | dds.flow_controller.token_bucket.churn |
| `bands_all` | Receiver/Status | HistoryKind.KEEP_LAST | 0 | DDS_DEFAULT_FLOW_CONTROLLER_NAME |
| `bands_all` | Source/Contacts | HistoryKind.KEEP_LAST | 2 | dds.flow_controller.token_bucket.churn |
| `bands_all` | Source/Event | HistoryKind.KEEP_ALL | 2 | dds.flow_controller.token_bucket.churn |
| `bands_all` | Source/Status | HistoryKind.KEEP_LAST | 0 | DDS_DEFAULT_FLOW_CONTROLLER_NAME |
| `bands_no_suppression` | Receiver/Contacts | HistoryKind.KEEP_LAST | 2 | dds.flow_controller.token_bucket.churn |
| `bands_no_suppression` | Receiver/Event | HistoryKind.KEEP_ALL | 2 | dds.flow_controller.token_bucket.churn |
| `bands_no_suppression` | Receiver/Status | HistoryKind.KEEP_LAST | 0 | DDS_DEFAULT_FLOW_CONTROLLER_NAME |
| `bands_no_suppression` | Source/Contacts | HistoryKind.KEEP_LAST | 2 | dds.flow_controller.token_bucket.churn |
| `bands_no_suppression` | Source/Event | HistoryKind.KEEP_ALL | 2 | dds.flow_controller.token_bucket.churn |
| `bands_no_suppression` | Source/Status | HistoryKind.KEEP_LAST | 0 | DDS_DEFAULT_FLOW_CONTROLLER_NAME |
| `mixed_all` | Receiver/Event | HistoryKind.KEEP_ALL | 2 | dds.flow_controller.token_bucket.churn |
| `mixed_all` | Receiver/Status | HistoryKind.KEEP_LAST | 0 | DDS_DEFAULT_FLOW_CONTROLLER_NAME |
| `mixed_all` | Source/Event | HistoryKind.KEEP_ALL | 2 | dds.flow_controller.token_bucket.churn |
| `mixed_all` | Source/Status | HistoryKind.KEEP_LAST | 0 | DDS_DEFAULT_FLOW_CONTROLLER_NAME |
| `mixed_no_suppression` | Receiver/Event | HistoryKind.KEEP_ALL | 2 | dds.flow_controller.token_bucket.churn |
| `mixed_no_suppression` | Receiver/Status | HistoryKind.KEEP_LAST | 0 | DDS_DEFAULT_FLOW_CONTROLLER_NAME |
| `mixed_no_suppression` | Source/Event | HistoryKind.KEEP_ALL | 2 | dds.flow_controller.token_bucket.churn |
| `mixed_no_suppression` | Source/Status | HistoryKind.KEEP_LAST | 0 | DDS_DEFAULT_FLOW_CONTROLLER_NAME |
| `split_keep_all` | Receiver/Contacts | HistoryKind.KEEP_ALL | 2 | dds.flow_controller.token_bucket.churn |
| `split_keep_all` | Receiver/Event | HistoryKind.KEEP_ALL | 2 | dds.flow_controller.token_bucket.churn |
| `split_keep_all` | Receiver/Status | HistoryKind.KEEP_LAST | 0 | DDS_DEFAULT_FLOW_CONTROLLER_NAME |
| `split_keep_all` | Source/Contacts | HistoryKind.KEEP_ALL | 2 | dds.flow_controller.token_bucket.churn |
| `split_keep_all` | Source/Event | HistoryKind.KEEP_ALL | 2 | dds.flow_controller.token_bucket.churn |
| `split_keep_all` | Source/Status | HistoryKind.KEEP_LAST | 0 | DDS_DEFAULT_FLOW_CONTROLLER_NAME |

## Outage results for slides

Values are **median [minimum-maximum] across trials**, not confidence intervals. Timing tables retain failed and scheduler-invalid trials; check the validity column.

| Case | Trials / valid | Correct final state + all required events | Recovery (s) | Converged while producing | Fresh RTT p95 (ms) | Worst fresh RTT (ms) | Acceptance passes |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2 topics; KEEP_ALL; default suppression | 3 / 3 | 3/3 | 61.925 [61.258-62.499] | 0/3 | 13.400 [13.356-602.247] | 176.418 [13.687-2492.719] | 0/3 |
| 3 topics; KEEP_ALL; default suppression | 3 / 3 | 3/3 | 61.975 [61.887-63.784] | 0/3 | 12.794 [12.635-13.484] | 13.851 [13.002-14.054] | 0/3 |
| 3 topics; contact KEEP_LAST(1); default suppression | 3 / 3 | 3/3 | 19.119 [19.098-20.302] | 3/3 | 13.060 [12.827-13.389] | 135.966 [13.788-147.007] | 0/3 |
| 2 topics; KEEP_ALL; zero suppression | 3 / 3 | 3/3 | 62.626 [62.299-62.887] | 0/3 | 13.886 [13.439-643.265] | 115.144 [14.022-2533.917] | 0/3 |
| 3 topics; contact KEEP_LAST(1); zero contact suppression | 3 / 3 | 3/3 | 14.359 [13.046-28.311] | 3/3 | 13.273 [12.629-13.299] | 13.415 [13.134-14.269] | 0/3 |

Required-event gates reject missing/duplicate/unexpected commands or ACKs. Acceptance also requires contact recovery within 10 s, worst fresh command RTT within 2 s, drained reliable queues, correct final contact state and scheduler lag at most 50 ms.

## Resource and wire costs

Outage trials only; median [minimum-maximum]. KiB = 1024 bytes.

| Case | Writers / readers per node | Startup discovery UDP (KiB) | Endpoint RSS increase (KiB) | Peak benchmark RSS (KiB) | Source native repair bytes (KiB) | Both-node recovery UDP (KiB) |
|---|---:|---:|---:|---:|---:|---:|
| 2 topics; KEEP_ALL; default suppression | 2 / 2 | 10.820 [10.148-12.738] | 38924.000 [38832.000-38976.000] | 143592.000 [143536.000-144004.000] | 7051.605 [7051.094-7051.605] | 5123.570 [5071.750-5143.148] |
| 3 topics; KEEP_ALL; default suppression | 3 / 3 | 11.746 [11.746-13.160] | 40492.000 [40476.000-40628.000] | 147412.000 [147300.000-147724.000] | 7051.094 [7051.094-7051.605] | 5158.742 [5131.711-5206.496] |
| 3 topics; contact KEEP_LAST(1); default suppression | 3 / 3 | 12.418 [12.418-12.734] | 40628.000 [40364.000-40656.000] | 116784.000 [114944.000-117028.000] | 414.312 [312.594-433.887] | 2545.637 [2447.293-2563.047] |
| 2 topics; KEEP_ALL; zero suppression | 2 / 2 | 11.492 [10.820-11.660] | 38940.000 [38912.000-39036.000] | 143724.000 [143420.000-143904.000] | 7051.094 [7051.094-7051.605] | 5135.344 [5016.508-5266.520] |
| 3 topics; contact KEEP_LAST(1); zero contact suppression | 3 / 3 | 12.418 [11.746-13.230] | 40660.000 [40524.000-40724.000] | 116340.000 [115404.000-116784.000] | 356.324 [296.980-468.949] | 2479.453 [2420.805-2611.352] |

- RSS is the Python/native benchmark process, including type loading and retained oracle/observation records; it is **not isolated DDS memory or production-router memory**. Endpoint RSS increase is measured before workload keys are created. Capture subprocess memory is excluded.
- Source repair bytes come from native pulled-sample counters, not total packet bytes.
- UDP totals include both owned participants, user data/repairs, health and recognized protocol traffic. Recovery UDP uses the common continued-production interval, not unequal drain durations.
- Startup discovery counts recognized builtin-only RTPS packets before workload start. Mixed builtin/user, prefixless RTPS and unknown/vendor packet bytes are separate CSV fields; initial user state is excluded from this discovery column. It is not RF overhead or fleet-scale discovery qualification.

## Health, deletion and healthy controls

| Case | Scenario | Trials | RTT p95 (ms) | Health age p95 (ms) | Largest health gap outside blackout (s) | Gaps >2 s, summed | Final ghosts, summed |
|---|---|---:|---:|---:|---:|---:|---:|
| 2 topics; KEEP_ALL; default suppression | healthy | 3 | 12.787 [11.907-13.191] | 2.239 [1.674-2.382] | 1.002 [1.002-1.002] | 0 | 0 |
| 2 topics; KEEP_ALL; default suppression | outage | 3 | 13.400 [13.356-602.247] | 4.028 [3.675-4.794] | 1.005 [1.004-1.005] | 0 | 0 |
| 3 topics; KEEP_ALL; default suppression | healthy | 3 | 13.007 [12.794-13.628] | 2.911 [2.608-3.038] | 1.002 [1.001-1.002] | 0 | 0 |
| 3 topics; KEEP_ALL; default suppression | outage | 3 | 12.794 [12.635-13.484] | 4.226 [4.136-4.309] | 1.004 [1.003-1.004] | 0 | 0 |
| 3 topics; contact KEEP_LAST(1); default suppression | healthy | 3 | 13.398 [12.920-13.418] | 2.799 [2.779-2.843] | 1.002 [1.002-1.002] | 0 | 0 |
| 3 topics; contact KEEP_LAST(1); default suppression | outage | 3 | 13.060 [12.827-13.389] | 4.119 [4.027-4.170] | 1.005 [1.003-1.018] | 0 | 0 |
| 2 topics; KEEP_ALL; zero suppression | healthy | 3 | 13.081 [12.217-13.111] | 2.143 [1.789-2.242] | 1.001 [1.001-1.002] | 0 | 0 |
| 2 topics; KEEP_ALL; zero suppression | outage | 3 | 13.886 [13.439-643.265] | 3.920 [3.905-4.137] | 1.004 [1.003-1.007] | 0 | 0 |
| 3 topics; contact KEEP_LAST(1); zero contact suppression | healthy | 3 | 13.276 [12.037-13.597] | 2.935 [2.441-3.018] | 1.002 [1.002-1.004] | 0 | 0 |
| 3 topics; contact KEEP_LAST(1); zero contact suppression | outage | 3 | 13.273 [12.629-13.299] | 3.974 [3.928-4.010] | 1.003 [1.003-1.004] | 0 | 0 |

- Health gaps include interval boundaries and exclude the intentional blackout. A gap >2 s is a freshness warning, **not a measured false router STALE transition**.
- Correct final state includes disposed contacts; a never-seen disposed key may remain absent under latest-state semantics. The CSV separately records notification delays for contacts visible as ALIVE when disposed. Those delays can include the intentional outage.
- Recovery is first current-state convergence, checked on receptions with a periodic fallback. It may occur while production continues or during the subsequent quiet drain; the table and CSV distinguish those phases. With 40 s of recovery production, longer recovery times include post-production drain. Final-state correctness is separate. It is not a sustained freshness guarantee, router restart/late-joiner test or bounded-admission qualification.
- Small samples on a shared VM do not establish statistical significance. Endpoint count and independent retention are structural benefits/costs; measured speed rankings are conditional.

## Every failed gate

- Repeat 1, `mixed_all`, outage: Current state did not converge within proposed 10s recovery budget
- Repeat 1, `split_keep_all`, outage: Current state did not converge within proposed 10s recovery budget
- Repeat 1, `bands_all`, outage: Current state did not converge within proposed 10s recovery budget
- Repeat 1, `mixed_no_suppression`, outage: Current state did not converge within proposed 10s recovery budget
- Repeat 1, `bands_no_suppression`, outage: Current state did not converge within proposed 10s recovery budget
- Repeat 2, `bands_no_suppression`, outage: Current state did not converge within proposed 10s recovery budget
- Repeat 2, `mixed_no_suppression`, outage: Current state did not converge within proposed 10s recovery budget; Post-restoration command RTT exceeded proposed 2s budget
- Repeat 2, `bands_all`, outage: Current state did not converge within proposed 10s recovery budget
- Repeat 2, `split_keep_all`, outage: Current state did not converge within proposed 10s recovery budget
- Repeat 2, `mixed_all`, outage: Current state did not converge within proposed 10s recovery budget; Post-restoration command RTT exceeded proposed 2s budget
- Repeat 3, `mixed_all`, outage: Current state did not converge within proposed 10s recovery budget
- Repeat 3, `split_keep_all`, outage: Current state did not converge within proposed 10s recovery budget
- Repeat 3, `bands_all`, outage: Current state did not converge within proposed 10s recovery budget
- Repeat 3, `mixed_no_suppression`, outage: Current state did not converge within proposed 10s recovery budget
- Repeat 3, `bands_no_suppression`, outage: Current state did not converge within proposed 10s recovery budget

## Reusable evidence

- [Per-trial CSV](wan-topic-tradeoffs-2026-11-12.csv) for slide tables/charts.
- [Machine-readable trial results](../debug/test_reports/three_topic_comparison_20261112T231057-743299/comparison.json).
- [Frozen manifest](../debug/test_reports/three_topic_comparison_20261112T231057-743299/manifest.json) and [exact compressed inputs](../debug/test_reports/three_topic_comparison_20261112T231057-743299/inputs.tar.gz).
- Each result links its original JSON, JUnit and closed-capture location through its manifest.
- [Offline repair-request timelines](../debug/test_reports/three_topic_comparison_20261112T231057-743299/repair_trace.json) (first 30 s after restoration, not the full KEEP_ALL drain).
- [Derived-analysis source hashes](../debug/test_reports/three_topic_comparison_20261112T231057-743299/analysis_manifest.json).
