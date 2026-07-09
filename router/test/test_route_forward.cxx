// test_route_forward.cxx — Phase 3: one discovered route forwards one topic end-to-end.
//
// Real DDS the whole way (no fake factory):
//   external source writer (domain A, "Phase3Fwd")
//     → router wan_in builtin discovery → DiscoveryDispatcher → RouterController match
//     → EntityFactory<RouterStatus> creates route reader (wan_in) + writer (lan_out),
//       ignores the output writer, attaches the forwarding ReadCondition to the AsyncWaitSet
//     → route reaches ROUTE_ENABLED (observed on the DDS status stream)
//     → RouteTopicRuntime pumps samples reader→writer on an AWS worker thread
//     → external sink reader (domain B, "Phase3Fwd") receives the forwarded sample
//   then: closing the source tears the route down (D32 detach barrier) and the route
//   leaves ROUTE_ENABLED.
//
// Input leg (wan_in, domain A) and output leg (lan_out, domain B) are separate participants
// on separate domains — the real router topology — so there is no intra-participant
// self-loop. RouterStatus is used only as a convenient generated payload type; the test
// validates route runtime/forwarding, not payload schema.
//
// UDPv4 only; per-process domains; creates no runtime files (vboxsf-safe).

#include "core/AsyncWaitSetDispatcher.hpp"
#include "core/DdsStatusPublisher.hpp"
#include "core/DiscoveryDispatcher.hpp"
#include "core/EntityFactory.hpp"
#include "core/Log.hpp"
#include "core/ParticipantRegistry.hpp"
#include "core/QosResolver.hpp"
#include "core/RouterController.hpp"
#include "core/TypeResolver.hpp"

#include "RouterAdminTypes.hpp"

#include <rti/core/cond/AsyncWaitSet.hpp>
#include <rti/core/policy/CorePolicy.hpp>
#include <dds/dds.hpp>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <string>
#include <thread>
#include <unistd.h>

using namespace router;

static int g_failures = 0;
#define CHECK(cond)                                                                    \
    do {                                                                               \
        if (!(cond)) {                                                                 \
            std::fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);       \
            ++g_failures;                                                              \
        }                                                                              \
    } while (0)

// Background strand: processes controller events so AWS handlers never mutate state.
class DrainThread {
public:
    explicit DrainThread(RouterController &ctrl) : ctrl_(ctrl), running_(true) {
        thread_ = std::thread([this]() {
            while (running_.load(std::memory_order_relaxed)) {
                ctrl_.wait_and_drain(std::chrono::milliseconds(100));
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

static dds::domain::DomainParticipant make_app_participant(int domain) {
    dds::domain::qos::DomainParticipantQos qos =
        dds::domain::DomainParticipant::default_participant_qos();
    qos << rti::core::policy::TransportBuiltin::UDPv4(); // untagged: application endpoint
    return dds::domain::DomainParticipant(domain, qos);
}

static dds::sub::qos::DataReaderQos reliable_tl_reader() {
    dds::sub::qos::DataReaderQos q;
    q << dds::core::policy::Reliability::Reliable();
    q << dds::core::policy::Durability::TransientLocal();
    q << dds::core::policy::History::KeepLast(16);
    return q;
}

static dds::pub::qos::DataWriterQos reliable_tl_writer() {
    dds::pub::qos::DataWriterQos q;
    q << dds::core::policy::Reliability::Reliable();
    q << dds::core::policy::Durability::TransientLocal();
    q << dds::core::policy::History::KeepLast(16);
    return q;
}

// Poll the status stream for whether smoke route "fwd_r1" is currently ROUTE_ENABLED.
static bool route_enabled_now(dds::sub::DataReader<RouterStatus> &status_reader) {
    bool enabled = false;
    auto samples = status_reader.read(); // read (not take) — status is KEEP_LAST(1) state
    for (auto it = samples.begin(); it != samples.end(); ++it) {
        if (!it->info().valid()) continue;
        const RouterStatus &st = it->data();
        for (size_t r = 0; r < st.routes.size(); ++r) {
            if (st.routes.at(r).route_name == "fwd_r1") {
                enabled = (st.routes.at(r).state
                           == RouterRouteOperationalState::ROUTE_ENABLED);
            }
        }
    }
    return enabled;
}

int main() {
    try {
        const int dom_in  = 130 + static_cast<int>(::getpid() % 30);
        const int dom_out = 160 + static_cast<int>(::getpid() % 30);
        const std::string topic = "Phase3Fwd";
        const std::string router_tag = "act.router=TestNode/fwd-smoke";

        // --- Router participants: input leg (wan_in) + output leg (lan_out) ---
        ParticipantRegistry::Config in_cfg;
        in_cfg.name = "wan_in";   in_cfg.domain = dom_in;  in_cfg.user_data_tag = router_tag;
        ParticipantRegistry::Config out_cfg;
        out_cfg.name = "lan_out"; out_cfg.domain = dom_out; out_cfg.user_data_tag = router_tag;
        ParticipantRegistry registry({in_cfg, out_cfg});

        dds::domain::DomainParticipant in_dp = registry.get("wan_in");

        // --- Status publisher on the input-leg participant (domain A) ---
        DdsStatusPublisher status_pub(in_dp, "ActRouterStatus");

        // --- Phase 3 runtime pieces ---
        rti::core::cond::AsyncWaitSet aws;
        AsyncWaitSetDispatcher route_disp(aws);
        TypeResolver types;
        QosResolver qos;
        EntityFactory<RouterStatus> factory(registry, types, qos, route_disp, "RouterStatus");

        // --- Route: fwd_r1, one explicit-QoS topic, wan_in -> lan_out ---
        RouterRouteTopicSpec topic_spec;
        topic_spec.name = topic;
        topic_spec.reader_qos = "default";
        topic_spec.writer_qos = "default";

        RouterRouteSpec route_spec;
        route_spec.route_name = "fwd_r1";
        route_spec.desired_enabled = true;
        route_spec.input.participant  = "wan_in";
        route_spec.output.participant = "lan_out";
        route_spec.topics.push_back(topic_spec);

        RouterIdentityInfo identity;
        identity.node_name = "TestNode"; identity.router_name = "fwd-smoke";
        identity.router_id = 3; identity.status_id = "phase3-fwd";

        ParticipantState ps_in;  ps_in.name = "wan_in";   ps_in.domain = dom_in;
        ParticipantState ps_out; ps_out.name = "lan_out"; ps_out.domain = dom_out;

        RouterController ctrl(identity, {route_spec}, {ps_in, ps_out}, &factory, &status_pub);
        factory.set_controller(&ctrl);

        DiscoveryDispatcher discovery(aws, ctrl, registry, router_tag);
        aws.start();
        DrainThread drain(ctrl);

        // --- External source (domain A) + sink (domain B) + status observer (domain A) ---
        dds::domain::DomainParticipant src_dp  = make_app_participant(dom_in);
        dds::domain::DomainParticipant sink_dp = make_app_participant(dom_out);
        dds::domain::DomainParticipant probe_dp = make_app_participant(dom_in);

        dds::topic::Topic<RouterStatus> sink_topic(sink_dp, topic);
        dds::sub::DataReader<RouterStatus> sink_reader(
            dds::sub::Subscriber(sink_dp), sink_topic, reliable_tl_reader());

        dds::topic::Topic<RouterStatus> status_topic(probe_dp, "ActRouterStatus");
        dds::sub::DataReader<RouterStatus> status_reader(
            dds::sub::Subscriber(probe_dp), status_topic, reliable_tl_reader());

        // Source writer whose discovery drives route creation, and whose samples get forwarded.
        dds::topic::Topic<RouterStatus> src_topic(src_dp, topic);
        dds::pub::DataWriter<RouterStatus> src_writer(
            dds::pub::Publisher(src_dp), src_topic, reliable_tl_writer());

        // --- Wait for ROUTE_ENABLED (route entities created from real discovery) ---
        bool enabled = false;
        for (int i = 0; i < 80 && !enabled; ++i) {
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
            enabled = route_enabled_now(status_reader);
        }
        CHECK(enabled);

        // --- Forward proof: write a recognizable sample, expect it downstream ---
        RouterStatus payload;
        payload.target_node = "src-node";
        payload.status_id = "phase3-forward";
        payload.router_id = 4242;

        bool received = false;
        for (int i = 0; i < 80 && !received; ++i) {
            payload.state_revision = static_cast<std::uint64_t>(i); // vary sample
            src_writer.write(payload);
            std::this_thread::sleep_for(std::chrono::milliseconds(150));
            auto samples = sink_reader.take();
            for (auto it = samples.begin(); it != samples.end(); ++it) {
                if (it->info().valid() && it->data().status_id == "phase3-forward"
                    && it->data().router_id == 4242) {
                    received = true;
                }
            }
        }
        CHECK(received);

        // --- Teardown proof: closing the source removes its endpoint; the route tears
        //     down (D32 detach barrier) and leaves ROUTE_ENABLED ---
        src_dp.close();
        bool left_enabled = false;
        for (int i = 0; i < 60 && !left_enabled; ++i) {
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
            left_enabled = !route_enabled_now(status_reader);
        }
        CHECK(left_enabled);

        // --- Ordered shutdown: route runtimes → discovery → drain → aws → participants ---
        route_disp.shutdown();
        discovery.shutdown();
        drain.stop();
        aws.stop();

        if (g_failures == 0) {
            std::printf("test_route_forward: OK in=%d out=%d fwd_r1 forwarded + torn down\n",
                        dom_in, dom_out);
            return 0;
        }
        std::fprintf(stderr, "test_route_forward: %d failure(s)\n", g_failures);
        return 1;

    } catch (const std::exception &e) {
        std::fprintf(stderr, "test_route_forward: exception: %s\n", e.what());
        return 1;
    }
}
