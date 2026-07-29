// LinkStatsCollector.hpp — Phase 9 link-metrics capture (D14/D18/D81; design:
// docs/cpp_router/link-health.md). Beside PresenceMonitor in the RouterInstance
// composition.
//
// Capture-only by charter (D14): raw per-peer reliable-protocol counters + app-ack RTT
// published on the LAN (ActRouterLinkStats) plus a structured log line per interval. NO
// thresholds, NO health inference — presence (RouterHealth DEAD/STALE) stays the only
// health authority until the netem correlation experiment.
//
// One owner of the WAN endpoints' protocol statuses. Each LinkStatsTick (config-fixed
// link_stats_period, default 1 s — a separate DrainThread knob from the heartbeat/refresh
// ticks) it, on the controller strand:
//   1. polls every registered IWanStatsSource (route WAN legs + PresenceMonitor's
//      RouterHealth pair, the idle-mesh bellwether), which self-compute interval deltas and
//      resolve each peer NAME via the middleware discovery DB (D81 — no roster join);
//   2. drains the app-ack accumulator its probe-writer listener fills, joining send-times
//      by RTPS sequence number and attributing each ack to a peer via
//      matched_subscription_participant_data (D81 item 3);
//   3. publishes one ActRouterLinkStats per peer on the LAN + a log line;
//   4. writes one RouterLinkProbe sample on the WAN (the only new WAN traffic this phase
//      adds — measured in test_link_stats.py's dumpcap check).
//
// Telemetry, not state: no ControllerEvents from the listener, no state_revision bump (D5).
//
// Threading: register/unregister + on_link_stats_tick run on the controller strand; the
// probe reader's take-condition runs on an AWS worker (take+discard to emit APPLICATION_AUTO
// acks back to peers); the app-ack listener runs on a middleware thread and only pushes into
// a mutex-guarded accumulator (held via shared_ptr so a late callback is always safe).
// shutdown() detaches the condition (D32 barrier) and resets the listener before the
// entities close (D31/D32 discipline extended to the codebase's first listener).

#pragma once

#include "Interfaces.hpp"
#include "LinkStatsSink.hpp"

#include "ActTypes.hpp"

#include <rti/core/cond/AsyncWaitSet.hpp>
#include <dds/dds.hpp>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace router {

// The D14/D75-pinned default link-stats poll period.
const int kLinkStatsPeriodMs = 1000;

// Accumulator the app-ack listener fills (middleware thread) and the tick drains (strand).
// Held by shared_ptr so a callback that fires during teardown is still safe.
struct ProbeAckAccumulator {
    struct Ack {
        dds::core::InstanceHandle subscription_handle;
        std::uint64_t rtps_seq = 0;
        std::int64_t recv_unix_ns = 0;
    };
    std::mutex mutex;
    std::vector<Ack> acks;
};

class LinkStatsCollector : public ILinkStatsTick, public IWanStatsRegistry {
public:
    // wan_participant carries the RouterLinkProbe pair (the D18 network = its name);
    // lan_participant (the admin participant) carries ActRouterLinkStats. Conditions
    // attach to aws here — construct BEFORE aws.start()/enable_all() (D52 ordering).
    LinkStatsCollector(rti::core::cond::AsyncWaitSet &aws,
                       dds::domain::DomainParticipant wan_participant,
                       dds::domain::DomainParticipant lan_participant,
                       const std::string &observer_router, // "<node>/<router>" (D79)
                       const std::string &network,         // local WAN participant name
                       int period_ms = kLinkStatsPeriodMs, // configured tick cadence
                       const std::string &probe_topic = "RouterLinkProbe",
                       const std::string &stats_topic = "ActRouterLinkStats");

    ~LinkStatsCollector();

    // IWanStatsRegistry — on the controller strand (dispatcher attach/detach, and
    // router_main's PresenceMonitor registration before the DrainThread starts).
    void register_source(IWanStatsSource *source) override;
    void unregister_source(IWanStatsSource *source) override;

    // ILinkStatsTick — poll + publish, on the controller strand.
    void on_link_stats_tick() override;

    // Detach the probe read condition (D32 barrier) and reset the probe writer's listener
    // before the entities close. Call before aws.stop().
    void shutdown();

private:
    void on_probe_data();            // AWS worker: take+discard -> emit APPLICATION_AUTO acks
    void write_probe();              // strand: one probe sample, record its send-time
    // Resolve a probe-ack subscription handle to a peer name via the discovery DB (D81).
    std::string probe_peer_name(const dds::core::InstanceHandle &handle);

    rti::core::cond::AsyncWaitSet &aws_;
    std::string observer_router_;
    std::string network_;
    int period_ms_; // configured cadence; the first tick's interval_ms fallback

    dds::pub::Publisher wan_publisher_;
    dds::sub::Subscriber wan_subscriber_;
    dds::topic::Topic<RouterLinkProbe> probe_topic_;
    dds::pub::DataWriter<RouterLinkProbe> probe_writer_;
    dds::sub::DataReader<RouterLinkProbe> probe_reader_;
    dds::pub::Publisher lan_publisher_;
    dds::topic::Topic<RouterLinkStats> stats_topic_;
    dds::pub::DataWriter<RouterLinkStats> stats_writer_;

    std::shared_ptr<ProbeAckAccumulator> acks_;
    std::vector<dds::core::cond::Condition> conditions_;

    // Registered WAN endpoint sources. Strand-only, so a plain vector suffices.
    std::vector<IWanStatsSource *> sources_;

    // Probe send-time bookkeeping (strand-only). A single reliable writer assigns strictly
    // monotonic RTPS sequence numbers 1,2,3,… so next_rtps_seq_ mirrors them; the app-ack's
    // sample_identity.sequence_number is that 1-based RTPS seq (spikes/link_probe/, D81).
    std::uint64_t next_rtps_seq_ = 1;
    std::uint64_t probe_seq_ = 0; // 0-based payload counter (informational)
    std::map<std::uint64_t, std::int64_t> send_times_; // rtps_seq -> send unix ns

    std::uint64_t capture_seq_ = 0;
    std::chrono::steady_clock::time_point last_tick_;
    bool have_last_tick_ = false;
    std::atomic<bool> shut_down_;
};

} // namespace router
