#include "GatewayModel.hpp"

#include <algorithm>
#include <stdexcept>
#include <yaml-cpp/yaml.h>

namespace act_rs {

GatewayModel::GatewayModel(const std::string& path, const std::string& role,
                          const std::string& node, const std::string& name,
                          const std::vector<std::string>& teams) {
    std::string error;
    if (!router::parse_route_config(path, config, error, role, node)) {
        throw std::runtime_error(error);
    }
    if (!name.empty()) config.router_name = name;
    if (config.wan_contract != "two_topic_union") {
        throw std::runtime_error("Routing Service variant requires wan_contract: two_topic_union");
    }
    if (!router::validate_wan_contract(config, error) ||
        !router::validate_qos_aliases(config, error)) {
        throw std::runtime_error(error);
    }
    for (const auto& mapping : router::wan_mappings()) {
        const std::string topic = mapping.topic;
        const bool high = topic == "ControlCommand" || topic == "PlatformCommandAck" ||
                          topic == "ActTeamAssignment" || topic == "ActPlatformStatusMode" ||
                          topic == "RouterHealth";
        topic_priorities[topic] = high ? "high" : "normal";
    }
    const auto settings = YAML::LoadFile(path)["routing_service"];
    if (settings) {
        if (!settings.IsMap()) throw std::runtime_error("routing_service must be a mapping");
        for (const auto& setting : settings) {
            const auto key = setting.first.as<std::string>();
            if (key != "topic_priorities" && key != "async_bandwidth_bps") {
                throw std::runtime_error("unknown routing_service setting: " + key);
            }
        }
        if (settings["async_bandwidth_bps"]) {
            async_bandwidth_bps = settings["async_bandwidth_bps"].as<int>();
            if (async_bandwidth_bps < 2000000 || async_bandwidth_bps % 800 != 0) {
                throw std::runtime_error(
                    "routing_service.async_bandwidth_bps must be >= 2000000 "
                    "and a multiple of 800 (10ms periods, minimum 2500-byte token)");
            }
        }
        if (settings["topic_priorities"]) {
            const auto priorities = settings["topic_priorities"];
            if (!priorities.IsMap()) {
                throw std::runtime_error("routing_service.topic_priorities must be a mapping");
            }
            for (const auto& priority : priorities) {
                const auto topic = priority.first.as<std::string>();
                router::wan_mapping(topic);
                const auto level = priority.second.as<std::string>();
                if (level != "high" && level != "normal") {
                    throw std::runtime_error("priority for " + topic + " must be high or normal");
                }
                topic_priorities[topic] = level;
            }
        }
    }
    for (const auto& participant : config.participants) {
        if (participant.on_wan &&
            (participant.role.empty() || participant.role == config.node_role)) {
            if (!wan.name.empty()) throw std::runtime_error("multiple local WAN participants");
            wan = participant;
        }
    }
    if (wan.name.empty()) throw std::runtime_error("no local WAN participant");
    if (!teams.empty() && !wan.team_partitions_on_endpoints) {
        throw std::runtime_error("startup teams require a team-scoped platform participant");
    }
    for (const auto& team : teams) {
        if (std::find(wan.team_partition.begin(), wan.team_partition.end(), team) ==
            wan.team_partition.end()) {
            wan.team_partition.push_back(team);
        }
    }
    for (const auto& route : config.routes) {
        const bool source = route.output.participant == wan.name;
        const bool destination = route.input.participant == wan.name;
        if (source == destination) {
            throw std::runtime_error("Routing Service variant requires LAN/WAN routes: " +
                                     route.route_name);
        }
        const auto& lan = source ? route.input : route.output;
        if (!lan.subscriber_partition.empty() || !lan.publisher_partition.empty()) {
            throw std::runtime_error("two-topic LAN partitions are unsupported: " + route.route_name);
        }
        for (const auto& topic : route.topics) {
            const auto& mapping = router::wan_mapping(topic.name);
            auto& bindings = (source ? sources : destinations)[topic.name];
            Binding binding{topic.name, lan.participant,
                            source ? lan.reader_qos : lan.writer_qos,
                            std::string(mapping.family) == "Status" || route.desired_enabled,
                            router::wan_interest(route, wan, config.node_name, source, topic.name)};
            if (!bindings.empty() &&
                (bindings.front().participant != binding.participant ||
                 bindings.front().qos != binding.qos)) {
                throw std::runtime_error("conflicting static LAN ports for " + topic.name);
            }
            bindings.push_back(std::move(binding));
        }
    }
    std::vector<router::WanInterest> active;
    for (const auto& topic : sources) {
        for (const auto& binding : topic.second) {
            if (binding.enabled) active.push_back(binding.interest);
        }
    }
    router::validate_wan_source_interests(active);
}

int GatewayModel::publication_priority(const std::string& topic) const {
    const bool high = topic_priorities.at(topic) == "high";
    return std::string(router::wan_mapping(topic).family) == "Event"
        ? (high ? 400 : 200) : (high ? 300 : 100);
}

std::string GatewayModel::profile(const std::string& alias, bool reader) const {
    if (alias.empty() || alias == "default") {
        return reader ? "ACT_QOS_LIB::router_default_reader" : "ACT_QOS_LIB::router_default_writer";
    }
    const auto found = config.qos_profiles.find(alias);
    if (found == config.qos_profiles.end()) {
        throw std::runtime_error("unresolved QoS alias: " + alias);
    }
    return found->second;
}

router::WanReceiveFilter GatewayModel::receive_filter(const std::string& family) const {
    std::vector<router::WanInterest> interests;
    for (const auto& topic : destinations) {
        if (family != router::wan_mapping(topic.first).family) continue;
        for (const auto& binding : topic.second) {
            if (binding.enabled) interests.push_back(binding.interest);
        }
    }
    return router::wan_receive_filter(family, config.node_name, interests);
}

router::WanMetadata GatewayModel::metadata(
        const std::string& topic, const dds::core::xtypes::DynamicData& data) const {
    router::WanMetadata result;
    bool initialized = false;
    for (const auto& binding : sources.at(topic)) {
        if (!binding.enabled) continue;
        auto current = router::wan_source_metadata(binding.interest, data,
                config.node_name, config.router_name, config.node_role, 0);
        if (!initialized) {
            result = std::move(current);
            initialized = true;
        } else {
            result.control = result.control || current.control;
            result.global = result.global || current.global;
            for (const auto& destination : current.destination_ids) {
                if (std::find(result.destination_ids.begin(), result.destination_ids.end(),
                              destination) == result.destination_ids.end()) {
                    result.destination_ids.push_back(destination);
                }
            }
        }
    }
    if (!initialized) throw std::runtime_error("no enabled source binding for " + topic);
    if (result.destination_ids.size() > 16) throw std::runtime_error("too many destination IDs");
    return result;
}

} // namespace act_rs
