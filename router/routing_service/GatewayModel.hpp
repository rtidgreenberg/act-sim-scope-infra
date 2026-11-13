#pragma once

#include "config/RouteConfigParser.hpp"
#include "core/WanInterests.hpp"

namespace act_rs {

struct Binding {
    std::string topic;
    std::string participant;
    std::string qos;
    bool enabled;
    router::WanInterest interest;
};

class GatewayModel {
public:
    GatewayModel(const std::string& path, const std::string& role,
                 const std::string& node, const std::string& name,
                 const std::vector<std::string>& teams = {});
    router::WanMetadata metadata(const std::string& topic,
                                const dds::core::xtypes::DynamicData& data) const;
    router::WanReceiveFilter receive_filter(const std::string& family) const;
    std::string profile(const std::string& alias, bool reader) const;
    int publication_priority(const std::string& topic) const;

    router::RouteConfig config;
    router::ParticipantState wan;
    std::map<std::string, std::vector<Binding>> sources;
    std::map<std::string, std::vector<Binding>> destinations;
    std::map<std::string, std::string> topic_priorities;
    int async_bandwidth_bps = 2000000;
};

} // namespace act_rs
