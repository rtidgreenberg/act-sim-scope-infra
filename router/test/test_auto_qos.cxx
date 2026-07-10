// test_auto_qos.cxx — Phase 5 evidence: asymmetric auto QoS + output readiness (D39/D42/D45).
//
// Real DDS end to end on the DynamicData lane (ExampleCommand from example_types.xml),
// route "auto_r1" with auto ("") QoS on both endpoints:
//
//   1. output readiness: with a discovered source writer but NO local reader on the
//      output side, the route waits (DISCOVERY_PARTIAL) instead of creating a writer —
//      and activates automatically once a reader appears.
//   2. a BEST_EFFORT + VOLATILE application writer matches the weakest-request route
//      input reader and forwards (the F5 case that used to be a silent no-match).
//   3. a local reader requesting AUTOMATIC liveliness with a finite lease matches the
//      route writer created after it (derived lease <= requested) and observes
//      liveliness with no router-side asserts (D42) — and the resolved summaries ride
//      the route status.
//   4. a reader requesting TRANSIENT durability produces a loud incompatible-QoS
//      warning naming DURABILITY; an EXCLUSIVE-ownership source writer produces one
//      naming OWNERSHIP on the reader side (equality RxO). Route status carries both.
//   5. a later local reader with a tighter deadline is accommodated in place via
//      set_qos — the route stays ENABLED (no teardown cycle) and the writer summary
//      shows the tightened offer.
//
// Topology mirrors test_dynamic_forward: input leg (domain A) and output leg (domain B)
// are separate participants/domains. UDPv4 only; per-process domains.

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

// Snapshot of the observable route facts this test asserts on.
struct RouteFacts {
    bool seen = false;
    RouterRouteOperationalState state = RouterRouteOperationalState::ROUTE_DISABLED;
    RouterRouteDiscoveryState discovery = RouterRouteDiscoveryState::DISCOVERY_NONE;
    std::string reader_summary, writer_summary, qos_warning;
};

static RouteFacts read_facts(dds::sub::DataReader<RouterStatus> &status_reader) {
    RouteFacts f;
    auto samples = status_reader.read();
    for (auto it = samples.begin(); it != samples.end(); ++it) {
        if (!it->info().valid()) continue;
        const RouterStatus &st = it->data();
        for (size_t r = 0; r < st.routes.size(); ++r) {
            if (st.routes.at(r).route_name != "auto_r1") continue;
            f.seen = true;
            f.state = st.routes.at(r).state;
            f.discovery = st.routes.at(r).discovery_state;
            if (!st.routes.at(r).topic_status.empty()) {
                f.reader_summary = st.routes.at(r).topic_status.at(0).reader_qos_summary;
                f.writer_summary = st.routes.at(r).topic_status.at(0).writer_qos_summary;
                f.qos_warning = st.routes.at(r).topic_status.at(0).qos_warning;
            }
        }
    }
    return f;
}

template <typename Pred>
static RouteFacts wait_for(dds::sub::DataReader<RouterStatus> &status_reader,
                           Pred pred, int tries = 80) {
    RouteFacts f;
    for (int i = 0; i < tries; ++i) {
        f = read_facts(status_reader);
        if (f.seen && pred(f)) return f;
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
    }
    return f;
}

int main() {
    try {
        const int dom_in  = 70 + static_cast<int>(::getpid() % 30);
        const int dom_out = 100 + static_cast<int>(::getpid() % 30);
        const std::string topic = "AutoQosCmd";
        const std::string router_tag = "act.router=Platform_30/autoqos";
        const std::string types_xml = std::string(ROUTER_CONFIG_DIR) + "/example_types.xml";

        TypeResolver types;
        types.load_types(types_xml);
        const dds::core::xtypes::DynamicType &cmd_type =
            types.get_dynamic_type("ExampleCommand");

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

        // Route auto_r1: wan_in -> lan_out, auto ("") QoS on both endpoints (D39).
        RouterRouteTopicSpec topic_spec; topic_spec.name = topic;
        RouterRouteSpec route_spec;
        route_spec.route_name = "auto_r1";
        route_spec.desired_enabled = true;
        route_spec.forwarding_mode = "dynamic_data";
        route_spec.input.participant  = "wan_in";
        route_spec.output.participant = "lan_out";
        route_spec.topics.push_back(topic_spec);

        RouterIdentityInfo identity;
        identity.node_name = "Platform_30"; identity.router_name = "autoqos";
        identity.router_id = 5; identity.status_id = "phase5-autoqos";

        ParticipantState ps_in;  ps_in.name = "wan_in";   ps_in.domain = dom_in;
        ParticipantState ps_out; ps_out.name = "lan_out"; ps_out.domain = dom_out;

        RouterController ctrl(identity, {route_spec}, {ps_in, ps_out}, &factory, &status_pub);
        factory.set_controller(&ctrl);

        DiscoveryDispatcher discovery(aws, ctrl, registry, router_tag);
        aws.start();
        DrainThread drain(ctrl);

        // --- App endpoints: BE+VOLATILE source (domain A), status probe (domain A) ---
        dds::domain::DomainParticipant src_dp   = make_app_participant(dom_in);
        dds::domain::DomainParticipant sink_dp  = make_app_participant(dom_out);
        dds::domain::DomainParticipant probe_dp = make_app_participant(dom_in);

        dds::topic::Topic<RouterStatus> status_topic(probe_dp, "ActRouterStatus");
        dds::sub::qos::DataReaderQos probe_qos;
        probe_qos << dds::core::policy::Reliability::Reliable();
        probe_qos << dds::core::policy::Durability::TransientLocal();
        probe_qos << dds::core::policy::History::KeepLast(16);
        dds::sub::DataReader<RouterStatus> status_reader(
            dds::sub::Subscriber(probe_dp), status_topic, probe_qos);

        dds::topic::Topic<DynData> src_topic(src_dp, topic, cmd_type);
        dds::pub::qos::DataWriterQos be_volatile;
        be_volatile << dds::core::policy::Reliability::BestEffort();
        be_volatile << dds::core::policy::Durability::Volatile();
        dds::pub::DataWriter<DynData> src_writer(
            dds::pub::Publisher(src_dp), src_topic, be_volatile);

        // --- (1) Output readiness: source discovered, no local reader -> route waits ---
        RouteFacts waiting = wait_for(status_reader, [](const RouteFacts &f) {
            return f.discovery == RouterRouteDiscoveryState::DISCOVERY_PARTIAL;
        });
        CHECK(waiting.seen);
        CHECK(waiting.discovery == RouterRouteDiscoveryState::DISCOVERY_PARTIAL);
        CHECK(waiting.state == RouterRouteOperationalState::ROUTE_WAITING_FOR_DISCOVERY);
        // Grace: still no writer after the source has been visible a while.
        std::this_thread::sleep_for(std::chrono::seconds(2));
        RouteFacts still_waiting = read_facts(status_reader);
        CHECK(still_waiting.state
              == RouterRouteOperationalState::ROUTE_WAITING_FOR_DISCOVERY);

        // --- (3 setup) Local reader: RELIABLE+VOLATILE, AUTOMATIC liveliness lease 2s,
        //     deadline 2s. Its appearance must activate the route (1). ---
        dds::topic::Topic<DynData> sink_topic(sink_dp, topic, cmd_type);
        dds::sub::qos::DataReaderQos sink_qos;
        sink_qos << dds::core::policy::Reliability::Reliable();
        sink_qos << dds::core::policy::Durability::Volatile();
        sink_qos << dds::core::policy::Deadline(dds::core::Duration::from_secs(2));
        sink_qos << dds::core::policy::Liveliness::Automatic().lease_duration(
                dds::core::Duration::from_secs(2));
        dds::sub::Subscriber sink_sub(sink_dp);
        dds::sub::DataReader<DynData> sink_reader(sink_sub, sink_topic, sink_qos);

        RouteFacts enabled = wait_for(status_reader, [](const RouteFacts &f) {
            return f.state == RouterRouteOperationalState::ROUTE_ENABLED;
        });
        CHECK(enabled.state == RouterRouteOperationalState::ROUTE_ENABLED);
        // Resolved summaries ride the status (D45): weakest-request input, strong offer
        // + derived deadline/liveliness output (AUTOMATIC:2000ms from the sink).
        CHECK(enabled.reader_summary == "BEST_EFFORT,VOLATILE");
        CHECK(enabled.writer_summary
              == "RELIABLE,TRANSIENT_LOCAL,deadline=2000ms,liveliness=AUTOMATIC:2000ms");

        // --- (2) BE+VOLATILE source forwards through the route ---
        int received = 0;
        for (int i = 0; i < 80 && received == 0; ++i) {
            DynData cmd(cmd_type);
            cmd.value<std::string>("msg.destination", std::string("Platform_30"));
            cmd.value<int32_t>("msg.seq", i);
            src_writer.write(cmd);
            std::this_thread::sleep_for(std::chrono::milliseconds(150));
            auto samples = sink_reader.take();
            for (auto it = samples.begin(); it != samples.end(); ++it) {
                if (it->info().valid()) ++received;
            }
        }
        CHECK(received > 0);

        // --- (3) Sink observes route-writer liveliness with no router-side asserts
        //     (derived kind is AUTOMATIC — the middleware asserts, D42) ---
        CHECK(sink_reader.liveliness_changed_status().alive_count() == 1);

        // --- (5) A later reader with a tighter deadline tightens the offer in place ---
        dds::sub::qos::DataReaderQos tight_qos;
        tight_qos << dds::core::policy::Reliability::Reliable();
        tight_qos << dds::core::policy::Durability::Volatile();
        tight_qos << dds::core::policy::Deadline(
                dds::core::Duration::from_millisecs(500));
        dds::sub::DataReader<DynData> tight_reader(sink_sub, sink_topic, tight_qos);

        RouteFacts tightened = wait_for(status_reader, [](const RouteFacts &f) {
            return f.writer_summary.find("deadline=500ms") != std::string::npos;
        });
        CHECK(tightened.writer_summary
              == "RELIABLE,TRANSIENT_LOCAL,deadline=500ms,liveliness=AUTOMATIC:2000ms");
        CHECK(tightened.state == RouterRouteOperationalState::ROUTE_ENABLED);
        // In place means no teardown cycle: the tight reader must match the SAME writer
        // and receive data without the route ever leaving ENABLED.
        int tight_received = 0;
        for (int i = 0; i < 80 && tight_received == 0; ++i) {
            DynData cmd(cmd_type);
            cmd.value<std::string>("msg.destination", std::string("Platform_30"));
            cmd.value<int32_t>("msg.seq", 1000 + i);
            src_writer.write(cmd);
            std::this_thread::sleep_for(std::chrono::milliseconds(150));
            auto samples = tight_reader.take();
            for (auto it = samples.begin(); it != samples.end(); ++it) {
                if (it->info().valid()) ++tight_received;
            }
            RouteFacts f = read_facts(status_reader);
            CHECK(f.state == RouterRouteOperationalState::ROUTE_ENABLED);
        }
        CHECK(tight_received > 0);

        // --- (4a) TRANSIENT-requesting reader: loud warning naming DURABILITY ---
        dds::sub::qos::DataReaderQos transient_qos;
        transient_qos << dds::core::policy::Reliability::Reliable();
        transient_qos << dds::core::policy::Durability::Transient();
        dds::sub::DataReader<DynData> transient_reader(sink_sub, sink_topic,
                                                       transient_qos);
        RouteFacts warned = wait_for(status_reader, [](const RouteFacts &f) {
            return f.qos_warning == "writer:DURABILITY";
        });
        CHECK(warned.qos_warning == "writer:DURABILITY");
        CHECK(warned.state == RouterRouteOperationalState::ROUTE_ENABLED); // warn only

        // --- (4b) EXCLUSIVE-ownership source writer: reader-side OWNERSHIP warning
        //     (ownership RxO is equality — the SHARED route reader never matches it) ---
        dds::pub::qos::DataWriterQos exclusive_qos;
        exclusive_qos << dds::core::policy::Ownership::Exclusive();
        dds::pub::DataWriter<DynData> exclusive_writer(
            dds::pub::Publisher(src_dp), src_topic, exclusive_qos);
        RouteFacts owner_warned = wait_for(status_reader, [](const RouteFacts &f) {
            return f.qos_warning == "reader:OWNERSHIP";
        });
        CHECK(owner_warned.qos_warning == "reader:OWNERSHIP");
        CHECK(owner_warned.state == RouterRouteOperationalState::ROUTE_ENABLED);

        route_disp.shutdown();
        discovery.shutdown();
        drain.stop();
        aws.stop();

        if (g_failures == 0) {
            std::printf("test_auto_qos: OK in=%d out=%d received=%d tight=%d\n",
                        dom_in, dom_out, received, tight_received);
            return 0;
        }
        std::fprintf(stderr, "test_auto_qos: %d failure(s)\n", g_failures);
        return 1;

    } catch (const std::exception &e) {
        std::fprintf(stderr, "test_auto_qos: exception: %s\n", e.what());
        return 1;
    }
}
