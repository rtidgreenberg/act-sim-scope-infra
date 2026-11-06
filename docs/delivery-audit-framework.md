# Delivery Audit Framework

## Purpose

The delivery audit turns a scenario run into an end-to-end, message-level result.
It answers whether each application message expected to traverse an enabled route reached
every intended receiver, rather than treating router health, DDS matching, or packet capture
as proof of delivery.

It is a test-harness artifact for Phase 4 instrumentation. It does not replace packet capture:
the audit measures application delivery, while packet capture measures wire cost and protocol
behavior. The wire-cost collection and classification contract is defined in
[traffic-accounting-design.md](traffic-accounting-design.md).

## Run Layout

Each node has a dedicated host-owned debug directory that is mounted read-write into only that
node's container. Node directories contain only the current run's raw artifacts; the retained
summary is keyed by stable `test_id`:

```text
debug/
  control_20_debug/
    events.jsonl
    logs/router.log
  platform_30_debug/
    events.jsonl
    logs/router.log
  platform_31_debug/
    events.jsonl
    logs/router.log
  test_controller_debug/
    manifest.json
  test_reports/
    <test_id>.json
    <test_id>.html
```

The harness creates the node directories before container startup and mounts exactly one of them
at a fixed in-container path. A control or platform process can write its own events, logs, and
captures but cannot alter another node's evidence. The test controller owns
`debug/test_controller_debug`, reads every node directory after the run, and writes the compact
summary to `debug/test_reports/<test_id>.json` and `debug/test_reports/<test_id>.html`.
SQLite/DWH files and other lock-sensitive transient state remain under `/tmp`.

### Current baseline

The implemented container baseline writes a versioned `manifest.json` and immutable
`expectations.json` with the selected nodes and minimum evidence requirements for
`ControlCommand` and `PlatformInitStatus`. The snapshot encodes the current baseline's
enabled `control_command` destination filter and `platform_init_status` control receiver;
the analyzer reads only those nodes' logs and uses that snapshot rather than hard-coded
recipient logic. It reports each declared topic independently and returns `inconclusive` for
malformed/missing evidence or insufficient samples. Generic route/topology compilation,
latency/order analysis, and command-ack correlation remain Phase 4 work.

### Retention

Before every run, the harness clears the contents of `debug/*_debug` and
`debug/test_controller_debug`, preserving only directory marker files. It must do this before
containers start so no process holds an open log while cleanup occurs. Raw events, router logs,
and captures are therefore available for live inspection and postmortem analysis only until the
next run.

After analysis, the harness atomically replaces the two retained summary files for that
`test_id`. It does not retain historical raw artifacts or unbounded timestamped report copies.
A test needing long-term evidence exports its selected summary and optional artifacts explicitly
to external durable storage.

## Event Contract

Application payload types participating in an audit carry:

- `run_id`: identifies the scenario run.
- `source_node`: stable origin identity.
- `sequence`: monotonically increasing per `(source_node, topic)` stream.
- `sent_at_ns`: source wall-clock timestamp for latency analysis.

Each simulator appends JSONL records synchronously enough for test use:

```json
{"event":"sent","run_id":"...","node":"Control_20","topic":"ControlCommand","source_node":"Control_20","sequence":42,"sent_at_ns":123}
{"event":"received","run_id":"...","node":"Platform_30","topic":"ControlCommand","source_node":"Control_20","sequence":42,"received_at_ns":456}
```

The current events include destination and audit identity fields. Phase 4 records also include
team, `command_id`, canonical payload hash, receive timestamp, and correlated command
acknowledgments. The analyzer must never infer message identity from payload values alone.

## Phase 4 Expected Recipients

At run setup, the scenario controller compiles `expectations.json` from the effective route
configuration, the initial topology, and each timestamped control action. Expectations are
versioned and retained with the run manifest; the analyzer reads this snapshot, never mutable
YAML files after the run.

For every sent event, the compiler determines one of:

| Route behavior | Expected recipients |
|---|---|
| Unfiltered route | Every matched destination endpoint in the route's output scope |
| Content-filtered command | Only endpoints satisfying the route filter, such as the addressed `destination` |
| Team/partition route | Only members of the team partition effective at the send timestamp |
| Disabled, unresolved, or unmatched route | None; classify the send as `not_expected`, not lost |
| Request/reply command | The filtered command receiver and its correlated acknowledgment sender |

The compiler must account for route enable/disable, participant partition changes, content-filter
parameters, discovered endpoint matching, and node lifecycle. A route's `samples_forwarded`
counter is diagnostic evidence only; it is not the expectation source or delivery verdict.

## Phase 4 Analysis And Verdict

The analyzer joins sends and receives by:

```text
(run_id, topic, source_node, sequence, receiver_node)
```

It reports sent, expected deliveries, received deliveries, missing deliveries, duplicates,
unexpected deliveries, and late deliveries. Delivery percentage is:

$$
\mathrm{completion} = \frac{\mathrm{received\ expected\ deliveries}}{\mathrm{expected\ deliveries}} \times 100\%.
$$

Every topic receives an independent completion rate and verdict. The aggregate result is a
roll-up only: it must never mask a failed or inconclusive high-priority topic. Future scenarios
may apply different threshold/latency policies by topic priority without changing event identity
or the per-topic denominator. Results are also grouped by source, receiver, route, team, and
scenario action window.
Command reports additionally show command-to-ack completion and latency. Sequence gaps are
listed explicitly so a partial stream cannot be hidden by aggregate percentages.

A verdict is `pass`, `fail`, or `inconclusive`:

- `pass`: all configured thresholds are met and no unexpected delivery violates isolation.
- `fail`: expected delivery, command acknowledgment, ordering, or isolation thresholds fail.
- `inconclusive`: logs, expectation snapshots, or required endpoint/topology observations are
  missing; never silently score this as delivery loss.

## Phase 4 Reports

`<test_id>.json` is the machine-readable source of truth. `<test_id>.html` is a self-contained test
artifact with run metadata, an overall completion summary, per-topic verdict/completion tables,
per-route tables, latency
percentiles, and drill-down lists for missing, duplicate, unexpected, and late sequences.

The HTML report links the expectation snapshot, node event logs, controller action log, router
health/status snapshots, and optional packet captures. This makes each percentage traceable to
the message and route rule that produced it.

## Implementation Order

1. Add `run_id`, `source_node`, `sequence`, and `sent_at_ns` to the audited application types;
   regenerate generated type XML/code.
2. Add JSONL event logging to control and platform simulators.
3. Have the container harness clear and mount one node-owned debug directory per container,
   then retain only the latest summary pair per `test_id`.
4. Implement the route/topology expectation compiler and JSON analyzer.
5. Generate the HTML report and add nominal, filtered-command, team, disabled-route, and
   induced-loss test cases.