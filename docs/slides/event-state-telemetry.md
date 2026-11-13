---
marp: true
theme: rti
paginate: true
size: 16:9
title: "Event / State / Telemetry"
description: "Three WAN delivery contracts, defined by meaning rather than payload names"
footer: "RTI Connext | WAN delivery semantics | 2026-11-13"
---

<!-- _class: compact -->

# Event / State / Telemetry

**Three delivery contracts, not just three topic names.**

| Class | Meaning | Delivery intent | Examples |
|---|---|---|---|
| **Event** | A required occurrence | Reliable; preserve required messages | Commands, acknowledgments, configuration changes |
| **State** | Current state of a keyed instance | Reliable; latest value per instance plus dispose / unregister lifecycle | Contacts; other authoritative objects as needed |
| **Telemetry** | A fresh observation | Best effort; prefer new measurements over stale replay | Position, power, thrusters, diagnostics, health |

- **State bootstrap:** consider transient local so late joiners get retained state.
- **Lifecycle:** verify deletion recovery, late join and restart; transient local is not a restart-persistent store.
- **Classification:** a contact transition that must be recorded is also an Event.

> **State reconstructs the current world. Telemetry observes it.<br>Events record required occurrences.**

<!--
Captured agreement, 2026-11-13. The main router's three_topic_union contract
uses Event / State / Telemetry as its physical WAN topic names. State initially
carries ContactReport; Telemetry carries the existing periodic/health branches.
The rename preserves existing payloads and delivery policies. Legacy and
two-topic modes retain their existing topic names.

State represents the current value of each keyed instance, not a history of
every intermediate update. Dispose and unregister carry instance lifecycle;
unregister is not automatically equivalent to application-object deletion,
especially with multiple producers. Qualify ownership and lifecycle together.
Contacts are the initial State payload. Additional branches require deliberate
classification rather than moving every message with "status" in its name.

Transient-local durability remains a candidate for State bootstrap, not an
approved complete QoS profile. Define retention/resource budgets and qualify
stationary contacts, deletion during disconnection, late readers, multiple
producers and writer restarts. Do not equate retained writer state with a
persistent database or promise restart recovery from this setting alone.

Telemetry is best effort and freshness-first by definition here. A measurement
whose loss is unacceptable must instead be classified as Event or State.
Health retains an explicit deadline/presence policy; best effort does not mean
that health timing, freshness or degraded-connectivity handling is optional.

Preserve required Event occurrences; this slide does not settle durability,
application execution guarantees or admission/resource budgets for Events.
The implementation rename does not change application LAN contracts, XML QoS
or schema version 3. Transient local and broader State classification remain
separate decisions, not consequences of the new names.

Context: ../wan-topic-tradeoffs-2026-11-12.md
Implementation history: ../two-topic-wan-migration-plan.md
-->
