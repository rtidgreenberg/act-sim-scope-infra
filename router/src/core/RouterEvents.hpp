// RouterEvents.hpp — the typed ControllerEvent set (Phase 1).
//
// Everything reaches RouterController as one of these events on the MPSC queue
// (code-architecture.md Controller Event Model; D3/D12). Phase 1 posts them from tests;
// Phase 2+ posts them from waitset dispatch threads. Completion events carry the
// entity-generation stamp they were issued for (D21/D23) so stale completions are
// discardable.
//
// Discovery events carry raw endpoint records (D22): endpoint→route-topic matching and
// the per-topic matched-endpoint sets are controller logic, not DiscoveryDispatcher logic.

#pragma once

#include "RouterAdminTypes.hpp"

#include <cstdint>
#include <string>

namespace router {

// Durations in these DDS-free structs are nanoseconds; infinite = kInfiniteNanos.
const std::int64_t kInfiniteNanos = INT64_MAX;

// Liveliness kind as a POD, ordered by RxO strength (AUTOMATIC < MANUAL_BY_PARTICIPANT
// < MANUAL_BY_TOPIC — validated 7.7), so "max requested kind" is numeric max (D42).
enum class LivelinessKindPod { Automatic = 0, ManualByParticipant = 1, ManualByTopic = 2 };

// A discovered endpoint, as posted by DiscoveryDispatcher (real in Phase 2, synthetic in
// Phase 1 tests). Upsert semantics per GUID (D12): a later record for the same GUID can
// add data — e.g. the discovered type arriving after the endpoint (D13). This struct is
// Phase 1's no-DDS stand-in for the real payload, which is a copy of the builtin topic
// data plus the origin_router/ignored sidecar (D27) — same facts, DDS-owned shape.
struct EndpointRecord {
    std::string guid;
    bool is_publication = true;  // false: subscription (auto-QoS output reader — D1)
    std::string topic_name;
    std::string type_name;
    bool has_type = false;       // TypeLookup resolved (D13: optional until resolved)
    std::string origin_router;   // empty for application endpoints (D15)

    // Requested QoS captured from subscription builtin data (D19/D27 pinned subset) —
    // the writer-side derivation inputs (D39/D42). Defaults are the DDS defaults, so a
    // record without captured QoS is derivation-neutral.
    std::int64_t deadline_nanos = kInfiniteNanos;
    LivelinessKindPod liveliness_kind = LivelinessKindPod::Automatic;
    std::int64_t lease_nanos = kInfiniteNanos;
};

// Writer-side QoS derived from the matched local readers at entity creation (D39/D42):
// deadline = min requested period, liveliness kind = max requested kind, lease = min
// requested lease. `derive` is false when the output endpoint names an explicit alias —
// the factory then applies the alias untouched.
struct DerivedWriterQos {
    bool derive = false;
    std::int64_t deadline_nanos = kInfiniteNanos;
    LivelinessKindPod liveliness_kind = LivelinessKindPod::Automatic;
    std::int64_t lease_nanos = kInfiniteNanos;
};

enum class ControllerEventKind {
    CommandReceived,        // post-admission RouterCommand (D24)
    PublicationDiscovered,  // raw record upsert (D22)
    SubscriptionDiscovered, // raw record upsert (D22)
    EndpointLost,           // by GUID; controller erases it from all matched sets
    TopicEntitiesReady,     // per-topic entity creation completed (D21)
    TopicTeardownComplete,  // per-topic teardown completed (D21)
    RouteEntityError,       // topic-scoped (topic_name set) or route-wide (empty) (D21)
    TopicQosWarning,        // incompatible-QoS status on a live build's entity (D39/D45)
    TopicMatchChanged       // matched-count change on a live build's entity (D64/D66)
};

// One flat event struct (POC-simple; only the fields for the given kind are meaningful).
struct ControllerEvent {
    ControllerEventKind kind;

    // CommandReceived
    RouterCommand command;

    // PublicationDiscovered / SubscriptionDiscovered / EndpointLost
    EndpointRecord endpoint;

    // TopicEntitiesReady / TopicTeardownComplete / RouteEntityError / TopicQosWarning
    std::string route_name;
    std::string topic_name;              // empty on RouteEntityError = route-wide (D21)
    std::uint64_t entity_generation = 0; // stamp the operation was issued for (D23)
    std::string error;                   // RouteEntityError only
    std::string reader_qos_summary;      // TopicEntitiesReady only (D45)
    std::string writer_qos_summary;      // TopicEntitiesReady only (D45)
    std::string qos_warning;             // TopicQosWarning only, "reader:<POLICY>" form

    // TopicMatchChanged only (D64/D66): which leg's matched count changed, and its
    // current value read from the entity's own matched status inside the handler.
    bool input_side = true;              // true: route reader; false: route writer
    std::int32_t matched_count = 0;

    static ControllerEvent command_received(const RouterCommand &cmd) {
        ControllerEvent e;
        e.kind = ControllerEventKind::CommandReceived;
        e.command = cmd;
        return e;
    }
    static ControllerEvent publication_discovered(const EndpointRecord &rec) {
        ControllerEvent e;
        e.kind = ControllerEventKind::PublicationDiscovered;
        e.endpoint = rec;
        e.endpoint.is_publication = true;
        return e;
    }
    static ControllerEvent subscription_discovered(const EndpointRecord &rec) {
        ControllerEvent e;
        e.kind = ControllerEventKind::SubscriptionDiscovered;
        e.endpoint = rec;
        e.endpoint.is_publication = false;
        return e;
    }
    static ControllerEvent endpoint_lost(const std::string &guid) {
        ControllerEvent e;
        e.kind = ControllerEventKind::EndpointLost;
        e.endpoint.guid = guid;
        return e;
    }
    static ControllerEvent topic_entities_ready(const std::string &route,
                                                const std::string &topic,
                                                std::uint64_t gen,
                                                const std::string &reader_summary = "",
                                                const std::string &writer_summary = "") {
        ControllerEvent e;
        e.kind = ControllerEventKind::TopicEntitiesReady;
        e.route_name = route;
        e.topic_name = topic;
        e.entity_generation = gen;
        e.reader_qos_summary = reader_summary;
        e.writer_qos_summary = writer_summary;
        return e;
    }
    static ControllerEvent topic_qos_warning(const std::string &route,
                                             const std::string &topic,
                                             std::uint64_t gen,
                                             const std::string &warning) {
        ControllerEvent e;
        e.kind = ControllerEventKind::TopicQosWarning;
        e.route_name = route;
        e.topic_name = topic;
        e.entity_generation = gen;
        e.qos_warning = warning;
        return e;
    }
    static ControllerEvent topic_teardown_complete(const std::string &route,
                                                   const std::string &topic,
                                                   std::uint64_t gen) {
        ControllerEvent e;
        e.kind = ControllerEventKind::TopicTeardownComplete;
        e.route_name = route;
        e.topic_name = topic;
        e.entity_generation = gen;
        return e;
    }
    static ControllerEvent topic_match_changed(const std::string &route,
                                               const std::string &topic,
                                               std::uint64_t gen,
                                               bool input_side,
                                               std::int32_t matched_count) {
        ControllerEvent e;
        e.kind = ControllerEventKind::TopicMatchChanged;
        e.route_name = route;
        e.topic_name = topic;
        e.entity_generation = gen;
        e.input_side = input_side;
        e.matched_count = matched_count;
        return e;
    }
    static ControllerEvent route_entity_error(const std::string &route,
                                              const std::string &topic,
                                              std::uint64_t gen,
                                              const std::string &error) {
        ControllerEvent e;
        e.kind = ControllerEventKind::RouteEntityError;
        e.route_name = route;
        e.topic_name = topic;
        e.entity_generation = gen;
        e.error = error;
        return e;
    }
};

} // namespace router
