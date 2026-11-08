---
marp: true
theme: rti
paginate: true
size: 16:9
title: Finding Peers in a Dynamic Squad
description: Multicast, static unicast, C2 directories, teammate repair, and Connext Cloud Discovery Service
footer: "RTI Connext | Discovery strategies | 2026-11-07"
---

<!-- _class: lead -->

# Finding peers in<br>a dynamic squad

## Multicast, unicast, C2-seeded, or CDS?

**Choose how peers find each other.<br>Control topic audiences separately.**

CDS = Connext Cloud Discovery Service.

---

<!-- _class: compact -->

# Four choices, four operational trade-offs

| Strategy | Best fit | Main benefit | Main cost |
|---|---|---|---|
| Multicast | Multicast-capable LAN | No platform address roster | Network must deliver multicast |
| Static unicast | Stable, known deployment | Explicit contacts; no required central service | Repeated address/configuration maintenance |
| C2-seeded | Mission-controlled, changing squad | Central roster and application policy | Custom directory/update/recovery logic |
| Connext CDS | Dynamic peers; multicast unavailable | RTI-managed discovery rendezvous | Operate a reachable discovery service |

> **Takeaway:** these establish discovery, not a user-data relay.
> Endpoint matching and data delivery still happen between peers.

---

# Multicast: no roster, but the network must work

```text
A announces to a shared group
B and C listen -> normal DDS discovery
```

**Gain**
- Dynamic arrivals without maintaining every platform address.
- Simple on a multicast-capable local network.

**Give up**
- Depends on multicast delivery across the actual network.
- Broad announcements may be undesirable over constrained radio links.

> **Takeaway:** verify multicast reachability first.
> Discovery multicast does not require user-data multicast.

---

# Unicast: explicit contacts, ongoing maintenance

```text
A contacts B's configured discovery address
B accepts A -> normal DDS discovery
```

**Gain:** no multicast requirement and no mandatory central service.

**Give up:** address changes and arrivals require roster/configuration updates.
Copying every address into every platform multiplies maintenance.

A discovery address is configured as a DDS **peer descriptor**.
One-sided contact can work when the receiver accepts unknown peers.

> **Takeaway:** all-to-all configuration is a choice, not a DDS rule.

---

# C2: centralize the roster, not the data path

```text
Platform -> C2: identity + discovery address
C2 -> platform: versioned directory
Platform -> peers: add new contacts, then discover directly
```

- Each platform starts with C2's address, not the whole squad roster.
- C2 distributes the peers needed for the mission and global services.
- Register explicit discovery addresses; user-data locator ports are not a substitute.
- Application policy chooses contacts; DDS `add_peer` applies them.

> **Takeaway:** C2 reduces configuration duplication.
> A full-squad directory does not automatically reduce discovery bandwidth.

---

# A teammate can repair a missed C2 update

```text
P30 has C2 revision 12 and knows P31's address
P30 contacts P31 -> compares revisions -> shares C2-issued records
P31 validates the records and adds missing contacts
```

- Allow incoming discovery: `accept_unknown_peers=true`.
- Participant scopes and directory endpoints must still match.
- Cache/version records, avoid duplicate adds, and bound retries.
- A helper still needs a reachable address for the stranded platform.

> **Takeaway:** this is application directory repair, not built-in DDS gossip.
> Accepting unknown peers makes the directory a contact list, not an allowlist.

---

<!-- _class: compact -->

# CDS: RTI-managed discovery rendezvous

```text
A / B -> CDS: participant announcements
CDS -> matching peers: forwarded announcements
A <-> B: direct endpoint discovery and user data
```

**Gain:** dynamic discovery without custom platform-directory distribution.
CDS can filter announcements by domain, tag, and participant partition.

**Give up:** service operations and a reachable bootstrap path.
Direct peer transport must still work; CDS is not Routing Service or a data broker.

7.7.0 SPDP2 mode forwards bootstrap only; configuration/liveliness stay peer-to-peer.
Configure compatible protocol modes and allow incoming peers.

> **Takeaway:** CDS supplies rendezvous; C2 can still own mission/team policy.

---

<!-- _class: compact -->

# Losing the seed is not the same as losing data

| Strategy | New peers or changed addresses | Existing peer communication |
|---|---|---|
| Multicast | Need multicast delivery or another seed | Depends on viable paths and normal DDS lifecycle |
| Static unicast | Need a reachable configured contact | Direct |
| C2-seeded | Need C2, cached records, or an informed teammate | Need not depend on C2 remaining available |
| CDS | Need CDS or an alternative bootstrap path | Documented to continue after discovery on viable direct paths |

> **Takeaway:** central outage primarily threatens fresh discovery.
> Restart, address change, lease expiry, or transport failure can still require rediscovery.

---

# Choose for operations. Measure traffic separately.

- **Multicast:** use where the network supports it.
- **Static unicast:** keep as a predictable baseline.
- **C2 directory + teammate repair:** reuse mission control and reduce roster maintenance.
- **CDS:** use RTI's rendezvous when avoiding custom bootstrap logic is the priority.

CDS and C2 policy can coexist; they solve different responsibilities.

Compare identical health, navigation, and team-data audiences under loss and restart.

> **Takeaway:** configuration simplicity is not a bandwidth result or authorization.

---

<!-- _class: sources -->

# Evidence and remaining gates

**Measured:** [C2 add-once bootstrap](../cpp_router/c2-peer-bootstrap-report.md), 6/6 isolated SPDP/SPDP2 runs.

**API-only:** [Routing Service processor access](../../spikes/routing_service_peer_api/README.md), compiled/linked but not loaded.

**Documented CDS behavior:** [introduction](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/addon_products/cloud_discovery_service/introduction.html),
[partition forwarding](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/addon_products/cloud_discovery_service/core_concepts.html),
[protocol modes](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/addon_products/cloud_discovery_service/configuration.html),
[service-outage tutorial](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/addon_products/cloud_discovery_service/tutorials.html).

**Still to test:** teammate repair, CDS deployment, loaded processor integration,
and comparable lossy-WAN traffic/convergence.

[7.7.0 peer API documentation](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/DISCOVERY_Qos.htm)
limits `remove_peer` to SPDP. Do not assume SPDP2 peer removal or authorization semantics.
