// test_controller_phase1.cxx — Phase 1 evidence: transition-table conformance for the
// controller-owned state machine, driven entirely by synthetic ControllerEvents against
// faked seams (D3). Contract: D1-D11, D21-D26 in docs/cpp_router/design-decisions.md.
//
// The fake EntityFactory records operations but never completes them itself — tests post
// TopicEntitiesReady / TopicTeardownComplete with the recorded generation stamp, the same
// pending-completion seam Phase 3's real async entity creation uses (D8/D21).

#include "core/RouterController.hpp"

#include <cstdio>
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
    };
    std::vector<Op> creates, teardowns, aborts;

    void create_topic_entities(const RouteView &view, const std::string &topic,
                               std::uint64_t gen) override {
        Op op;
        op.route = view.spec.route_name;
        op.topic = topic;
        op.gen = gen;
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

// --- Fixture helpers ---

static RouterRouteTopicSpec topic_spec(const std::string &name, bool auto_qos = false) {
    RouterRouteTopicSpec t;
    t.name = name;
    if (!auto_qos) {
        t.reader_qos = "reliable_alias";
        t.writer_qos = "reliable_alias";
    }
    return t;
}

static RouterRouteSpec route_spec(const std::string &name, bool enabled,
                                  const std::vector<RouterRouteTopicSpec> &topics) {
    RouterRouteSpec s;
    s.route_name = name;
    s.desired_enabled = enabled;
    s.forwarding_mode = "dynamic_data";
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

    static RouterIdentityInfo identity() {
        RouterIdentityInfo id;
        id.node_name = "Platform_30";
        id.router_name = "control-platform";
        id.router_id = 30;
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

// Drive one explicit-QoS single-topic route to FORWARDING. Returns the build stamp.
static std::uint64_t drive_to_forwarding(Fixture &f, const std::string &route,
                                         const std::string &topic,
                                         const std::string &guid) {
    f.post(ControllerEvent::publication_discovered(writer_record(guid, topic)));
    std::uint64_t gen = f.last_create_gen();
    f.post(ControllerEvent::topic_entities_ready(route, topic, gen));
    return gen;
}

// --- Tests ---

// Startup snapshot at revision 0: disabled and waiting routes both visible (D7 evidence).
static void test_startup_snapshot() {
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
    CHECK(f.route("on_route").discovery_state
          == RouterRouteDiscoveryState::DISCOVERY_NONE);
    CHECK(f.route("on_route").topic_status.size() == 1);
    CHECK(f.route("on_route").topic_status.at(0).topic_state
          == RouterRouteTopicState::TOPIC_IDLE);
}

// ENABLE_ROUTE with discovery not READY: accepted, route waits (D2 row 1).
static void test_enable_waits_for_discovery() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", false, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);

    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "c1", "r")));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_WAITING_FOR_DISCOVERY);
    CHECK(f.route("r").caused_by_command_id == "c1");
    CHECK(f.revision() == 1);
    CHECK(f.factory.creates.empty());
}

// Duplicate command_id: cached ack replayed, no state change, no revision bump (D4).
static void test_duplicate_command_returns_cached_ack() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", false, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);

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

// Full single-topic walk: WAITING -> RESOLVING -> ENABLED -> DEGRADED -> WAITING, then
// the DEGRADED -> RESOLVING branch (D2 teardown-complete rows).
static void test_transition_walk_single_topic() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);

    // Writer discovered with type + explicit QoS: NONE -> READY, IDLE -> CREATING.
    f.post(ControllerEvent::publication_discovered(writer_record("w1", "T")));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_RESOLVING);
    CHECK(f.route("r").discovery_state == RouterRouteDiscoveryState::DISCOVERY_READY);
    CHECK(f.factory.creates.size() == 1);
    std::uint64_t gen = f.last_create_gen();

    f.post(ControllerEvent::topic_entities_ready("r", "T", gen));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_ENABLED);
    CHECK(f.route("r").topic_status.at(0).topic_state
          == RouterRouteTopicState::TOPIC_FORWARDING);

    // Last writer lost while forwarding: ENABLED -> DEGRADED, teardown begins (D2).
    f.post(ControllerEvent::endpoint_lost("w1"));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_DEGRADED);
    CHECK(f.factory.teardowns.size() == 1);
    CHECK(f.factory.teardowns.back().gen == gen);

    // Teardown completes with discovery not READY: DEGRADED -> WAITING (D2).
    f.post(ControllerEvent::topic_teardown_complete("r", "T", gen));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_WAITING_FOR_DISCOVERY);
    CHECK(f.route("r").discovery_state == RouterRouteDiscoveryState::DISCOVERY_NONE);

    // Round 2: rediscovered while tearing down -> teardown completes into RESOLVING (D2).
    f.post(ControllerEvent::publication_discovered(writer_record("w2", "T")));
    std::uint64_t gen2 = f.last_create_gen();
    CHECK(gen2 > gen); // one global counter, stamps never repeat (D23)
    f.post(ControllerEvent::topic_entities_ready("r", "T", gen2));
    f.post(ControllerEvent::endpoint_lost("w2"));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_DEGRADED);
    f.post(ControllerEvent::publication_discovered(writer_record("w3", "T")));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_DEGRADED); // still tearing
    f.post(ControllerEvent::topic_teardown_complete("r", "T", gen2));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_RESOLVING);
    CHECK(f.factory.creates.size() == 3);
}

// RESOLVING aborts to WAITING on discovery regression; the aborted build's late
// completion is discarded by stale stamp (D8/D21/D23).
static void test_resolving_abort_and_stale_completion() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);

    f.post(ControllerEvent::publication_discovered(writer_record("w1", "T")));
    std::uint64_t gen = f.last_create_gen();
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_RESOLVING);

    // Discovery regresses mid-resolve: abort, discard partials, back to WAITING — not
    // sticky ERROR (D8).
    f.post(ControllerEvent::endpoint_lost("w1"));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_WAITING_FOR_DISCOVERY);
    CHECK(f.factory.aborts.size() == 1);
    CHECK(f.factory.aborts.back().gen == gen);

    // The aborted creation completes late: stale stamp, discarded, no state change.
    std::uint64_t rev = f.revision();
    f.post(ControllerEvent::topic_entities_ready("r", "T", gen));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_WAITING_FOR_DISCOVERY);
    CHECK(f.revision() == rev);
}

// Redundant ENABLE_ROUTE with a NEW command_id on an already-enabled route: idempotent
// accept, ack cached, no state change, no revision bump (D8).
static void test_redundant_enable_idempotent_accept() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);
    drive_to_forwarding(f, "r", "T", "w1");
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

// Route-wide error is sticky until command re-arm (D2): discovery cannot move it;
// ENABLE_ROUTE clears last_error and re-enters the table.
static void test_error_sticky_until_rearm() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);

    f.post(ControllerEvent::route_entity_error("r", "", 0, "participant lost"));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_ERROR);
    CHECK(f.route("r").last_error == "participant lost");
    CHECK(f.route("r").caused_by_command_id.empty()); // not command-caused (D8)

    // Discovery change: ERROR holds (no auto-retry), no entity creation.
    f.post(ControllerEvent::publication_discovered(writer_record("w1", "T")));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_ERROR);
    CHECK(f.factory.creates.empty());

    // Re-arm (the only exit): last_error cleared; discovery is READY so -> RESOLVING.
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "rearm", "r")));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_RESOLVING);
    CHECK(f.route("r").last_error.empty());
    CHECK(f.route("r").caused_by_command_id == "rearm");
    CHECK(f.factory.creates.size() == 1);
}

// Two-topic route (D11): active as soon as one topic is ready; the second joins in place;
// one topic's failure is contained; route ERROR only when all topics errored.
static void test_per_topic_activation_two_topics() {
    std::vector<RouterRouteTopicSpec> topics;
    topics.push_back(topic_spec("PlatformCommandAck"));
    topics.push_back(topic_spec("ContactReport"));
    std::vector<RouterRouteSpec> specs(1, route_spec("platform_events", true, topics));
    Fixture f(specs);

    // First topic ready + built: route ENABLED while the sibling is still cold.
    f.post(ControllerEvent::publication_discovered(
            writer_record("w1", "PlatformCommandAck")));
    f.post(ControllerEvent::topic_entities_ready("platform_events", "PlatformCommandAck",
                                                 f.last_create_gen()));
    const RouterRouteStatus &r1 = f.route("platform_events");
    CHECK(r1.state == RouterRouteOperationalState::ROUTE_ENABLED);
    CHECK(r1.topic_status.size() == 2);
    CHECK(r1.topic_status.at(0).topic_state == RouterRouteTopicState::TOPIC_FORWARDING);
    CHECK(r1.topic_status.at(1).topic_state == RouterRouteTopicState::TOPIC_IDLE);
    CHECK(r1.topic_status.at(1).discovery_state
          == RouterRouteDiscoveryState::DISCOVERY_NONE);
    std::uint64_t rev = f.revision();

    // Second topic joins in place: revision bumps, per-topic status updates, but NO
    // route-level operational transition (D11).
    f.post(ControllerEvent::publication_discovered(writer_record("w2", "ContactReport")));
    std::uint64_t gen2 = f.last_create_gen();
    CHECK(f.revision() > rev);
    CHECK(f.route("platform_events").state == RouterRouteOperationalState::ROUTE_ENABLED);

    // Its creation fails: TOPIC_ERROR, contained — the forwarding sibling unaffected.
    f.post(ControllerEvent::route_entity_error("platform_events", "ContactReport", gen2,
                                               "writer creation failed"));
    const RouterRouteStatus &r2 = f.route("platform_events");
    CHECK(r2.state == RouterRouteOperationalState::ROUTE_ENABLED);
    CHECK(r2.topic_status.at(1).topic_state == RouterRouteTopicState::TOPIC_ERROR);
    CHECK(r2.topic_status.at(1).last_error == "writer creation failed");
    CHECK(r2.topic_status.at(0).topic_state == RouterRouteTopicState::TOPIC_FORWARDING);

    // First topic errors too: ALL topics errored -> route ERROR (D11 boundary).
    f.post(ControllerEvent::route_entity_error(
            "platform_events", "PlatformCommandAck",
            f.factory.creates.at(0).gen, "write path failed"));
    CHECK(f.route("platform_events").state == RouterRouteOperationalState::ROUTE_ERROR);

    // Re-arm retries errored topics (D11): both READY -> both CREATING -> RESOLVING.
    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::ENABLE_ROUTE, "rearm", "platform_events")));
    CHECK(f.route("platform_events").state == RouterRouteOperationalState::ROUTE_RESOLVING);
    CHECK(f.factory.creates.size() == 4);
}

// Matched-set boundary (D20/D22): with two matched writers, losing one changes facts but
// not the rollup — and does NOT bump revision; losing the last regresses the rollup.
static void test_matched_set_boundary() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);

    f.post(ControllerEvent::publication_discovered(writer_record("w1", "T")));
    f.post(ControllerEvent::publication_discovered(writer_record("w2", "T")));
    f.post(ControllerEvent::topic_entities_ready("r", "T", f.last_create_gen()));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_ENABLED);
    std::uint64_t rev = f.revision();
    size_t snapshots = f.status.snapshots.size();

    // One of two writers lost: set shrinks, rollup stays READY, no bump, no publish.
    f.post(ControllerEvent::endpoint_lost("w1"));
    CHECK(f.revision() == rev);
    CHECK(f.status.snapshots.size() == snapshots);
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_ENABLED);
    CHECK(f.factory.teardowns.empty());

    // Last writer lost: rollup regresses, teardown begins, revision bumps.
    f.post(ControllerEvent::endpoint_lost("w2"));
    CHECK(f.revision() == rev + 1);
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_DEGRADED);
    CHECK(f.factory.teardowns.size() == 1);
}

// Asynchronous type arrival (D13 model): endpoint appears without its type (PARTIAL),
// a later upsert of the same GUID adds the type (READY). NONE -> PARTIAL -> READY.
static void test_type_arrives_late_via_upsert() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);

    f.post(ControllerEvent::publication_discovered(writer_record("w1", "T", false, "")));
    CHECK(f.route("r").discovery_state == RouterRouteDiscoveryState::DISCOVERY_PARTIAL);
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_WAITING_FOR_DISCOVERY);
    CHECK(f.factory.creates.empty());

    f.post(ControllerEvent::publication_discovered(writer_record("w1", "T")));
    CHECK(f.route("r").discovery_state == RouterRouteDiscoveryState::DISCOVERY_READY);
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_RESOLVING);
    CHECK(f.factory.creates.size() == 1);
}

// Auto-QoS topic (D1): READY additionally requires a discovered output reader.
static void test_auto_qos_requires_output_reader() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true,
                          std::vector<RouterRouteTopicSpec>(1, topic_spec("T", true))));
    Fixture f(specs);

    f.post(ControllerEvent::publication_discovered(writer_record("w1", "T")));
    CHECK(f.route("r").discovery_state == RouterRouteDiscoveryState::DISCOVERY_PARTIAL);
    CHECK(f.factory.creates.empty());

    EndpointRecord reader;
    reader.guid = "rd1";
    reader.topic_name = "T";
    f.post(ControllerEvent::subscription_discovered(reader));
    CHECK(f.route("r").discovery_state == RouterRouteDiscoveryState::DISCOVERY_READY);
    CHECK(f.factory.creates.size() == 1);
}

// DISABLE while forwarding: event-bounded teardown, then DISABLED (D2/D11 derivation).
static void test_disable_tears_down() {
    std::vector<RouterRouteSpec> specs(
            1, route_spec("r", true, std::vector<RouterRouteTopicSpec>(1, topic_spec("T"))));
    Fixture f(specs);
    std::uint64_t gen = drive_to_forwarding(f, "r", "T", "w1");

    f.post(ControllerEvent::command_received(
            command(RouterCommandKind::DISABLE_ROUTE, "d1", "r")));
    CHECK(f.status.last_ack().accepted);
    CHECK(f.factory.teardowns.size() == 1);
    // Teardown in progress: derivation shows DEGRADED until complete (D11).
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_DEGRADED);

    f.post(ControllerEvent::topic_teardown_complete("r", "T", gen));
    CHECK(f.route("r").state == RouterRouteOperationalState::ROUTE_DISABLED);
    // Still-discoverable writer must NOT re-create entities on a disabled route.
    CHECK(f.factory.creates.size() == 1);
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
    RUN(test_startup_snapshot);
    RUN(test_enable_waits_for_discovery);
    RUN(test_duplicate_command_returns_cached_ack);
    RUN(test_rejected_command_ack_replay);
    RUN(test_transition_walk_single_topic);
    RUN(test_resolving_abort_and_stale_completion);
    RUN(test_redundant_enable_idempotent_accept);
    RUN(test_error_sticky_until_rearm);
    RUN(test_per_topic_activation_two_topics);
    RUN(test_matched_set_boundary);
    RUN(test_type_arrives_late_via_upsert);
    RUN(test_auto_qos_requires_output_reader);
    RUN(test_disable_tears_down);
    RUN(test_history_fifo_eviction);

    if (g_failures == 0) {
        std::printf("test_controller_phase1: OK\n");
        return 0;
    }
    std::fprintf(stderr, "test_controller_phase1: %d failure(s)\n", g_failures);
    return 1;
}
