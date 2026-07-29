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
#include "ActTypes.hpp"
#include "RouterEvents.hpp" // EndpointRecord QoS PODs + DerivedWriterQos (D45)

#include <algorithm>
#include <cstdint>
#include <deque>
#include <map>
#include <memory>
#include <string>

namespace router {

// Minimal per-endpooint info kept inside a matched set entry (upserted per GUID, D12/D22).
// Reader entries additionally carry the requested QoS subset the output writer derives
// from at creation (D39/D42); defaults are derivation-neutral.
struct MatchedEndpoint {
    std::string type_name;
    bool has_type = false;
    std::int64_t deadline_nanos = kInfiniteNanos;
    LivelinessKindPod liveliness_kind = LivelinessKindPod::Automatic;
    std::int64_t lease_nanos = kInfiniteNanos;
};

struct TopicRouteState {
    // Endpoint records from builtin discovery, maintained by the controller from raw
    // records. DEMOTED by D64/D66 from matching authority to derivation-and-diagnosis
    // input: matched_readers feeds the D39/D42 writer-QoS derivation + in-place deadline
    // tightening; neither map gates creation or drives teardown any more.
    std::map<std::string, MatchedEndpoint> matched_writers; // guid -> info
    std::map<std::string, MatchedEndpoint> matched_readers; // auto-QoS output readers

    // Live matched counts from the build's own entities (D64/D66) — the discovery truth.
    // Updated only by stamp-gated TopicMatchChanged events; part of the entity facts
    // (cleared with the build).
    std::int32_t input_matched_count = 0;  // route reader's matched publications
    std::int32_t output_matched_count = 0; // route writer's matched subscriptions

    RouterRouteTopicState topic_state = RouterRouteTopicState::TOPIC_IDLE;
    std::uint64_t entity_generation = 0; // stamp at last entity build; 0 = none (D23)
    std::string resolved_type_name;      // first-resolved-wins (D20)

    // 7c (D64/D70): the creation gate — true once this topic's DynamicType has been
    // learned from the wire (TypeResolved). Process-lifetime (first-learned-wins, D66):
    // NOT cleared with the build, so re-enable/re-arm rebuilds without a new event.
    bool type_available = false;
    std::string last_error;

    // Sample counters (7d/D63): pulled from the live build's runtime by the periodic
    // RefreshCounters tick; deliberately excluded from the D5 fingerprint (the tick
    // republishes status without a revision bump). Entity facts — they describe the
    // CURRENT build (the runtime's counter starts at 0 per build), so they clear with
    // it; leaving them would let a rebuild's first tick "decrease" a stale count.
    std::uint64_t samples_forwarded = 0;
    std::uint64_t lifecycle_events_forwarded = 0; // always 0 until Phase 10 mirrors

    // Entity facts of the live build (D45): resolved QoS summaries from
    // TopicEntitiesReady, the writer deadline currently offered (for in-place
    // tightening decisions), and the last incompatible-QoS warning. Cleared whenever
    // the build they describe stops existing (teardown/abort/error/re-arm).
    std::string reader_qos_summary;
    std::string writer_qos_summary;
    std::string qos_warning;
    std::int64_t offered_deadline_nanos = kInfiniteNanos;

    void clear_entity_facts() {
        reader_qos_summary.clear();
        writer_qos_summary.clear();
        qos_warning.clear();
        offered_deadline_nanos = kInfiniteNanos;
        input_matched_count = 0;
        output_matched_count = 0;
        samples_forwarded = 0;
        lifecycle_events_forwarded = 0;
    }
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
    std::string role;   // YAML participants.<name>.role — config-time only, matched
                        // against node.role to select which participants this process
                        // actually needs (D50 follow-up: participants were previously
                        // built unconditionally regardless of role).
    // Multi-valued partition set (D83): protected node-identity entry (always
    // "${node.name}", substituted at parse time, never removable by command) + optional
    // config-seeded team entries + ad-hoc direct-peer joins via ADD/REMOVE_PARTICIPANT_
    // PARTITION. Applied to DomainParticipantQos.partition at creation and mutated in
    // place via set_qos thereafter.
    std::vector<std::string> participant_partition;
    std::string qos_profile_alias;
    // YAML participants.<name>.team_scoped (D83; renamed from `wan` in the is_wan
    // decomposition, 2026-07-22): true for a team-partition-scoped participant — gates ONLY
    // the protected-identity partition default (RouteConfigParser) and the non-removable
    // protection (is_protected_partition_name). platform_wan-only in current configs
    // post-D103 (was team_wan-only before D103 retired it). Explicit, not name-sniffed.
    // Does NOT drive link-stats WAN-leg detection (that is on_wan) and does NOT select
    // SPDP2 (that is use_spdp2) — all three are decoupled.
    bool team_scoped = false;
    // YAML participants.<name>.on_wan (link-stats decomposition, 2026-07-22): true for a
    // participant whose route legs live on the WAN — drives WAN-leg flagging so the
    // LinkStatsCollector polls each leg's per-matched-endpoint protocol statuses
    // (RouteEntityFactory / ParticipantRegistry::on_wan). Set on EVERY WAN participant
    // (control_wan/platform_wan post-D103), unlike team_scoped (platform_wan-only). This
    // is the concept D87 accidentally dropped from platform_wan/control_wan when it
    // cleared their `wan: true` for a partition-matching reason, silently un-covering
    // their data legs.
    bool on_wan = false;
    // YAML participants.<name>.spdp2 (D78, reinstated; D87 retraction reversed by the D92
    // CORRECTION 2026-07-22): select SPDP2|SEDP discovery for this participant. Set on every
    // WAN-facing participant (control_wan/platform_wan post-D103). Decoupled from is_wan so
    // all WAN participants use SPDP2 while only team-scoped ones take the D83 partition.
    bool use_spdp2 = false;
    // YAML participants.<name>.protected_partition_entries (D103): literal partition
    // values that are auto-seeded into participant_partition at parse time (same
    // auto-seed convention as team_scoped's ${node.name}) and non-removable via
    // REMOVE_PARTICIPANT_PARTITION. Independent of team_scoped — a participant can be
    // protected this way without being a team-identity participant (e.g. control_wan's
    // standing "*" wildcard, needed post-D103 to keep matching platform_wan once
    // platform_wan is no longer on the free default partition).
    std::vector<std::string> protected_partition_entries;
};

// Mirrors RouterAdminTypes.idl's RouterParticipantStatus.participant_partition bound
// (sequence<string, 16>) — the accept path and the config parser both reject overflow
// against this before it ever reaches a bounded_sequence assignment on the status-publish
// path (build_snapshot), where it would otherwise throw uncaught.
constexpr std::size_t kMaxParticipantPartitionEntries = 16;

// D83: the protected node-identity partition entry — every team_scoped participant's set
// always contains its own name, seeded at config time (RouteConfigParser) and never
// removable by command (RouterController::handle_participant_partition_membership). D103
// generalizes this: protected_partition_entries lists additional literal values
// (independent of team_scoped) that are equally non-removable — e.g. control_wan's
// standing "*" wildcard, which isn't a node-identity entry but must survive the same
// REMOVE_PARTICIPANT_PARTITION guard.
//
// Deliberately two independent checks, not folded into one list at parse time: this
// predicate must stay correct for ANY ParticipantState, regardless of how it was built
// (RouteConfigParser, a unit-test fixture, or any future caller) — an earlier version of
// this change folded team_scoped's identity entry into protected_partition_entries
// inside RouteConfigParser and dropped the inline team_scoped check here, which silently
// broke protection for every ParticipantState constructed by hand (a test fixture that
// sets team_scoped directly without replicating the parser's seeding step caught this
// immediately). Keeping the team_scoped check self-contained here means it can never be
// forgotten by a caller that doesn't route through the parser.
inline bool is_protected_partition_name(const ParticipantState &ps,
                                        const std::string &node_name,
                                        const std::string &candidate) {
    return (ps.team_scoped && candidate == node_name) ||
           std::find(ps.protected_partition_entries.begin(),
                    ps.protected_partition_entries.end(),
                    candidate) != ps.protected_partition_entries.end();
}

struct MutableRouterState {
    std::string node_name;
    std::string router_name;
    std::string config_hash; // SHA-256 of the loaded config, for the heartbeat (D80)
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

// True if the route's OUTPUT endpoint names no explicit writer QoS (the canonical alias
// location, D41). Only the output side depends on discovery under D39's asymmetric
// contract: an auto writer derives deadline/liveliness from the matched local readers at
// creation, so readiness gates on >=1 discovered local reader. The input reader's auto
// profile (weakest-request) matches every writer by RxO construction and needs nothing
// from discovery (D39/D45 — refines the old both-sides predicate).
bool output_uses_auto_qos(const RouterRouteSpec &spec);

// The writer-side derivation (D39/D42): deadline = min requested period, liveliness
// kind = max requested kind, lease = min requested lease across the matched readers.
// derive=false when the output endpoint names an explicit alias.
DerivedWriterQos derive_writer_qos(const TopicRouteState &topic,
                                   const RouterRouteSpec &route_spec);

// Find a topic by name within a route spec, or nullptr if absent. The one shared lookup
// (D44) — RouteEntityFactory and RouterController both scan the same RouterRouteSpec's
// topics vector and had drifted into independent copies.
const RouterRouteTopicSpec *find_topic_spec(const RouterRouteSpec &spec,
                                            const std::string &topic_name);

// Per-topic discovery rollup — pure function of the live build's matched counts
// (D64/D66: DDS is the matching authority): both legs >=1 -> READY, one -> PARTIAL,
// none (or no entities) -> NONE. No memory (D1).
RouterRouteDiscoveryState derive_topic_discovery(const TopicRouteState &topic,
                                                 const RouterRouteSpec &route_spec);

// Created-but-unmatched status reason (D66): names the unmatched leg(s) of a live
// FORWARDING build ("input_unmatched" / "output_unmatched" / both comma-joined);
// empty for any other topic state or when both legs are matched. A status reason,
// NOT a new operational state — the D2/D11 contract is unchanged.
std::string derive_match_reason(const TopicRouteState &topic);

// Route-level discovery = best (max) topic rollup (D11).
RouterRouteDiscoveryState derive_route_discovery(const RouteState &route);

// Route operational state — pure derivation over topic states (D11 table).
RouterRouteOperationalState derive_operational(const RouteState &route);

// Externally-visible fingerprint of one route: the D5 increment predicate compares this
// before/after each event (counters deliberately excluded — they never bump revision).
std::string route_fingerprint(const RouteState &route);

// Externally-visible fingerprint of one participant (D83): the partition set is
// runtime-mutable via ADD/REMOVE_PARTICIPANT_PARTITION, so a change is D5 externally
// visible state exactly like a route's endpoint partitions (route_fingerprint above).
std::string participant_fingerprint(const ParticipantState &participant);

} // namespace router
