# Spike: Modern C++ verification of participant_partition-gated discovery

## Question

The Python spikes (`spikes/partition_retarget/`, `spikes/spdp2_partition_visibility/`) already
proved, empirically, that two `DomainParticipant`s with disjoint concrete `participant_partition`
(DomainParticipantQos-level `PARTITION` QoS) values never appear in each other's
`discovered_participants()` — full mutual invisibility at the SPDP level, not just SEDP/endpoint-
match suppression. That's the `rti.connextdds` Python binding. This spike re-runs the same
structural claim (Parts A/B) through the **Modern C++ (C++11) API** on this install (7.7.0,
x64Linux4gcc7.3.0), because a prior round in this conversation caught `validate_modern_cpp_code`
fabricating a QoS call (`rti::core::policy::Discovery::participant_partition(...)`) that does not
exist in the real header (see `docs/connext-ai-issues/connext-ai-issues.md` for the logged entry)
— the MCP is not trusted for load-bearing C++ API claims per repo policy, so this needs a real
build + run, not just another MCP answer.

## Claims under test

- **A (baseline / positive control):** two participants sharing one concrete
  `participant_partition` ("SHARED") each see the other in
  `dds::domain::discovered_participants()` within a short timeout.
- **B (the money claim):** two participants with disjoint concrete `participant_partition`
  values ("TEAM_A" / "TEAM_B") NEVER see each other — `discovered_participants()` held empty on
  BOTH sides across a continuously-polled hold window, not just checked once.

Plain SPDP (no SPDP2), UDPv4-only transport (per repo guardrail: SIGKILL-safe, no `/dev/shm`
leakage risk), single process, two `DomainParticipant`s on one domain — same shape as
`partition_retarget_spike.py` Parts A/B, minus the topics/readers/writers (this spike only
concerns participant-level discovery, not endpoint matching, so no data type or topic is needed).

## Correct QoS API (header-verified, NOT the MCP's fabricated form)

`PARTITION` is `dds::core::policy::Partition`, listed as a field of the underlying
`DomainParticipantQosImpl` (`rti/domain/qos/DomainParticipantQosImpl.hpp:141`) -- not a method on
`rti::core::policy::Discovery` (whose real member list -- `rti/core/policy/CorePolicy.hpp:2608` --
is `enabled_transports`, `initial_peers`, `multicast_receive_addresses`,
`metatraffic_transport_priority`, `accept_unknown_peers`, `enable_endpoint_discovery`; no
`participant_partition`). But `dds::domain::qos::DomainParticipantQos` is actually
`dds::core::TEntityQos<DomainParticipantQosImpl>` (`dds/core/TEntityQos.hpp:40`), which wraps the
impl rather than inheriting its public members directly -- so the impl's `partition` field isn't
reachable as `qos.partition`. Access goes through the `policy<Policy>()` getter/setter or the
`operator<<` idiom (confirmed by an actual build failure when direct-member access was tried
first):

```cpp
dds::domain::qos::DomainParticipantQos qos;
qos << dds::core::policy::Partition("TEAM_A");   // operator<< idiom, single-partition ctor
```

## Pass / fail

PASS iff A and B both hold. Exit nonzero on any structural failure.

## Method

`spdp_partition_discovery_spike.cxx`, built via `CMakeLists.txt` (same `RTIConnextDDS::cpp2_api`
pattern as `relay/cpp/CMakeLists.txt`, no codegen needed — no topics/types involved). Poll
`dds::domain::discovered_participants(participant).size()` on a plain loop with
`std::this_thread::sleep_for`.
