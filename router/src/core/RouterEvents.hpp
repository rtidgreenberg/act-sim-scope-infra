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
};

enum class ControllerEventKind {
    CommandReceived,        // post-admission RouterCommand (D24)
    PublicationDiscovered,  // raw record upsert (D22)
    SubscriptionDiscovered, // raw record upsert (D22)
    EndpointLost,           // by GUID; controller erases it from all matched sets
    TopicEntitiesReady,     // per-topic entity creation completed (D21)
    TopicTeardownComplete,  // per-topic teardown completed (D21)
    RouteEntityError        // topic-scoped (topic_name set) or route-wide (empty) (D21)
};

// One flat event struct (POC-simple; only the fields for the given kind are meaningful).
struct ControllerEvent {
    ControllerEventKind kind;

    // CommandReceived
    RouterCommand command;

    // PublicationDiscovered / SubscriptionDiscovered / EndpointLost
    EndpointRecord endpoint;

    // TopicEntitiesReady / TopicTeardownComplete / RouteEntityError
    std::string route_name;
    std::string topic_name;              // empty on RouteEntityError = route-wide (D21)
    std::uint64_t entity_generation = 0; // stamp the operation was issued for (D23)
    std::string error;                   // RouteEntityError only

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
                                                std::uint64_t gen) {
        ControllerEvent e;
        e.kind = ControllerEventKind::TopicEntitiesReady;
        e.route_name = route;
        e.topic_name = topic;
        e.entity_generation = gen;
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
