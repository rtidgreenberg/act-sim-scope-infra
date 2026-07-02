# Thesis & Claim Traceability

> Part of the **ACT EMANE Simulation & Thesis-Validation** plan — start at the [overview & index](EMANE_SIMULATION_PLAN.md). Section numbers (§X.Y) are preserved from the original monolithic plan; the overview maps every section to its doc.

---

## 1. The thesis we are validating

> RTI Connext DDS + a per-node Routing Service gateway can serve as the resilient
> "connective tissue" for **Autonomous Collaborative Teaming** over **DDIL**
> (Denied, Degraded, Intermittent, Limited) networks — enabling 1:N control,
> domain isolation, QoS-based bandwidth prioritization, dynamic runtime team CRUD,
> and peer-to-peer mesh coordination.

Source of truth: [SYSTEM_ARCH.md](../SYSTEM_ARCH.md). Testable claims and their
current status are in [§7 Thesis-claim traceability](#7-thesis-claim-traceability).


## 7. Thesis-claim traceability

| Claim (req) | Demonstrable now? | Notes |
|---|---|---|
| Domain isolation (SYS-REQ-13) | ✅ with interface pinning | LAN never crosses `emane0` |
| Content-filtered targeted cmds (DTR-05) | ✅ | Already in routing config |
| Dynamic team CRUD (SYS-REQ-04) | ✅ | `remote_admin` partition switch |
| Graceful degradation (SYS-REQ-02) | ◑ partial | best-effort vs reliable split; no true priority yet |
| Bandwidth prioritization (SYS-REQ-11) | ✗ needs QoS | TransportPriority/flow control |
| Peer-loss detection (SYS-REQ-10) | ✗ needs QoS | finite liveliness/deadline |
| Discovery-storm avoidance (SYS-REQ-01/06) | ◑ | multicast now; add CDS/unicast |
| Mesh resilience / relay (US-C6, SYS-REQ-16) | ◑ / ✗ | S4 shows peer survival; gossip relay is net-new |

