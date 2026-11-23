#include "GatewayModel.hpp"
#include "core/WanPolicy.hpp"

#include <iostream>
#include <memory>
#include <yaml-cpp/yaml.h>

int main(int argc, char** argv) {
    if (argc < 3 || (std::string(argv[1]) != "--properties" && argc < 5)) {
        std::cerr << "usage: act_rs_config CONFIG ROLE NODE ROUTER [TEAM ...]\n"
                     "       act_rs_config --properties PROPERTY_MAP [TEAM ...]\n";
        return 2;
    }
    try {
        const auto policy = router::WanPolicy::from_environment();
        const bool explicit_config = std::string(argv[1]) == "--properties";
        const std::vector<std::string> teams(argv + (explicit_config ? 3 : 5), argv + argc);
        std::unique_ptr<act_rs::GatewayModel> owned;
        if (explicit_config) {
            const auto properties = YAML::LoadFile(argv[2]).as<std::map<std::string, std::string>>();
            owned.reset(new act_rs::GatewayModel(properties));
            if (!teams.empty()) owned->update_teams(teams);
        } else {
            owned.reset(new act_rs::GatewayModel(argv[1], argv[2], argv[3], argv[4], teams));
        }
        auto& model = *owned;
        YAML::Node result;
        result["node"] = model.config.node_name;
        result["role"] = model.config.node_role;
        result["router"] = model.config.router_name;
        result["wan_contract"] = model.config.wan_contract;
        result["teams"] = teams;
        result["types"] = model.config.types_xml_path;
        result["qos_libraries"] = model.config.qos_library_paths;
        result["presence"] = !model.config.presence_participant.empty();
        result["receive_profile"] = model.receive_profile();
        result["receive_state"] = model.receive_state;
        result["timeout"] = policy.timeout_seconds;
        result["heartbeat"] = policy.heartbeat_seconds;
        result["retries"] = policy.heartbeat_retries;
        result["async_bandwidth_bps"] = model.async_bandwidth_bps;
        result["properties"] = model.properties();
        for (auto property : result["properties"]) {
            property.second.SetTag("tag:yaml.org,2002:str");
        }
        result["admin_participant"] = model.config.admin_participant;
        for (const auto& field : {"node", "role", "router", "wan_contract", "types", "admin_participant"}) {
            result[field].SetTag("tag:yaml.org,2002:str");
        }
        for (const auto& priority : model.topic_priorities) {
            result["topic_priorities"][priority.first] = priority.second;
            result["publication_priorities"][priority.first] =
                model.publication_priority(priority.first);
            result["topic_families"][priority.first] = model.transport_mapping(priority.first).family;
        }
        for (const auto& participant : model.config.participants) {
            if (!participant.role.empty() && participant.role != model.config.node_role) continue;
            if (participant.domain < 0 || participant.domain > 232) {
                throw std::runtime_error("DDS domains must be between 0 and 232");
            }
            YAML::Node entry;
            entry["name"] = participant.name;
            entry["domain"] = participant.domain;
            entry["profile"] = explicit_config ? "" : model.profile(participant.qos_profile_alias, true);
            entry["partitions"] = participant.participant_partition;
            entry["team_partition"] = participant.team_partition;
            result["participants"].push_back(entry);
        }
        for (bool source : {true, false}) {
            for (const auto& topic : source ? model.sources : model.destinations) {
                const auto& binding = topic.second.front();
                YAML::Node entry;
                entry["name"] = source ? model.input_port(topic.first) : model.output_port(topic.first);
                entry["topic"] = topic.first;
                entry["type"] = model.transport_mapping(topic.first).type;
                entry["participant"] = binding.participant;
                const auto filtered = model.periodic_input_profiles.find(topic.first);
                auto profile = model.profile(binding.qos, source);
                const std::string family = model.transport_mapping(topic.first).family;
                if (source && (family == "Periodic" || family == "Status")) {
                    if (filtered != model.periodic_input_profiles.end()) {
                        profile = filtered->second;
                    } else if (profile == "ACT_QOS_LIB::lan_status_1s") {
                        // The inherited legacy 1 Hz alias is not an explicit v1 opt-in.
                        profile = "ACT_QOS_LIB::lan_status";
                    }
                }
                entry["profile"] = profile;
                result[source ? "inputs" : "outputs"].push_back(entry);
            }
        }
        for (const auto& family : model.families()) {
            const auto alias = model.is_event_family(family) ? "wan_event"
                             : model.is_state_family(family) ? "wan_contacts" : "wan_status";
            YAML::Node entry;
            entry["name"] = family;
            entry["type"] = model.transport_type(family);
            entry["participant"] = model.wan.name;
            entry["profile"] = explicit_config ? "" : model.profile(alias, true);
            entry["writer_profile"] = model.six_topic()
                ? (family == "P1_Event" ? "ACT_QOS_LIB::rs_six_topic_p1_event"
                   : family == "P1_Periodic" ? "ACT_QOS_LIB::rs_six_topic_p1_periodic"
                   : family == "P2_Event" ? "ACT_QOS_LIB::rs_six_topic_p2_event"
                   : family == "P2_State" ? "ACT_QOS_LIB::rs_six_topic_p2_state"
                   : family == "P2_Periodic" ? "ACT_QOS_LIB::rs_six_topic_p2_periodic"
                                             : "ACT_QOS_LIB::rs_six_topic_p3_periodic")
                : model.three_topic()
                ? (family == "Event" ? "ACT_QOS_LIB::rs_three_topic_event"
                   : family == "State" ? "ACT_QOS_LIB::rs_three_topic_state"
                                       : "ACT_QOS_LIB::rs_three_topic_periodic")
                : (family == "Event" ? "ACT_QOS_LIB::rs_wan_event"
                                    : "ACT_QOS_LIB::rs_wan_status");
            const auto filter = model.receive_filter(family);
            entry["expression"] = filter.expression;
            entry["parameters"] = filter.parameters;
            result["wan"].push_back(entry);
        }
        YAML::Emitter output;
        output << result;
        std::cout << output.c_str() << "\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "act_rs_config: " << error.what() << "\n";
        return 2;
    }
}
