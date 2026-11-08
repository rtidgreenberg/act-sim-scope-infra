---
marp: true
theme: rti
paginate: true
size: 16:9
title: DDS Isolation Choices
description: Discovery visibility, data audiences, security, control layers, and participant costs
footer: "RTI Connext | Isolation choices | 2026-11-07"
---

<!-- _class: lead -->

# Choose what must<br>be isolated.

## Discovery visibility, data delivery, or security?

**Different goals need different controls.**

DP = DomainParticipant, the DDS entity that owns a discovery scope.

---

<!-- _class: compact -->

# Seeing a topic is not receiving its data

| Mechanism | Discovery effect | User-data effect | Main limit |
|---|---|---|---|
| Publisher/Subscriber partitions | Endpoint metadata can remain visible | Only matching audiences exchange data | Not metadata isolation |
| DP partitions | Unmatched scopes do not exchange endpoint metadata | No matching across those scopes | Applies to every endpoint on that DP |
| C2 directory, incoming peers accepted | Supplies discovery contacts | Topic matching and routes still apply | Contact list, not an exclusive allowlist |
| DDS Security | Authentication/protection per policy | Authorized reads/writes and configured protection | Identity, keys, and permission lifecycle |

> **Takeaway:** discovery control, data-audience control, and authorization are separate.

---

<!-- _class: compact -->

# Application policy can drive DDS controls

| Policy choice | Who decides? | Who applies it? |
|---|---|---|
| Which topic routes exist or forward | Application / operator | Router and endpoint lifecycle |
| Which peers to contact | C2 / application | DDS discovery APIs: `add_peer`, `remove_peer` |
| Which participant scopes match | Application / XML / admin | DDS participant matching |
| Which endpoints exchange data | Application / XML / admin | DDS Publisher/Subscriber matching |
| Which samples pass a filter | Application / operator | DDS reader filter or application forwarding |
| Which network paths are reachable | Network controller | Transport/firewall rules |
| Which identities may read/write | Security authority | DDS security plugins |

> **Takeaway:** an API called by an application can still enforce a DDS-level rule.

---

<!-- _class: compact -->

# One WAN participant or two?

| Required outcome | Arrangement | Trade-off |
|---|---|---|
| Global health/navigation; team-only payloads | One WAN DP; endpoint partitions | Simple lifecycle, broader metadata visibility |
| Global services; team-only endpoint metadata | Global WAN DP + team WAN DP | Separate discovery scopes, extra DP overhead |
| One restricted discovery audience for all traffic | One DP with that membership scope | Every endpoint shares that discovery boundary |

Counts exclude each platform's LAN participant.

> **Takeaway:** DP partitioning does not inherently require another DP.
> Two DPs help when global and team-only discovery must coexist.

A common `SHARED` participant partition makes platforms eligible to discover one another.

---

<!-- _class: compact -->

# Other controls solve different problems

| Control | Benefit | Cost or limitation |
|---|---|---|
| Separate domains / tags | Separate DDS matching scopes | Concurrent scopes need participants and deliberate bridges |
| Content / destination filters | Select samples within a topic | Do not hide peer or endpoint metadata |
| Paused route | Stops that path's forwarding | Endpoints may still be discoverable |
| On-demand endpoints | Remove dormant route metadata | Startup/type/match delay on activation |
| Network segmentation | Blocks unwanted network paths | Must account for discovery, payload, and repair traffic |

> **Takeaway:** fewer routes or packets do not automatically mean fewer discovered peers.

---

# Squad takeaway and evidence

- Keep one WAN DP when global visibility and team-only data meet the requirement.
- Use C2 to manage contacts and team policy, not as an implied security boundary.
- Add a second scope only when team metadata must be hidden from global peers.
- Measure bandwidth and convergence; do not assume an extra DP is cheaper.

**Tested:** isolated C2 add-once bootstrap and documented endpoint team isolation.
**Not yet tested:** teammate repair and a loaded Routing Service peer-management processor.

[Full comparison and evidence](../cpp_router/dds-isolation-strategies.md)

In 7.7.0, `remove_peer` is documented for SPDP only; peer removal is not an authorization design.
