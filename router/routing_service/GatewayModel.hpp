#pragma once

#include "config/RouteConfigParser.hpp"
#include "core/WanInterests.hpp"
#include <functional>

namespace act_rs {

std::string diagnostic_message(const std::string& message);
std::string lan_participant_identity(const std::string& node, const std::string& router_name,
                                     const std::string& participant);

struct Binding {
    std::string topic;
    std::string participant;
    std::string qos;
    bool enabled;
    router::WanInterest interest;
};

class GatewayModel {
public:
    explicit GatewayModel(const std::map<std::string, std::string>& properties);
    GatewayModel(const std::string& path, const std::string& role,
                 const std::string& node, const std::string& name,
                 const std::vector<std::string>& teams = {});
    router::WanMetadata metadata(const std::string& topic,
                                const dds::core::xtypes::DynamicData& data) const;
    router::WanReceiveFilter receive_filter(const std::string& family) const;
    std::string profile(const std::string& alias, bool reader) const;
    int publication_priority(const std::string& topic) const;
    bool three_topic() const;
    std::vector<std::string> families() const;
    router::WanMapping transport_mapping(const std::string& topic) const;
    std::string transport_type(const std::string& family) const;
    std::vector<router::WanMapping> codec_mappings() const;
    std::string input_port(const std::string& topic) const;
    std::string output_port(const std::string& topic) const;
    void update_teams(const std::vector<std::string>& teams);
    void update_receive_state(const std::string& state);
    void apply_filters(GatewayModel next,
        const std::function<void(const std::string&, const router::WanReceiveFilter&)>& apply);
    bool mission_policy() const;
    std::string receive_profile() const;
    std::string receive_state = "Init";
    std::string receive_filter_error;
    std::map<std::string, std::string> properties() const;

    router::RouteConfig config;
    router::ParticipantState wan;
    std::map<std::string, std::vector<Binding>> sources;
    std::map<std::string, std::vector<Binding>> destinations;
    std::map<std::string, std::string> topic_priorities;
    std::map<std::string, std::string> periodic_input_profiles;
    int async_bandwidth_bps = 2000000;

private:
    bool explicit_ = false;
    std::string receive_policy_ = "mission";
    void validate_receive_policy() const;
    void peer_interests();
    std::map<std::string, std::string> input_ports_, output_ports_;
    std::map<std::string, std::string> binding_ids_;
    std::map<std::string, std::string> source_recipients_, destination_recipients_;
    std::map<std::string, router::WanMapping> custom_mappings_;
    std::map<std::string, std::string> custom_families_;
    void validate_interests() const;
};

} // namespace act_rs
