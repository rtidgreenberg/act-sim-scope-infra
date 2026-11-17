#pragma once

#include "GatewayModel.hpp"
#include "GatewayPorts.hpp"
#include "core/LinkStatsCollector.hpp"
#include "core/PresenceMonitor.hpp"

#include <rti/routing/processor/Route.hpp>

namespace act_rs {

class GatewayMonitor : public router::WanPresenceTransport, public router::IWanStatsSource {
public:
    using Data = dds::core::xtypes::DynamicData;
    using Route = rti::routing::processor::Route;

    GatewayMonitor(Route& route, GatewayModel& model, dds::core::QosProvider types,
                   GatewayPorts* ports = nullptr);
    ~GatewayMonitor();
    void tick();
    void received(const std::string& family, const Data& frame, const dds::sub::SampleInfo& info);
    void forwarded(const std::string& topic, bool source, bool valid);
    void failed(const std::string& topic, bool source, const std::string& error, bool timeout);
    static void apply_model(Route& route, GatewayModel& current, GatewayModel next);
    void policy_changed();
    void policy_applied();

    void bind_status(std::shared_ptr<router::WanStatusCallbacks> callbacks) override;
    void bind_config(std::shared_ptr<router::WanConfigCallbacks> callbacks) override;
    dds::pub::DataWriter<Data> status_writer() const override;
    dds::sub::DataReader<Data> status_reader() const override;
    void write_status(const Data& frame) override;
    void write_config(const Data& frame) override;
    std::vector<RouterWriterFilterObservation> writer_filter_observations() const override;
    void collect_wan_stats(router::LinkStatsSink& sink) override;

private:
    struct Counters {
        uint64_t samples = 0;
        uint64_t lifecycle = 0;
        uint64_t timeouts = 0;
        std::string error;
    };
    RouterStatus snapshot() const;
    void command(const RouterCommand& command);
    void update_teams(const std::vector<std::string>& teams);
    void check_deadline();
    static std::string key(const std::string& topic, bool source);

    Route& route_;
    GatewayPorts* ports_;
    GatewayModel& model_;
    rti::core::cond::AsyncWaitSet waitset_;
    std::shared_ptr<router::WanStatusCallbacks> status_callbacks_;
    std::shared_ptr<router::WanConfigCallbacks> config_callbacks_;
    bool policy_audited_ = false;
    std::string audited_profile_;
    std::vector<std::string> audited_teams_;
    std::unique_ptr<router::PresenceMonitor> presence_;
    std::unique_ptr<router::LinkStatsCollector> links_;
    std::map<std::string, Counters> counters_;
    std::map<std::string, std::map<std::string, router::WriterTotals>> writer_previous_;
    std::map<std::string, std::map<std::string, router::ReaderTotals>> reader_previous_;
    std::chrono::steady_clock::time_point last_status_{};
    std::chrono::steady_clock::time_point last_heartbeat_{};
    std::chrono::steady_clock::time_point last_links_{};
    uint64_t revision_ = 1;
    uint64_t heartbeat_ = 0;
    uint64_t diagnostics_ = 0;
    int deadline_total_ = 0;
    std::string command_id_;
};

} // namespace act_rs
