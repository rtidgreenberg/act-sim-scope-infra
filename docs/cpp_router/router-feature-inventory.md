# Router Feature Inventory

This document captures the implemented features and runtime functionality of the ACT C++ DDS router as reviewed on 2026-09-14. It is an implementation inventory, not a future design or roadmap. The [router README](../../router/README.md), source code, and end-to-end tests remain authoritative when this document and implementation diverge.

## Executive Summary

The router is a role-aware DDS bridge implemented as the `router_main` executable. It runs as either a `control` or `platform` router, loads a shared YAML configuration, discovers DDS participants and endpoint types, builds per-topic forwarding routes, and exposes runtime control and telemetry over DDS.

Its main capabilities are:

- Forwarding DynamicData samples across configured LAN/WAN participants.
- Learning application types from inline DDS discovery metadata.
- Selecting routes by role, topic, partition, and content-filter expression.
- Enabling and disabling routes at runtime.
- Retargeting route and participant partitions at runtime.
- Publishing route status, command acknowledgements, controller journal records, presence, mesh health, and link metrics.
- Detecting peer liveness and aggregating a bounded router mesh view.
- Preventing same-node loops and discarding stale asynchronous work.
- Validating configuration, QoS aliases, referenced files, and identity before startup.

## Runtime Shape

### Binary and roles

The production target is `router_main`, built from [router/CMakeLists.txt](../../router/CMakeLists.txt). Its runtime composition is assembled in [router_main.cxx](../../router/src/router_main.cxx).

A process starts with a role and configuration, for example:

```text
router_main --config <path> --role control
router_main --config <path> --role platform
```

The process also supports node and router identity overrides through `--node-name` and `--name`, plus participant-selection overrides for administrative and presence traffic. A single system-wide configuration can describe both sides of the system; each process materializes only the participants and route legs relevant to its role.

### Core runtime pipeline

At startup, the router:

1. Parses and validates YAML configuration.
2. Creates disabled DDS participants and endpoint infrastructure.
3. Attaches discovery and runtime wait-set conditions.
4. Starts asynchronous dispatch.
5. Enables participants after dispatch is ready, avoiding a startup discovery race.
6. Learns remote endpoint types from DDS discovery.
7. Builds eligible route entities when discovery and configuration prerequisites are satisfied.
8. Forwards valid samples through the configured route graph.
9. Publishes status, health, journal, and link telemetry.

The controller serializes state-changing work on one controller strand. Entity creation and teardown use generation stamps and asynchronous wait-set barriers so stale completions cannot mutate a newer route generation.

## Configuration

Configuration parsing and role-aware selection are implemented in [RouteConfigParser.hpp](../../router/src/config/RouteConfigParser.hpp) and [RouteConfigParser.cxx](../../router/src/config/RouteConfigParser.cxx). The production-shaped example is [control-platform.yaml](../../router/config/control-platform.yaml).

The configuration model supports:

- Router and node identity.
- Control and platform roles.
- Multiple DDS participants and domains.
- LAN/WAN participant classification.
- UDPv4 transport and optional SPDP2/SEDP discovery selection.
- Participant QoS aliases.
- Publishers, subscribers, partitions, and protected partitions.
- Bidirectional and role-specific route definitions.
- Routes containing one or multiple topics.
- Endpoint QoS aliases and built-in/default QoS behavior.
- SQL content-filter expressions and substituted parameters.
- Presence and mesh-health settings.
- Link-statistics sampling cadence.
- Runtime route enablement.

`${node.name}` substitution is available in filter and partition values, allowing one shared configuration to specialize per-router behavior.

Before launching DDS runtime work, startup validation checks configuration shape and references, including participant and route references, route-role consistency, QoS aliases and profiles, required XML/QoS files, partition bounds, and stale `router.id` identity state. The raw configuration is SHA-256 hashed and exposed through router health so peers and operators can detect configuration drift.

## Routing and Forwarding

The routing path is implemented around [RouteEntityFactory.hpp](../../router/src/core/RouteEntityFactory.hpp), [DynamicRouteFactory.hpp](../../router/src/core/DynamicRouteFactory.hpp), and [RouteRuntime.hpp](../../router/src/core/RouteRuntime.hpp).

For an eligible topic, the router creates the required DDS readers, writers, publishers, subscribers, optional `ContentFilteredTopic`, and wait-set conditions. The runtime uses DynamicData so a single forwarding implementation can handle application types learned from the wire.

Supported behavior includes:

- Forwarding samples between configured source and destination participants.
- Multiple topics in one logical route.
- Independent enable/disable state for routes.
- SQL content filtering on input topics.
- Partition-based endpoint isolation.
- Per-route forwarding counters.
- DDS match and incompatible-QoS reporting.
- Clean teardown and rebuild when a route is disabled, retargeted, or recreated.
- Discovery-time readiness gating so routes are not built before their type and endpoint prerequisites exist.

The forwarding loop sends valid live samples and increments `samples_forwarded` for successful forwarding. Invalid or unsupported metadata samples are skipped.

### Loop and endpoint protection

The router identifies its own publications and ignores same-node router endpoints using DDS `ignore` operations. Newly created output writers are ignored before input conditions are attached, preventing a newly built route from immediately consuming its own output. Ignored endpoints do not teach types to the route builder.

Generation stamps protect against late completions from a previous route generation. Teardown detaches wait-set conditions before DDS entities are closed.

## Discovery and Type Resolution

[DiscoveryDispatcher.hpp](../../router/src/core/DiscoveryDispatcher.hpp) consumes builtin participant, publication, and subscription discovery. Router participants advertise an identity using the participant name form `<node>/<router>` and the `act.router` role sentinel.

[TypeResolver.hpp](../../router/src/core/TypeResolver.hpp) learns application types from inline COMPLETE SEDP TypeObjects. The first learned type for a topic is retained. A publication without an inline type object produces a warning and does not make that topic route-ready.

The normal forwarding path is wire-learned DynamicData. Legacy XML type loading remains available for configuration and compatibility, but it is not the normal mechanism for building forwarded application payloads.

Participant discovery is UDPv4-based. WAN participants can select SPDP2 plus SEDP through [ParticipantRegistry.hpp](../../router/src/core/ParticipantRegistry.hpp).

## QoS

QoS resolution is handled by [QosResolver.hpp](../../router/src/core/QosResolver.hpp). The router supports configured XML QoS-library aliases, a built-in `default` alias, and an empty alias that requests asymmetric auto-QoS.

The current auto-QoS policy is:

| Endpoint | Reliability | Durability | History |
|---|---|---|---|
| Input reader | BEST_EFFORT | VOLATILE | KEEP_LAST(16) |
| Output writer | RELIABLE | TRANSIENT_LOCAL | KEEP_LAST(16) |

Writer deadline, liveliness, and lease settings are derived from matched local readers when auto-QoS is used. The `default` alias supplies RELIABLE, TRANSIENT_LOCAL, and KEEP_LAST(16).

QoS aliases are preflight-validated at startup and resolved QoS summaries are exposed through status. Residual incompatible QoS is reported rather than dynamically adapting arbitrary incompatibilities.

**Known limitation:** an auto-QoS WAN writer can wait for a reader while the remote reader waits for that writer, producing a readiness deadlock. WAN-crossing production routes therefore use explicit QoS aliases.

## Runtime Administration and Status

The administration path is implemented by [CommandReader.hpp](../../router/src/core/CommandReader.hpp), [RouterController.hpp](../../router/src/core/RouterController.hpp), and [DdsStatusPublisher.hpp](../../router/src/core/DdsStatusPublisher.hpp).

The router accepts `ActRouterCommand` messages and uses target-node and target-router content filtering so commands are processed only by their intended router. Command IDs are idempotent and acknowledgements are cached.

Implemented runtime controls include:

- Enable a route.
- Disable a route.
- Change a route's partition membership.
- Add a participant partition.
- Remove a participant partition.

The router publishes `ActRouterStatus` and command acknowledgements. Status includes, as applicable:

- Router and node identity.
- Route and topic state.
- Discovery and readiness state.
- State revision.
- DDS reader/writer match counts.
- Incompatible QoS information.
- Resolved QoS summaries.
- Participant state.
- Forwarded-sample counters.
- Configuration hash and health information.

State-changing events increment `state_revision`. Periodic telemetry refreshes counters without falsely presenting telemetry refresh as a configuration/state transition.

## Presence, Health, and Mesh Awareness

[PresenceMonitor.hpp](../../router/src/core/PresenceMonitor.hpp) publishes router health over the WAN and aggregates peer observations into `ActRouterMeshStatus` on the LAN.

Peer state handling includes:

- `ALIVE` for an actively observed peer.
- `STALE` after deadline misses.
- `DEAD` after liveliness loss.
- Eventual pruning of dead peers.

Mesh identity is router-name based, using the discovered `<node>/<router>` participant identity. Mesh records include bounded peer lists and transitive `peers_seen` data. The live team-scoped participant partition is copied into the reported `team_partition` field.

Mesh status is published periodically with BEST_EFFORT/VOLATILE semantics. Mesh publication cadence is separate from the heartbeat cadence. This gives operators a topology/health view without making mesh status a durable data path.

[LinkStatsCollector.hpp](../../router/src/core/LinkStatsCollector.hpp) reports WAN protocol counters and application probe acknowledgement RTT. Link statistics are telemetry; they do not independently infer peer health.

## Observability and Audit

[Log.hpp](../../router/src/core/Log.hpp) provides thread-safe structured logfmt output on stderr with `source=router`.

[ControllerJournalPublisher.hpp](../../router/src/core/ControllerJournalPublisher.hpp) publishes one journal record for each processed non-telemetry controller event. Journal records include:

- Event sequence.
- Command context.
- Decision.
- Reason.
- Pre-event revision.
- Post-event revision.

A `journal_falling_behind` diagnostic reports journal backlog pressure. Router status exposes state transitions and forwarding counters, while link metrics are published as `ActRouterLinkStats`.

## Extensibility Boundary

The router does not provide a dynamic plugin loader or external extension ABI. Its extensibility is internal and compile-time oriented:

- [Interfaces.hpp](../../router/src/core/Interfaces.hpp) defines seams for entity factories, status, presence, journals, and link statistics.
- [RouteEntityFactory.hpp](../../router/src/core/RouteEntityFactory.hpp) is templated over payload type.
- Generated-type support exists as an internal template lane.
- The shipped runtime selects DynamicData through `DynamicRouteFactory`.

The repository's `plugins` terminology in the broader Zenoh documentation is not applicable to this router implementation; router extensions here are source-level components and test fakes, not loadable runtime plugins.

## Test-Backed Feature Coverage

The router has two complementary test layers:

- Four registered C++ unit tests cover configuration identity, route parsing, controller state-machine behavior, and generated admin types. They run through [run_tests.sh](../../router/run_tests.sh).
- The Python end-to-end suite launches real `router_main` processes and drives DDS traffic through them. See [router/test_e2e/README.md](../../router/test_e2e/README.md).

The end-to-end suite covers:

- Control-to-platform and platform-to-control forwarding.
- Content-filtered routing.
- Wire-learned multi-type routes.
- Startup discovery timing.
- Same-node endpoint ignore.
- Named QoS aliases and auto-QoS.
- Participant and route partitions.
- Runtime route and participant partition changes.
- Admin commands, acknowledgements, and idempotency.
- Controller journal output.
- Presence, stale/dead health transitions, and mesh roster behavior.
- Configuration-hash drift detection.
- Link metrics and application acknowledgement RTT.
- Team partition membership.
- The full production-shaped `control-platform.yaml`.
- Dashboard-equivalent route and platform control flows.

## Current Limitations

The implementation and test documentation identify these boundaries:

- Serialized-CDR forwarding fast path is not implemented.
- Keyed lifecycle, dispose, and unregister mirroring is not implemented.
- Forwarding skips invalid or unsupported metadata samples.
- Arbitrary incompatible endpoint QoS is reported but not automatically reconciled.
- Auto-QoS has the WAN readiness deadlock described above.
- Router identity currently relies on the reserved `act.router` role marker; an application that impersonates that marker is a residual identity risk.
- Test isolation primarily uses unique DDS domain IDs; stronger participant-level isolation for concurrent pytest processes remains deferred.

## Validation Commands

From the repository root, after setting the Connext environment:

```bash
export NDDSHOME=/home/rti/rti_connext_dds-7.7.0
cmake -B router/build -DCONNEXTDDS_ARCH=x64Linux4gcc7.3.0
cmake --build router/build
router/run_tests.sh
pytest router/test_e2e -v -p no:cacheprovider
```

Use the repository mesh lifecycle harness for live mesh or dashboard validation. Do not manually restart individual routers in a running mesh.
