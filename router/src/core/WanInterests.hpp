#pragma once

#include "WanAdapterFactory.hpp"
#include "RouterState.hpp"

namespace router {

struct WanInterest {
    std::string topic;
    bool control = false;
    bool global = false;
    bool addressed = false;
    std::vector<std::string> teams;
};

WanInterest wan_interest(const RouterRouteSpec& route, const ParticipantState& wan,
                         const std::string& node, bool source, const std::string& topic);
WanReceiveFilter wan_receive_filter(const std::string& family, const std::string& node,
                                   const std::vector<WanInterest>& interests);
void validate_wan_source_interests(const std::vector<WanInterest>& interests);
WanMetadata wan_source_metadata(const WanInterest& interest,
                               const dds::core::xtypes::DynamicData& data,
                               const std::string& node, const std::string& router,
                               const std::string& role, uint64_t revision);

} // namespace router
