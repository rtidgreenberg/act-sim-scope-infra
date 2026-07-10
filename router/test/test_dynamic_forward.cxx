// test_dynamic_forward.cxx — Phase 4 steps 1-2: DynamicData forwarding + content filter.
//
// Real DDS end to end, no compiled payload type (D35). The router loads ExampleCommand
// from a router-authored DDS-type XML and forwards it as DynamicData, with a
// ContentFilteredTopic on the input reader keeping only msg.destination = this node:
//
//   source writer (domain A, ExampleCommand DynamicData, msg.destination in {P30, P31})
//     -> router wan_in discovery -> DynamicRouteFactory creates CFT reader + writer
//     -> RouteTopicRuntime<DynamicData> forwards only P30 samples
//     -> sink reader (domain B) receives P30, never P31
//   then closing the source tears the route down (D32) and it leaves ROUTE_ENABLED,
//   and a NEW source rebuilds the filtered route (CFT recreated by name) and forwards
//   again (the D32 rebuild goal, D41).
//
// Input leg (domain A) and output leg (domain B) are separate participants/domains — the
// real router topology, so no intra-participant self-loop. UDPv4 only; per-process domains.

#include "core/AsyncWaitSetDispatcher.hpp"
#include "core/DdsStatusPublisher.hpp"
#include "core/DiscoveryDispatcher.hpp"
#include "core/DynamicRouteFactory.hpp"
#include "core/Log.hpp"
#include "core/ParticipantRegistry.hpp"
#include "core/QosResolver.hpp"
#include "core/RouterController.hpp"
#include "core/TypeResolver.hpp"

#include "RouterAdminTypes.hpp"

#include <rti/core/cond/AsyncWaitSet.hpp>
#include <rti/core/policy/CorePolicy.hpp>
#include <dds/dds.hpp>
#include <dds/core/xtypes/DynamicData.hpp>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <string>
#include <thread>
#include <unistd.h>

using namespace router;
using DynData = dds::core::xtypes::DynamicData;

static int g_failures = 0;
#define CHECK(cond)                                                                    \
    do {                                                                               \
        if (!(cond)) {                                                                 \
            std::fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);       \
            ++g_failures;                                                              \
        }                                                                              \
    } while (0)

#ifndef ROUTER_CONFIG_DIR
#define ROUTER_CONFIG_DIR "."
#endif

class DrainThread {
public:
    explicit DrainThread(RouterController &ctrl) : ctrl_(ctrl), running_(true) {
        thread_ = std::thread([this]() {
            while (running_.load(std::memory_order_relaxed)) {
                ctrl_.wait_and_drain(std::chrono::milliseconds(100));
            }
        });
    }
    void stop() { if (running_.exchange(false)) thread_.join(); }
    ~DrainThread() { stop(); }
private:
    RouterController &ctrl_;
    std::atomic<bool> running_;
    std::thread thread_;
};

static dds::domain::DomainParticipant make_app_participant(int domain) {
    dds::domain::qos::DomainParticipantQos qos =
        dds::domain::DomainParticipant::default_participant_qos();
    qos << rti::core::policy::TransportBuiltin::UDPv4();
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

static bool route_enabled_now(dds::sub::DataReader<RouterStatus> &status_reader) {
    bool enabled = false;
    auto samples = status_reader.read();
    for (auto it = samples.begin(); it != samples.end(); ++it) {
        if (!it->info().valid()) continue;
        const RouterStatus &st = it->data();
        for (size_t r = 0; r < st.routes.size(); ++r) {
            if (st.routes.at(r).route_name == "cmd_r1") {
                enabled = (st.routes.at(r).state
                           == RouterRouteOperationalState::ROUTE_ENABLED);
            }
        }
    }
    return enabled;
}

int main() {
    try {
        const int dom_in  = 70 + static_cast<int>(::getpid() % 30);
        const int dom_out = 100 + static_cast<int>(::getpid() % 30);
        const std::string topic = "ExampleCmd";
        const std::string router_tag = "act.router=Platform_30/cmd-smoke";
        const std::string this_node = "Platform_30";
        const std::string types_xml = std::string(ROUTER_CONFIG_DIR) + "/example_types.xml";

        // --- Type registry: load ExampleCommand from the router-authored XML (D35) ---
        TypeResolver types;
        types.load_types(types_xml);
        CHECK(types.has_dynamic_type("ExampleCommand"));
        const dds::core::xtypes::DynamicType &cmd_type =
            types.get_dynamic_type("ExampleCommand");

        // --- Router participants: input leg (wan_in) + output leg (lan_out) ---
        ParticipantRegistry::Config in_cfg;
        in_cfg.name = "wan_in"; in_cfg.domain = dom_in; in_cfg.user_data_tag = router_tag;
        ParticipantRegistry::Config out_cfg;
        out_cfg.name = "lan_out"; out_cfg.domain = dom_out; out_cfg.user_data_tag = router_tag;
        ParticipantRegistry registry({in_cfg, out_cfg});
        dds::domain::DomainParticipant in_dp = registry.get("wan_in");

        DdsStatusPublisher status_pub(in_dp, "ActRouterStatus");

        rti::core::cond::AsyncWaitSet aws;
        AsyncWaitSetDispatcher route_disp(aws);
        QosResolver qos;
        DynamicRouteFactory factory(registry, types, qos, route_disp, "ExampleCommand");

        // --- Route cmd_r1: wan_in -> lan_out, input filtered to this node ---
        RouterRouteTopicSpec topic_spec; topic_spec.name = topic;
        RouterRouteSpec route_spec;
        route_spec.route_name = "cmd_r1";
        route_spec.desired_enabled = true;
        route_spec.forwarding_mode = "dynamic_data";
        route_spec.input.participant  = "wan_in";
        route_spec.input.reader_qos   = "default";
        route_spec.input.filter_expression = "msg.destination = %0";
        route_spec.input.filter_parameters.push_back("'" + this_node + "'");
        route_spec.output.participant = "lan_out";
        route_spec.output.writer_qos  = "default";
        route_spec.topics.push_back(topic_spec);

        RouterIdentityInfo identity;
        identity.node_name = this_node; identity.router_name = "cmd-smoke";
        identity.router_id = 4; identity.status_id = "phase4-dyn";

        ParticipantState ps_in;  ps_in.name = "wan_in";   ps_in.domain = dom_in;
        ParticipantState ps_out; ps_out.name = "lan_out"; ps_out.domain = dom_out;

        RouterController ctrl(identity, {route_spec}, {ps_in, ps_out}, &factory, &status_pub);
        factory.set_controller(&ctrl);

        DiscoveryDispatcher discovery(aws, ctrl, registry, router_tag);
        aws.start();
        DrainThread drain(ctrl);

        // --- External source (domain A), sink (domain B), status observer (domain A) ---
        dds::domain::DomainParticipant src_dp  = make_app_participant(dom_in);
        dds::domain::DomainParticipant sink_dp = make_app_participant(dom_out);
        dds::domain::DomainParticipant probe_dp = make_app_participant(dom_in);

        dds::topic::Topic<DynData> sink_topic(sink_dp, topic, cmd_type);
        dds::sub::DataReader<DynData> sink_reader(
            dds::sub::Subscriber(sink_dp), sink_topic, reliable_tl_reader());

        dds::topic::Topic<RouterStatus> status_topic(probe_dp, "ActRouterStatus");
        dds::sub::DataReader<RouterStatus> status_reader(
            dds::sub::Subscriber(probe_dp), status_topic, reliable_tl_reader());

        dds::topic::Topic<DynData> src_topic(src_dp, topic, cmd_type);
        dds::pub::DataWriter<DynData> src_writer(
            dds::pub::Publisher(src_dp), src_topic, reliable_tl_writer());

        // --- Wait for ROUTE_ENABLED (route entities created from real discovery) ---
        bool enabled = false;
        for (int i = 0; i < 80 && !enabled; ++i) {
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
            enabled = route_enabled_now(status_reader);
        }
        CHECK(enabled);

        // --- Write one addressed to us (P30) and one addressed elsewhere (P31) ---
        int p30_seen = 0, p31_seen = 0;
        for (int i = 0; i < 80 && p30_seen == 0; ++i) {
            DynData to_us(cmd_type);
            to_us.value<std::string>("msg.destination", this_node);
            to_us.value<int32_t>("msg.seq", i);
            src_writer.write(to_us);

            DynData to_other(cmd_type);
            to_other.value<std::string>("msg.destination", std::string("Platform_31"));
            to_other.value<int32_t>("msg.seq", 1000 + i);
            src_writer.write(to_other);

            std::this_thread::sleep_for(std::chrono::milliseconds(150));
            auto samples = sink_reader.take();
            for (auto it = samples.begin(); it != samples.end(); ++it) {
                if (!it->info().valid()) continue;
                std::string dest = it->data().value<std::string>("msg.destination");
                if (dest == this_node) ++p30_seen;
                else if (dest == "Platform_31") ++p31_seen;
            }
        }
        // Grace period to catch any late (wrongly-forwarded) P31 sample.
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        {
            auto samples = sink_reader.take();
            for (auto it = samples.begin(); it != samples.end(); ++it) {
                if (!it->info().valid()) continue;
                std::string dest = it->data().value<std::string>("msg.destination");
                if (dest == this_node) ++p30_seen;
                else if (dest == "Platform_31") ++p31_seen;
            }
        }
        CHECK(p30_seen > 0);   // addressed-to-us forwarded
        CHECK(p31_seen == 0);  // addressed-elsewhere filtered out

        // --- Teardown proof: closing the source leaves ROUTE_ENABLED (D32) ---
        src_dp.close();
        bool left_enabled = false;
        for (int i = 0; i < 60 && !left_enabled; ++i) {
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
            left_enabled = !route_enabled_now(status_reader);
        }
        CHECK(left_enabled);

        // --- Rebuild proof (D32 rebuild goal, D41): a NEW source re-enables the
        //     filtered route — the "<topic>_cft" name was reclaimed on teardown, so
        //     the second build recreates it instead of dying on a name collision — and
        //     filtered forwarding works end-to-end again. ---
        int p30_rebuilt = 0;
        {
            dds::domain::DomainParticipant src2_dp = make_app_participant(dom_in);
            dds::topic::Topic<DynData> src2_topic(src2_dp, topic, cmd_type);
            dds::pub::DataWriter<DynData> src2_writer(
                dds::pub::Publisher(src2_dp), src2_topic, reliable_tl_writer());

            bool re_enabled = false;
            for (int i = 0; i < 80 && !re_enabled; ++i) {
                std::this_thread::sleep_for(std::chrono::milliseconds(250));
                re_enabled = route_enabled_now(status_reader);
            }
            CHECK(re_enabled);

            for (int i = 0; i < 80 && p30_rebuilt == 0; ++i) {
                DynData to_us(cmd_type);
                to_us.value<std::string>("msg.destination", this_node);
                to_us.value<int32_t>("msg.seq", 2000 + i);
                src2_writer.write(to_us);

                std::this_thread::sleep_for(std::chrono::milliseconds(150));
                auto samples = sink_reader.take();
                for (auto it = samples.begin(); it != samples.end(); ++it) {
                    if (!it->info().valid()) continue;
                    if (it->data().value<std::string>("msg.destination") == this_node) {
                        ++p30_rebuilt;
                    }
                }
            }
            CHECK(p30_rebuilt > 0); // rebuilt route forwards again
            src2_dp.close();
        }

        route_disp.shutdown();
        discovery.shutdown();
        drain.stop();
        aws.stop();

        if (g_failures == 0) {
            std::printf("test_dynamic_forward: OK in=%d out=%d cmd_r1 p30=%d p31=%d "
                        "rebuilt=%d\n",
                        dom_in, dom_out, p30_seen, p31_seen, p30_rebuilt);
            return 0;
        }
        std::fprintf(stderr, "test_dynamic_forward: %d failure(s)\n", g_failures);
        return 1;

    } catch (const std::exception &e) {
        std::fprintf(stderr, "test_dynamic_forward: exception: %s\n", e.what());
        return 1;
    }
}
