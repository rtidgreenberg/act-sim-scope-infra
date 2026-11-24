---
marp: true
theme: rti
paginate: true
size: 16:9
title: "Telemetry: Typed or Catalog-Driven?"
description: "Two payload options within the Event / State / Telemetry delivery model"
footer: "RTI Connext | Proposed telemetry options | 2026-11-13"
---

<!-- _class: compact -->

# Telemetry: typed or catalog-driven?

**Same goal: best-effort, freshness-first delivery. Different ways to structure the data.**

| | Typed telemetry | Catalog-driven telemetry |
|---|---|---|
| **Data model** | Fixed data types defined up front | `State` catalog lists items; `Telemetry` sends ID + value pairs |
| **Example** | `Power`: battery ID, voltage, temp, time | `Value`: object ID, metric ID, value, time |
| **How readers filter** | Filter by source, type, and declared fields | Read catalog, pick IDs, filter `Telemetry` to those IDs |
| **Strength / cost** | Grouped fields; new fields need schema update | Runtime selection; must keep catalog & versions in sync |

**Catalog flow:** 1. Read `State` catalog → 2. App picks IDs → 3. Filter `Telemetry` by IDs.

- **Catalog:** current objects, keys, types, and units (consider `transient local`).
- **Safety:** include source + version in every ID; handle deleted items or early values.

> **Application-managed filters, not automatic cross-topic joins. Neither option selected.**

<!--
Proposed alternatives captured on 2026-11-13, not implemented or benchmarked.
Companion: event-state-telemetry.md.

Typed telemetry retains declared payload structures, potentially as branches
of a shared Telemetry union. The Power example is conceptual, not a quotation
of the production IDL. Grouped fields can express a coherent observation in
one sample. Typed delivery does not require sending every branch every tick,
nor does it prohibit an optional inventory/catalog for user selection.

Catalog-driven telemetry uses a declared generic value schema with an explicit
value type/tag, not an untyped opaque byte bag. Catalog State records describe
which objects exist and their available measurements, identifiers, value types,
units and versions. Definition records and current object inventory are distinct
responsibilities even if both use State variants.

For example, State defines Platform_30 / battery_1 / voltage as a floating-point
measurement in volts, generation 7. A Telemetry value carries that source,
object ID, measurement ID, generation, timestamp and numeric value.
The application resolves the catalog and translates selections into filters
on identifiers actually carried in Telemetry. This is not an automatic DDS
cross-topic join. Exact filter expressions, bounded interest capacity and
writer-side bandwidth savings require separate implementation qualification.

Define handling for values arriving before catalog records, catalog changes,
ID reuse, deletion and writer restart. Source-qualified stable identities and
version/generation checks prevent accidental reinterpretation. Define whether
unknown values are buffered within a bound or explicitly diagnosed/dropped.
Disposing an inventory record does not automatically remove old measurements,
change other readers' filters or imply every associated definition is deleted.
Unregister is not universally equivalent to application-object deletion.

Transient local remains a State bootstrap candidate, not restart persistence.
Independently selected values need explicit grouping/version/time rules if
consumers require coherent multi-field snapshots. Do not promise that generic
catalog telemetry is inherently smaller or faster; compare concrete workloads.

Required occurrences remain Event; authoritative current objects remain State.
The alternatives describe Telemetry payloads, not extra WAN topic names.
No application, schema, QoS or running mesh is changed by this slide.
-->
