// RouterController.hpp — the single writer of router state (Phase 1).
//
// Everything reaches it as ControllerEvents on the MPSC queue; only the controller strand
// mutates MutableRouterState. After each event it applies the D5 increment predicate
// (before/after fingerprint), bumps the global state_revision on externally visible
// change, stamps the changed routes, builds a generated RouterStatus snapshot (D25), and
// publishes it (change-driven only, D26).

#pragma once

#include "EventQueue.hpp"
#include "Interfaces.hpp"
#include "RouterState.hpp"

#include <chrono>
#include <memory>
#include <string>
#include <vector>

namespace router {

struct RouterIdentityInfo {
    std::string node_name;
    std::string router_name;
    std::uint32_t router_id = 0;
    std::string status_id;
};

class RouterController {
public:
    // Phase 1: routes from fixture specs only (D10); participants read-only from
    // config for status completeness (D7). Publishes the startup snapshot (revision 0).
    // journal is optional (D55): nullptr (the default, e.g. Phase 1 tests) disables the
    // debug journal entirely — additive, no behavior change.
    RouterController(const RouterIdentityInfo &identity,
                     const std::vector<RouterRouteSpec> &route_specs,
                     const std::vector<ParticipantState> &participants,
                     IEntityFactory *entity_factory,
                     IStatusPublisher *status_publisher,
                     IControllerJournal *journal = nullptr);

    // Create entities for every startup-enabled route (D64/D66 create-and-observe: the
    // creation gate is retired — with XML-provided types, creation needs nothing from
    // discovery). Called once, from the strand-owning thread, AFTER the participants are
    // enabled (entity creation ignores/instance handles need enabled participants — D52
    // ordering) and BEFORE the drain thread starts. Tests call it right after
    // construction; the constructor itself stays creation-free so the revision-0 startup
    // snapshot still precedes any entity activity.
    void activate();

    // Thread-safe (MPSC producer side, D12).
    void post(const ControllerEvent &event);

    // Drain and process all pending events on the caller's thread (the strand).
    void drain();

    // Block briefly for events, then process the batch on the caller's thread.
    void wait_and_drain(std::chrono::milliseconds timeout);

    // Re-publish the current status snapshot. Used by disabled startup (D52), where the
    // constructor-time snapshot was written before the status writer was enabled. Call
    // from the strand thread only (reads the controller state the drain thread mutates).
    void republish_status();

    // Test/observability access (const — routes read state, controller writes it).
    const MutableRouterState &state() const { return state_; }

private:
    // Process one drained event on the strand: fingerprint, process, publish-if-changed,
    // then emit one journal record (D55). Shared by drain() and wait_and_drain().
    void process_one(const ControllerEvent &event);
    void process(const ControllerEvent &event);

    // Build the debug journal record for a just-processed event (D55/D56): maps the event
    // kind (D46), stamps pre/post state_revision + state_changed, and fills the
    // decision/reason from the event's outcome (the cached ack for commands). Mutates only
    // the journal sequence counter.
    ControllerJournalRecord build_journal_record(const ControllerEvent &event,
                                                 std::uint64_t pre_revision);

    void handle_command(const RouterCommand &cmd);
    void handle_enable(const RouterCommand &cmd, RouterCommandAck &ack);
    void handle_disable(const RouterCommand &cmd, RouterCommandAck &ack);
    void handle_set_route_partition(const RouterCommand &cmd, RouterCommandAck &ack);
    void cache_ack(const RouterCommandAck &ack);

    void apply_publication(const EndpointRecord &rec);
    void apply_subscription(const EndpointRecord &rec);
    void apply_endpoint_lost(const std::string &guid);
    void apply_entities_ready(const ControllerEvent &e);
    void apply_teardown_complete(const ControllerEvent &e);
    void apply_entity_error(const ControllerEvent &e);
    void apply_qos_warning(const ControllerEvent &e);
    void apply_match_changed(const ControllerEvent &e);

    // Tighten a FORWARDING build's writer deadline in place when the derivation over
    // the current matched readers is stricter than the offer (D39/D45).
    void maybe_tighten_deadline(RouteState &route, TopicRouteState &topic,
                                const std::string &topic_name);

    // Drive one topic toward its desired state per the D2/D8/D11 tables.
    void reconcile_topic(RouteState &route, const std::string &topic_name);
    void reconcile_route(RouteState &route);

    std::uint64_t next_generation() { return ++state_.entity_generation_counter; }

    std::shared_ptr<const RouterStatus> build_snapshot() const;
    void publish_if_changed(const std::vector<std::string> &pre_fingerprints);
    std::vector<std::string> fingerprints() const;

    MutableRouterState state_;
    EventQueue queue_;
    IEntityFactory *factory_;
    IStatusPublisher *status_;
    IControllerJournal *journal_;    // optional debug journal (D55); nullptr = disabled
    std::uint64_t journal_sequence_; // monotonic per-record sequence (D55)
    std::string current_cause_; // accepted command id during this event, else empty (D8)
};

} // namespace router
