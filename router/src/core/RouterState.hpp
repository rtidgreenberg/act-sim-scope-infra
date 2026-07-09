// RouterState.hpp — controller-owned mutable state + the pure derivation functions.
//
// One source of truth per field (D1/D11/D20/D22):
//   matched-endpoint sets (controller state, fed by raw endpoint-record events)
//     -> discovery facts (derived: seen <=> set non-empty)
//     -> per-topic discovery_state rollup (pure function, no memory)
//   per-topic topic_state (entity lifecycle, event-bounded)
//     -> route operational_state (pure derivation, D11 table)
// Lifecycle memory ("was forwarding") exists only in the derived DEGRADED value; sticky
// errors are explicit flags cleared only by command re-arm (D2/D11).

#pragma once

#include "RouteView.hpp"
#include "RouterAdminTypes.hpp"

#include <cstdint>
#include <deque>
#include <map>
#include <memory>
#include <string>

namespace router {

// Minimal per-endpooint info kept inside a matched set entry (upserted per GUID, D12/D22).
struct MatchedEndpoint {
    std::string type_name;
    bool has_type = false;
};

struct TopicRouteState {
    // Matched-endpoint sets, maintained by the controller from raw records (D20/D22).
    std::map<std::string, MatchedEndpoint> matched_writers; // guid -> info
    std::map<std::string, MatchedEndpoint> matched_readers; // auto-QoS output readers

    RouterRouteTopicState topic_state = RouterRouteTopicState::TOPIC_IDLE;
    std::uint64_t entity_generation = 0; // stamp at last entity build; 0 = none (D23)
    std::string resolved_type_name;      // first-resolved-wins (D20)
    std::string last_error;
    std::uint64_t samples_forwarded = 0;
    std::uint64_t lifecycle_events_forwarded = 0;
};

struct RouteState {
    RouterRouteSpec desired;                 // includes desired_enabled
    std::shared_ptr<const RouteView> view;   // minted with a D23 stamp
    std::map<std::string, TopicRouteState> topics; // key: topic name, from desired.topics
    bool route_error = false;                // sticky route-wide ERROR flag (D2)
    std::string last_error;                  // route-wide errors only (per-topic on topic)
    std::uint64_t state_revision = 0;        // stamp of last visible change (D5)
    std::string caused_by_command_id;        // empty unless command-caused (D8)
};

struct ParticipantState {
    std::string name;
    std::int32_t domain = 0;
    std::string participant_partition;
    std::string qos_profile_alias;
};

struct MutableRouterState {
    std::string node_name;
    std::string router_name;
    std::uint32_t router_id = 0;
    std::string status_id; // process identity; restart detection is not revision's job (D5)

    std::uint64_t state_revision = 0;           // one global counter (D5)
    std::uint64_t entity_generation_counter = 0; // one global counter (D23)

    std::map<std::string, ParticipantState> participants; // read-only in Phase 1 (D7)
    std::map<std::string, RouteState> routes;

    // Bounded command history (D4/D26): state-changing kinds only, FIFO 256.
    std::map<std::string, RouterCommandAck> ack_by_command_id;
    std::deque<std::string> ack_fifo;
};

// --- Pure derivations ---

// True if this topic spec names no explicit QoS: QoS must then be resolved from a
// discovered compatible output reader (D1 "auto-QoS output reader"; real resolution is
// Phase 5 — Phase 1 models the readiness fact only).
bool topic_uses_auto_qos(const RouterRouteTopicSpec &spec);

// Per-topic discovery rollup — pure function of the current matched sets, no memory (D1).
RouterRouteDiscoveryState derive_topic_discovery(const TopicRouteState &topic,
                                                 const RouterRouteTopicSpec &spec);

// Route-level discovery = best (max) topic rollup (D11).
RouterRouteDiscoveryState derive_route_discovery(const RouteState &route);

// Route operational state — pure derivation over topic states (D11 table).
RouterRouteOperationalState derive_operational(const RouteState &route);

// Externally-visible fingerprint of one route: the D5 increment predicate compares this
// before/after each event (counters deliberately excluded — they never bump revision).
std::string route_fingerprint(const RouteState &route);

} // namespace router
