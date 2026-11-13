#include "GatewayModel.hpp"
#include "core/WanPolicy.hpp"

#include <iostream>
#include <yaml-cpp/yaml.h>

int main(int argc, char** argv) {
    if (argc < 5) {
        std::cerr << "usage: act_rs_config CONFIG ROLE NODE ROUTER [TEAM ...]\n";
        return 2;
    }
    try {
        const auto policy = router::WanPolicy::from_environment();
        const std::vector<std::string> teams(argv + 5, argv + argc);
        act_rs::GatewayModel model(argv[1], argv[2], argv[3], argv[4], teams);
        YAML::Node result;
        result["node"] = model.config.node_name;
        result["role"] = model.config.node_role;
        result["router"] = model.config.router_name;
        result["teams"] = teams;
        result["types"] = model.config.types_xml_path;
        result["qos_libraries"] = model.config.qos_library_paths;
        result["presence"] = !model.config.presence_participant.empty();
        result["timeout"] = policy.timeout_seconds;
        result["heartbeat"] = policy.heartbeat_seconds;
        result["retries"] = policy.heartbeat_retries;
        result["async_bandwidth_bps"] = model.async_bandwidth_bps;
        for (const auto& priority : model.topic_priorities) {
            result["topic_priorities"][priority.first] = priority.second;
            result["publication_priorities"][priority.first] =
                model.publication_priority(priority.first);
        }
        for (const auto& participant : model.config.participants) {
            if (!participant.role.empty() && participant.role != model.config.node_role) continue;
            if (participant.domain < 0 || participant.domain > 232) {
                throw std::runtime_error("DDS domains must be between 0 and 232");
            }
            YAML::Node entry;
            entry["name"] = participant.name;
            entry["domain"] = participant.domain;
            entry["profile"] = model.profile(participant.qos_profile_alias, true);
            entry["partitions"] = participant.participant_partition;
            result["participants"].push_back(entry);
        }
        for (bool source : {true, false}) {
            for (const auto& topic : source ? model.sources : model.destinations) {
                const auto& binding = topic.second.front();
                YAML::Node entry;
                entry["name"] = std::string(source ? "lan_in_" : "lan_out_") + topic.first;
                entry["topic"] = topic.first;
                entry["type"] = router::wan_mapping(topic.first).type;
                entry["participant"] = binding.participant;
                entry["profile"] = source &&
                    std::string(router::wan_mapping(topic.first).family) == "Status"
                    ? "ACT_QOS_LIB::lan_status_1s" : model.profile(binding.qos, source);
                result[source ? "inputs" : "outputs"].push_back(entry);
            }
        }
        for (const std::string family : {"Event", "Status"}) {
            const auto alias = family == "Event" ? "wan_event" : "wan_status";
            YAML::Node entry;
            entry["name"] = family;
            entry["participant"] = model.wan.name;
            entry["profile"] = model.profile(alias, true);
            entry["writer_profile"] = family == "Event"
                ? "ACT_QOS_LIB::rs_wan_event" : "ACT_QOS_LIB::rs_wan_status";
            const auto filter = model.receive_filter(family);
            entry["expression"] = filter.expression;
            entry["parameters"] = filter.parameters;
            result["wan"].push_back(entry);
        }
        std::cout << result << "\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "act_rs_config: " << error.what() << "\n";
        return 2;
    }
}
