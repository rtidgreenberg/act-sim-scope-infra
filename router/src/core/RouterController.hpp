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
    RouterController(const RouterIdentityInfo &identity,
                     const std::vector<RouterRouteSpec> &route_specs,
                     const std::vector<ParticipantState> &participants,
                     IEntityFactory *entity_factory,
                     IStatusPublisher *status_publisher);

    // Thread-safe (MPSC producer side, D12).
    void post(const ControllerEvent &event);

    // Drain and process all pending events on the caller's thread (the strand).
    void drain();

    // Block briefly for events, then process the batch on the caller's thread.
    void wait_and_drain(std::chrono::milliseconds timeout);

    // Test/observability access (const — routes read state, controller writes it).
    const MutableRouterState &state() const { return state_; }

private:
    void process(const ControllerEvent &event);

    void handle_command(const RouterCommand &cmd);
    void handle_enable(const RouterCommand &cmd, RouterCommandAck &ack);
    void handle_disable(const RouterCommand &cmd, RouterCommandAck &ack);
    void cache_ack(const RouterCommandAck &ack);

    void apply_publication(const EndpointRecord &rec);
    void apply_subscription(const EndpointRecord &rec);
    void apply_endpoint_lost(const std::string &guid);
    void apply_entities_ready(const ControllerEvent &e);
    void apply_teardown_complete(const ControllerEvent &e);
    void apply_entity_error(const ControllerEvent &e);

    // Drive one topic toward its desired state per the D2/D8/D11 tables.
    void reconcile_topic(RouteState &route, const std::string &topic_name);
    void reconcile_route(RouteState &route);

    const RouterRouteTopicSpec *find_topic_spec(const RouteState &route,
                                                const std::string &topic_name) const;

    std::uint64_t next_generation() { return ++state_.entity_generation_counter; }

    std::shared_ptr<const RouterStatus> build_snapshot() const;
    void publish_if_changed(const std::vector<std::string> &pre_fingerprints);
    std::vector<std::string> fingerprints() const;

    MutableRouterState state_;
    EventQueue queue_;
    IEntityFactory *factory_;
    IStatusPublisher *status_;
    std::string current_cause_; // accepted command id during this event, else empty (D8)
};

} // namespace router
