# WAN Traffic Accounting Design

## Implementation status

This is a future semantic-accounting design, not a description of the currently deployed
monitor. The live monitor records complete RTPS frame classes and sender/receiver counters in
`debug/<node>_debug/traffic_stats.jsonl`; it does not yet map endpoints to topics, produce the
semantic buckets below, or generate the proposed test-report artifacts.

## Requirement

The test harness must measure WAN cost separately from application delivery. For every test
run, it must quantify payload traffic, application/router control traffic, DDS protocol
overhead, and unclassified traffic on each node's WAN interface. Results must be available
live to the mesh dashboard and written to the node's mounted debug directory for postmortem
analysis.

The measurement must not classify all router-emitted samples as overhead: a router forwarding
a mission/status payload remains payload traffic. Route provenance is a separate reporting
dimension used to measure relay amplification.

## Collection Points And Artifacts

Each container runs the existing `debug/scripts/domain_traffic_monitor.py` on its WAN interface
(`emane0` in the EMANE topology). It writes bounded interval records to its own mounted folder:

```text
debug/platform_30_debug/
  traffic_intervals.jsonl
  traffic_summary.json
  capture_diagnostics.jsonl
```

The control node uses `debug/control_20_debug/` equivalently. The test controller reads these
files after the run and includes aggregate and per-node accounting in
`debug/test_reports/<test_id>.json` and `.html`. Raw files are cleared before the next run;
only the latest compact report per `test_id` is retained.

## Classification Model

Every captured byte receives exactly one wire bucket and, when it is RTPS user data with a
known endpoint, one semantic bucket.

### Wire buckets

| Bucket | RTPS evidence |
|---|---|
| `dds_discovery` | SPDP, SEDP, participant-message, and other builtin discovery writers/readers |
| `dds_reliability_control` | ACKNACK, HEARTBEAT, GAP, NACK_FRAG, HEARTBEAT_FRAG, and related RTPS reliability/control submessages |
| `user_data` | DATA/DATA_FRAG from a user writer |
| `unknown_wire` | Traffic that cannot be safely identified |

The analyzer counts complete captured frame bytes and records UDP-payload bytes separately.
Retransmissions, repair data, and duplicated routed writes are deliberately counted as WAN cost.

### Semantic buckets for user data

The analyzer maps `(writer GUID prefix, writer entity ID)` to a topic using captured SEDP
publication discovery. The test controller emits a versioned topic-class manifest alongside the
expectation snapshot. It assigns each resolved topic one of:

| Bucket | Examples |
|---|---|
| `payload` | Mission, platform-status, sensor, and other business data |
| `application_control` | Targeted commands, team assignments, status-resolution requests |
| `router_admin` | Router command, acknowledgment, and configuration topics |
| `router_observability` | Router health, mesh status, presence, and link statistics |
| `unresolved_topic` | User DATA without a current SEDP/topic mapping or manifest entry |

`unresolved_topic` is a required visible result. The analyzer must never guess a semantic class
from a port, payload shape, or router process identity.

## Interval Log Contract

The monitor emits one JSONL object per `(node, interface, direction, domain, interval)`. It
contains the run/test identity, byte counters for every bucket, packet counters, unresolved
endpoint count, and capture health:

```json
{"run_id":"r-17","test_id":"targeted-command","node":"Platform_30","interface":"emane0","direction":"egress","domain_id":200,"interval_ms":1000,"payload_bytes":12000,"application_control_bytes":800,"router_admin_bytes":0,"router_observability_bytes":1400,"dds_discovery_bytes":600,"dds_reliability_control_bytes":1800,"unresolved_topic_bytes":0,"unknown_wire_bytes":0,"total_frame_bytes":16600}
```

The existing dashboard POST remains a compact live summary. The JSONL record is the
postmortem source of truth; failed dashboard POSTs must not suppress local logging.

## Reported Measures

Reports show bytes, packets, and rates by node, direction, route, topic class, and scenario
window. They calculate:

$$
\mathrm{DDS\ protocol\ overhead} =
\frac{\mathrm{dds\ discovery} + \mathrm{dds\ reliability\ control}}
{\mathrm{delivered\ payload\ bytes}}
$$

$$
\mathrm{control\ and\ router\ overhead} =
\frac{\mathrm{application\ control} + \mathrm{router\ admin} + \mathrm{router\ observability}}
{\mathrm{delivered\ payload\ bytes}}
$$

$$
\mathrm{total\ WAN\ cost\ per\ delivered\ payload\ byte} =
\frac{\mathrm{total\ captured\ frame\ bytes}}
{\mathrm{delivered\ payload\ bytes}}
$$

The delivered-payload denominator comes from the delivery audit's sequence logs, not from
capture counts. The report also shows the unresolved-byte percentage; a high unresolved rate
makes semantic breakdowns `inconclusive` rather than precise-looking but unsupported.

## Implementation Order

1. Extend `domain_traffic_monitor.py` to identify RTPS reliability/control submessages and
   write interval JSONL to its node-owned debug mount.
2. Capture and maintain the SEDP endpoint map, retaining a timestamped mapping history.
3. Generate the versioned topic-class manifest from the effective route/test configuration.
4. Add semantic user-data accounting and capture-health diagnostics.
5. Add the live dashboard breakdown and merge node logs into the delivery-audit HTML/JSON report.
6. Validate against a bounded known-traffic fixture and report unresolved endpoints explicitly.