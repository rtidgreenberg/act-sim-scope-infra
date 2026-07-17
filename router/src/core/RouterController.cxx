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
    case RouterCommandKind::SET_ROUTE_PARTITION:       return "SET_ROUTE_PARTITION";
    }
    return "?";
}

// Internal ControllerEventKind -> wire ControllerJournalEventKind (D46: the two enums are
// 1:1 by construction, so every case maps).
ControllerJournalEventKind journal_kind(ControllerEventKind kind) {
    switch (kind) {
    case ControllerEventKind::CommandReceived:
        return ControllerJournalEventKind::JOURNAL_COMMAND_RECEIVED;
    case ControllerEventKind::PublicationDiscovered:
        return ControllerJournalEventKind::JOURNAL_PUBLICATION_DISCOVERED;
    case ControllerEventKind::SubscriptionDiscovered:
        return ControllerJournalEventKind::JOURNAL_SUBSCRIPTION_DISCOVERED;
    case ControllerEventKind::EndpointLost:
        return ControllerJournalEventKind::JOURNAL_ENDPOINT_LOST;
    case ControllerEventKind::TopicEntitiesReady:
        return ControllerJournalEventKind::JOURNAL_TOPIC_ENTITIES_READY;
    case ControllerEventKind::TopicTeardownComplete:
        return ControllerJournalEventKind::JOURNAL_TOPIC_TEARDOWN_COMPLETE;
    case ControllerEventKind::RouteEntityError:
        return ControllerJournalEventKind::JOURNAL_ROUTE_ENTITY_ERROR;
    case ControllerEventKind::TopicQosWarning:
        return ControllerJournalEventKind::JOURNAL_TOPIC_QOS_WARNING;
    case ControllerEventKind::TopicMatchChanged:
        return ControllerJournalEventKind::JOURNAL_TOPIC_MATCH_CHANGED;
    case ControllerEventKind::TypeResolved:
        return ControllerJournalEventKind::JOURNAL_TYPE_RESOLVED;
    case ControllerEventKind::RefreshCounters:
    case ControllerEventKind::PresenceTick:
        break; // never journaled (D63/D71/D75 — process_one skips the ticks); fall through
    }
    return ControllerJournalEventKind::JOURNAL_COMMAND_RECEIVED; // unreachable
}

} // namespace

RouterController::RouterController(const RouterIdentityInfo &identity,
                                   const std::vector<RouterRouteSpec> &route_specs,
                                   const std::vector<ParticipantState> &participants,
                                   IEntityFactory *entity_factory,
                                   IStatusPublisher *status_publisher,
                                   IControllerJournal *journal,
                                   IPresencePublisher *presence)
        : factory_(entity_factory),
          status_(status_publisher),
          journal_(journal),
          journal_sequence_(0),
          presence_(presence),
          node_role_(identity.node_role) {
    state_.node_name = identity.node_name;
    state_.router_name = identity.router_name;
    state_.config_hash = identity.config_hash;
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

void RouterController::activate() {
    // Create-and-observe (D64/D66): every startup-enabled route builds its entities now
    // — nothing gates on discovery. Runs through the same fingerprint/publish/journal
    // path as a drained event so the resulting CREATING states are published normally.
    std::vector<std::string> pre = fingerprints();
    current_cause_.clear();
    for (std::map<std::string, RouteState>::iterator it = state_.routes.begin();
         it != state_.routes.end(); ++it) {
        if (it->second.desired.desired_enabled) {
            reconcile_route(it->second);
        }
    }
    publish_if_changed(pre);
}

void RouterController::post(const ControllerEvent &event) {
    queue_.post(event);
}

void RouterController::republish_status() {
    status_->publish(build_snapshot());
}

void RouterController::drain() {
    std::vector<ControllerEvent> events = queue_.drain();
    for (size_t i = 0; i < events.size(); ++i) {
        process_one(events[i]);
    }
}

void RouterController::wait_and_drain(std::chrono::milliseconds timeout) {
    std::vector<ControllerEvent> events = queue_.wait_and_drain(timeout);
    for (size_t i = 0; i < events.size(); ++i) {
        process_one(events[i]);
    }
}

void RouterController::process_one(const ControllerEvent &event) {
    // The RefreshCounters tick (D63/D71) only ever touches fields the D5 fingerprint
    // deliberately excludes, so the fingerprint/publish-if-changed/journal machinery below
    // is guaranteed to be a no-op for it every single time — skip straight to the handler,
    // which owns its own (revision-less) publish decision. This also means it's never
    // journaled: at 1 Hz it would evict the journal's bounded KEEP_LAST history (~4 min at
    // depth 256) with pure no-op records.
    if (event.kind == ControllerEventKind::RefreshCounters) {
        apply_refresh_counters();
        return;
    }
    // The PresenceTick (Phase 8, D75) is the same shape: pure telemetry off controller
    // state, never a revision bump, never journaled.
    if (event.kind == ControllerEventKind::PresenceTick) {
        apply_presence_tick();
        return;
    }
    std::vector<std::string> pre = fingerprints();
    std::uint64_t pre_revision = state_.state_revision;
    current_cause_.clear();
    process(event);
    publish_if_changed(pre);
    // Debug journal (D55): one record per processed event, after the status publish so the
    // post-revision reflects any bump. Skipped entirely when no journal is attached.
    if (journal_ != nullptr) {
        journal_->record(build_journal_record(event, pre_revision));
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
    case ControllerEventKind::TopicQosWarning:
        apply_qos_warning(event);
        break;
    case ControllerEventKind::TopicMatchChanged:
        apply_match_changed(event);
        break;
    case ControllerEventKind::TypeResolved:
        apply_type_resolved(event);
        break;
    case ControllerEventKind::RefreshCounters:
    case ControllerEventKind::PresenceTick:
        break; // handled directly by process_one() before ever reaching here (D63/D71/D75)
    }
}

ControllerJournalRecord RouterController::build_journal_record(
        const ControllerEvent &event, std::uint64_t pre_revision) {
    ControllerJournalRecord rec;
    rec.target_node = state_.node_name;
    rec.target_router = state_.router_name;
    rec.status_id = state_.status_id;
    rec.event_sequence = ++journal_sequence_;
    rec.timestamp_unix_nanos = static_cast<std::int64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
    rec.event_kind = journal_kind(event.kind);
    rec.pre_state_revision = pre_revision;
    rec.post_state_revision = state_.state_revision;
    rec.state_changed = (state_.state_revision != pre_revision);

    switch (event.kind) {
    case ControllerEventKind::CommandReceived: {
        rec.command_id = event.command.command_id;
        rec.route_name = event.command.route_name;
        // A processed command ALWAYS has its ack cached by command_id (D4) — every
        // handle_command path caches one, for both fresh and duplicate-replay commands — and
        // that ack IS the decision. If it were somehow absent, decision stays empty (a
        // truthful "unknown") rather than a fabricated outcome.
        std::map<std::string, RouterCommandAck>::const_iterator it =
                state_.ack_by_command_id.find(event.command.command_id);
        if (it != state_.ack_by_command_id.end()) {
            rec.decision = it->second.accepted ? "accepted" : "rejected";
            rec.reason = it->second.message;
        }
        break;
    }
    case ControllerEventKind::PublicationDiscovered:
    case ControllerEventKind::SubscriptionDiscovered:
    case ControllerEventKind::EndpointLost:
        rec.endpoint_guid = event.endpoint.guid;
        rec.decision = rec.state_changed ? "state_updated" : "no_change";
        break;
    case ControllerEventKind::TopicEntitiesReady:
    case ControllerEventKind::TopicTeardownComplete:
        rec.route_name = event.route_name;
        rec.topic_name = event.topic_name;
        rec.entity_generation = event.entity_generation;
        rec.decision = rec.state_changed ? "state_updated" : "no_change";
        break;
    case ControllerEventKind::TopicQosWarning:
        // Distinct decision (not the generic state_updated/no_change): a QoS warning is the
        // exact diagnostic the journal exists to surface, so it shouldn't read as a no-op.
        rec.route_name = event.route_name;
        rec.topic_name = event.topic_name;
        rec.entity_generation = event.entity_generation;
        rec.decision = "qos_warning";
        rec.reason = event.qos_warning;
        break;
    case ControllerEventKind::RouteEntityError:
        rec.route_name = event.route_name;
        rec.topic_name = event.topic_name;
        rec.entity_generation = event.entity_generation;
        rec.decision = "error";
        rec.reason = event.error;
        break;
    case ControllerEventKind::TopicMatchChanged:
        rec.route_name = event.route_name;
        rec.topic_name = event.topic_name;
        rec.entity_generation = event.entity_generation;
        rec.decision = rec.state_changed ? "state_updated" : "no_change";
        rec.reason = std::string(event.input_side ? "input:" : "output:")
                + std::to_string(event.matched_count);
        break;
    case ControllerEventKind::TypeResolved:
        rec.topic_name = event.topic_name;
        rec.decision = rec.state_changed ? "state_updated" : "no_change";
        break;
    case ControllerEventKind::RefreshCounters:
    case ControllerEventKind::PresenceTick:
        break; // unreachable — process_one never journals the ticks (D63/D71/D75)
    }

    // Status is published iff externally-visible state changed (publish_if_changed) — so
    // that IS the side effect this event triggered.
    rec.action = rec.state_changed ? "status_published" : "none";
    return rec;
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
    case RouterCommandKind::SET_ROUTE_PARTITION:
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
    } else if (cmd.kind == RouterCommandKind::DISABLE_ROUTE) {
        handle_disable(cmd, ack);
    } else {
        handle_set_route_partition(cmd, ack);
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
            it->second.clear_entity_facts();
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
            topic.clear_entity_facts();
            break;
        case RouterRouteTopicState::TOPIC_ERROR:
            topic.topic_state = RouterRouteTopicState::TOPIC_IDLE;
            topic.entity_generation = 0;
            topic.clear_entity_facts();
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

// Runtime per-route partition change (7b/D69). The command's embedded
// route.input.subscriber_partition / route.output.publisher_partition become the
// route's desired values (empty = default partition — callers read the current values
// off the status desired spec). Live builds are adjusted IN PLACE via pub/sub set_qos
// (D15: runtime-mutable, automatic rematch — no rebuild, no teardown); the rematch is
// observable as the D66/D67 matched counts moving. The RouteView is re-minted so any
// FUTURE build (re-enable, re-arm) uses the new spec.
void RouterController::handle_set_route_partition(const RouterCommand &cmd,
                                                  RouterCommandAck &ack) {
    RouteState &route = state_.routes[cmd.route_name];
    const std::string &new_sub = cmd.route.input.subscriber_partition;
    const std::string &new_pub = cmd.route.output.publisher_partition;

    if (route.desired.input.subscriber_partition == new_sub
        && route.desired.output.publisher_partition == new_pub) {
        // Redundant command, new command_id: idempotent accept, no state change (D8).
        ack.accepted = true;
        ack.message = "partition unchanged";
        return;
    }

    route.desired.input.subscriber_partition = new_sub;
    route.desired.output.publisher_partition = new_pub;

    // Re-mint the immutable view with a fresh stamp (D23) so future builds read the
    // updated spec. Live builds keep their generation — they are adjusted, not rebuilt.
    std::shared_ptr<RouteView> view(new RouteView());
    view->spec = route.desired;
    view->entity_generation = next_generation();
    route.view = view;

    for (std::map<std::string, TopicRouteState>::iterator t = route.topics.begin();
         t != route.topics.end(); ++t) {
        RouterRouteTopicState st = t->second.topic_state;
        if (st != RouterRouteTopicState::TOPIC_CREATING
            && st != RouterRouteTopicState::TOPIC_FORWARDING) {
            continue; // no live entities; the next build uses the new view
        }
        if (!factory_->update_route_partitions(cmd.route_name, t->first, new_sub,
                                               new_pub)) {
            // Log-only: a racing teardown means the next build applies the new spec
            // from the re-minted view anyway.
            Log::warn("route_partition_update_failed",
                      {{"route", cmd.route_name}, {"topic", t->first}});
        }
    }

    ack.accepted = true;
    ack.message = "partition updated";
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

// --- Discovery (raw records — DEMOTED by D64/D66 to derivation/diagnosis input: the
// maps feed writer-QoS derivation + deadline tightening + the type-conflict warning;
// they no longer gate creation or drive teardown, so no reconcile here) ---

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
        entry.deadline_nanos = rec.deadline_nanos;
        entry.liveliness_kind = rec.liveliness_kind;
        entry.lease_nanos = rec.lease_nanos;
        maybe_tighten_deadline(r->second, t->second, rec.topic_name);
    }
}

// A later local reader with a tighter deadline than the live build's offer is
// accommodated in place via set_qos — no entity recreation, no D32 teardown (D39).
// Only meaningful for auto-output routes on a FORWARDING build; a build still CREATING
// is re-checked when its TopicEntitiesReady lands (apply_entities_ready).
void RouterController::maybe_tighten_deadline(RouteState &route, TopicRouteState &topic,
                                              const std::string &topic_name) {
    if (!output_uses_auto_qos(route.desired)
        || topic.topic_state != RouterRouteTopicState::TOPIC_FORWARDING) {
        return;
    }
    DerivedWriterQos d = derive_writer_qos(topic, route.desired);
    if (d.deadline_nanos >= topic.offered_deadline_nanos) {
        return; // never relax; equal is a no-op
    }
    std::string summary = factory_->update_writer_deadline(
            route.desired.route_name, topic_name, d.deadline_nanos);
    if (summary.empty()) {
        Log::warn("deadline_tighten_failed",
                  {{"route", route.desired.route_name}, {"topic", topic_name}});
        topic.qos_warning = "writer:DEADLINE(update-failed)";
        return;
    }
    Log::info("deadline_tightened",
              {{"route", route.desired.route_name}, {"topic", topic_name},
               {"deadline", summary}});
    topic.offered_deadline_nanos = d.deadline_nanos;
    topic.writer_qos_summary = summary;
    // A transient DEADLINE mismatch warning recorded before the tighten is now stale.
    if (topic.qos_warning == "writer:DEADLINE") {
        topic.qos_warning.clear();
    }
}

void RouterController::apply_endpoint_lost(const std::string &guid) {
    // Record-map hygiene only (D64/D66): losing a builtin record never tears anything
    // down — the live build's own matched counts (TopicMatchChanged) are the
    // connectivity truth, and an unmatched entity persists as an observable zero.
    for (std::map<std::string, RouteState>::iterator r = state_.routes.begin();
         r != state_.routes.end(); ++r) {
        for (std::map<std::string, TopicRouteState>::iterator t =
                     r->second.topics.begin();
             t != r->second.topics.end(); ++t) {
            t->second.matched_writers.erase(guid);
            t->second.matched_readers.erase(guid);
        }
    }
}

// A topic's DynamicType was learned from the wire (7c, D64/D70): open the creation gate
// on every route carrying the topic and reconcile — an enabled waiting topic builds now.
// First-learned-wins (D66): the flag never clears, so this is naturally idempotent.
void RouterController::apply_type_resolved(const ControllerEvent &e) {
    for (std::map<std::string, RouteState>::iterator r = state_.routes.begin();
         r != state_.routes.end(); ++r) {
        std::map<std::string, TopicRouteState>::iterator t =
                r->second.topics.find(e.topic_name);
        if (t == r->second.topics.end()) {
            continue;
        }
        t->second.type_available = true;
        reconcile_topic(r->second, e.topic_name);
    }
}

// Matched-count change from a live build's own entity statuses (D64/D66) — the
// discovery truth. Exact-stamp gated like every completion (D23); a stale event from a
// torn-down build is discarded.
void RouterController::apply_match_changed(const ControllerEvent &e) {
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
    if (e.entity_generation != topic.entity_generation) {
        return; // stale stamp (D23)
    }
    if (e.input_side) {
        topic.input_matched_count = e.matched_count;
    } else {
        topic.output_matched_count = e.matched_count;
    }
}

// The D63 counter path (7d): pull every live build's forwarded() count into
// TopicRouteState, and if any counter moved, republish RouterStatus WITHOUT bumping
// state_revision — the one sanctioned exception to "status publishes only on revision
// change" (D5 stays authoritative for what changes revision; the fingerprint excludes
// counters, so publish_if_changed in process_one stays a no-op for this event). The
// pull is strand-confined: forwarded_count is synchronous on the controller strand,
// reading the runtime's relaxed atomic — exact-at-tick sampling is sufficient.
void RouterController::apply_refresh_counters() {
    bool any_changed = false;
    for (std::map<std::string, RouteState>::iterator r = state_.routes.begin();
         r != state_.routes.end(); ++r) {
        for (std::map<std::string, TopicRouteState>::iterator t =
                     r->second.topics.begin();
             t != r->second.topics.end(); ++t) {
            TopicRouteState &topic = t->second;
            // FORWARDING is the only state with a live runtime behind forwarded_count():
            // entity_generation alone isn't a safe guard here, since it stays non-zero
            // through TEARING_DOWN even though handle_disable() already tore the runtime
            // down synchronously — pulling on generation alone would read a stale zero
            // from the gone runtime before TopicTeardownComplete resets the count for real.
            if (topic.topic_state != RouterRouteTopicState::TOPIC_FORWARDING) {
                continue;
            }
            std::uint64_t count =
                    factory_->forwarded_count(r->first, t->first);
            if (count != topic.samples_forwarded) {
                topic.samples_forwarded = count;
                any_changed = true;
            }
        }
    }
    if (any_changed) {
        republish_status(); // same state_revision, fresh counters
    }
}

void RouterController::apply_presence_tick() {
    // Phase 8 (D75): build the compact RouterHealth summary from controller state on
    // the strand and hand it to the presence publisher. Pure telemetry — reads state_,
    // never mutates it (heartbeat_sequence_ is presence-local, outside the D5
    // fingerprint by construction).
    if (presence_ == nullptr) {
        return;
    }
    RouterHealth hb;
    hb.router = state_.node_name + "/" + state_.router_name; // D74/D79 name-only key
    hb.role = node_role_;
    hb.config_hash = state_.config_hash; // D80 drift detection
    hb.heartbeat_seq = ++heartbeat_sequence_;
    hb.send_timestamp = static_cast<std::int64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(
                    std::chrono::system_clock::now().time_since_epoch()).count());
    hb.state_revision = state_.state_revision;
    std::uint32_t degraded = 0;
    std::uint32_t error = 0;
    for (std::map<std::string, RouteState>::const_iterator r = state_.routes.begin();
         r != state_.routes.end(); ++r) {
        switch (derive_operational(r->second)) {
        case RouterRouteOperationalState::ROUTE_DEGRADED: ++degraded; break;
        case RouterRouteOperationalState::ROUTE_ERROR:    ++error;    break;
        default: break;
        }
    }
    hb.n_routes = static_cast<std::uint32_t>(state_.routes.size());
    hb.n_degraded = degraded;
    hb.n_error = error;
    hb.overall_state = (error > 0) ? RouterOverallState::ROUTER_ERROR
                     : (degraded > 0) ? RouterOverallState::ROUTER_DEGRADED
                                      : RouterOverallState::ROUTER_OK;
    presence_->publish_heartbeat(hb);
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
    topic.reader_qos_summary = e.reader_qos_summary;
    topic.writer_qos_summary = e.writer_qos_summary;
    // A reader that arrived while the build was in flight may request a tighter
    // deadline than the derivation the build was issued with — re-check now (D39).
    maybe_tighten_deadline(r->second, topic, e.topic_name);
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
    topic.clear_entity_facts();
    // Teardown complete: rebuild immediately if the route is (still or again) enabled —
    // e.g. an ENABLE that landed mid-teardown (D64/D66: creation gates on nothing).
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
    // Exact-stamp match only. In particular a zeroed generation (abort / teardown
    // complete / re-arm) discards any error still in flight from the invalidated build —
    // no `!= 0` escape hatch, or an aborted build's late error would force a topic that
    // legitimately returned to IDLE into sticky ERROR (D23/D41). Errors on a live build
    // (CREATING or a runtime fault while FORWARDING) carry the current stamp and apply.
    if (e.entity_generation != topic.entity_generation) {
        Log::warn("stale_entity_error",
                  {{"route", e.route_name}, {"topic", e.topic_name}});
        return; // stale stamp (D23)
    }
    // Contained per-topic failure: sibling topics unaffected (D11/D21).
    topic.topic_state = RouterRouteTopicState::TOPIC_ERROR;
    topic.entity_generation = 0;
    topic.last_error = e.error;
    topic.clear_entity_facts();
}

// Incompatible-QoS status on a live build's entity (D39/D45): record the failing policy
// as a warning — status reason only, the topic keeps forwarding for whatever DOES match.
// Exact-stamp gated like errors, so a warning from an invalidated build is discarded.
void RouterController::apply_qos_warning(const ControllerEvent &e) {
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
    if (e.entity_generation != topic.entity_generation) {
        return; // stale stamp (D23)
    }
    // A DEADLINE mismatch the tightening already resolved can still be in flight from
    // the dispatch thread (status fired before set_qos landed) — drop it rather than
    // record a warning that no longer describes the offer.
    if (e.qos_warning == "writer:DEADLINE") {
        DerivedWriterQos d = derive_writer_qos(topic, r->second.desired);
        if (d.deadline_nanos >= topic.offered_deadline_nanos) {
            return;
        }
    }
    topic.qos_warning = e.qos_warning;
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
    const RouterRouteTopicSpec *spec = find_topic_spec(route.desired, topic_name);
    std::map<std::string, TopicRouteState>::iterator t = route.topics.find(topic_name);
    if (spec == NULL || t == route.topics.end()) {
        return;
    }
    TopicRouteState &topic = t->second;

    // Create-and-observe (D64/D66/D70): an enabled IDLE topic builds as soon as its
    // type is known — the ONLY creation gate is wait-for-wire-type (7c); nothing gates
    // on matching. Live builds are never reconciled against discovery: an unmatched
    // entity persists as an observable zero (the D2 regression-abort and
    // regression-teardown edges are retired); teardown is command/error-driven only.
    switch (topic.topic_state) {
    case RouterRouteTopicState::TOPIC_IDLE: {
        if (!topic.type_available) {
            break; // wait for TypeResolved (7c, D70) — the honest wait state
        }
        topic.entity_generation = next_generation(); // D23 stamp at build
        topic.topic_state = RouterRouteTopicState::TOPIC_CREATING;
        // Writer-side derivation from the readers currently known via builtin discovery
        // (D39/D42, best-effort input — possibly none, then the strong baseline); the
        // offer is remembered so later readers can tighten the deadline in place.
        DerivedWriterQos derived = derive_writer_qos(topic, route.desired);
        topic.offered_deadline_nanos = derived.deadline_nanos;
        factory_->create_topic_entities(*route.view, topic_name,
                                        topic.entity_generation, derived);
        break;
    }
    case RouterRouteTopicState::TOPIC_CREATING:
    case RouterRouteTopicState::TOPIC_FORWARDING:
    case RouterRouteTopicState::TOPIC_TEARING_DOWN: // wait for TopicTeardownComplete
    case RouterRouteTopicState::TOPIC_ERROR:        // sticky until command re-arm (D2/D11)
        break;
    }
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
            ts.discovery_state = derive_topic_discovery(t->second, route.desired);
            ts.topic_state = t->second.topic_state;
            ts.samples_forwarded = t->second.samples_forwarded;
            ts.lifecycle_events_forwarded = t->second.lifecycle_events_forwarded;
            ts.last_error = t->second.last_error;
            ts.reader_qos_summary = t->second.reader_qos_summary;
            ts.writer_qos_summary = t->second.writer_qos_summary;
            ts.qos_warning = t->second.qos_warning;
            ts.input_matched = static_cast<std::uint32_t>(
                    t->second.input_matched_count > 0 ? t->second.input_matched_count
                                                      : 0);
            ts.output_matched = static_cast<std::uint32_t>(
                    t->second.output_matched_count > 0 ? t->second.output_matched_count
                                                       : 0);
            ts.match_reason = derive_match_reason(t->second);
            rs.topic_status.push_back(ts);
            rs.samples_forwarded += ts.samples_forwarded; // aggregates (D11)
            rs.lifecycle_events_forwarded += ts.lifecycle_events_forwarded;
        }
        s->routes.push_back(rs);
    }
    return s;
}

} // namespace router
