# Spike: Modern C++ verification of participant_partition-gated discovery

See `PLAN.md` for the question, claims, and the QoS API correction this spike required.

## Result — PASS (2026-07-21, Connext 7.7.0, x64Linux4gcc7.3.0, Modern C++11), stable 3/3

- **A (baseline):** two participants sharing `participant_partition = "SHARED"` mutually
  discovered each other via `dds::domain::discovered_participants()` within ~1s (0ms / 725-957ms
  across runs -- one side always sees the other's SPDP announcement first).
- **B (the money claim):** two participants with disjoint concrete `participant_partition`
  values (`"TEAM_A"` / `"TEAM_B"`) held `discovered_participants()` empty on **both** sides
  across a continuously-polled 4s window, all 3 runs. Confirms the Python-binding finding
  (`spikes/partition_retarget/`, `spikes/spdp2_partition_visibility/`) also holds through the
  Modern C++ API: `get_discovered_participants()`/`dds::domain::discovered_participants()` does
  **not** return the full participant map when `participant_partition` doesn't match -- the
  non-matching participant is absent entirely, not merely present-but-unmatched.

Plain SPDP (no SPDP2), UDPv4-only transport, single process, one domain (5750) -- no stray
`/dev/shm` segments after the runs (checked, per repo transport-hygiene guardrail).

## API correction found along the way

While writing this spike, `mcp__connext__validate_modern_cpp_code` "validated" a snippet that set
`participant_partition` via `rti::core::policy::Discovery::participant_partition(...)`. That
method does not exist -- confirmed by grepping the real header
(`rti/core/policy/CorePolicy.hpp:2608`, `Discovery` class member list: `enabled_transports`,
`initial_peers`, `multicast_receive_addresses`, `metatraffic_transport_priority`,
`accept_unknown_peers`, `enable_endpoint_discovery` -- no `participant_partition`) and by an
actual build failure. The real mechanism is `dds::core::policy::Partition`, set via
`qos << dds::core::policy::Partition("TEAM_A")` (`DomainParticipantQos` is
`dds::core::TEntityQos<DomainParticipantQosImpl>` -- `TEntityQos` wraps the impl's fields behind
`policy<Policy>()`/`operator<<`, so even the impl's own direct-member declaration
(`rti/domain/qos/DomainParticipantQosImpl.hpp:141`) isn't reachable as `qos.partition` from
outside). Logged in `docs/connext-ai-issues/connext-ai-issues.md`.

## Design implications

- The mutual-invisibility property (D73/D87 Finding 2) is not a Python-binding artifact -- it's
  the same behavior at the Modern C++ API, as expected (both bindings sit on the same underlying
  discovery implementation). No new risk for a C++ router relying on `participant_partition` as
  the team-isolation mechanism.
- `dds::domain::discovered_participants(participant)` is the correct C++ call shape (a free
  function in `dds::domain`, matching the header at
  `dds/domain/discovery.hpp:100` -- already confirmed in the prior conversation round via header
  read, not just the MCP).
