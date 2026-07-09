// test_runtime_spine.cxx — Phase 2.5 smoke: thin real runtime spine.
//
// End-to-end flow verified:
//   Probe DataWriter appears in router participant's builtin discovery data
//   → DiscoveryDispatcher translates to PublicationDiscovered event
//   → RouterController matches to smoke_r1.ActRouterPhase25Smoke
//   → SelfCompletingFakeFactory immediately posts TopicEntitiesReady
//   → route reaches ROUTE_ENABLED
//   → DdsStatusPublisher writes RouterStatus sample
//   → probe DataReader reads status and verifies ROUTE_ENABLED
//
// Uses a per-process DDS domain (200 + pid%30) and UDPv4 only.
// Runtime files must stay in local /tmp; the test creates none.

#include "core/ParticipantRegistry.hpp"
#include "core/DiscoveryDispatcher.hpp"
#include "core/DdsStatusPublisher.hpp"
#include "core/RouterController.hpp"
#include "core/RouterState.hpp"
#include "core/Interfaces.hpp"
#include "core/Log.hpp"

#include "RouterAdminTypes.hpp"

#include <rti/core/cond/AsyncWaitSet.hpp>
#include <rti/core/policy/CorePolicy.hpp>
#include <dds/dds.hpp>

#include <atomic>
#include <cstdio>
#include <thread>
#include <chrono>

using namespace router;

// ---------------------------------------------------------------------------
// Test harness helpers
// ---------------------------------------------------------------------------
static int g_failures = 0;
#define CHECK(cond)                                                                    \
    do {                                                                               \
        if (!(cond)) {                                                                 \
            std::fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);      \
            ++g_failures;                                                              \
        }                                                                              \
    } while (0)

// ---------------------------------------------------------------------------
// SelfCompletingFakeFactory — immediately queues TopicEntitiesReady so the
// controller moves the route to ENABLED without a real DDS entity creation step.
// ---------------------------------------------------------------------------
struct SelfCompletingFakeFactory : IEntityFactory {
    RouterController *ctrl = nullptr;

    void create_topic_entities(const RouteView &view,
                               const std::string &topic,
                               std::uint64_t gen) override {
        if (ctrl) {
            ctrl->post(ControllerEvent::topic_entities_ready(
                view.spec.route_name, topic, gen));
        }
    }
    void teardown_topic_entities(const std::string &route,
                                 const std::string &topic,
                                 std::uint64_t gen) override {
        if (ctrl) {
            ctrl->post(ControllerEvent::topic_teardown_complete(route, topic, gen));
        }
    }
    void abort_topic_creation(const std::string &, const std::string &,
                              std::uint64_t) override {}
};

// ---------------------------------------------------------------------------
// DrainThread — calls RouterController::drain() in a tight loop on a background
// thread so the async controller strand is processed without a hand-written loop.
// ---------------------------------------------------------------------------
class DrainThread {
public:
    explicit DrainThread(RouterController &ctrl) : ctrl_(ctrl), running_(true) {
        thread_ = std::thread([this]() {
            while (running_.load(std::memory_order_relaxed)) {
                ctrl_.drain();
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
            }
        });
    }
    void stop() {
        if (running_.exchange(false)) {
            thread_.join();
        }
    }
    ~DrainThread() { stop(); }

private:
    RouterController &ctrl_;
    std::atomic<bool> running_;
    std::thread thread_;
};

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
int main() {
    try {
        // Per-process domain so parallel test runs don't cross-pollinate.
        const int domain = 200 + static_cast<int>(::getpid() % 30);

        // ---------------------------------------------------------------
        // Router participant (registry creates it enabled with UDPv4 + tag)
        // ---------------------------------------------------------------
        const std::string router_tag = "act.router=TestNode/spine-smoke";

        ParticipantRegistry::Config cfg;
        cfg.name          = "lan";
        cfg.domain        = domain;
        cfg.user_data_tag = router_tag;
        ParticipantRegistry registry({cfg});

        dds::domain::DomainParticipant router_dp = registry.get("lan");

        // ---------------------------------------------------------------
        // Status publisher (RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1))
        // ---------------------------------------------------------------
        DdsStatusPublisher status_pub(router_dp, "ActRouterStatus");

        // ---------------------------------------------------------------
        // Route spec: one explicit-QoS topic (reader_qos non-empty → qos_resolved=true)
        // ---------------------------------------------------------------
        RouterRouteTopicSpec topic_spec;
        topic_spec.name       = "ActRouterPhase25Smoke";
        topic_spec.reader_qos = "default";
        topic_spec.writer_qos = "default";

        RouterRouteSpec route_spec;
        route_spec.route_name     = "smoke_r1";
        route_spec.desired_enabled = true;
        route_spec.topics.push_back(topic_spec);

        RouterIdentityInfo identity;
        identity.node_name   = "TestNode";
        identity.router_name = "spine-smoke";
        identity.router_id   = 1;
        identity.status_id   = "phase25-spine";

        ParticipantState ps;
        ps.name   = "lan";
        ps.domain = domain;

        SelfCompletingFakeFactory fake_factory;
        RouterController ctrl(identity, {route_spec}, {ps}, &fake_factory, &status_pub);
        fake_factory.ctrl = &ctrl;

        // ---------------------------------------------------------------
        // Discovery dispatcher: attaches builtin reader conditions to AWS
        // ---------------------------------------------------------------
        rti::core::cond::AsyncWaitSet aws;
        DiscoveryDispatcher dispatcher(aws, ctrl, registry, router_tag);
        aws.start();

        // ---------------------------------------------------------------
        // Drain thread: processes controller events on a background thread
        // ---------------------------------------------------------------
        DrainThread drain(ctrl);

        // ---------------------------------------------------------------
        // Probe participant: observes RouterStatus + emits an app-level writer
        // that triggers the discovery path in the router
        // ---------------------------------------------------------------
        dds::domain::qos::DomainParticipantQos probe_qos =
            dds::domain::DomainParticipant::default_participant_qos();
        probe_qos << rti::core::policy::TransportBuiltin::UDPv4();
        dds::domain::DomainParticipant probe_dp(domain, probe_qos);

        // RouterStatus reader — TRANSIENT_LOCAL so it sees samples written before join.
        dds::topic::Topic<RouterStatus> status_topic(probe_dp, "ActRouterStatus");
        {
            auto rqos = dds::sub::Subscriber(probe_dp).default_datareader_qos();
            rqos << dds::core::policy::Reliability::Reliable();
            rqos << dds::core::policy::Durability::TransientLocal();
            rqos << dds::core::policy::History::KeepLast(1);
            dds::sub::DataReader<RouterStatus> status_reader(
                dds::sub::Subscriber(probe_dp), status_topic, rqos);

            // Application-data writer: its discovery triggers PublicationDiscovered in
            // the router, which the controller matches to smoke_r1.ActRouterPhase25Smoke.
            dds::topic::Topic<RouterStatus> app_topic(probe_dp, "ActRouterPhase25Smoke");
            dds::pub::DataWriter<RouterStatus> app_writer(
                dds::pub::Publisher(probe_dp), app_topic);
            (void)app_writer; // presence alone drives discovery; no data written needed

            // ---------------------------------------------------------------
            // Wait up to 15 s for ROUTE_ENABLED in the status stream
            // ---------------------------------------------------------------
            dds::sub::cond::ReadCondition status_cond(
                status_reader, dds::sub::status::DataState::any());
            dds::core::cond::WaitSet ws;
            ws += status_cond;

            bool route_enabled = false;
            for (int i = 0; i < 60 && !route_enabled; ++i) {
                try {
                    ws.wait(dds::core::Duration::from_millisecs(250));
                } catch (const dds::core::TimeoutError &) {}

                auto samples = status_reader.take();
                for (auto it = samples.begin(); it != samples.end(); ++it) {
                    if (!it->info().valid()) continue;
                    const RouterStatus &st = it->data();
                    for (size_t r = 0; r < st.routes.size(); ++r) {
                        const RouterRouteStatus &rs = st.routes.at(r);
                        if (rs.route_name == "smoke_r1" &&
                            rs.state == RouterRouteOperationalState::ROUTE_ENABLED) {
                            route_enabled = true;
                        }
                    }
                }
            }

            // ---------------------------------------------------------------
            // Shutdown — safe order: drain → dispatcher → aws → entities
            // ---------------------------------------------------------------
            drain.stop();
            dispatcher.shutdown();
            aws.stop();

            CHECK(route_enabled);
        } // probe_dp, status_reader, app_writer, app_topic go out of scope

        if (g_failures == 0) {
            std::printf("test_runtime_spine: OK domain=%d route=smoke_r1 ROUTE_ENABLED\n",
                        domain);
            return 0;
        }
        std::fprintf(stderr, "test_runtime_spine: %d failure(s)\n", g_failures);
        return 1;

    } catch (const std::exception &e) {
        std::fprintf(stderr, "test_runtime_spine: exception: %s\n", e.what());
        return 1;
    }
}
