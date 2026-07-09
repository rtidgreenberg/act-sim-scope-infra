#include "RouterController.hpp"

#include "Log.hpp"

#include <sstream>

namespace router {

namespace {

const size_t kCommandHistoryBound = 256; // D4

std::string kind_name(RouterCommandKind kind) {
    switch (kind) {
    case RouterCommandKind::ENABLE_ROUTE:              return "ENABLE_ROUTE";
    case RouterCommandKind::DISABLE_ROUTE:             return "DISABLE_ROUTE";
    case RouterCommandKind::UPDATE_ROUTE:              return "UPDATE_ROUTE";
    case RouterCommandKind::SET_PARTICIPANT_PARTITION: return "SET_PARTICIPANT_PARTITION";
    }
    return "?";
}

} // namespace

RouterController::RouterController(const RouterIdentityInfo &identity,
                                   const std::vector<RouterRouteSpec> &route_specs,
                                   const std::vector<ParticipantState> &participants,
                                   IEntityFactory *entity_factory,
                                   IStatusPublisher *status_publisher)
        : factory_(entity_factory),
          status_(status_publisher) {
    state_.node_name = identity.node_name;
    state_.router_name = identity.router_name;
    state_.router_id = identity.router_id;
    state_.status_id = identity.status_id;

    for (size_t i = 0; i < participants.size(); ++i) {
        state_.participants[participants[i].name] = participants[i];
    }

    for (size_t i = 0; i < route_specs.size(); ++i) {
        RouteState route;
        route.desired = route_specs[i];
        std::shared_ptr<RouteView> view(new RouteView());
        view->spec = route.desired;
        view->entity_generation = next_generation(); // D23 stamp at mint
        route.view = view;
        for (size_t t = 0; t < route.desired.topics.size(); ++t) {
            route.topics[route.desired.topics.at(t).name] = TopicRouteState();
        }
        state_.routes[route.desired.route_name] = route;
    }

    // Startup snapshot at revision 0 (Phase 1 evidence: disabled routes visible).
    status_->publish(build_snapshot());
}

void RouterController::post(const ControllerEvent &event) {
    queue_.post(event);
}

void RouterController::drain() {
    std::vector<ControllerEvent> events = queue_.drain();
    for (size_t i = 0; i < events.size(); ++i) {
        std::vector<std::string> pre = fingerprints();
        current_cause_.clear();
        process(events[i]);
        publish_if_changed(pre);
    }
}

void RouterController::wait_and_drain(std::chrono::milliseconds timeout) {
    std::vector<ControllerEvent> events = queue_.wait_and_drain(timeout);
    for (size_t i = 0; i < events.size(); ++i) {
        std::vector<std::string> pre = fingerprints();
        current_cause_.clear();
        process(events[i]);
        publish_if_changed(pre);
    }
}

void RouterController::process(const ControllerEvent &event) {
    switch (event.kind) {
    case ControllerEventKind::CommandReceived:
        handle_command(event.command);
        break;
    case ControllerEventKind::PublicationDiscovered:
        apply_publication(event.endpoint);
        break;
    case ControllerEventKind::SubscriptionDiscovered:
        apply_subscription(event.endpoint);
        break;
    case ControllerEventKind::EndpointLost:
        apply_endpoint_lost(event.endpoint.guid);
        break;
    case ControllerEventKind::TopicEntitiesReady:
        apply_entities_ready(event);
        break;
    case ControllerEventKind::TopicTeardownComplete:
        apply_teardown_complete(event);
        break;
    case ControllerEventKind::RouteEntityError:
        apply_entity_error(event);
        break;
    }
}

// --- Commands (post-admission, D24) ---

void RouterController::handle_command(const RouterCommand &cmd) {
    // Duplicate command_id: return the cached ack, no state change, no revision bump
    // (D2/D4).
    std::map<std::string, RouterCommandAck>::const_iterator cached =
            state_.ack_by_command_id.find(cmd.command_id);
    if (cached != state_.ack_by_command_id.end()) {
        status_->publish_ack(cached->second);
        return;
    }

    RouterCommandAck ack;
    ack.target_node = state_.node_name;
    ack.target_router = state_.router_name;
    ack.command_id = cmd.command_id;
    ack.route_name = cmd.route_name;
    ack.accepted = false;

    switch (cmd.kind) {
    case RouterCommandKind::UPDATE_ROUTE:
    case RouterCommandKind::SET_PARTICIPANT_PARTITION:
        // Unsupported kinds are parsed-and-rejected (D4/D7), reject cached like any
        // other ack.
        ack.message = kind_name(cmd.kind) + std::string(" unsupported in this build");
        cache_ack(ack);
        status_->publish_ack(ack);
        return;
    case RouterCommandKind::ENABLE_ROUTE:
    case RouterCommandKind::DISABLE_ROUTE:
        break;
    }

    std::map<std::string, RouteState>::iterator r = state_.routes.find(cmd.route_name);
    if (r == state_.routes.end()) {
        // Unknown route: cached reject, never implicit route creation (D24).
        ack.message = "unknown route";
        cache_ack(ack);
        status_->publish_ack(ack);
        return;
    }

    if (cmd.kind == RouterCommandKind::ENABLE_ROUTE) {
        handle_enable(cmd, ack);
    } else {
        handle_disable(cmd, ack);
    }
    cache_ack(ack);
    status_->publish_ack(ack);
}

void RouterController::handle_enable(const RouterCommand &cmd, RouterCommandAck &ack) {
    RouteState &route = state_.routes[cmd.route_name];

    bool any_topic_error = false;
    for (std::map<std::string, TopicRouteState>::const_iterator it = route.topics.begin();
         it != route.topics.end(); ++it) {
        if (it->second.topic_state == RouterRouteTopicState::TOPIC_ERROR) {
            any_topic_error = true;
        }
    }

    if (route.desired.desired_enabled && !route.route_error && !any_topic_error) {
        // Redundant command, new command_id: idempotent accept, no state change (D8).
        ack.accepted = true;
        ack.message = "already enabled";
        return;
    }

    // Fresh enable, or re-arm out of sticky ERROR (route-wide and/or per-topic) — the
    // only exit from ERROR (D2); at route scope re-arm also clears per-topic errors and
    // retries errored topics (D11).
    bool rearmed = route.route_error || any_topic_error;
    route.desired.desired_enabled = true;
    route.route_error = false;
    route.last_error.clear();
    for (std::map<std::string, TopicRouteState>::iterator it = route.topics.begin();
         it != route.topics.end(); ++it) {
        if (it->second.topic_state == RouterRouteTopicState::TOPIC_ERROR) {
            it->second.topic_state = RouterRouteTopicState::TOPIC_IDLE;
            it->second.entity_generation = 0;
            it->second.last_error.clear();
        }
    }
    ack.accepted = true;
    ack.message = rearmed ? "enabled (re-armed)" : "enabled";
    current_cause_ = cmd.command_id;
    reconcile_route(route);
}

void RouterController::handle_disable(const RouterCommand &cmd, RouterCommandAck &ack) {
    RouteState &route = state_.routes[cmd.route_name];

    if (!route.desired.desired_enabled) {
        // Redundant disable: idempotent accept, no state change (D8).
        ack.accepted = true;
        ack.message = "already disabled";
        return;
    }

    route.desired.desired_enabled = false;
    route.route_error = false; // DISABLE is a valid ERROR exit (D2)
    for (std::map<std::string, TopicRouteState>::iterator it = route.topics.begin();
         it != route.topics.end(); ++it) {
        TopicRouteState &topic = it->second;
        switch (topic.topic_state) {
        case RouterRouteTopicState::TOPIC_FORWARDING:
            topic.topic_state = RouterRouteTopicState::TOPIC_TEARING_DOWN;
            factory_->teardown_topic_entities(route.desired.route_name, it->first,
                                              topic.entity_generation);
            break;
        case RouterRouteTopicState::TOPIC_CREATING:
            factory_->abort_topic_creation(route.desired.route_name, it->first,
                                           topic.entity_generation);
            topic.topic_state = RouterRouteTopicState::TOPIC_IDLE;
            topic.entity_generation = 0;
            break;
        case RouterRouteTopicState::TOPIC_ERROR:
            topic.topic_state = RouterRouteTopicState::TOPIC_IDLE;
            topic.entity_generation = 0;
            break;
        case RouterRouteTopicState::TOPIC_TEARING_DOWN:
        case RouterRouteTopicState::TOPIC_IDLE:
            break;
        }
    }
    ack.accepted = true;
    ack.message = "disabled";
    current_cause_ = cmd.command_id;
}

void RouterController::cache_ack(const RouterCommandAck &ack) {
    state_.ack_by_command_id[ack.command_id] = ack;
    state_.ack_fifo.push_back(ack.command_id);
    while (state_.ack_fifo.size() > kCommandHistoryBound) {
        state_.ack_by_command_id.erase(state_.ack_fifo.front());
        state_.ack_fifo.pop_front();
    }
}

// --- Discovery (raw records; matching is controller logic, D22) ---

void RouterController::apply_publication(const EndpointRecord &rec) {
    for (std::map<std::string, RouteState>::iterator r = state_.routes.begin();
         r != state_.routes.end(); ++r) {
        std::map<std::string, TopicRouteState>::iterator t =
                r->second.topics.find(rec.topic_name);
        if (t == r->second.topics.end()) {
            continue;
        }
        TopicRouteState &topic = t->second;
        MatchedEndpoint &entry = topic.matched_writers[rec.guid]; // upsert (D12)
        entry.type_name = rec.type_name;
        entry.has_type = rec.has_type;
        if (rec.has_type) {
            if (topic.resolved_type_name.empty()) {
                topic.resolved_type_name = rec.type_name; // first-resolved-wins (D20)
            } else if (topic.resolved_type_name != rec.type_name) {
                Log::warn("type_name_conflict",
                          {{"route", r->first},
                           {"topic", rec.topic_name},
                           {"resolved", topic.resolved_type_name},
                           {"ignored", rec.type_name},
                           {"guid", rec.guid}});
            }
        }
        reconcile_topic(r->second, rec.topic_name);
    }
}

void RouterController::apply_subscription(const EndpointRecord &rec) {
    for (std::map<std::string, RouteState>::iterator r = state_.routes.begin();
         r != state_.routes.end(); ++r) {
        std::map<std::string, TopicRouteState>::iterator t =
                r->second.topics.find(rec.topic_name);
        if (t == r->second.topics.end()) {
            continue;
        }
        MatchedEndpoint &entry = t->second.matched_readers[rec.guid];
        entry.type_name = rec.type_name;
        entry.has_type = rec.has_type;
        reconcile_topic(r->second, rec.topic_name);
    }
}

void RouterController::apply_endpoint_lost(const std::string &guid) {
    for (std::map<std::string, RouteState>::iterator r = state_.routes.begin();
         r != state_.routes.end(); ++r) {
        for (std::map<std::string, TopicRouteState>::iterator t =
                     r->second.topics.begin();
             t != r->second.topics.end(); ++t) {
            bool changed = t->second.matched_writers.erase(guid) > 0;
            changed = (t->second.matched_readers.erase(guid) > 0) || changed;
            if (changed) {
                reconcile_topic(r->second, t->first);
            }
        }
    }
}

// --- Entity operation completions (D21), stale-stamp discard (D23) ---

void RouterController::apply_entities_ready(const ControllerEvent &e) {
    std::map<std::string, RouteState>::iterator r = state_.routes.find(e.route_name);
    if (r == state_.routes.end()) {
        return;
    }
    std::map<std::string, TopicRouteState>::iterator t =
            r->second.topics.find(e.topic_name);
    if (t == r->second.topics.end()) {
        return;
    }
    TopicRouteState &topic = t->second;
    if (topic.topic_state != RouterRouteTopicState::TOPIC_CREATING
        || e.entity_generation != topic.entity_generation) {
        Log::warn("stale_entities_ready",
                  {{"route", e.route_name},
                   {"topic", e.topic_name},
                   {"gen", std::to_string(e.entity_generation)},
                   {"current_gen", std::to_string(topic.entity_generation)}});
        return; // stale stamp: rebuilt or aborted since issue — discard (D21/D23)
    }
    topic.topic_state = RouterRouteTopicState::TOPIC_FORWARDING;
}

void RouterController::apply_teardown_complete(const ControllerEvent &e) {
    std::map<std::string, RouteState>::iterator r = state_.routes.find(e.route_name);
    if (r == state_.routes.end()) {
        return;
    }
    std::map<std::string, TopicRouteState>::iterator t =
            r->second.topics.find(e.topic_name);
    if (t == r->second.topics.end()) {
        return;
    }
    TopicRouteState &topic = t->second;
    if (topic.topic_state != RouterRouteTopicState::TOPIC_TEARING_DOWN
        || e.entity_generation != topic.entity_generation) {
        Log::warn("stale_teardown_complete",
                  {{"route", e.route_name}, {"topic", e.topic_name}});
        return;
    }
    topic.topic_state = RouterRouteTopicState::TOPIC_IDLE;
    topic.entity_generation = 0;
    // Teardown complete: rebuild if discovery is READY again, else quiesce
    // (DEGRADED -> RESOLVING | WAITING_FOR_DISCOVERY, D2).
    reconcile_topic(r->second, e.topic_name);
}

void RouterController::apply_entity_error(const ControllerEvent &e) {
    std::map<std::string, RouteState>::iterator r = state_.routes.find(e.route_name);
    if (r == state_.routes.end()) {
        return;
    }
    RouteState &route = r->second;
    if (e.topic_name.empty()) {
        // Route-wide failure: sticky ERROR until command re-arm (D2/D21).
        route.route_error = true;
        route.last_error = e.error;
        return;
    }
    std::map<std::string, TopicRouteState>::iterator t = route.topics.find(e.topic_name);
    if (t == route.topics.end()) {
        return;
    }
    TopicRouteState &topic = t->second;
    if (topic.entity_generation != 0 && e.entity_generation != topic.entity_generation) {
        Log::warn("stale_entity_error",
                  {{"route", e.route_name}, {"topic", e.topic_name}});
        return; // stale stamp (D23)
    }
    // Contained per-topic failure: sibling topics unaffected (D11/D21).
    topic.topic_state = RouterRouteTopicState::TOPIC_ERROR;
    topic.entity_generation = 0;
    topic.last_error = e.error;
}

// --- Reconciliation (the D2/D8/D11 tables) ---

void RouterController::reconcile_route(RouteState &route) {
    for (size_t i = 0; i < route.desired.topics.size(); ++i) {
        reconcile_topic(route, route.desired.topics.at(i).name);
    }
}

void RouterController::reconcile_topic(RouteState &route, const std::string &topic_name) {
    if (!route.desired.desired_enabled || route.route_error) {
        return; // disabled routes quiesce via handle_disable; ERROR is sticky (D2)
    }
    const RouterRouteTopicSpec *spec = find_topic_spec(route, topic_name);
    std::map<std::string, TopicRouteState>::iterator t = route.topics.find(topic_name);
    if (spec == NULL || t == route.topics.end()) {
        return;
    }
    TopicRouteState &topic = t->second;
    RouterRouteDiscoveryState d = derive_topic_discovery(topic, *spec);

    switch (topic.topic_state) {
    case RouterRouteTopicState::TOPIC_IDLE:
        if (d == RouterRouteDiscoveryState::DISCOVERY_READY) {
            topic.entity_generation = next_generation(); // D23 stamp at build
            topic.topic_state = RouterRouteTopicState::TOPIC_CREATING;
            factory_->create_topic_entities(*route.view, topic_name,
                                            topic.entity_generation);
        }
        break;
    case RouterRouteTopicState::TOPIC_CREATING:
        if (d != RouterRouteDiscoveryState::DISCOVERY_READY) {
            // Discovery regressed mid-resolve: abort, discard partial entities, back to
            // waiting — never sticky ERROR for a flap (D8).
            factory_->abort_topic_creation(route.desired.route_name, topic_name,
                                           topic.entity_generation);
            topic.topic_state = RouterRouteTopicState::TOPIC_IDLE;
            topic.entity_generation = 0; // invalidates any in-flight completion (D23)
        }
        break;
    case RouterRouteTopicState::TOPIC_FORWARDING:
        if (d != RouterRouteDiscoveryState::DISCOVERY_READY) {
            // Required endpoint lost while forwarding: event-bounded teardown (D2/D11).
            topic.topic_state = RouterRouteTopicState::TOPIC_TEARING_DOWN;
            factory_->teardown_topic_entities(route.desired.route_name, topic_name,
                                              topic.entity_generation);
        }
        break;
    case RouterRouteTopicState::TOPIC_TEARING_DOWN:
        break; // wait for TopicTeardownComplete
    case RouterRouteTopicState::TOPIC_ERROR:
        break; // sticky until command re-arm (D2/D11)
    }
}

const RouterRouteTopicSpec *RouterController::find_topic_spec(
        const RouteState &route, const std::string &topic_name) const {
    for (size_t i = 0; i < route.desired.topics.size(); ++i) {
        if (route.desired.topics.at(i).name == topic_name) {
            return &route.desired.topics.at(i);
        }
    }
    return NULL;
}

// --- Snapshot + revision predicate (D5/D25/D26) ---

std::vector<std::string> RouterController::fingerprints() const {
    std::vector<std::string> out;
    for (std::map<std::string, RouteState>::const_iterator it = state_.routes.begin();
         it != state_.routes.end(); ++it) {
        out.push_back(route_fingerprint(it->second));
    }
    return out;
}

void RouterController::publish_if_changed(const std::vector<std::string> &pre) {
    std::vector<std::string> post = fingerprints();
    bool changed = false;
    size_t i = 0;
    // First pass: did any route's externally visible state change (D5 predicate)?
    for (std::map<std::string, RouteState>::iterator it = state_.routes.begin();
         it != state_.routes.end(); ++it, ++i) {
        if (post[i] != pre[i]) {
            changed = true;
        }
    }
    if (!changed) {
        return; // duplicate/idempotent/no-op events: no bump, no publish
    }
    ++state_.state_revision;
    i = 0;
    for (std::map<std::string, RouteState>::iterator it = state_.routes.begin();
         it != state_.routes.end(); ++it, ++i) {
        if (post[i] != pre[i]) {
            it->second.state_revision = state_.state_revision;
            it->second.caused_by_command_id = current_cause_; // empty unless command (D8)
        }
    }
    status_->publish(build_snapshot());
}

std::shared_ptr<const RouterStatus> RouterController::build_snapshot() const {
    // The generated type IS the snapshot (D25).
    std::shared_ptr<RouterStatus> s(new RouterStatus());
    s->target_node = state_.node_name;
    s->target_router = state_.router_name;
    s->router_id = state_.router_id;
    s->status_id = state_.status_id;
    s->state_revision = state_.state_revision;
    s->caused_by_command_id = current_cause_;

    for (std::map<std::string, ParticipantState>::const_iterator p =
                 state_.participants.begin();
         p != state_.participants.end(); ++p) {
        RouterParticipantStatus ps;
        ps.name = p->second.name;
        ps.domain = p->second.domain;
        ps.participant_partition = p->second.participant_partition;
        ps.qos_profile_alias = p->second.qos_profile_alias;
        s->participants.push_back(ps);
    }

    for (std::map<std::string, RouteState>::const_iterator r = state_.routes.begin();
         r != state_.routes.end(); ++r) {
        const RouteState &route = r->second;
        RouterRouteStatus rs;
        rs.route_name = route.desired.route_name;
        rs.desired = route.desired;
        rs.state = derive_operational(route);
        rs.discovery_state = derive_route_discovery(route);
        rs.state_revision = route.state_revision;
        rs.caused_by_command_id = route.caused_by_command_id;
        rs.last_error = route.last_error;
        rs.samples_forwarded = 0;
        rs.lifecycle_events_forwarded = 0;
        for (size_t i = 0; i < route.desired.topics.size(); ++i) {
            const RouterRouteTopicSpec &spec = route.desired.topics.at(i);
            std::map<std::string, TopicRouteState>::const_iterator t =
                    route.topics.find(spec.name);
            if (t == route.topics.end()) {
                continue;
            }
            RouterRouteTopicStatus ts;
            ts.name = spec.name;
            ts.discovery_state = derive_topic_discovery(t->second, spec);
            ts.topic_state = t->second.topic_state;
            ts.samples_forwarded = t->second.samples_forwarded;
            ts.lifecycle_events_forwarded = t->second.lifecycle_events_forwarded;
            ts.last_error = t->second.last_error;
            rs.topic_status.push_back(ts);
            rs.samples_forwarded += ts.samples_forwarded; // aggregates (D11)
            rs.lifecycle_events_forwarded += ts.lifecycle_events_forwarded;
        }
        s->routes.push_back(rs);
    }
    return s;
}

} // namespace router
