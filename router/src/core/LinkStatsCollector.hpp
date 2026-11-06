// LAN aggregation of WAN protocol counters and passive RouterHealth RTT.
#pragma once

#include "Interfaces.hpp"
#include "LinkStatsSink.hpp"
#include "ActTypes.hpp"

#include <dds/dds.hpp>
#include <atomic>
#include <chrono>
#include <string>
#include <vector>

namespace router {

const int kLinkStatsPeriodMs = 1000;

class LinkStatsCollector : public ILinkStatsTick, public IWanStatsRegistry {
public:
    LinkStatsCollector(dds::domain::DomainParticipant lan_participant,
                       const std::string &observer_router,
                       const std::string &network,
                       const dds::pub::qos::DataWriterQos &stats_writer_qos,
                       int period_ms = kLinkStatsPeriodMs,
                       const std::string &stats_topic = "ActRouterLinkStats");
    ~LinkStatsCollector();
    void register_source(IWanStatsSource *source) override;
    void unregister_source(IWanStatsSource *source) override;
    void on_link_stats_tick() override;
    // Called after the strand stops, before registered sources are destroyed.
    void shutdown();

private:
    std::string observer_router_;
    std::string network_;
    int period_ms_;
    dds::pub::Publisher lan_publisher_;
    dds::topic::Topic<RouterLinkStats> stats_topic_;
    dds::pub::DataWriter<RouterLinkStats> stats_writer_;
    std::vector<IWanStatsSource *> sources_;
    std::uint64_t capture_seq_ = 0;
    std::chrono::steady_clock::time_point last_tick_;
    bool have_last_tick_ = false;
    std::atomic<bool> shut_down_;
};

} // namespace router
