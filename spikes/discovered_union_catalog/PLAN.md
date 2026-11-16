# Discovered unions and State catalog selection

## Question

Can a gateway learn whole command structures through LAN discovery, construct
a WAN union at startup, advertise its bindings on State, and let a schema-blind
platform discover and select the desired commands?

## Method and acceptance

- Three separate processes: application fixture/verifier, gateway, platform.
- Only the application loads original command types. No generated classes,
  application schema XML or predefined command union in gateway/platform.
- Gateway learns two complete structures from real publication discovery, then
  constructs the union and envelope before creating the WAN Event writer.
- Publish one reliable/transient-local State catalog. Start the platform
  process only after that publication, proving late catalog bootstrap.
- Platform resolves the envelope and nested types through DDS discovery.
  Catalog selection determines the discriminator filter; destination selection
  excludes another platform. Recreate only the reader to change selections.
- Send both command families to both destinations in each of three phases:
  select Move, select Mode, select none. Expect respectively 3, 3, 0 receives
  and exactly the same native writer DATA push deltas, out of 12 inputs each.
- Restore complete payloads to their original LAN topics; the independent
  fixture compares complete DynamicData equality and rejects duplicates.
- Exercise nested structures, empty/partial/full bounded sequences, an array,
  a maximum-length string, booleans, signed values, keys and timestamps.
- Reject an unknown catalog selection and a catalog/discovered-type mismatch.
- Run on UDPv4 in an owned container with private network and IPC namespaces,
  domains 91-93, no host network, with temporary state/builds under `/tmp`.

## Non-goals

No live integration, new branch insertion, union evolution, coordinated
multi-sender manifests, command execution/authorization, application ACKs,
disconnect recovery, lifecycle forwarding, performance or fleet-scale claims.
The common bootstrap catalog schema is supplied in advance. State advertises
bindings; actual structural schemas come from DDS type discovery.

## Discovery-only extension (2026-11-16)

Preserve the catalog-mode test and add `--discovery-only`:

- No State topic/writer/reader and no per-sample `original_topic` field.
- Source topic names become union member names. Use sparse integer labels,
  inspected through the discovered union, not inferred from branch order.
- Match alive local subscriptions by topic name and exact discovered structure.
  Do not select solely by registered type name or by local publications.
- Create local application readers for Move, Mode, neither, a same-named wrong
  Mode structure, and both. Include an unmapped same-type reader in every phase.
- Change destination participants between phases so incompatible definitions
  sharing a registered name do not collide in one participant's type registry.
- Keep harness phase/read-count synchronization, but transmit no topic selection,
  branch map or type definitions through that control path.
- Require 3/3/0/0/6 native Event push deltas and exact restored command counts
  out of 12 inputs per phase, full payload equality and explicit type rejection.
- Assert no State publication/subscription in WAN discovery.
- Rebuild/run both modes in isolated namespaces. Continuous discovery updates,
  schema evolution and wider restart/load/lifecycle qualification remain out
  of scope.
