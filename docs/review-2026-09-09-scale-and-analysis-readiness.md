# Review -- scale and analysis readiness -- 2026-09-09

Scope: current C++ router, `harness_v2/` live-mesh path, the container image, and the
EMANE/test-harness roadmap. This is a readiness review for an AWS host capable of running
at least eight platforms. It distinguishes a functional router-scale demonstration from
an experiment that can support claims about network overhead, intermittent faults, or
protocol-statistics detection.

## What was verified

| Check | Result | Notes |
|---|---|---|
| Container image | pass | The Connext 7.7 image builds from RTI's Debian repository and imports `rti.connextdds`. |
| Host-process mesh | pass | Three- and six-platform runs launched through `harness_v2/scripts/run_mesh.sh`, each router logged `router.start.ok`, and the dashboard returned HTTP 200. |
| C++ unit suite | pass via `bash router/run_tests.sh` | The direct `./router/run_tests.sh` invocation fails with exit 126 because the file mode is `0644`, not executable. |
| Python router e2e suite | not run in this review | Required before declaring a router regression gate green. |
| EMANE/CommEffect/RF Pipe | not implemented | No `emane0`, EMANE configuration, or impairment runner was executed. |
| Scenario controller | not implemented | No lifecycle API, timed fault schedule, run journal, or artifact bundle exists. |

## Overall assessment

The router is ready for bounded functional scale exploration. Router presence and
per-peer reliable-protocol metric capture are implemented, and the current host-process
mesh works at six platforms. The AWS host provides useful capacity, but capacity does not
remove the principal experimental limitation: the current mesh shares host networking and
uses DDS domains for isolation. It cannot prove that WAN traffic is isolated, impaired by
RF conditions, or measured without bypass paths.

The system is therefore **not ready for quantitative network-overhead, fault-correlation,
or detector-performance claims**. It is appropriate for exploratory router/dashboard
testing only until the Phase 0.5 and Phase 3 boundaries are implemented and verified.

# HIGH

## H1 -- Eight-platform traffic totals are incomplete

`run_mesh.sh` creates one control plus eight platform WAN participants. Its WAN discovery
peer range allows that population, but `domain_traffic_monitor.py` defaults to
`max_participants=8` and only scans participant ids 0 through 7. The launcher does not
override this default.

**Impact.** Domain-200 traffic involving higher participant ids is omitted. Eight-platform
traffic totals and overhead ratios would be biased low.

**Required action.** Make the monitor's participant range derive from `--platforms` (with
control headroom), and add an automated eight-platform completeness check.

## H2 -- Current packet classification is unsuitable for scientific overhead accounting

The traffic monitor assigns a whole frame from the first RTPS writer entity id. RTPS can
bundle multiple submessages, including discovery, reliability control, and user data.
Its UDP-port-to-domain mapping also accepts port remainders beyond the valid RTPS
participant-port slots.

**Impact.** Discovery/data byte totals are not reliable enough for bytes-per-delivered-
payload or fault-correlation conclusions.

**Required action.** Parse every RTPS submessage, account bytes by RTPS class without
double-counting frame headers, validate the RTPS port mapping, and retain raw bounded
pcaps plus parser version/configuration in every artifact bundle.

## H3 -- LAN transport and future WAN isolation are not enforced

The WAN profile selects UDPv4, but the LAN profiles do not restrict their transport.
They can therefore use shared memory. There is also no `allow_interfaces_list` binding
WAN participants to `emane0` or LAN participants to loopback.

**Impact.** A host-process run does not represent the future RF topology. Packet capture
cannot establish LAN/WAN separation, and shared-memory behavior can hide LAN wire traffic.

**Required action.** In the EMANE slice, make LAN UDPv4-only and loopback-pinned, make WAN
`emane0`-pinned, and add a negative capture test proving that LAN traffic never occurs on
`emane0`.

## H4 -- Harness lifecycle can orphan a live mesh

`run_mesh.sh up` removes its workdir before establishing that the previous tracked PIDs are
gone. It checks the dashboard port but not a previous run's ownership state. Its `down`
path signals raw PIDs, does not verify process identity, and only reports residual shared
memory after waiting.

**Impact.** A repeated launch can lose PID/log ownership, overlap DDS participants, and
contaminate later measurements. PID reuse can also target an unrelated process.

**Required action.** Do not use the host-PID harness as the experiment controller. Phase
0.5 must provide container labels/run ids, ownership-safe lifecycle operations, a clean
reset check, and an append-only action/result journal.

# MEDIUM

## M1 -- Launch success is not a full readiness check

After a fixed delay, the launcher reports only the control log tail. It does not establish
that every router, simulator, mesh-control process, dashboard, and monitor is alive and
participating.

**Required action.** Add per-component readiness and peer-discovery assertions before a
run receives a start timestamp.

## M2 -- Active WAN receive-buffer tuning is likely ineffective

The WAN profile selects UDPv4 but places the configured receive socket buffer beneath the
UDPv6 transport configuration.

**Required action.** Put the tuned value on the active UDPv4 path and prove the effective
socket configuration under load.

## M3 -- Link probes are traffic and must be accounted for

`LinkStatsCollector` creates reliable probe traffic among WAN peers. At larger mesh sizes,
the probe and acknowledgement cost grows with peer pairs.

**Required action.** Classify probe bytes separately from application, discovery, and
reliability-repair traffic. Do not present aggregate WAN bytes as application overhead.

## M4 -- Protocol counters require fault-ground-truth correlation

Per-peer counters are a valuable capture surface, but they are not a detector by themselves.
NACKs, repair bytes, backlog, send-window changes, and RTT can also reflect rediscovery,
quiet traffic, or local limits.

**Required action.** Use controller-labeled baseline, congestion, bounded loss,
intermittent loss, cutout, recovery, and node-restart windows. Retain raw measurements,
then evaluate detection/recovery latency, precision/recall, and false alarms before
assigning a `degraded`, `congested`, or `unreachable` state.

## Roadmap comparison

| Roadmap phase | Current status | Decision |
|---|---|---|
| 0 -- container node stack | partial | Current Connext container supports the host-process harness; the planned per-node Compose stack is still stale. |
| 0.5 -- isolation/controller baseline | missing | Implement next. This is the gate for repeatable experiments. |
| 1 -- ISC relay PoC | partial | Spikes exist, but this is separate from the immediate network test path. |
| 2 -- passive Scope/sniffer | missing | Current dashboard is not the planned passive Scope product. |
| 3 -- EMANE RF fabric | missing | No RF topology, `emane0`, or interface pinning. |
| 4 -- instrumentation/metrics | partial | Router counters and diagnostic loopback capture exist; defensible WAN accounting is missing. |
| 5 -- degradation/prioritization | missing | No CommEffect/EEL scenarios or DDS prioritization configuration. |
| 6-9 -- dynamic C2, peer loss, ISC at RF scale, discovery/security | partial to missing | Router features lead the harness; scenario evidence is absent. |

## Decision gate

Perform a focused code review **now**, before testing and analysis. It should cover the
host mesh lifecycle, QoS isolation, packet-accounting correctness, link-stat semantics,
and the migration to Compose. A broad end-to-end review is most useful after the following
Phase 0.5 gate passes:

1. One control and two platform containers start through a controller-owned run id.
2. `emane_ctrl` is a management/EMANE-control bridge only; direct WAN DDS routing is absent.
3. Reset proves no owned containers, listeners, DDS shared-memory entries, or run-owned
   processes remain.
4. Every lifecycle/fault action has a timestamped request and correlated result in local
   run artifacts.

After that gate, implement EMANE RF Pipe and CommEffect, validate nominal S1, then begin
labeled S2 degradation experiments. An eight-platform run before these gates is still
useful for router discovery and dashboard load observation, but it must not be used as
evidence for RF behavior, DDS wire overhead, or impairment detection.

## Recommended next sequence

1. Fix H1-H4 and M1-M3 in the current diagnostic path where applicable.
2. Repair or replace the stale `harness/` Compose scaffold with the Phase 0.5 topology.
3. Add a minimal scenario controller: lifecycle, run ids, event journal, reset, and
   artifact directory on local storage.
4. Add QoS pinning and EMANE RF Pipe; prove S1 with bounded capture on `emane0`.
5. Add CommEffect fault actions and controlled traffic profiles.
6. Run the correlation matrix, then evaluate candidate link-impairment detectors.