// Interfaces.hpp — the seams RouterController depends on (D3).
//
// Phase 1 fakes all three in tests; Phase 2 delivers the real DiscoveryIndex and
// StatusPublisher, Phase 3 the real EntityFactory. The fake EntityFactory must model
// create/teardown as PENDING operations completed by explicit TopicEntitiesReady /
// TopicTeardownComplete events (D8/D21) — the same seam real async creation needs.

#pragma once

#include "RouteView.hpp"
#include "RouterEvents.hpp"

#include <cstdint>
#include <memory>
#include <string>

namespace router {

// Creates/destroys per-topic route DDS entities. Operations are stamped (D23); the
// implementation reports completion by posting TopicEntitiesReady / TopicTeardownComplete
// / RouteEntityError events carrying the same stamp (D21).
struct IEntityFactory {
    virtual ~IEntityFactory() {}
    virtual void create_topic_entities(const RouteView &view,
                                       const std::string &topic_name,
                                       std::uint64_t generation) = 0;
    virtual void teardown_topic_entities(const std::string &route_name,
                                         const std::string &topic_name,
                                         std::uint64_t generation) = 0;
    // Abort an in-flight creation and discard partial entities (D8 RESOLVING-abort edge).
    virtual void abort_topic_creation(const std::string &route_name,
                                      const std::string &topic_name,
                                      std::uint64_t generation) = 0;
};

// Publishes the controller's outward report. The snapshot IS the generated RouterStatus
// (D25); publication is change-driven only (D26). Also carries command acks (the DDS ack
// writer arrives with the admin plumbing in Phase 6; Phase 1 fakes capture both).
struct IStatusPublisher {
    virtual ~IStatusPublisher() {}
    virtual void publish(std::shared_ptr<const RouterStatus> snapshot) = 0;
    virtual void publish_ack(const RouterCommandAck &ack) = 0;
};

// GUID-keyed endpoint-record cache (D22). Near-vestigial in Phase 1: the controller
// receives raw records as events and owns all matching; the index only serves lookups
// (first real consumer: InputOriginObserved origin resolution, Phase 3).
struct IDiscoveryIndex {
    virtual ~IDiscoveryIndex() {}
    virtual bool lookup(const std::string &guid, EndpointRecord &out) const = 0;
};

} // namespace router
