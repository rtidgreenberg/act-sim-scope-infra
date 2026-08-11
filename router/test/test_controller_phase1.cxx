// test_controller_phase1.cxx — transition-table conformance for the controller-owned
// state machine, driven entirely by synthetic ControllerEvents against faked seams (D3).
// Contract: D1-D11, D21-D26, migrated to create-and-observe by D64/D66/D70 — the only
// creation gate is wait-for-wire-type (TypeResolved opens it; activate()/ENABLE build
// once the type is known), DDS is the matching authority (TopicMatchChanged carries the
// live build's matched counts; the regression-abort and regression-teardown edges are
// retired), and builtin-discovery records are demoted to writer-QoS-derivation/diagnosis
// input.
//
// The fake EntityFactory records operations but never completes them itself — tests post
// TopicEntitiesReady / TopicTeardownComplete with the recorded generation stamp, the same
// pending-completion seam Phase 3's real async entity creation uses (D8/D21).

#include "core/RouterController.hpp"

#include <cstdio>
#include <map>
#include <sstream>
#include <string>
#include <vector>

using namespace router;

static int g_failures = 0;
static const char *g_test = "";

#define CHECK(cond)                                                                      \
    do {                                                                                 \
        if (!(cond)) {                                                                   \
            std::fprintf(stderr, "FAIL [%s] %s:%d  %s\n", g_test, __FILE__, __LINE__,    \
                         #cond);                                                         \
            ++g_failures;                                                                \
        }                                                                                \
    } while (0)

#define RUN(fn)                                                                          \
    do {                                                                                 \
        g_test = #fn;                                                                    \
        fn();                                                                            \
    } while (0)

// --- Fakes (D3) ---

struct FakeEntityFactory : IEntityFactory {
    struct Op {
        std::string route, topic;
        std::uint64_t gen;
        DerivedWriterQos derived; // creates only (D39/D45)
        std::int64_t deadline_nanos = 0; // deadline updates only
        std::string sub_partition, pub_partition; // partition updates + creates (7b/D69)
    };
    std::vector<Op> creates, teardowns, aborts, deadline_updates, partition_updates;
    bool fail_deadline_update = false;
    // Counter the D63 RefreshCounters pull samples, keyed "route|topic" (7d).
    std::map<std::string, std::uint64_t> forwarded_counts;

    // D83: participant-level partition apply calls, keyed by participant name -> the
    // full name set passed. fail_participant_partition_apply models a live-entity
    // failure (e.g. participant not found) for the controller's rollback path.
    std::map<std::string, std::vector<std::string> > participant_partition_applies;
    bool fail_participant_partition_apply = false;

    void create_topic_entities(const RouteView &view, const std::string &topic,
                               std::uint64_t gen,
                               const DerivedWriterQos &derived) override {
        Op op;
        op.route = view.spec.route_name;
        op.topic = topic;
        op.gen = gen;
        op.derived = derived;
        op.sub_partition = view.spec.input.subscriber_partition;
        op.pub_partition = view.spec.output.publisher_partition;
        creates.push_back(op);
    }
    void teardown_topic_entities(const std::string &route, const std::string &topic,
                                 std::uint64_t gen) override {
        Op op;
        op.route = route;
        op.topic = topic;
        op.gen = gen;
        teardowns.push_back(op);
    }
    void abort_topic_creation(const std::string &route, const std::string &topic,
                              std::uint64_t gen) override {
        Op op;
        op.route = route;
        op.topic = topic;
        op.gen = gen;
        aborts.push_back(op);
    }
    std::string update_writer_deadline(const std::string &route,
                                       const std::string &topic,
                                       std::int64_t deadline_nanos) override {
        Op op;
        op.route = route;
        op.topic = topic;
        op.gen = 0;
        op.deadline_nanos = deadline_nanos;
        deadline_updates.push_back(op);
        return fail_deadline_update ? std::string() : "RELIABLE,TRANSIENT_LOCAL,updated";
    }
    bool update_route_partitions(const std::string &route, const std::string &topic,
                                 const std::string &subscriber_partition,
                                 const std::string &publisher_partition) override {
        Op op;
        op.route = route;
        op.topic = topic;
        op.gen = 0;
        op.sub_partition = subscriber_partition;
        op.pub_partition = publisher_partition;
        partition_updates.push_back(op);
        return true;
    }
    std::uint64_t forwarded_count(const std::string &route,
                                  const std::string &topic) const override {
        std::map<std::string, std::uint64_t>::const_iterator it =
                forwarded_counts.find(route + "|" + topic);
        return it == forwarded_counts.end() ? 0 : it->second;
    }
    bool apply_participant_partition(const std::string &participant_name,
                                     const std::vector<std::string> &names) override {
        if (fail_participant_partition_apply) {
            return false;
        }
        participant_partition_applies[participant_name] = names;
        return true;
    }
};

struct FakeStatusPublisher : IStatusPublisher {
    std::vector<std::shared_ptr<const RouterStatus> > snapshots;
    std::vector<RouterCommandAck> acks;

    void publish(std::shared_ptr<const RouterStatus> snapshot) override {
        snapshots.push_back(snapshot);
    }
    void publish_ack(const RouterCommandAck &ack) override { acks.push_back(ack); }

    const RouterStatus &last() const { return *snapshots.back(); }
    const RouterCommandAck &last_ack() const { return acks.back(); }
};

struct FakePresencePublisher : IPresencePublisher {
    std::vector<RouterHealth> heartbeats;

    void publish_heartbeat(const RouterHealth &heartbeat) override {
        heartbeats.push_back(heartbeat);
    }
    void publish_mesh_tick() override {}
};

// --- Fixture helpers ---

static RouterRouteTopicSpec topic_spec(const std::string &name) {
    RouterRouteTopicSpec t;
    t.name = name;
    return t;
}

// QoS aliases live on the endpoint spec (D41): explicit by default, empty for auto-QoS.
static RouterRouteSpec route_spec(const std::string &name, bool enabled,
                                  const std::vector<RouterRouteTopicSpec> &topics,
                                  bool auto_qos = false) {
    RouterRouteSpec s;
    s.route_name = name;
    s.desired_enabled = enabled;
    s.forwarding_mode = "dynamic_data";
    if (!auto_qos) {
        s.input.reader_qos = "reliable_alias";
        s.output.writer_qos = "reliable_alias";
    }
    for (size_t i = 0; i < topics.size(); ++i) {
        s.topics.push_back(topics[i]);
    }
    return s;
}

static EndpointRecord writer_record(const std::string &guid, const std::string &topic,
                                    bool has_type = true,
                                    const std::string &type_name = "TheType") {
    EndpointRecord r;
    r.guid = guid;
    r.topic_name = topic;
    r.type_name = type_name;
    r.has_type = has_type;
    return r;
}

static EndpointRecord reader_record(const std::string &guid, const std::string &topic,
                                    std::int64_t deadline_nanos = kInfiniteNanos,
                                    LivelinessKindPod kind = LivelinessKindPod::Automatic,
                                    std::int64_t lease_nanos = kInfiniteNanos) {
    EndpointRecord r;
    r.guid = guid;
    r.is_publication = false;
    r.topic_name = topic;
    r.deadline_nanos = deadline_nanos;
    r.liveliness_kind = kind;
    r.lease_nanos = lease_nanos;
    return r;
}

static RouterCommand command(RouterCommandKind kind, const std::string &id,
                             const std::string &route = "") {
    RouterCommand c;
    c.command_id = id;
    c.kind = kind;
    c.route_name = route;
    return c;
}

struct Fixture {
    FakeEntityFactory factory;
    FakeStatusPublisher status;
    RouterController controller;

    explicit Fixture(const std::vector<RouterRouteSpec> &specs)
            : controller(identity(), specs, participants(), &factory, &status) {}
    Fixture(const std::vector<RouterRouteSpec> &specs,
           const std::vector<ParticipantState> &parts)
            : controller(identity(), specs, parts, &factory, &status) {}

    static RouterIdentityInfo identity() {
        RouterIdentityInfo id;
        id.node_name = "Platform_30";
        id.router_name = "control-platform";
        id.status_id = "status-test";
        return id;
    }
    static std::vector<ParticipantState> participants() {
        ParticipantState p;
        p.name = "platform_lan";
        p.domain = 100;
        p.qos_profile_alias = "lan_default";
        std::vector<ParticipantState> v;
        v.push_back(p);
        return v;
    }
    // D83: a team-scoped participant already carrying its protected node-identity entry,
    // exactly as RouteConfigParser would seed it — used only by the participant-partition
    // tests so the default participants() (and its participants.size()==1 assertion) stays
    // untouched for every other test.
    static std::vector<ParticipantState> participants_with_team_wan() {
        std::vector<ParticipantState> v = participants();
        ParticipantState wan;
        wan.name = "team_wan";
        wan.domain = 200;
        wan.team_scoped = true;
        wan.participant_partition.push_back(identity().node_name); // protected identity
        v.push_back(wan);
        return v;
    }
    // D103: a participant carrying a protected_partition_entries wildcard, NOT
    // team_scoped — mirrors control_wan's standing "*" post-D103, exercising the
    // protected-entry path independently of D83's node-identity case.
    static std::vector<ParticipantState> participants_with_protected_wildcard() {
        std::vector<ParticipantState> v = participants();
        ParticipantState wan;
        wan.name = "control_wan";
        wan.domain = 200;
        wan.protected_partition_entries.push_back("*");
        wan.participant_partition.push_back("*"); // as RouteConfigParser auto-seeds it
        v.push_back(wan);
        return v;
    }

    void activate() { controller.activate(); }
    void post(const ControllerEvent &e) {
        controller.post(e);
        controller.drain();
    }
    const RouterRouteStatus &route(const std::string &name) const {
        const RouterStatus &s = status.last();
        for (size_t i = 0; i < s.routes.size(); ++i) {
            if (s.routes.at(i).route_name == name) {
                return s.routes.at(i);
            }
        }
        std::fprintf(stderr, "FAIL [%s] route %s missing from snapshot\n", g_test,
                     name.c_str());
        ++g_failures;
        static RouterRouteStatus dummy;
        return dummy;
    }
    std::uint64_t revision() const { return status.last().state_revision; }
    std::uint64_t last_create_gen() const { return factory.creates.back().gen; }
};

// Drive one enabled single-topic route to FORWARDING: the topic's type arrives from the
// wire (7c/D70 — the only creation gate), activate builds, the completion event lands.
static std::uint64_t drive_to_forwarding(Fixture &f, const std::string &route,
                                         const std::string &topic) {
    f.post(ControllerEvent::type_resolved(topic));
    f.activate();
    std::uint64_t gen = f.last_create_gen();
    f.post(ControllerEvent::topic_entities_ready(route, topic, gen));
    return gen;
}

// --- Tests ---

// Startup snapshot at revision 0: disabled and enabled routes both visible before any
// entity exists; the enabled route waits for its wire type (7c/D70), then builds.
static void test_startup_snapshot_then_activate() {
    std::vector<RouterRouteSpec> specs;
    specs.push_back(route_spec("off_route", false,
                               std::vector<RouterRouteTopicSpec>(1, topic_spec("T1"))));
    specs.push_back(route_spec("on_route", true,
                               std::vector<RouterRouteTopicSpec>(1, topic_spec("T2"))));
    Fixture f(specs);

    CHECK(f.status.snapshots.size() == 1);
    CHECK(f.revision() == 0);
    CHECK(f.status.last().target_node == "Platform_30");
    CHECK(f.status.last().participants.size() == 1);
    CHECK(f.route("off_route").state == RouterRouteOperationalState::ROUTE_DISABLED);
    CHECK(f.route("on_route").state
          == RouterRouteOperationalState::ROUTE_WAITING_FOR_DISCOVERY);
    CHECK(f.route("on_route").topic_status.at(0).topic_state
          == RouterRouteTopicState::TOPIC_IDLE);
    CHECK(f.factory.creates.empty());

    // No wire type yet: activate leaves the enabled route waiting (the honest 7c gate).
    f.activate();
    CHECK(f.factory.creates.empty());
    CHECK(f.route("on_route").state
          == RouterRouteOperationalState::ROUTE_WAITING_FOR_DISCOVERY);

    // The type arrives from the wire: the waiting topic builds now.
    f.post(ControllerEvent::type_resolved("T2"));
    CHECK(f.factory.creates.size() == 1); // enabled route only
    CHECK(f.factory.creates.back().topic == "T2");
    CHECK(f.route("on_route").state == RouterRouteOperationalState::ROUTE_RESOLVING);
    CHECK(f.route("on_route").topic_status.at(0).topic_state
          == RouterRouteTopicState::TOPIC_CREATING);
    CHECK(f.route("off_route").state == RouterRouteOperationalState::ROUTE_DISABLED);
    CHECK(f.revision() == 1);

    // Duplicate TypeResolved: flag already set, build in flight — no change, no bump.
    f.post(ControllerEvent::type_resolved("T2"));
    CHECK(f.factory.creates.size() == 1);
    CHECK(f.revision() == 1);
}

// ENABLE_ROUTE creates immediately once the type is known (D64/D66/D70).
static void test_enable_creates_immediately() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", false, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);
    f.post(ControllerEvent::type_resolved("T"));
    f.activate();
    CHECK(f.factory.creates.empty()); // disabled: nothing to build

    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "c1", "r")));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.factory.creates.size() == 1);
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_RESOLVING);
    CHECK(f.route("r").caused_by_command_id == "c1");
    CHECK(f.revision() == 1);
}

// ENABLE before the type is known: accepted, route waits; the type's arrival builds it
// (7c/D70 — the wait-for-type edge in both orders).
static void test_enable_then_type_builds() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", false, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);
    f.activate();

    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "c1", "r")));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.factory.creates.empty());
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_WAITING_FOR_DISCOVERY);

    f.post(ControllerEvent::type_resolved("T"));
    CHECK(f.factory.creates.size() == 1);
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_RESOLVING);
}

// Duplicate command_id: cached ack replayed, no state change, no revision bump (D4).
static void test_duplicate_command_returns_cached_ack() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", false, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);
    f.post(ControllerEvent::type_resolved("T"));

    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "c1", "r")));
    std::uint64_t rev = f.revision();
    size_t snapshots = f.status.snapshots.size();
    std::string first_msg = f.status.last_ack().message;

    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "c1", "r")));
    CHECK(f.status.acks.size() == 2);
    CHECK(f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == first_msg);
    CHECK(f.revision() == rev);                       // no bump
    CHECK(f.status.snapshots.size() == snapshots);    // no publish
    CHECK(f.factory.creates.size() == 1);             // no second build
}

// Rejected commands are cached and replay identically (D4): unsupported kind + unknown route.
static void test_rejected_command_ack_replay() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", false, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);

    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::UPDATE_ROUTE, "u1", "r")));
    CHECK(!f.status.last_ack().accepted);
    std::string reject_msg = f.status.last_ack().message;
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::UPDATE_ROUTE, "u1", "r")));
    CHECK(!f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == reject_msg);

    // Unknown route_name: cached reject, never implicit creation (D24).
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "x1", "no_such_route")));
    CHECK(!f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == "unknown route");
    CHECK(f.revision() == 0);
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "x1", "no_such_route")));
    CHECK(f.status.last_ack().message == "unknown route");
    CHECK(f.status.acks.size() == 4);
}

// The create-and-observe walk (D64/D66): build immediately, matched counts from the
// entities' own statuses drive discovery_state; zero matches is a status reason, never a
// teardown; stale match events are discarded by stamp.
static void test_create_and_observe_walk() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);

    f.post(ControllerEvent::type_resolved("T"));
    f.activate();
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_RESOLVING);
    CHECK(f.factory.creates.size() == 1);
    std::uint64_t gen = f.last_create_gen();

    // Built but nothing matched yet: ENABLED with an observable zero (D66) — created-
    // but-unmatched is a status reason, not a state.
    f.post(ControllerEvent::topic_entities_ready("r", "T", gen));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_ENABLED);
    CHECK(f.route("r").discovery_state == RouterRouteDiscoveryState::DISCOVERY_NONE);
    CHECK(f.route("r").topic_status.at(0).input_matched == 0);
    CHECK(f.route("r").topic_status.at(0).output_matched == 0);
    CHECK(f.route("r").topic_status.at(0).match_reason
          == "input_unmatched,output_unmatched");

    // Input leg matches: PARTIAL; then output: READY, reason clears.
    f.post(ControllerEvent::topic_match_changed("r", "T", gen, /*input=*/true, 1));
    CHECK(f.route("r").discovery_state == RouterRouteDiscoveryState::DISCOVERY_PARTIAL);
    CHECK(f.route("r").topic_status.at(0).match_reason == "output_unmatched");
    f.post(ControllerEvent::topic_match_changed("r", "T", gen, /*input=*/false, 1));
    CHECK(f.route("r").discovery_state == RouterRouteDiscoveryState::DISCOVERY_READY);
    CHECK(f.route("r").topic_status.at(0).match_reason.empty());
    std::uint64_t rev = f.revision();

    // Input peer goes away: count regresses to zero, but the build PERSISTS — the
    // regression-teardown edge is retired (D66); state stays ENABLED with the reason.
    f.post(ControllerEvent::topic_match_changed("r", "T", gen, /*input=*/true, 0));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_ENABLED);
    CHECK(f.route("r").topic_status.at(0).topic_state
          == RouterRouteTopicState::TOPIC_FORWARDING);
    CHECK(f.route("r").discovery_state == RouterRouteDiscoveryState::DISCOVERY_PARTIAL);
    CHECK(f.route("r").topic_status.at(0).match_reason == "input_unmatched");
    CHECK(f.factory.teardowns.empty());
    CHECK(f.revision() == rev + 1); // counts are externally visible (D66/D5)

    // Stale match event from an invalidated build: discarded, no visible change.
    std::uint64_t rev2 = f.revision();
    f.post(ControllerEvent::topic_match_changed("r", "T", gen + 99, /*input=*/true, 7));
    CHECK(f.route("r").topic_status.at(0).input_matched == 0);
    CHECK(f.revision() == rev2);
}

// A count change that is visible in status bumps revision (D66: matched counts are
// externally visible D5 state — unlike the sample counters, which never bump).
static void test_match_count_change_bumps_revision() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);
    std::uint64_t gen = drive_to_forwarding(f, "r", "T");

    f.post(ControllerEvent::topic_match_changed("r", "T", gen, /*input=*/true, 2));
    CHECK(f.route("r").topic_status.at(0).input_matched == 2);
    std::uint64_t rev = f.revision();

    // 2 -> 1: discovery enum unchanged (still PARTIAL-by-input), but the count itself
    // is a status field, so the change publishes.
    f.post(ControllerEvent::topic_match_changed("r", "T", gen, /*input=*/true, 1));
    CHECK(f.route("r").topic_status.at(0).input_matched == 1);
    CHECK(f.revision() == rev + 1);

    // Same count re-reported: no visible change, no bump (D5 predicate).
    f.post(ControllerEvent::topic_match_changed("r", "T", gen, /*input=*/true, 1));
    CHECK(f.revision() == rev + 1);
}

// DISABLE during CREATING aborts the in-flight build; its late completion is discarded
// by stale stamp (D8/D21/D23 — the abort edge is command-driven now, D66).
static void test_disable_aborts_inflight_create() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);

    f.post(ControllerEvent::type_resolved("T"));
    f.activate();
    std::uint64_t gen = f.last_create_gen();
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_RESOLVING);

    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::DISABLE_ROUTE, "d1", "r")));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.factory.aborts.size() == 1);
    CHECK(f.factory.aborts.back().gen == gen);
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_DISABLED);

    // The aborted creation completes late: stale stamp, discarded, no state change.
    std::uint64_t rev = f.revision();
    f.post(ControllerEvent::topic_entities_ready("r", "T", gen));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_DISABLED);
    CHECK(f.revision() == rev);
}

// An aborted build's late RouteEntityError is discarded by the same stale-stamp rule as
// its late TopicEntitiesReady — a topic that legitimately returned to IDLE must not be
// forced into sticky ERROR (D23/D41); a live build's error still applies.
static void test_stale_error_after_abort_discarded() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);

    f.post(ControllerEvent::type_resolved("T"));
    f.activate();
    std::uint64_t gen = f.last_create_gen();
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::DISABLE_ROUTE, "d1", "r")));
    CHECK(f.factory.aborts.size() == 1);

    // The aborted creation fails late: stale stamp, discarded — NOT sticky ERROR.
    std::uint64_t rev = f.revision();
    f.post(ControllerEvent::route_entity_error("r", "T", gen, "create failed late"));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_DISABLED);
    CHECK(f.route("r").topic_status.at(0).topic_state == RouterRouteTopicState::TOPIC_IDLE);
    CHECK(f.revision() == rev);

    // A fresh build's error with the CURRENT stamp still lands (sticky per-topic ERROR).
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "e1", "r")));
    std::uint64_t gen2 = f.last_create_gen();
    CHECK(gen2 > gen); // one global counter, stamps never repeat (D23)
    f.post(ControllerEvent::route_entity_error("r", "T", gen2, "writer creation failed"));
    CHECK(f.route("r").topic_status.at(0).topic_state == RouterRouteTopicState::TOPIC_ERROR);
}

// Redundant ENABLE_ROUTE with a NEW command_id on an already-enabled route: idempotent
// accept, ack cached, no state change, no revision bump (D8).
static void test_redundant_enable_idempotent_accept() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);
    drive_to_forwarding(f, "r", "T");
    std::uint64_t rev = f.revision();
    size_t snapshots = f.status.snapshots.size();

    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "new-id", "r")));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == "already enabled");
    CHECK(f.revision() == rev);
    CHECK(f.status.snapshots.size() == snapshots);

    // The idempotent ack was cached (D8): replay returns it.
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "new-id", "r")));
    CHECK(f.status.last_ack().message == "already enabled");

    // Same for DISABLE on a DISABLED route.
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::DISABLE_ROUTE, "d1", "r")));
    f.post(ControllerEvent::topic_teardown_complete("r", "T",
                                                    f.factory.teardowns.back().gen));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_DISABLED);
    std::uint64_t rev2 = f.revision();
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::DISABLE_ROUTE, "d2", "r")));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == "already disabled");
    CHECK(f.revision() == rev2);
}

// Route-wide error is sticky until command re-arm (D2): neither discovery records nor
// match events move it; ENABLE_ROUTE clears last_error and re-enters the table.
static void test_error_sticky_until_rearm() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);
    f.post(ControllerEvent::type_resolved("T"));
    f.activate();
    std::uint64_t gen = f.last_create_gen();

    f.post(ControllerEvent::route_entity_error("r", "", 0, "participant lost"));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_ERROR);
    CHECK(f.route("r").last_error == "participant lost");
    CHECK(f.route("r").caused_by_command_id.empty()); // not command-caused (D8)

    // Discovery record: ERROR holds (no auto-retry), no new build.
    f.post(ControllerEvent::publication_discovered(writer_record("w1", "T")));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_ERROR);
    CHECK(f.factory.creates.size() == 1);

    // Re-arm (the only exit): last_error cleared; the in-flight build (its stamp was
    // never invalidated) completes normally afterward.
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "rearm", "r")));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_RESOLVING);
    CHECK(f.route("r").last_error.empty());
    CHECK(f.route("r").caused_by_command_id == "rearm");
    f.post(ControllerEvent::topic_entities_ready("r", "T", gen));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_ENABLED);
}

    static void test_presence_heartbeat_carries_route_diagnostics() {
        std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
        FakeEntityFactory factory;
        FakeStatusPublisher status;
        FakePresencePublisher presence;
        RouterController controller(Fixture::identity(), specs, Fixture::participants(),
                    &factory, &status, nullptr, &presence);
        controller.post(ControllerEvent::type_resolved("T"));
        controller.drain();
        controller.activate();
        CHECK(factory.creates.size() == 1);

        controller.post(ControllerEvent::route_entity_error(
            "r", "T", factory.creates.back().gen, "Failed to create ContentFilteredTopic"));
        controller.drain();
        controller.post(ControllerEvent::presence_tick());
        controller.drain();

        CHECK(presence.heartbeats.size() == 1);
        const RouterHealth &heartbeat = presence.heartbeats.back();
        CHECK(heartbeat.overall_state == RouterOverallState::ROUTER_ERROR);
        CHECK(heartbeat.routes.size() == 1);
        CHECK(heartbeat.routes.at(0).route_name == "r");
        CHECK(heartbeat.routes.at(0).topic_status.size() == 1);
        CHECK(heartbeat.routes.at(0).topic_status.at(0).topic_state ==
          RouterRouteTopicState::TOPIC_ERROR);
        CHECK(heartbeat.routes.at(0).topic_status.at(0).last_error ==
          "Failed to create ContentFilteredTopic");
    }

// Two-topic route (D11): activate builds both; the route is active as soon as one topic
// is ready; one topic's failure is contained; route ERROR only when all topics errored.
static void test_per_topic_activation_two_topics() {
    std::vector<RouterRouteTopicSpec> topics;
    topics.push_back(topic_spec("PlatformCommandAck"));
    topics.push_back(topic_spec("ContactReport"));
    std::vector<RouterRouteSpec> specs(1, route_spec("platform_events", true, topics));
    Fixture f(specs);

    f.post(ControllerEvent::type_resolved("PlatformCommandAck"));
    f.post(ControllerEvent::type_resolved("ContactReport"));
    f.activate();
    CHECK(f.factory.creates.size() == 2); // both topics build once typed (D66/D70)
    std::uint64_t gen_ack = f.factory.creates.at(0).gen;
    std::uint64_t gen_contact = f.factory.creates.at(1).gen;

    // First topic ready: route ENABLED while the sibling is still building.
    f.post(ControllerEvent::topic_entities_ready("platform_events", "PlatformCommandAck",
                                                 gen_ack));
    const RouterRouteStatus &r1 = f.route("platform_events");
    CHECK(r1.state == RouterRouteOperationalState::ROUTE_ENABLED);
    CHECK(r1.topic_status.size() == 2);
    CHECK(r1.topic_status.at(0).topic_state == RouterRouteTopicState::TOPIC_FORWARDING);
    CHECK(r1.topic_status.at(1).topic_state == RouterRouteTopicState::TOPIC_CREATING);

    // The sibling's creation fails: TOPIC_ERROR, contained — forwarding unaffected.
    f.post(ControllerEvent::route_entity_error("platform_events", "ContactReport",
                                               gen_contact, "writer creation failed"));
    const RouterRouteStatus &r2 = f.route("platform_events");
    CHECK(r2.state == RouterRouteOperationalState::ROUTE_ENABLED);
    CHECK(r2.topic_status.at(1).topic_state == RouterRouteTopicState::TOPIC_ERROR);
    CHECK(r2.topic_status.at(1).last_error == "writer creation failed");
    CHECK(r2.topic_status.at(0).topic_state == RouterRouteTopicState::TOPIC_FORWARDING);

    // First topic errors too (runtime fault on the live build): ALL topics errored ->
    // route ERROR (D11 boundary).
    f.post(ControllerEvent::route_entity_error("platform_events", "PlatformCommandAck",
                                               gen_ack, "write path failed"));
    CHECK(f.route("platform_events").state == RouterRouteOperationalState::ROUTE_ERROR);

    // Re-arm retries errored topics (D11): both rebuild immediately.
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "rearm", "platform_events")));
    CHECK(f.route("platform_events").state == RouterRouteOperationalState::ROUTE_RESOLVING);
    CHECK(f.factory.creates.size() == 4);
}

// Builtin-discovery records are derivation/diagnosis input only (D66): losing one never
// tears down a live build and — being internal state — never bumps revision.
static void test_endpoint_records_never_drive_teardown() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T")),
                          /*auto_qos=*/true));
    Fixture f(specs);
    f.post(ControllerEvent::subscription_discovered(reader_record("rd1", "T")));
    std::uint64_t gen = drive_to_forwarding(f, "r", "T");
    f.post(ControllerEvent::topic_match_changed("r", "T", gen, /*input=*/true, 1));
    std::uint64_t rev = f.revision();
    size_t snapshots = f.status.snapshots.size();

    f.post(ControllerEvent::endpoint_lost("rd1"));
    CHECK(f.factory.teardowns.empty());
    CHECK(f.route("r").topic_status.at(0).topic_state
          == RouterRouteTopicState::TOPIC_FORWARDING);
    CHECK(f.revision() == rev);
    CHECK(f.status.snapshots.size() == snapshots); // internal map change: no publish
}

// An auto-QoS route with NO readers known builds immediately with the neutral
// derivation — the strong baseline (D66: derivation is best-effort input, not a gate).
static void test_auto_route_creates_with_baseline() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T")),
                          /*auto_qos=*/true));
    Fixture f(specs);

    f.post(ControllerEvent::type_resolved("T"));
    f.activate();
    CHECK(f.factory.creates.size() == 1);
    const DerivedWriterQos &d = f.factory.creates.back().derived;
    CHECK(d.derive);
    CHECK(d.deadline_nanos == kInfiniteNanos);
    CHECK(d.liveliness_kind == LivelinessKindPod::Automatic);
    CHECK(d.lease_nanos == kInfiniteNanos);
}

// Writer derivation (D39/D42): deadline = min period, kind = max, lease = min across
// the readers known via builtin discovery at issue time.
static void test_writer_qos_derivation() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", false, std::vector<RouterRouteTopicSpec>(1, topic_spec("T")),
                          /*auto_qos=*/true));
    Fixture f(specs);
    f.post(ControllerEvent::type_resolved("T"));

    f.post(ControllerEvent::subscription_discovered(reader_record(
            "rd1", "T", 2000000000LL, LivelinessKindPod::Automatic, 5000000000LL)));
    f.post(ControllerEvent::subscription_discovered(reader_record(
            "rd2", "T", 500000000LL, LivelinessKindPod::ManualByTopic, kInfiniteNanos)));
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "e1", "r")));

    CHECK(f.factory.creates.size() == 1);
    const DerivedWriterQos &d = f.factory.creates.back().derived;
    CHECK(d.derive);
    CHECK(d.deadline_nanos == 500000000LL);
    CHECK(d.liveliness_kind == LivelinessKindPod::ManualByTopic);
    CHECK(d.lease_nanos == 5000000000LL);
}

// A later reader with a tighter deadline tightens in place (D39); looser is a no-op;
// summaries and warnings ride the snapshot.
static void test_deadline_tightening_and_warning() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", false, std::vector<RouterRouteTopicSpec>(1, topic_spec("T")),
                          /*auto_qos=*/true));
    Fixture f(specs);
    f.post(ControllerEvent::type_resolved("T"));

    f.post(ControllerEvent::subscription_discovered(
            reader_record("rd1", "T", 2000000000LL)));
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "e1", "r")));
    std::uint64_t gen = f.last_create_gen();
    f.post(ControllerEvent::topic_entities_ready("r", "T", gen, "BEST_EFFORT,VOLATILE",
                                                 "RELIABLE,TRANSIENT_LOCAL,orig"));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_ENABLED);
    CHECK(f.route("r").topic_status.at(0).reader_qos_summary == "BEST_EFFORT,VOLATILE");
    CHECK(f.route("r").topic_status.at(0).writer_qos_summary
          == "RELIABLE,TRANSIENT_LOCAL,orig");

    // Looser deadline: no update issued.
    f.post(ControllerEvent::subscription_discovered(
            reader_record("rd2", "T", 3000000000LL)));
    CHECK(f.factory.deadline_updates.empty());

    // Tighter deadline: in-place update, no teardown/recreate, summary refreshed.
    f.post(ControllerEvent::subscription_discovered(
            reader_record("rd3", "T", 500000000LL)));
    CHECK(f.factory.deadline_updates.size() == 1);
    CHECK(f.factory.deadline_updates.back().deadline_nanos == 500000000LL);
    CHECK(f.factory.teardowns.empty());
    CHECK(f.factory.creates.size() == 1);
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_ENABLED);
    CHECK(f.route("r").topic_status.at(0).writer_qos_summary
          == "RELIABLE,TRANSIENT_LOCAL,updated");

    // Incompatible-QoS warning with the live stamp lands in status; a stale one is
    // discarded (D23 discipline).
    f.post(ControllerEvent::topic_qos_warning("r", "T", gen, "writer:DURABILITY"));
    CHECK(f.route("r").topic_status.at(0).qos_warning == "writer:DURABILITY");
    std::uint64_t rev = f.revision();
    f.post(ControllerEvent::topic_qos_warning("r", "T", gen + 99, "writer:OWNERSHIP"));
    CHECK(f.route("r").topic_status.at(0).qos_warning == "writer:DURABILITY");
    CHECK(f.revision() == rev); // stale warning: no visible change, no bump
}

// DISABLE while forwarding: event-bounded teardown, then DISABLED (D2/D11 derivation).
static void test_disable_tears_down() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);
    std::uint64_t gen = drive_to_forwarding(f, "r", "T");

    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::DISABLE_ROUTE, "d1", "r")));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.factory.teardowns.size() == 1);
    // Teardown in progress: derivation shows DEGRADED until complete (D11).
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_DEGRADED);

    f.post(ControllerEvent::topic_teardown_complete("r", "T", gen));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_DISABLED);
    // Discovery records must NOT re-create entities on a disabled route.
    f.post(ControllerEvent::publication_discovered(writer_record("w9", "T")));
    CHECK(f.factory.creates.size() == 1);
}

// Runtime per-route partition change (7b/D69): SET_ROUTE_PARTITION updates the desired
// spec (revision bump — it rides status), adjusts live builds IN PLACE (no teardown, no
// rebuild), re-mints the view so future builds use the new spec, and is idempotent for
// unchanged values (D8).
static void test_set_route_partition() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);
    drive_to_forwarding(f, "r", "T");
    std::uint64_t rev = f.revision();

    RouterCommand cmd = command(RouterCommandKind::SET_ROUTE_PARTITION, "p1", "r");
    cmd.route.input.subscriber_partition = "APPS";
    cmd.route.output.publisher_partition = "PLATFORM";
    f.post(ControllerEvent::command_received(cmd));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == "partition updated");
    CHECK(f.revision() == rev + 1); // desired-spec partitions are D5-visible state
    CHECK(f.factory.partition_updates.size() == 1);
    CHECK(f.factory.partition_updates.back().sub_partition == "APPS");
    CHECK(f.factory.partition_updates.back().pub_partition == "PLATFORM");
    CHECK(f.factory.teardowns.empty()); // in place, never a rebuild cycle
    CHECK(f.factory.creates.size() == 1);
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_ENABLED);
    CHECK(f.route("r").desired.output.publisher_partition == "PLATFORM");

    // Same values, new command_id: idempotent accept, no state change, no factory call.
    RouterCommand dup = command(RouterCommandKind::SET_ROUTE_PARTITION, "p2", "r");
    dup.route.input.subscriber_partition = "APPS";
    dup.route.output.publisher_partition = "PLATFORM";
    f.post(ControllerEvent::command_received(dup));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == "partition unchanged");
    CHECK(f.revision() == rev + 1);
    CHECK(f.factory.partition_updates.size() == 1);

    // Future builds use the re-minted view's spec: disable/enable rebuilds with the new
    // partitions on the RouteView the factory receives.
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::DISABLE_ROUTE, "d1", "r")));
    f.post(ControllerEvent::topic_teardown_complete("r", "T",
                                                    f.factory.teardowns.back().gen));
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "e2", "r")));
    CHECK(f.factory.creates.size() == 2);
    CHECK(f.factory.creates.back().sub_partition == "APPS");
    CHECK(f.factory.creates.back().pub_partition == "PLATFORM");
}

// D83: participant-level partition membership — ADD/REMOVE_PARTICIPANT_PARTITION accept
// path (protected identity, not-present reject, idempotent duplicate, live-apply
// failure rollback) and the D5 fingerprint wiring that makes a partition-only change
// bump state_revision even though no route changed.
static void test_participant_partition_accept_path() {
    Fixture f(std::vector<RouterRouteSpec>(), Fixture::participants_with_team_wan());
    std::uint64_t rev = f.revision();

    // ADD a new name: accepted, live-applied, revision bumps.
    RouterCommand add;
    add.command_id = "pp1";
    add.kind = RouterCommandKind::ADD_PARTICIPANT_PARTITION;
    add.participant_name = "team_wan";
    add.partition_name = "TEAM_A";
    f.post(ControllerEvent::command_received(add));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == "added");
    CHECK(f.revision() == rev + 1);
    CHECK(f.factory.participant_partition_applies.count("team_wan") == 1);
    CHECK(f.factory.participant_partition_applies.at("team_wan").size() == 2);
    CHECK(f.factory.participant_partition_applies.at("team_wan").at(0) == "Platform_30");
    CHECK(f.factory.participant_partition_applies.at("team_wan").at(1) == "TEAM_A");

    // Status reflects the live set.
    const RouterStatus &snap1 = f.status.last();
    bool found_wan = false;
    for (std::size_t i = 0; i < snap1.participants.size(); ++i) {
        if (snap1.participants.at(i).name == "team_wan") {
            found_wan = true;
            CHECK(snap1.participants.at(i).participant_partition.size() == 2);
        }
    }
    CHECK(found_wan);

    // Idempotent duplicate ADD (D8): accept, no state change, no new live apply.
    RouterCommand add_dup;
    add_dup.command_id = "pp2";
    add_dup.kind = RouterCommandKind::ADD_PARTICIPANT_PARTITION;
    add_dup.participant_name = "team_wan";
    add_dup.partition_name = "TEAM_A";
    f.post(ControllerEvent::command_received(add_dup));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == "already present");
    CHECK(f.revision() == rev + 1);

    // REMOVE a name not currently present: rejected.
    RouterCommand remove_absent;
    remove_absent.command_id = "pp3";
    remove_absent.kind = RouterCommandKind::REMOVE_PARTICIPANT_PARTITION;
    remove_absent.participant_name = "team_wan";
    remove_absent.partition_name = "TEAM_B";
    f.post(ControllerEvent::command_received(remove_absent));
    CHECK(!f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == "partition not present");
    CHECK(f.revision() == rev + 1);

    // REMOVE the protected node-identity entry: rejected, never a silent no-op.
    RouterCommand remove_protected;
    remove_protected.command_id = "pp4";
    remove_protected.kind = RouterCommandKind::REMOVE_PARTICIPANT_PARTITION;
    remove_protected.participant_name = "team_wan";
    remove_protected.partition_name = "Platform_30";
    f.post(ControllerEvent::command_received(remove_protected));
    CHECK(!f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == "cannot remove a protected partition entry");
    CHECK(f.revision() == rev + 1);

    // Unknown participant: rejected.
    RouterCommand add_unknown;
    add_unknown.command_id = "pp5";
    add_unknown.kind = RouterCommandKind::ADD_PARTICIPANT_PARTITION;
    add_unknown.participant_name = "no_such_participant";
    add_unknown.partition_name = "TEAM_A";
    f.post(ControllerEvent::command_received(add_unknown));
    CHECK(!f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == "unknown participant");
    CHECK(f.revision() == rev + 1);

    // Live-apply failure: state rolls back, no revision bump.
    f.factory.fail_participant_partition_apply = true;
    RouterCommand add_fail;
    add_fail.command_id = "pp6";
    add_fail.kind = RouterCommandKind::ADD_PARTICIPANT_PARTITION;
    add_fail.participant_name = "team_wan";
    add_fail.partition_name = "TEAM_C";
    f.post(ControllerEvent::command_received(add_fail));
    CHECK(!f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == "failed to apply partition to live participant");
    CHECK(f.revision() == rev + 1);
    f.factory.fail_participant_partition_apply = false;

    // REMOVE a present, non-protected name: accepted, revision bumps again.
    RouterCommand remove_ok;
    remove_ok.command_id = "pp7";
    remove_ok.kind = RouterCommandKind::REMOVE_PARTICIPANT_PARTITION;
    remove_ok.participant_name = "team_wan";
    remove_ok.partition_name = "TEAM_A";
    f.post(ControllerEvent::command_received(remove_ok));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == "removed");
    CHECK(f.revision() == rev + 2);
    CHECK(f.factory.participant_partition_applies.at("team_wan").size() == 1);
    CHECK(f.factory.participant_partition_applies.at("team_wan").at(0) == "Platform_30");

    // Capacity bound (RouterAdminTypes.idl: sequence<string, 16> on
    // RouterParticipantStatus.participant_partition) — the accept path must reject
    // before the set ever grows past 16, not accept and let build_snapshot() overflow
    // the bounded wire field on the next status publish.
    std::uint64_t rev_before_fill = f.revision();
    for (int i = 0; i < 15; ++i) {
        RouterCommand fill;
        fill.command_id = "pp-fill-" + std::to_string(i);
        fill.kind = RouterCommandKind::ADD_PARTICIPANT_PARTITION;
        fill.participant_name = "team_wan";
        fill.partition_name = "TEAM_FILL_" + std::to_string(i);
        f.post(ControllerEvent::command_received(fill));
        CHECK(f.status.last_ack().accepted);
    }
    // team_wan now holds the protected "Platform_30" entry + 15 TEAM_FILL_* = 16, the cap.
    CHECK(f.factory.participant_partition_applies.at("team_wan").size() == 16);
    CHECK(f.revision() == rev_before_fill + 15);

    RouterCommand add_over_cap;
    add_over_cap.command_id = "pp-over-cap";
    add_over_cap.kind = RouterCommandKind::ADD_PARTICIPANT_PARTITION;
    add_over_cap.participant_name = "team_wan";
    add_over_cap.partition_name = "TEAM_OVERFLOW";
    f.post(ControllerEvent::command_received(add_over_cap));
    CHECK(!f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == "partition set full (max 16)");
    CHECK(f.revision() == rev_before_fill + 15); // rejected: no state change
    CHECK(f.factory.participant_partition_applies.at("team_wan").size() == 16); // unchanged
}

// D103: protected_partition_entries protects a literal wildcard entry the same way D83
// protects a team_scoped participant's node-identity entry — but on a participant that
// is NOT team_scoped at all (mirrors control_wan's standing "*" post-D103), proving the
// two protection paths are independent (RouterState.hpp::is_protected_partition_name).
static void test_participant_partition_protected_wildcard() {
    Fixture f(std::vector<RouterRouteSpec>(), Fixture::participants_with_protected_wildcard());
    std::uint64_t rev = f.revision();

    // REMOVE the protected wildcard: rejected, never a silent no-op.
    RouterCommand remove_wildcard;
    remove_wildcard.command_id = "ppw1";
    remove_wildcard.kind = RouterCommandKind::REMOVE_PARTICIPANT_PARTITION;
    remove_wildcard.participant_name = "control_wan";
    remove_wildcard.partition_name = "*";
    f.post(ControllerEvent::command_received(remove_wildcard));
    CHECK(!f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == "cannot remove a protected partition entry");
    CHECK(f.revision() == rev);

    // ADD a non-protected name: accepted, live-applied, revision bumps normally —
    // protection is scoped to the wildcard entry alone, not the whole participant.
    RouterCommand add;
    add.command_id = "ppw2";
    add.kind = RouterCommandKind::ADD_PARTICIPANT_PARTITION;
    add.participant_name = "control_wan";
    add.partition_name = "DIRECT_TAP";
    f.post(ControllerEvent::command_received(add));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.revision() == rev + 1);

    // REMOVE that same non-protected name: accepted normally.
    RouterCommand remove_ok;
    remove_ok.command_id = "ppw3";
    remove_ok.kind = RouterCommandKind::REMOVE_PARTICIPANT_PARTITION;
    remove_ok.participant_name = "control_wan";
    remove_ok.partition_name = "DIRECT_TAP";
    f.post(ControllerEvent::command_received(remove_ok));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.revision() == rev + 2);
    CHECK(f.factory.participant_partition_applies.at("control_wan").size() == 1);
    CHECK(f.factory.participant_partition_applies.at("control_wan").at(0) == "*");
}

// The D63 counter path (7d): RefreshCounters pulls forwarded() into status and
// republishes WITHOUT bumping state_revision (the one sanctioned D5 exception); an
// unchanged pull publishes nothing; counters are entity facts cleared with the build.
static void test_refresh_counters_republish_no_bump() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);
    drive_to_forwarding(f, "r", "T");
    std::uint64_t rev = f.revision();
    size_t snapshots = f.status.snapshots.size();

    // Counter moved: republish with fresh counters, SAME revision.
    f.factory.forwarded_counts["r|T"] = 5;
    f.post(ControllerEvent::refresh_counters());
    CHECK(f.status.snapshots.size() == snapshots + 1);
    CHECK(f.revision() == rev);
    CHECK(f.route("r").topic_status.at(0).samples_forwarded == 5);
    CHECK(f.route("r").samples_forwarded == 5); // route aggregate (D11)

    // Counter unchanged at the next tick: nothing to say, no publish.
    f.post(ControllerEvent::refresh_counters());
    CHECK(f.status.snapshots.size() == snapshots + 1);
    CHECK(f.revision() == rev);

    // Counter advanced again: republish, still no bump.
    f.factory.forwarded_counts["r|T"] = 9;
    f.post(ControllerEvent::refresh_counters());
    CHECK(f.status.snapshots.size() == snapshots + 2);
    CHECK(f.route("r").topic_status.at(0).samples_forwarded == 9);
    CHECK(f.revision() == rev);

    // Teardown clears the counter with the rest of the entity facts (the runtime's
    // counter is per-build); with no live build a tick pulls nothing and stays quiet
    // even though the fake still reports 9.
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::DISABLE_ROUTE, "d1", "r")));
    f.post(ControllerEvent::topic_teardown_complete("r", "T",
                                                    f.factory.teardowns.back().gen));
    CHECK(f.route("r").topic_status.at(0).samples_forwarded == 0);
    size_t after_disable = f.status.snapshots.size();
    f.post(ControllerEvent::refresh_counters());
    CHECK(f.status.snapshots.size() == after_disable);
    CHECK(f.route("r").topic_status.at(0).samples_forwarded == 0);
}

// Command history bound (D4): FIFO 256, evicted ids are treated as new commands.
static void test_history_fifo_eviction() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", false, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);

    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "first", "r")));
    CHECK(f.status.last_ack().message == "enabled");

    // 256 more state-changing commands evict "first" from the FIFO.
    for (int i = 0; i < 256; ++i) {
        std::ostringstream id;
        id << "fill-" << i;
        f.post(ControllerEvent::command_received(
                command(RouterCommandKind::UPDATE_ROUTE, id.str(), "r")));
    }
    // Replay of the evicted id is a NEW command: route already enabled -> idempotent
    // accept (D8 turned the D4 eviction risk into a harmless accept).
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "first", "r")));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.status.last_ack().message == "already enabled");
}

int main() {
    RUN(test_startup_snapshot_then_activate);
    RUN(test_enable_creates_immediately);
    RUN(test_enable_then_type_builds);
    RUN(test_duplicate_command_returns_cached_ack);
    RUN(test_rejected_command_ack_replay);
    RUN(test_create_and_observe_walk);
    RUN(test_match_count_change_bumps_revision);
    RUN(test_disable_aborts_inflight_create);
    RUN(test_stale_error_after_abort_discarded);
    RUN(test_redundant_enable_idempotent_accept);
    RUN(test_error_sticky_until_rearm);
    RUN(test_presence_heartbeat_carries_route_diagnostics);
    RUN(test_per_topic_activation_two_topics);
    RUN(test_endpoint_records_never_drive_teardown);
    RUN(test_auto_route_creates_with_baseline);
    RUN(test_writer_qos_derivation);
    RUN(test_deadline_tightening_and_warning);
    RUN(test_disable_tears_down);
    RUN(test_set_route_partition);
    RUN(test_participant_partition_accept_path);
    RUN(test_participant_partition_protected_wildcard);
    RUN(test_refresh_counters_republish_no_bump);
    RUN(test_history_fifo_eviction);

    if (g_failures == 0) {
        std::printf("test_controller_phase1: OK\n");
        return 0;
    }
    std::fprintf(stderr, "test_controller_phase1: %d failure(s)\n", g_failures);
    return 1;
}
