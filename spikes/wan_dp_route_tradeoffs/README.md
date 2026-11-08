# WAN DP and on-demand route tradeoff spike

Runnable Stage 1 of [the plan](PLAN.md): compare two versus one WAN DP per platform,
with eager versus on-demand pair-route endpoints. The runner uses disposable
Connext containers with **no external network**, a read-only checkout/license
mount, UDPv4-only participants, domain 201, and container-local loopback captures.
The live mesh is not stopped or modified.

## Current optimized goal model (v2, 2026-11-08)

**Test cancelled by user on 2026-11-08.** The small native validation had
already finished; the full optimized matrix was never started and is no longer
scheduled. Optimized validation reports and the archived run inputs were removed;
raw runs were already cleaned. Keep the model/code and earlier expanded-model
results. The commands below are reference only, not authorization to resume.

Optimize compatible payloads into **six application topics plus one separate
synthetic health topic**, rather than keeping pose/velocity/speed as separate
topics. The runner now defaults to this model with three consolidated team topics.

| DDS topic | Audience | Combined content | Modeled updates |
|---|---|---|---|
| NavigationState | All platforms + C2 | Pose, heading, velocity vector, speed | 1 Hz |
| MissionState | Same team + C2 | Mission state/progress and task state/progress | 0.2 Hz |
| SituationalAwareness | Same team + C2 | Contacts and observations, keyed by source/track slot | 0.8 Hz |
| CoordinationState | Same team + C2 | Rendezvous/location and intent/heading/speed | 0.2 Hz |
| ControlCommand | C2 to target platform | Mission/operator command envelope | Event; synthetic traffic also periodic |
| ControlFeedback | Platform to C2 | Tagged ack, control report, transfer report, transfer request, control request | Reliable event |
| RouterHealth (`ScaleHealth`) | C2/platform | Separate synthetic heartbeat | 1 Hz |

Command and feedback direction remain separate. Feedback tags 1-5 distinguish
the five event families; all use the existing external reliable sample profile.
Navigation and team state use the existing periodic profile. Do not merge
incompatible reliability, retention, audience or direction just to reduce counts.
This changes snapshot granularity/rates and serialized sizes, not only names:
awareness combines the previous 0.8 Hz observation and 0.4 Hz contact streams.
The synthetic goal uses primitive snapshot fields and a bounded event payload;
it is not a production mission-plan, multi-track list or vendor INS schema.

The worker creates **only seven types/topics**. Dormant legacy slot indices do
not create extra schemas, writers or readers. Every platform has 12 endpoints;
C2 has 8 and learns exactly four operational types from discovery. Totals are
`12N+8` (392 at N32), versus 816 for the expanded six-team-topic model.
Single-DP membership updates affect six endpoints per changed platform;
dual-DP changes one participant partition. All merged primitive fields and
feedback discriminators are verified against decoded destination samples.

```bash
# Four native validation cases; not a full bandwidth comparison.
PYTHONDONTWRITEBYTECODE=1 python3 spikes/wan_dp_route_tradeoffs/run_scale.py \
  --goal-model-version 2 --populations 6 --team-sizes 2 --team-topic-counts 3 \
  --protocols SPDP,SPDP2 --variants AB --health-profile scale_health30 \
  --seconds 3 --repetitions 1 --parallel 2

python3 spikes/wan_dp_route_tradeoffs/scale_report.py EXACT-COMPLETED-RUN \
  --output debug/test_reports/wan_optimized_goal_validation
```

The full optimized matrix is **cancelled**, not scheduled for this correction.
If explicitly reauthorized, an N32/T4,T8/SPDP,SPDP2/A,B/two-repeat matrix has 16 cases.
Existing [expanded-model measurements](../../debug/test_reports/wan_goal_model_n32_spdp_vs_spdp2_20261107.md)
remain unchanged and describe v1, not this optimization. Reproduce that model
with `--goal-model-version 1 --team-topic-counts 3,6`; never pool v1/v2 results.

## Measurement assurance and review impact (2026-11-07)

New Stage-1 and scale runs copy their measured worker/QoS/accounting sources into
a run-owned snapshot and launch the resolved Docker image ID, not its mutable tag.
Every case checks its execution hashes against the run manifest before DDS startup
and again before accepting results. Scale runs also hash the generated schema inputs
and mount them read-only. Reports preserve those execution hashes; the Stage-1
report generator's own hash is labeled separately.

Acceptance now requires an owned graceful capture stop, exit code zero, all four
installed dumpcap drop counters present and zero, reconciled packet counts, and
owned RTPS traffic covering the initial window and the final measurement boundary.
Forced termination, premature exit (even exit zero), missing counters, drops, or
truncated final-phase coverage reject the run. Both runners and report generators
enforce the new manifest-version gates.

The HTML winner is derived from measured startup-plus-activation discovery bytes,
including ties. Both HTML and slide-deck percentage wording handle increases and
zero baselines rather than always claiming a saving.

**Earlier results are not numerically changed by these fixes.** The original
mission smoke data really has B lowest (94,856 bytes versus A 179,664 and C 107,482).
The accepted 32-platform, 30-second-health comparison retains its at-run source/image,
metadata, cadence and byte/window checks, with B using 29.2-31.3% less steady
discovery. However, those historical summaries do not retain the new zero-drop
and per-case execution proofs. With raw captures removed, capture completeness
cannot now be retrospectively certified. Do not add a new verification pass to old
data; rerun a bandwidth matrix with the new guards if that stronger certification
is required. Historical artifacts remain unchanged.

This uncertainty concerns packet-measurement assurance, not the independently
checked application-delivery, presence timing, C++/DDS integration or live team
isolation results. It does not establish a production migration regression, and the
synthetic 30-second-lease savings still are not a prediction for the deployed
3-second-lease mesh.

Fix validation: 40 DDS-free tests passed, as did six fresh isolated native smokes
(all four Stage-1 variants and two-platform A/B with the 30-second health profile).
Every smoke retained matching execution hashes, zero capture drops, and final-window
coverage; both report pipelines accepted them. HTML browser and slide serialization
checks covered the original B winner, an A winner with B more expensive, and a
zero-byte baseline. These short smokes validate the guards, not a new 32-platform
bandwidth comparison.

## Results: 2026-11-06

Completed 12 full runs: four variants, three repetitions each, with 150-second idle
windows. Median discovery TX fell from **13.86 to 7.67 kb/s (44.7%)** with one WAN
DP per platform. Total idle TX fell by approximately 7-8% in this synthetic workload.

On-demand pair routes reduced local endpoints from 44 to 32, but did **not** reduce
average periodic discovery further. One-second peaks varied with assertion alignment;
on-demand endpoints are not established as a peak-reduction mechanism.

There were **1,920 audited scenario sends, 2,520 expected destination receives, and
zero unintended scenario deliveries**. Shared-DP participants exposed out-of-team
writer metadata while endpoint partitions preserved the tested data audiences.

See the retained [report](../../debug/test_reports/wan_dp_route_tradeoffs_20261106.md)
and [machine-readable results](../../debug/test_reports/wan_dp_route_tradeoffs_20261106.json).
Builtin-reader metadata cardinality varied in the two-DP runs and is flagged
incomplete; do not use those counts as authoritative discovery-state sizing.
Real-router, representative-type, scale, and RF-loss gates remain open.

## Run

Prerequisites: Docker daemon access, the existing `connext:7.7.0` image with
`rti.connextdds`, `dumpcap`, and `tshark`, and a readable license directory.
The capture container receives NET_RAW/NET_ADMIN capabilities, not host networking.

```bash
# DDS-free checks
PYTHONPYCACHEPREFIX=/tmp/wan-dp-unit-pycache \
  python3 -m unittest discover -s spikes/wan_dp_route_tradeoffs -p 'test_*.py' -v

# Short functional smoke matrix; not a discovery-bandwidth measurement
python3 spikes/wan_dp_route_tradeoffs/run.py --idle-seconds 3 --repetitions 1

# Three repetitions, each with five 30-second assertion periods per variant
python3 spikes/wan_dp_route_tradeoffs/run.py --idle-seconds 150 --repetitions 3
```

The last command runs repetitions serially by default. `--parallel 3` runs them in
separate network namespaces but shares host CPU; report that interference caveat.
Orders are balanced as ABCD, DCBA, and BADC. Each worker has a fresh session token,
and the restart test cannot accidentally correlate samples from its previous run.

The runner prints its explicitly resolved `/tmp/wan-dp-spike-...` artifact root.
Produce compact retained reports from that completed run:

```bash
python3 spikes/wan_dp_route_tradeoffs/report.py /tmp/wan-dp-spike-EXACT-RUN \
  --output debug/test_reports/wan_dp_route_tradeoffs_20261106
```

Replace the example path with the runner's actual output. Raw captures and worker
metadata remain in that temporary root until explicitly cleaned after inspection.
Do not delete unrelated temporary directories or the live mesh's debug artifacts.

## Variants and checks

| Variant | Platform WAN DPs | Pair endpoints while dormant |
|---|---:|---|
| A | 2 | Present, forwarding paused |
| B | 1 | Present, forwarding paused |
| C | 1 | Absent |
| D | 2 | Absent |

One control node and three platforms test:

- Commands to P0 only.
- Navigation from P0 to both other platforms, across teams.
- Team data to same-team peers only, before and after moving P1 between teams.
- A P0-to-P2 route created/opened, audited, then closed.
- On-demand endpoints removed on closure.
- Health/probe delivery and platform restart recovery.
- P2 team-writer metadata absent from P0's main DP in A/D, visible in B/C.

Each topic uses the same small XML-defined sample type with a 64-byte payload,
source/session/sequence/phase identity, and externally authored QoS. Health and
APPLICATION_AUTO-ack probe traffic remain identical across variants; this does not
implement the separate RouterLinkProbe removal.

## Measurements and limitations

Captures begin before startup. Required endpoint matches, exact recipient delivery,
and a stable discovery inventory gate the idle window. The full run measures
150 aligned seconds, separating startup, idle, loaded, route-open/close,
team-change, and restart windows.

Accounting attributes packets by owned participant GUID prefix and counts whole
frames once. Mixed/unknown categories remain explicit; multiple writer IDs in one
packet do not multiply its frame bytes. Discovery control packets with builtin
writer IDs are included in discovery TX. Writer-entity-set totals are retained,
but no assertion/response interpretation is inferred from aggregate discovery.

This is **not** a real-router, production type-metadata, 6/12-platform, or EMANE
result. Unicast discovery probes localhost participant IDs 0-31, so absolute byte
rates and peaks cannot be projected onto the live RF topology. Command-to-first-
delivery latency is not established by the post-command match-poll duration.
Those are later gates in the plan, not silently passed tests.

## Fourteen-type population sweep

[run_scale.py](run_scale.py) implements a separate workload based on the local
[comms characterization](../../docs/comms-workload-characterization.md).
It does not use UMAA definitions or customer captures. Following the clarified
scope, only **serialized sample sizes** are targeted, not TypeObject sizes.
[mission_types.xml](mission_types.xml) is the reusable generated type catalog;
[schema_profile.py](schema_profile.py) is its source.

```bash
# Functional baseline and largest-population smoke tests
python3 spikes/wan_dp_route_tradeoffs/run_scale.py \
  --populations 2,12 --team-sizes 3 --team-topic-counts 1 --protocols SPDP2 \
  --variants ABC --health-profile scale_periodic \
  --seconds 3 --repetitions 1 --parallel 3

# Population sweep with five 30-second periods at each population/design
python3 spikes/wan_dp_route_tradeoffs/run_scale.py \
  --populations 2,7,8,12 --team-sizes 3 --team-topic-counts 1 --protocols SPDP2 \
  --variants ABC --health-profile scale_periodic \
  --seconds 150 --repetitions 2 --parallel 4

# Use the exact temporary directory printed by that run
python3 spikes/wan_dp_route_tradeoffs/scale_report.py /tmp/wan-dp-scale-EXACT-RUN \
  --output debug/test_reports/wan_dp_mission_type_scale_20261107
```

The fourteen mission schemas contain named mission/task/contact identifiers,
position/velocity fields, status values, and bounded octet payloads. The common
`source`, `instance`, `stamp`, and `session` members support delivery auditing;
`instance` also identifies keyed elements and requests. The small ControlRequest
has an optional action field to permit its smallest samples.

There are no artificial optional-member banks, TypeObject-size targets, C++ sizing
helper dependency, or explicit TypeObject traffic injections. Types/topics use the
existing Connext discovery configuration. Payload length is adjusted using actual
CDR serialization, staying inside each supplied min/max range and within three
bytes of the target. This models sample sizes, not exact customer schema layouts
or a particular TypeObject wire size.

### Squad WAN: 32 platforms, teams of 4 and 8

"Squad WAN" here means direct DDS communication on the same WAN, not a
swarm-radio mesh or multi-hop forwarding. The two edge configurations are eight
teams of four and four teams of eight. The workload still shares pose across the
squad and contact parents inside teams; other operational reports go to control.
It is not fourteen team-to-team payload streams.

The runner supports up to 32 platforms and a `--team-sizes` sweep. Its external
XML probes participant IDs 0-95, sufficient for 65 WAN DPs in the two-DP case.
This expanded probe range is identical across variants. Earlier reports used
0-31, so cold-start byte totals across those different configurations must not
be treated as like-for-like measurements.

Capture accounting v2 counts pure builtin discovery DATA/heartbeat/ACK bundles
as discovery, not mixed application traffic. Actual discovery/application mixtures
remain separate. Epoch boundaries are retained for every window. Earlier reports
used frame-category accounting that could leave pure discovery/control bundles
in `mixed`; their discovery-only totals must not decide the crossover.

```bash
# Verify all six squad/team/design combinations
python3 spikes/wan_dp_route_tradeoffs/run_scale.py \
  --populations 32 --team-sizes 4,8 --team-topic-counts 1 --protocols SPDP2 \
  --variants ABC --health-profile scale_periodic \
  --seconds 3 --repetitions 1 --parallel 2

# Repeat 150-second windows for each squad/team/design combination
python3 spikes/wan_dp_route_tradeoffs/run_scale.py \
  --populations 32 --team-sizes 4,8 --team-topic-counts 1 --protocols SPDP2 \
  --variants ABC --health-profile scale_periodic \
  --seconds 150 --repetitions 2 --parallel 2
```

Results and report validation include team size as a separate dimension; teams
of four and eight are never pooled into the same average. At 32 platforms the
final inventory is 592 endpoints; staged mode starts with 164. This remains an
isolated loopback test, not RF-link or production-router validation.

The [corrected 32-platform comparison](../../debug/test_reports/wan_dp_squad32_teams4_8_accounting_v2_20261107.md)
and [retained data](../../debug/test_reports/wan_dp_squad32_teams4_8_accounting_v2_20261107.json)
use accounting v2 and twelve accepted 150-second cases. One eager DP/platform
uses about 170 kb/s aggregate steady discovery versus 360-363 kb/s for two DPs,
approximately 53% less. Two DPs reduce startup SEDP writer-frame traffic,
but total startup-plus-activation discovery is only lower with teams of four.
That roughly 7% startup advantage is overtaken after about 30 additional steady
seconds. With teams of eight, one eager DP is lower both at startup and in steady
discovery. Staged topics do not improve steady discovery over eager creation.
This establishes the comparison at 32 platforms, not the crossover population.
All 2,202,924 eligible destination receives were verified, with none missing or
unintended.

The replacement catalog passed installed Connext 7.7 XML loading and min/max CDR
checks for all fourteen types, plus fifteen DDS-free tests. Three A/B/C baseline
smoke cases audited 631 sends and 841 destination receives with none missing or
unintended. The [smoke data](../../debug/test_reports/wan_dp_mission_types_smoke_20261107.json)
retains actual sample sizes. Its three-second windows are **not** new scale or
periodic-discovery measurements.

### Isolated health-liveliness timing

[liveliness.py](liveliness.py) compares automatic health writer/reader leases of
3 s and 30 s without changing production QoS. Its external
[profiles](liveliness_qos.xml) retain 1 Hz health writes, a 2 s deadline,
SPDP2 participant assertions configured at 30 s, and a 100 s participant lease.
Only the health liveliness leases differ. Installed Connext 7.7 confirms three
assertions per lease, so the expected automatic cadence is 1 s versus 10 s,
not 30 s.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 spikes/wan_dp_route_tradeoffs/liveliness.py \
  --seconds 65 --repetitions 2
```

Each case uses one control and one platform in an isolated container with
UDPv4-only loopback, two participant-ID probes, and domain 201. After the healthy
window, the platform withholds application writes for 35 s while its automatic
liveliness remains running, resumes writes for 5 s, then is SIGKILLed by its
owned process handle. The survivor measures deadline misses and writer expiry
before participant purge. Reader expiry observation allows two health leases
plus 7 s; the lease is not assumed to be an exact callback timer.

The [timing report](../../debug/test_reports/health_liveliness_3_vs_30_20261107.md)
and [retained data](../../debug/test_reports/health_liveliness_3_vs_30_20261107.json)
include actual packet intervals, per-participant writer-frame bytes, heartbeat
cadence, deadline timing, and crash/last-assertion expiry timing. Builtin writer
`000200c2` is verified against the installed SDK's participant-message writer
identifier. Source hashes and the unchanged production-QoS hash are retained.
The timing payload is synthetic, not the full RouterHealth type or router.

This is separate from the 32-platform campaign: `scale_periodic` uses a default
**infinite** writer liveliness lease, verified in the installed SDK. Do not apply
this 3-to-30 s optimization percentage to that campaign's discovery totals.

The four accepted cases measured approximately 1.000 s versus 10.000 s
participant-message intervals. Aggregate liveliness frame traffic fell from
2.336 to 0.252 kb/s (89.2% lower); all discovery fell from 2.539 to 0.454 kb/s
in this two-participant rig. Application heartbeat timing remained approximately
1 Hz with all 496 eligible destination receives verified.

Deadline-based STALE remained approximately 2 s after the last heartbeat.
Writer liveliness loss after SIGKILL moved from 3.97-3.98 s to 39.97-39.98 s.
The corresponding last-assertion-to-expiry intervals were 4.08-4.09 s and
40.09-40.09 s: a 30 s lease does not promise a callback exactly 30 s after a
crash. Withholding writes alone did not produce DEAD while automatic liveliness
continued. Before applying this to a real WAN DP, inventory other automatic
writers: a shorter lease on another writer can keep the assertion cadence fast.

### Assignment-gated A/B comparison with 30 s health leases

The `scale_health30` profile applies automatic 30 s health writer/reader leases
to both designs, retaining 1 Hz health samples and a 2 s deadline. The other
application endpoints retain their existing profiles. Expected participant-message
assertions are every 10 s on participants with health writers and every 30 s on
A's position-only participants; the capture checks logical writer sequence numbers
so destination fanout does not masquerade as a faster assertion cadence.

Assignment model v2 keeps all endpoints eager:

- **A:** an operational participant starts with its private identity partition;
  assignment adds its team participant partition. The separate position participant
  discovers the whole squad. Control remains visible through its wildcard partition.
- **B:** one participant retains the `SHARED` discovery partition throughout.
  Contact publisher/subscriber partitions change only after assignment, filtering
  application delivery without restricting endpoint discovery.

Before assignment, a contact probe must reach control and no other platform.
After assignment, the delivery audit checks exact intended recipients. Publication
and subscription builtin-reader caches independently verify the exact remote owner
sets and endpoint counts before and after assignment, rather than inferring SEDP
visibility from participant bootstrap handles or matched application endpoints.

Connext creates builtin readers lazily and does not backfill discovery missed before
lookup. Each worker therefore creates its participants disabled, obtains both builtin
readers, restores the process-local factory setting, and enables the participants
before creating application endpoints. This follows the
[7.7 builtin-reader lifecycle](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/Built_in_DataReaders.htm)
and is verified with separate-process peer discovery. A late-reader experiment is
not a valid endpoint-cache census.

```bash
# Verify assignment scoping and the main-participant assertion cadence first.
PYTHONDONTWRITEBYTECODE=1 python3 spikes/wan_dp_route_tradeoffs/run_scale.py \
  --populations 2 --team-sizes 1,2 --team-topic-counts 1 --protocols SPDP2 --variants AB \
  --health-profile scale_health30 --seconds 35 --repetitions 1 --parallel 2

# Eight squad cases with complete 150-second steady windows.
PYTHONDONTWRITEBYTECODE=1 python3 spikes/wan_dp_route_tradeoffs/run_scale.py \
  --populations 32 --team-sizes 4,8 --team-topic-counts 1 --protocols SPDP2 --variants AB \
  --health-profile scale_health30 --seconds 150 --repetitions 2 --parallel 2
```

Both use the same broad 0-95 initial-peer probe range. This comparison isolates the
participant/pub-sub assignment design, not an optimized peer-probing deployment.
The earlier campaign used infinite health liveliness and preconfigured B contact
partitions; its rates cannot substitute for this new model.

The [30 s squad comparison](../../debug/test_reports/wan_dp_squad32_health30_participant_vs_pubsub_20261107.md)
and [retained measurements](../../debug/test_reports/wan_dp_squad32_health30_participant_vs_pubsub_20261107.json)
contain eight accepted cases: two repetitions of each A/B and team-size combination.
At 32 platforms, A uses 65 participants and B uses 33, including control; both
have 592 application/health reader and writer endpoints.

| Teams | A discovery kb/s | B discovery kb/s | A remote endpoint records/platform | B remote endpoint records/platform |
|---:|---:|---:|---:|---:|
| 4 | 377.88 | 267.39 | 142 | 574 |
| 8 | 384.85 | 264.24 | 206 | 574 |

One DP reduces total steady discovery by 29.2-31.3%. Two DPs genuinely reduce
endpoint-cache exposure and participant-message liveliness frame traffic
(62.29/77.24 versus 123.34 kb/s), but that does not offset their other discovery
traffic with this broad probe configuration. B's higher startup-plus-assignment
discovery cost is recovered after approximately 108 s for teams of four or 32 s
for teams of eight, assuming the measured steady conditions persist. These are
time amortization estimates, not population crossovers.

All native endpoint-owner/count checks, health-profile and per-DP cadence checks,
source/image provenance, and capture byte/window reconciliations passed.
The audit verified 1,475,988 eligible destination receives from 99,534 sends,
with none missing or unintended. Production QoS remained unchanged.

### Expanded goal datamodel campaign (v1): protocol and membership comparison

The goal-model upgrade is a **separate proposed compact synthetic
coordination-state model**, not a numerical revision of the accepted assignment
model v2 campaign above. Historical v2 reports retain their 18N+16 endpoint
inventory and original grouping/text. Do not pool those runs with goal model v1 /
assignment model v3, or pool different protocols or selected team-topic counts.

The requested full matrix is **32 cases**: N=32, team sizes 4/8, k=3/6 selected
team topics, SPDP/SPDP2, eager designs A/B, two repetitions, `scale_health30`,
150-second steady windows, and both swap and reassignment with a default
10-second settled hold. The [accepted expanded-model report](../../debug/test_reports/wan_goal_model_n32_spdp_vs_spdp2_20261107.md)
contains all 32 completed cases; its numbers are not optimized-model results.
A small goal smoke needs at least three
complete teams, each with at least two members (for example N=6 / team size 2),
so reassignment can mix peer groups while changing every node's team.

| Application families: 14 distinct types | k=3 | k=6 |
|---|---|---|
| Pose, Velocity, Speed | Squad-wide | Squad-wide |
| TaskProgress, MissionState, ContactReport | Team + C2 | Team + C2 |
| SharedObservation, RendezvousState, CoordinationIntent | C2 only | Team + C2 |
| MissionAdd, TransferRequest, ControlReport, TransferReport, ControlRequest | C2 only | C2 only |

| Selected team family | Synthetic sample min–max bytes | Modeled rate |
|---|---:|---:|
| TaskProgress | 80–160 | 0.2 Hz |
| MissionState | 96–256 | 0.2 Hz |
| RendezvousState | 96–160 | 0.2 Hz |
| CoordinationIntent | 96–332 | 0.2 Hz |
| SharedObservation | 156–4212; typical 284 | 0.8 Hz |
| ContactReport | 100–120 | 0.4 Hz |

Coordination schemas use compact state/progress/location/intent primitives,
**not multi-KiB full mission plans renamed as peer state**. Only unchanged
squad/C2 types inherit the historical serialized-size ranges/field layouts.
Squad-wide Pose, Velocity and Speed have modeled 1 Hz rates. These are proposed synthetic
sizes/rates, not vendor/source schema fidelity, calibrated TypeObject sizes,
customer measurements, or RF/production forecasts. Actual serialization evidence
and the goal datamodel are retained per run; operational rates are not a claim
that all topics transmit during the discovery steady window.

A uses a position DP for all three squad types and a main DP for operational
types. Private+team main-DP partitions gate peer discovery; its team Publisher
stays `TEAM_DATA` + `PLATFORM`, and Subscriber stays `TEAM_DATA`. Membership
updates change **only participant partitions**. B keeps one `SHARED` main DP and
changes **both Publisher and Subscriber parents for every selected team topic**.
All enabled endpoints are eager. Each platform has 19+k endpoints (22/25),
control has 16; totals are (19+k)N+16, with 2N+1 WAN DPs in A and N+1 in B.

Health remains identical synthetic C2-to-platform/platform-to-C2 traffic in both
designs, not the full production RouterHealth observer roster. `scale_health30`
uses automatic 30-second leases, not the deployed production lease. Native
protocol selection and effective QoS must be verified per case: installed SDK
discovery masks are SPDP|SEDP = 3 and SPDP2|SEDP = 18.

Swap exchanges one member between two teams (two changed nodes). Reassignment
changes every platform's team, mixes old peer groups, and preserves balanced
complete teams. GUIDs/endpoints must survive both. Local update counts are
changed-node count / zero parent updates in A; zero DP updates /
2*k*changed-node count parent updates in B.

Change windows (`swap_change`, `reassignment_change`) contain normal continuously
transmitting background traffic; **their bytes are not isolated serialized
membership transaction sizes**. Transitional team samples are captured but
**not strict-audited**. After convergence, settled windows prove exact scope
and delivery against authoritative membership maps and ledger targets saved at
send time, with positive probes/sends and zero missing/unintended receives.
Before initial assignment, all selected team families must reach C2 only.

Select model version 1 explicitly for this expanded campaign; keep each protocol/k
dimension explicit in the run manifest (`protocols`, `team_topic_counts`).
Generate the requested compact Markdown/JSON from the runner's exact completed
artifact root; HTML/slides remain historical and are not upgraded in this iteration:

```bash
# DDS-free report tests only; no mesh or DDS startup.
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=spikes/wan_dp_route_tradeoffs \
  python3 -m unittest test_goal_report -v

# Expanded goal campaign: 32 cases, not current runner defaults.
PYTHONDONTWRITEBYTECODE=1 python3 spikes/wan_dp_route_tradeoffs/run_scale.py \
  --goal-model-version 1 --populations 32 --team-sizes 4,8 --team-topic-counts 3,6 \
  --protocols SPDP,SPDP2 --variants AB --health-profile scale_health30 \
  --seconds 150 --repetitions 2 --parallel 2 --change-hold-seconds 10

# Native goal smoke: 8 cases; short windows do not establish steady savings.
PYTHONDONTWRITEBYTECODE=1 python3 spikes/wan_dp_route_tradeoffs/run_scale.py \
  --goal-model-version 1 --populations 6 --team-sizes 2 --team-topic-counts 3,6 \
  --protocols SPDP,SPDP2 --variants AB --health-profile scale_health30 \
  --seconds 3 --repetitions 1 --parallel 2 --change-hold-seconds 10

python3 spikes/wan_dp_route_tradeoffs/scale_report.py EXACT-COMPLETED-GOAL-RUN \
  --output debug/test_reports/wan_dp_goal32_protocol_membership
```

Legacy workload reproduction must explicitly pass `--team-topic-counts 1
--protocols SPDP2`, along with the historical population/team/design arguments;
otherwise old commands now select the default goal dimensions. For example,
the historical eight-case N32 assignment-v2 campaign uses:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 spikes/wan_dp_route_tradeoffs/run_scale.py \
  --populations 32 --team-sizes 4,8 --team-topic-counts 1 \
  --protocols SPDP2 --variants AB --health-profile scale_health30 \
  --seconds 150 --repetitions 2 --parallel 2
```

Goal reporting rejects missing/duplicate Cartesian cases, unverified protocols,
wrong inventories, missing selected-family assignment proofs, incomplete change
audits, mismatched execution source/image provenance, and invalid capture
integrity. New goal runs require accounting v2 plus provenance/integrity v1:
owned graceful zero-drop capture shutdown, reconciled bytes/frames, and coverage
through the final settled/operational boundary. Historical uncertified results
cannot be retroactively promoted. Reports retain each run, min/median/max per
dimension, memberships, capture windows, source hashes and native proof fields.
Scale captures now request a **64 MiB dumpcap buffer** (`-B 64`); both the
manifest and each result record `capture_buffer_mib=64`, with the executing
source pinned by provenance. This reduces capture-buffer pressure, not the
acceptance standard: every installed drop counter must still be present and zero.
The initial concurrent N32/SPDP attempt with the default 2 MiB buffer rejected
one completed case with 4,966 pcap drops. Its entire incomplete campaign was
stopped and its exact owned containers cleaned; rejected or incomplete captures
must not be merged into accepted results. Validate the larger buffer under the
same concurrent load, then rerun a fresh complete matrix. Buffer size alone is
not proof that a rerun has passed the zero-drop/completion gates.
Control must learn exactly 12 native type identities (all 14 except MissionAdd
and Pose). Goal audit eligibility excludes the final two seconds of **each**
strict phase using per-worker `phase_ends`, and records `expected_receivers` at
send time. Historical legacy audit eligibility is deliberately unchanged.

Setup/steady metrics and change-window bytes, SEDP frames, peaks, convergence,
local updates and metadata exposure are reported separately per team/k/protocol/
design. Architecture savings compare B with A at fixed protocol; protocol savings
compare SPDP2 with SPDP at fixed design. Winners, increases, ties and undefined
zero-baseline percentages are derived from measured data, never assumed.

### HTML comparison report

The existing HTML/PNG report and PowerPoint/PDF deck below are historical
snapshots using pre-v2 capture accounting. Their discovery-only figures must
not determine a DP-layout choice; use the corrected squad comparison above.

The [standalone HTML report](../../debug/test_reports/wan_dp_mission_comparison_20261107.html)
includes startup/activation charts, the fourteen sample-size checks, verified
type-discovery configuration, and a clearly separated historical appendix.
It is self-contained, responsive, and printable. Regenerate the current
two-platform smoke comparison with:

```bash
python3 spikes/wan_dp_route_tradeoffs/html_report.py \
  debug/test_reports/wan_dp_mission_types_smoke_20261107.json \
  --historical debug/test_reports/wan_dp_fourteen_type_scale_20261106.json \
  --output debug/test_reports/wan_dp_mission_comparison_20261107.html
```

The input JSON includes SDK sample-size and effective-QoS verification evidence.
The HTML renderer rejects missing or mismatched evidence. It is intentionally
scoped to this smoke comparison rather than presenting a new full-scale result.

The [slide deck](../../debug/test_reports/wan_dp_crossover_brief_20261107.pptx)
and [PDF preview](../../debug/test_reports/wan_dp_crossover_brief_20261107.pdf)
brief the v2 comparison, measured baseline, illustrative endpoint-scope model,
and proposed crossover campaign. Speaker notes document assumptions and sources.
[slide_deck.py](slide_deck.py) regenerates the editable PowerPoint using
`python-pptx`; LibreOffice can export the PDF. The deck does not claim a measured
6/12/24-platform break-even.

One control plus 2/7/8/12 platforms compare:

- A: two WAN DPs per platform, with position isolated on its own DP.
- B: one shared WAN DP, with eager operational endpoints.
- C: one shared WAN DP, with non-core endpoints created after team assignment.

All retain squad-wide pose at 1 Hz, addressed commands, and health. Teams contain
up to three platforms. The operational phase adds 1 Hz velocity/speed, 0.4 Hz
contact parents, and 0.8 Hz aggregate updates across keyed contact-element slots.
Contact parents also go to team peers; that peer rate is a modeled extension,
not a measured customer rate. Other topic families are sparse test events,
including explicit large-sample bursts, not continuous maximum-size streams.

The control learns twelve operational types through discovery before constructing
their readers. This avoids treating a small preloaded schema as proof of type
discovery scalability. Source-side types are locally defined, as they would be at
the exporting applications.

Startup, team/topic activation, steady squad-wide position, mission burst, and
operational windows are separate. Exact receiver audits check squad delivery,
command destination, and team-contact isolation; activation timing includes
endpoint creation through all control-side reader matches.

This sweep is still unthrottled loopback, not real routers or RF. Local
100-150-topic/40-participant populations, exact schema nesting, rejoin/restart,
1268-byte MTU, and rate/loss impairment are not implemented. CDR targets allow
up to three bytes of synthetic layout alignment; actual size histograms are retained.

### Historical scale result: superseded schema model

**The measurements below used the older TypeObject-v1-proxy-calibrated optional-field
schemas, not the replacement mission schemas. Do not treat their discovery-byte
results as measurements of the new workload.** The historical report remains
unchanged for traceability; a new full sweep is required for replacement numbers.

The [scale report](../../debug/test_reports/wan_dp_fourteen_type_scale_20261106.md)
and [compact data](../../debug/test_reports/wan_dp_fourteen_type_scale_20261106.json)
retain all 24 cases: two repetitions of A/B/C at each population, with 150-second
steady windows. Medians:

| Platforms | Discovery average, A / B / C kb/s | Startup + activation discovery, A / B / C KiB | Steady total TX, B kb/s |
|---:|---:|---:|---:|
| 2 | 9.88 / 5.82 / 5.92 | 176.8 / 92.5 / 104.9 | 29.91 |
| 7 | 28.79 / 14.69 / 15.84 | 612.6 / 405.2 / 529.0 | 197.87 |
| 8 | 32.44 / 16.50 / 17.69 | 720.3 / 496.6 / 666.2 | 249.05 |
| 12 | 46.53 / 22.60 / 25.88 | 1095.2 / 879.5 / 1262.1 | 508.67 |

One eager shared DP cut average discovery by roughly half at 7-12 platforms.
Its cumulative startup/activation saving shrank from 33.9% at seven platforms
to 19.7% at twelve. Staging topics reduced initial inventory but shifted discovery
into assignment: at twelve platforms C emitted 768.1 KiB during activation,
and its cumulative discovery exceeded A by 15.2%.

Whole-frame SEDP writer traffic also grew with the broader scope: at twelve
platforms, startup-plus-activation SEDP writer-frame totals were 667.1 / 1096.7 /
1199.4 KiB for A / B / C. These are not pure metadata bytes. Total discovery
includes other builtin traffic, so the category totals answer different questions.

The 71,027 eligible sends required 329,479 destination receives, with no missing
or unintended delivery. With squad-wide pose retained, one-DP steady total TX
still grew to about 509 kb/s at twelve platforms; reducing periodic discovery
does not remove application fanout. Loopback delivery success does not establish
RF-link tolerance.
