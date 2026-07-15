// Interfaces.hpp — the seams RouterController depends on (D3).
//
// Phase 1 fakes both in tests; Phase 2 delivers the real StatusPublisher, Phase 3 the
// real EntityFactory. DiscoveryDispatcher is not a controller dependency: it is a pure event
// source (translator + participant table, D30) the controller never queries — tests post
// its events directly. The fake EntityFactory must model
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
// / RouteEntityError events carrying the same stamp (D21). The controller derives the
// writer-side QoS from the matched local readers at issue time and passes it in (D39/D42):
// applied only when the output endpoint uses auto QoS (derived.derive).
struct IEntityFactory {
    virtual ~IEntityFactory() {}
    virtual void create_topic_entities(const RouteView &view,
                                       const std::string &topic_name,
                                       std::uint64_t generation,
                                       const DerivedWriterQos &derived) = 0;
    virtual void teardown_topic_entities(const std::string &route_name,
                                         const std::string &topic_name,
                                         std::uint64_t generation) = 0;
    // Abort an in-flight creation and discard partial entities (D8 RESOLVING-abort edge).
    virtual void abort_topic_creation(const std::string &route_name,
                                      const std::string &topic_name,
                                      std::uint64_t generation) = 0;
    // Tighten the (mutable) deadline of a live build's output writer in place — no
    // entity recreation, no teardown cycle (D39). Synchronous on the controller strand;
    // returns the writer's new QoS summary, or "" if no such runtime exists / the
    // update failed (the caller then surfaces a warning).
    virtual std::string update_writer_deadline(const std::string &route_name,
                                               const std::string &topic_name,
                                               std::int64_t deadline_nanos) = 0;
    // Change a live build's Subscriber/Publisher partitions in place (7b/D69, D15:
    // runtime-mutable via set_qos, automatic rematch — no rebuild). Synchronous on the
    // controller strand; returns false if no such runtime exists / the update failed
    // (the caller logs; a future rebuild uses the re-minted view's new spec anyway).
    virtual bool update_route_partitions(const std::string &route_name,
                                         const std::string &topic_name,
                                         const std::string &subscriber_partition,
                                         const std::string &publisher_partition) = 0;
};

// Publishes the controller's outward report. The snapshot IS the generated RouterStatus
// (D25); publication is change-driven only (D26). Also carries command acks (the DDS ack
// writer arrives with the admin plumbing in Phase 6; Phase 1 fakes capture both).
struct IStatusPublisher {
    virtual ~IStatusPublisher() {}
    virtual void publish(std::shared_ptr<const RouterStatus> snapshot) = 0;
    virtual void publish_ack(const RouterCommandAck &ack) = 0;
};

// Debug-analysis journal seam (Phase 6 slice 6b, D55). The controller invokes record()
// exactly once per processed ControllerEvent, carrying the input event, decision/outcome,
// and pre/post state_revision. It is OPTIONAL: the controller holds an IControllerJournal*
// that is nullptr by default (Phase 1 tests pass nothing), in which case no record is built
// — the seam is additive with zero behavior change. The real DDS-backed implementation
// (ControllerJournalPublisher) writes the record as a generated ControllerJournalRecord with
// D49 QoS. Observability only: record() must never block route control (D49 send-window fix).
struct IControllerJournal {
    virtual ~IControllerJournal() {}
    virtual void record(const ControllerJournalRecord &rec) = 0;
};

} // namespace router
