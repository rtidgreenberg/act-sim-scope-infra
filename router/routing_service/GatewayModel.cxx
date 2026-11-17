#include "GatewayModel.hpp"
#include "config/Sha256.hpp"
#include "core/Log.hpp"

#include <algorithm>
#include <set>
#include <sstream>
#include <stdexcept>
#include <yaml-cpp/yaml.h>

namespace act_rs {
namespace {

using Properties = std::map<std::string, std::string>;

const std::string& required(const Properties& properties, const std::string& key) {
    const auto found = properties.find(key);
    if (found == properties.end() || found->second.empty()) {
        throw std::runtime_error("missing processor property: " + key);
    }
    return found->second;
}

std::vector<std::string> split(const std::string& value) {
    std::vector<std::string> result;
    if (value.empty()) return result;
    std::istringstream input(value);
    std::string item;
    while (std::getline(input, item, ';')) {
        if (item.empty() || std::find(result.begin(), result.end(), item) != result.end()) {
            throw std::runtime_error("empty or duplicate list entry: " + value);
        }
        result.push_back(item);
    }
    if (value.back() == ';') throw std::runtime_error("empty list entry: " + value);
    return result;
}

std::string join(const std::vector<std::string>& values) {
    std::string result;
    for (const auto& value : values) {
        if (value.find(';') != std::string::npos) {
            throw std::runtime_error("semicolon is reserved in XML property lists");
        }
        if (!result.empty()) result += ';';
        result += value;
    }
    return result;
}

int integer(const std::string& value, const std::string& key, int low, int high) {
    size_t end = 0;
    int number;
    try { number = std::stoi(value, &end); }
    catch (const std::exception&) { throw std::runtime_error("invalid integer property: " + key); }
    if (end != value.size() || number < low || number > high) {
        throw std::runtime_error("out-of-range processor property: " + key);
    }
    return number;
}

router::WanInterest interest(const std::string& topic, const std::string& recipients,
                            const router::ParticipantState& wan, const std::string& node,
                            bool source) {
    router::WanInterest result;
    result.topic = topic;
    for (const auto& recipient : split(recipients)) {
        if (recipient == "control") result.control = true;
        else if (recipient == "global") result.global = true;
        else if (recipient == "addressed") {
            if (topic != "Mission" && topic != "ActPlatformStatusMode") {
                throw std::runtime_error("addressed recipients unsupported for " + topic);
            }
            result.addressed = true;
            if (!source) {
                result.destination_ids.insert(result.destination_ids.end(),
                                              wan.team_partition.begin(), wan.team_partition.end());
                result.destination_ids.push_back(node);
            }
        } else if (recipient == "teams") {
            result.destination_ids.insert(result.destination_ids.end(),
                                          wan.team_partition.begin(), wan.team_partition.end());
            result.destination_ids.push_back(node);
        } else if (recipient.compare(0, 6, "exact:") == 0) {
            result.destination_ids.push_back(recipient.substr(6));
        } else if (recipient != "none" || recipients != "none") {
            throw std::runtime_error("unknown recipients for " + topic + ": " + recipients);
        }
    }
    std::sort(result.destination_ids.begin(), result.destination_ids.end());
    result.destination_ids.erase(std::unique(result.destination_ids.begin(),
        result.destination_ids.end()), result.destination_ids.end());
    router::validate_wan_source_interests({result});
    return result;
}

std::string recipients(const router::WanInterest& value,
                       const router::ParticipantState& wan, const std::string& node,
                       bool source) {
    std::vector<std::string> selectors;
    if (value.addressed) selectors.push_back("addressed");
    if (value.global) selectors.push_back("global");
    if (value.control) selectors.push_back("control");
    const auto dynamic = interest(value.topic, "teams", wan, node, source);
    auto ids = value.destination_ids;
    std::sort(ids.begin(), ids.end());
    if (ids == dynamic.destination_ids && !value.addressed) selectors.push_back("teams");
    else if (!value.addressed) {
        for (const auto& id : ids) selectors.push_back("exact:" + id);
    }
    return selectors.empty() ? "none" : join(selectors);
}

} // namespace

std::string diagnostic_message(const std::string& message) {
    std::string result = message;
    if (result.size() <= 255) return result;
    if (result.find("ROLLBACK FAILED") != std::string::npos) {
        result = "ROLLBACK FAILED; " + result;
    }
    size_t end = 252;
    while (end && (static_cast<unsigned char>(result[end]) & 0xc0) == 0x80) --end;
    return result.substr(0, end) + "...";
}

std::string lan_participant_identity(const std::string& node, const std::string& router_name,
                                     const std::string& participant) {
    const auto identity = node + "/" + router_name + "/" + participant;
    if (identity.size() <= 255) return identity;
    std::string canonical;
    for (const auto& value : {node, router_name, participant}) {
        canonical += std::to_string(value.size()) + ":" + value;
    }
    return "act.lan.sha256/" + router::sha256_hex(canonical);
}

GatewayModel::GatewayModel(const Properties& properties) : explicit_(true) {
    std::string canonical;
    for (const auto& property : properties) {
        if (property.first == "act.runtime_mode" || property.first == "act.route_role" ||
            property.first == "act.route_topic") continue;
        canonical += std::to_string(property.first.size()) + ":" + property.first +
                     std::to_string(property.second.size()) + ":" + property.second;
    }
    config.config_hash = router::sha256_hex(canonical);
    const std::set<std::string> globals{
        "act.version", "act.types_uri", "act.node", "act.role", "act.router",
        "act.wan_contract", "act.monitoring", "act.teams", "act.admin_participant",
        "act.lan_participant", "act.wan_participant", "act.lan_domain", "act.wan_domain",
        "act.async_bandwidth_bps", "act.receive_policy",
        "act.runtime_mode", "act.route_role", "act.route_topic"};
    std::map<std::string, Properties> groups;
    for (const auto& property : properties) {
        if (property.second.find("$(") != std::string::npos ||
            property.second.find("${") != std::string::npos) {
            throw std::runtime_error("unresolved variable in processor property: " + property.first);
        }
        if (globals.count(property.first)) continue;
        const std::string prefix = "act.binding.";
        const auto dot = property.first.find('.', prefix.size());
        if (property.first.compare(0, prefix.size(), prefix) != 0 ||
            dot == std::string::npos || dot == prefix.size()) {
            throw std::runtime_error("unknown processor property: " + property.first);
        }
        const auto field = property.first.substr(dot + 1);
        const std::set<std::string> fields{"topic_name", "type", "channel", "branch",
            "instance_id", "tx.port", "rx.port", "tx.recipients", "rx.recipients",
            "tx.enabled", "rx.enabled"};
        if (!fields.count(field)) throw std::runtime_error("unknown binding property: " + property.first);
        groups[property.first.substr(prefix.size(), dot - prefix.size())][field] = property.second;
    }
    if (required(properties, "act.version") != "1") {
        throw std::runtime_error("unsupported act.version (expected 1)");
    }
    const auto runtime = properties.find("act.runtime_mode");
    if (runtime != properties.end()) {
        if (runtime->second != "independent") throw std::runtime_error("unsupported act.runtime_mode");
        const auto& role = required(properties, "act.route_role");
        if (role != "hub" && role != "tx" && role != "rx")
            throw std::runtime_error("unsupported act.route_role");
        if (role != "hub") required(properties, "act.route_topic");
        else if (properties.count("act.route_topic"))
            throw std::runtime_error("hub must not declare act.route_topic");
    } else if (properties.count("act.route_role") || properties.count("act.route_topic")) {
        throw std::runtime_error("route role requires act.runtime_mode");
    }
    config.node_name = required(properties, "act.node");
    config.node_role = required(properties, "act.role");
    const auto policy = properties.find("act.receive_policy");
    if (policy != properties.end()) receive_policy_ = policy->second;
    if (receive_policy_ != "mission" && receive_policy_ != "static") {
        throw std::runtime_error("act.receive_policy must be mission or static");
    }
    config.router_name = required(properties, "act.router");
    config.types_xml_path = required(properties, "act.types_uri");
    config.wan_contract = required(properties, "act.wan_contract");
    if (config.node_role != "platform" && config.node_role != "control") {
        throw std::runtime_error("act.role must be platform or control");
    }
    if (!three_topic() && config.wan_contract != "two_topic_union") {
        throw std::runtime_error("unsupported act.wan_contract");
    }
    const auto monitoring = required(properties, "act.monitoring");
    if (monitoring != "true" && monitoring != "false") {
        throw std::runtime_error("act.monitoring must be true or false");
    }
    router::ParticipantState lan;
    lan.name = required(properties, "act.lan_participant");
    lan.role = config.node_role;
    lan.domain = integer(required(properties, "act.lan_domain"), "act.lan_domain", 0, 232);
    wan.name = required(properties, "act.wan_participant");
    wan.role = config.node_role;
    wan.domain = integer(required(properties, "act.wan_domain"), "act.wan_domain", 0, 232);
    if (wan.name == lan.name) throw std::runtime_error("LAN and WAN participants must differ");
    wan.on_wan = true;
    wan.team_scoped = config.node_role == "platform";
    wan.team_partitions_on_endpoints = true;
    config.admin_participant = required(properties, "act.admin_participant");
    if (config.admin_participant != lan.name) {
        throw std::runtime_error("act.admin_participant must be the local LAN participant");
    }
    config.presence_participant = monitoring == "true" ? wan.name : "";
    config.participants = {lan, wan};
    const auto bandwidth = properties.find("act.async_bandwidth_bps");
    if (bandwidth != properties.end()) {
        async_bandwidth_bps = integer(bandwidth->second, bandwidth->first, 2000000, 2147483647);
        if (async_bandwidth_bps % 800) throw std::runtime_error("async bandwidth must be a multiple of 800");
    }
    for (const auto& mapping : router::wan_mappings()) {
        const std::string topic = mapping.topic;
        topic_priorities[topic] = (topic == "Mission" || topic == "PlatformCommandAck" ||
            topic == "Team" || topic == "ActPlatformStatusMode" ||
            topic == "RouterHealth") ? "high" : "normal";
    }
    if (groups.empty()) throw std::runtime_error("at least one explicit act.binding is required");
    std::set<std::string> topics, ports;
    for (const auto& group : groups) {
        const auto& binding = group.second;
        const auto topic = required(binding, "topic_name");
        if (!topics.insert(topic).second) throw std::runtime_error("duplicate binding topic: " + topic);
        binding_ids_[topic] = group.first;
        const auto channel = required(binding, "channel");
        const auto& catalog = router::wan_mappings();
        const auto builtin = std::find_if(catalog.begin(), catalog.end(),
                [&](const router::WanMapping& mapping) { return mapping.topic == topic; });
        if (builtin == catalog.end()) {
            if (receive_policy_ != "static" || monitoring != "false") {
                throw std::runtime_error("custom bindings require static receive policy and monitoring=false");
            }
            const auto separator = channel.find('_');
            const auto channel_family = channel.substr(0, separator);
            const std::string family = channel_family == "event" ? "Event" :
                channel_family == "state" ? "State" :
                channel_family == "periodic" ? (three_topic() ? "Periodic" : "Status") : "";
            if (family.empty() || (family == "State" && !three_topic())) {
                throw std::runtime_error("unsupported channel for " + topic);
            }
            custom_mappings_[topic] = router::wan_full_type_mapping(
                dds::core::QosProvider(config.types_xml_path), topic, required(binding, "type"),
                family == "Event" || family == "State" ? "Event" : "Status",
                required(binding, "branch"), required(binding, "instance_id"));
            custom_families_[topic] = family;
        }
        const auto mapping = builtin == catalog.end() ? custom_mappings_.at(topic) : *builtin;
        if (required(binding, "type") != mapping.type ||
            required(binding, "branch") != mapping.branch ||
            required(binding, "instance_id") != mapping.instance_field) {
            throw std::runtime_error("binding " + group.first + " disagrees with fixed ACT codec catalog for " + topic);
        }
        const auto family = std::string(transport_mapping(topic).family);
        const auto prefix = family == "Event" ? "event_" : family == "State" ? "state_" : "periodic_";
        if (channel != std::string(prefix) + "high" && channel != std::string(prefix) + "normal") {
            throw std::runtime_error("binding " + group.first + ": channel/family mismatch for " + topic);
        }
        topic_priorities[topic] = channel.substr(channel.find('_') + 1);
        if (topic == "RouterHealth" && channel != "periodic_high") {
            throw std::runtime_error("RouterHealth requires periodic_high");
        }
        bool any = false;
        for (bool source : {true, false}) {
            const std::string direction = source ? "tx" : "rx";
            const auto port = binding.find(direction + ".port");
            if (port == binding.end()) {
                if (binding.count(direction + ".recipients") || binding.count(direction + ".enabled")) {
                    throw std::runtime_error("direction settings without " + direction + ".port for " + topic);
                }
                continue;
            }
            const bool internal_diagnostics = source && topic == "ActRouterDiagnostics" &&
                    !config.presence_participant.empty() && port->second == "monitor_diagnostics";
            if (port->second.empty() || !ports.insert(port->second).second ||
                port->second.compare(0, 7, "wan_in_") == 0 ||
                port->second.compare(0, 8, "wan_out_") == 0 ||
                (port->second.compare(0, 8, "monitor_") == 0 && !internal_diagnostics)) {
                throw std::runtime_error("invalid or duplicate LAN port for " + topic);
            }
            any = true;
            const auto recipient = required(binding, direction + ".recipients");
            const auto enabled_property = binding.find(direction + ".enabled");
            const auto enabled_value = enabled_property == binding.end() ? "true" : enabled_property->second;
            if (enabled_value != "true" && enabled_value != "false") {
                throw std::runtime_error("invalid " + direction + ".enabled for " + topic);
            }
            const bool enabled = enabled_value == "true";
            (source ? input_ports_ : output_ports_)[topic] = port->second;
            (source ? source_recipients_ : destination_recipients_)[topic] = recipient;
            (source ? sources : destinations)[topic].push_back(
                Binding{topic, lan.name, "", enabled, interest(topic, recipient, wan, config.node_name, source)});
            RouterRouteSpec route;
            route.route_name = group.first + "." + direction;
            route.desired_enabled = enabled;
            route.forwarding_mode = "dynamic_data";
            route.input.participant = source ? lan.name : wan.name;
            route.output.participant = source ? wan.name : lan.name;
            RouterRouteTopicSpec spec;
            spec.name = topic;
            route.topics.push_back(spec);
            config.routes.push_back(route);
        }
        if (!any) throw std::runtime_error("binding has neither tx.port nor rx.port: " + topic);
    }
    const auto teams = properties.find("act.teams");
    validate_receive_policy();
    update_teams(teams == properties.end() ? std::vector<std::string>{} : split(teams->second));
    validate_receive_policy();
    router::WanCodec codec{dds::core::QosProvider(config.types_xml_path), codec_mappings()};
}

GatewayModel::GatewayModel(const std::string& path, const std::string& role,
                          const std::string& node, const std::string& name,
                          const std::vector<std::string>& teams) {
    std::string error;
    if (!router::parse_route_config(path, config, error, role, node)) {
        throw std::runtime_error(error);
    }
    if (!name.empty()) config.router_name = name;
    if (config.wan_contract != "two_topic_union" && !three_topic()) {
        throw std::runtime_error(
                "Routing Service variant requires wan_contract: two_topic_union or three_topic_union");
    }
    if (!router::validate_wan_contract(config, error) ||
        !router::validate_qos_aliases(config, error)) {
        throw std::runtime_error(error);
    }
    for (const auto& mapping : router::wan_mappings()) {
        const std::string topic = mapping.topic;
        const bool high = topic == "Mission" || topic == "PlatformCommandAck" ||
                          topic == "Team" || topic == "ActPlatformStatusMode" ||
                          topic == "RouterHealth";
        topic_priorities[topic] = high ? "high" : "normal";
    }
    const auto settings = YAML::LoadFile(path)["routing_service"];
    if (settings) {
        if (!settings.IsMap()) throw std::runtime_error("routing_service must be a mapping");
        for (const auto& setting : settings) {
            const auto key = setting.first.as<std::string>();
            if (key != "topic_priorities" && key != "async_bandwidth_bps" &&
                key != "periodic_input_profiles" && key != "receive_policy") {
                throw std::runtime_error("unknown routing_service setting: " + key);
            }
            if (settings["receive_policy"]) {
                receive_policy_ = settings["receive_policy"].as<std::string>();
                if (receive_policy_ != "mission" && receive_policy_ != "static") {
                    throw std::runtime_error("routing_service.receive_policy must be mission or static");
                }
            }
            if (settings["periodic_input_profiles"]) {
                const auto profiles = settings["periodic_input_profiles"];
                if (!profiles.IsMap()) throw std::runtime_error("periodic_input_profiles must be a mapping");
                for (const auto& entry : profiles) {
                    const auto topic = entry.first.as<std::string>();
                    const std::string family = transport_mapping(topic).family;
                    if (family == "Event" || family == "State") {
                        throw std::runtime_error("LAN input filtering is only supported for Periodic: " + topic);
                    }
                    periodic_input_profiles[topic] = profile(entry.second.as<std::string>(), true);
                }
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
    if (mission_policy()) {
        for (const auto& topic : {"PlatformMissionStatus", "ContactReport"}) {
            const auto found = sources.find(topic);
            if (found == sources.end()) {
                throw std::runtime_error("mission receive policy requires local source: " + std::string(topic));
            }
            if (!destinations.count(topic)) {
                auto binding = found->second.front();
                binding.enabled = true;
                binding.qos = "";
                destinations[topic].push_back(binding);
                RouterRouteSpec route;
                route.route_name = "mission_peer_" + std::string(topic);
                route.desired_enabled = true;
                route.forwarding_mode = "dynamic_data";
                route.input.participant = wan.name;
                route.output.participant = binding.participant;
                RouterRouteTopicSpec spec;
                spec.name = topic;
                route.topics.push_back(spec);
                config.routes.push_back(route);
            }
        }
    }
    update_teams(wan.team_partition);
    validate_receive_policy();
}

bool GatewayModel::mission_policy() const {
    return config.node_role == "platform" && receive_policy_ == "mission";
}

std::string GatewayModel::receive_profile() const {
    if (!mission_policy()) return "static";
    return receive_state == "TRANSIT" || receive_state == "ORBIT" ||
           receive_state == "RTB" || receive_state == "HOLDING" ? "ActiveMission" : "Init";
}

void GatewayModel::validate_receive_policy() const {
    if (!mission_policy()) return;
    for (const auto& topic : {"Mission", "Team", "PlatformMissionStatus", "ContactReport",
                              "PlatformStatus"}) {
        const auto found = destinations.find(topic);
        if (found == destinations.end() || !std::any_of(found->second.begin(), found->second.end(),
                [](const Binding& binding) { return binding.enabled; })) {
            throw std::runtime_error("mission receive policy requires enabled static LAN output: " +
                                     std::string(topic));
        }
    }
    for (const auto& topic : {"PlatformMissionStatus", "ContactReport"}) {
        const auto found = sources.find(topic);
        if (found == sources.end() || !std::any_of(found->second.begin(), found->second.end(),
                [](const Binding& binding) { return binding.enabled; })) {
            throw std::runtime_error("mission receive policy requires enabled local input: " +
                                     std::string(topic));
        }
    }
}

void GatewayModel::update_receive_state(const std::string& state) {
    const std::set<std::string> states{"IDLE", "COMPLETE", "CANCELLED",
                                     "TRANSIT", "ORBIT", "RTB", "HOLDING"};
    if (!states.count(state)) throw std::runtime_error("invalid local mission state: " + state);
    auto next = *this;
    next.receive_state = state;
    next.validate_interests();
    *this = std::move(next);
}

void GatewayModel::apply_filters(GatewayModel next,
        const std::function<void(const std::string&, const router::WanReceiveFilter&)>& apply) {
    std::map<std::string, router::WanReceiveFilter> old_filters, new_filters;
    for (const auto& family : families()) {
        old_filters[family] = receive_filter(family);
        new_filters[family] = next.receive_filter(family);
    }
    try {
        for (const auto& family : families()) apply(family, new_filters.at(family));
    } catch (const std::exception& error) {
        std::string message = std::string("receive filter update failed: ") + error.what();
        // Restore even the failing family: a native update may mutate before throwing.
        for (const auto& family : families()) {
            try {
                apply(family, old_filters.at(family));
            } catch (const std::exception& rollback) {
                message += "; ROLLBACK FAILED " + family + ": " + rollback.what();
            }
        }
        receive_filter_error = diagnostic_message(message);
        router::Log::error("rs.receive_policy.update_failed", {{"error", message}});
        throw std::runtime_error(message);
    }
    next.receive_filter_error.clear();
    *this = std::move(next);
}

void GatewayModel::peer_interests() {
    if (!mission_policy()) return;
    for (auto& binding : destinations.at("PlatformStatus")) {
        binding.interest = router::WanInterest{};
        binding.interest.topic = "PlatformStatus";
        binding.interest.global = true;
    }
    const auto navigation = sources.find("PlatformStatus");
    if (navigation != sources.end()) {
        for (auto& binding : navigation->second) binding.interest.global = true;
    }
    for (const auto& topic : {"PlatformMissionStatus", "ContactReport"}) {
        for (auto& binding : sources.at(topic)) {
            const auto teams = interest(topic, "teams", wan, config.node_name, true);
            for (const auto& id : teams.destination_ids) {
                if (std::find(binding.interest.destination_ids.begin(),
                              binding.interest.destination_ids.end(), id) ==
                              binding.interest.destination_ids.end()) {
                    binding.interest.destination_ids.push_back(id);
                }
            }
            binding.interest.control = true;
        }
        for (auto& binding : destinations.at(topic)) {
            binding.interest = router::WanInterest{};
            binding.interest.topic = topic;
            for (const auto& team : wan.team_partition) {
                if (!router::is_protected_partition_name(wan, config.node_name, team)) {
                    binding.interest.destination_ids.push_back(team);
                }
            }
        }
    }
}

int GatewayModel::publication_priority(const std::string& topic) const {
    const bool high = topic_priorities.at(topic) == "high";
    if (three_topic()) {
        const std::string family = transport_mapping(topic).family;
        if (family == "Event") return high ? 600 : 300;
        if (family == "State") return high ? 400 : 200;
        return high ? 500 : 100;
    }
    return transport_mapping(topic).family == "Event"
        ? (high ? 400 : 200) : (high ? 300 : 100);
}

bool GatewayModel::three_topic() const {
    return config.wan_contract == "three_topic_union";
}

std::vector<std::string> GatewayModel::families() const {
    return three_topic() ? std::vector<std::string>{"Event", "State", "Periodic"}
                         : std::vector<std::string>{"Event", "Status"};
}

router::WanMapping GatewayModel::transport_mapping(const std::string& topic) const {
    const auto custom = custom_mappings_.find(topic);
    if (custom != custom_mappings_.end()) {
        auto mapping = custom->second;
        mapping.family = custom_families_.at(topic);
        if (mapping.family == "State") mapping.qos_profile = "wan_contacts";
        return mapping;
    }
    auto mapping = router::wan_transport_mapping(topic, three_topic());
    if (std::string(mapping.family) == "Telemetry") mapping.family = "Periodic";
    return mapping;
}

std::vector<router::WanMapping> GatewayModel::codec_mappings() const {
    if (custom_mappings_.empty()) return router::wan_mappings();
    std::set<std::string> topics;
    for (const auto& source : sources) topics.insert(source.first);
    for (const auto& destination : destinations) topics.insert(destination.first);
    std::vector<router::WanMapping> result;
    for (const auto& topic : topics) {
        const auto custom = custom_mappings_.find(topic);
        result.push_back(custom == custom_mappings_.end() ? router::wan_mapping(topic) : custom->second);
    }
    return result;
}

std::string GatewayModel::transport_type(const std::string& family) const {
    const auto supported = families();
    if (std::find(supported.begin(), supported.end(), family) == supported.end()) {
        throw std::runtime_error("unsupported Routing Service WAN family: " + family);
    }
    return router::wan_transport_type(family == "Periodic" ? "Telemetry" : family);
}

std::string GatewayModel::input_port(const std::string& topic) const {
    const auto port = input_ports_.find(topic);
    return port == input_ports_.end() ? "lan_in_" + topic : port->second;
}

std::string GatewayModel::output_port(const std::string& topic) const {
    const auto port = output_ports_.find(topic);
    return port == output_ports_.end() ? "lan_out_" + topic : port->second;
}

void GatewayModel::validate_interests() const {
    std::vector<router::WanInterest> active;
    for (const auto& topic : sources) {
        for (const auto& binding : topic.second) {
            if (binding.enabled) active.push_back(binding.interest);
        }
    }
    router::validate_wan_source_interests(active);
    for (const auto& family : families()) receive_filter(family);
}

void GatewayModel::update_teams(const std::vector<std::string>& teams) {
    auto next = *this;
    std::set<std::string> seen;
    for (const auto& team : teams) {
        if (team.find(';') != std::string::npos || !seen.insert(team).second) {
            throw std::runtime_error("invalid or duplicate team: " + team);
        }
        router::WanInterest check;
        check.topic = "PlatformData";
        check.destination_ids = {team};
        router::validate_wan_source_interests({check});
    }
    if (teams.size() > 16) throw std::runtime_error("team set exceeds 16 names");
    next.wan.team_partition.clear();
    if (wan.team_scoped) next.wan.team_partition.push_back(config.node_name);
    for (const auto& entry : wan.team_partition) {
        if (router::is_protected_partition_name(wan, config.node_name, entry) &&
            std::find(next.wan.team_partition.begin(), next.wan.team_partition.end(), entry) ==
                next.wan.team_partition.end()) {
            next.wan.team_partition.push_back(entry);
        }
    }
    for (const auto& team : teams) {
        if (std::find(next.wan.team_partition.begin(), next.wan.team_partition.end(), team) ==
            next.wan.team_partition.end()) next.wan.team_partition.push_back(team);
    }
    if (next.wan.team_partition.size() > 16) throw std::runtime_error("team set exceeds 16 names");
    for (auto& participant : next.config.participants) {
        if (participant.name == wan.name) participant = next.wan;
    }
    if (explicit_) {
        for (bool source : {true, false}) {
            const auto& selectors = source ? source_recipients_ : destination_recipients_;
            for (auto& topic : source ? next.sources : next.destinations) {
                topic.second.front().interest = interest(topic.first, selectors.at(topic.first),
                    next.wan, config.node_name, source);
            }
        }
    } else {
        std::map<std::string, size_t> source_indexes, destination_indexes;
        for (const auto& route : config.routes) {
            const bool source = route.output.participant == wan.name;
            for (const auto& topic : route.topics) {
                auto& index = (source ? source_indexes : destination_indexes)[topic.name];
                auto& bindings = (source ? next.sources : next.destinations).at(topic.name);
                bindings.at(index++).interest = router::wan_interest(
                    route, next.wan, config.node_name, source, topic.name);
            }
        }
    }
    next.peer_interests();
    next.validate_interests();
    *this = std::move(next);
}

std::map<std::string, std::string> GatewayModel::properties() const {
    Properties result{
        {"act.version", "1"}, {"act.types_uri", config.types_xml_path},
        {"act.node", config.node_name}, {"act.role", config.node_role},
        {"act.router", config.router_name}, {"act.wan_contract", config.wan_contract},
        {"act.receive_policy", receive_policy_},
        {"act.monitoring", config.presence_participant.empty() ? "false" : "true"},
        {"act.admin_participant", config.admin_participant},
        {"act.wan_participant", wan.name}, {"act.wan_domain", std::to_string(wan.domain)},
        {"act.async_bandwidth_bps", std::to_string(async_bandwidth_bps)}};
    std::vector<std::string> teams;
    for (const auto& name : wan.team_partition) {
        if (!router::is_protected_partition_name(wan, config.node_name, name)) teams.push_back(name);
    }
    result["act.teams"] = join(teams);
    for (const auto& participant : config.participants) {
        if (participant.name == config.admin_participant) {
            result["act.lan_participant"] = participant.name;
            result["act.lan_domain"] = std::to_string(participant.domain);
        }
    }
    std::set<std::string> topics;
    for (const auto& topic : sources) topics.insert(topic.first);
    for (const auto& topic : destinations) topics.insert(topic.first);
    for (const auto& topic : topics) {
        const auto mapping = transport_mapping(topic);
        const auto id = binding_ids_.find(topic);
        const auto prefix = "act.binding." + (id == binding_ids_.end() ? topic : id->second) + ".";
        result[prefix + "topic_name"] = topic;
        result[prefix + "type"] = mapping.type;
        result[prefix + "branch"] = mapping.branch;
        result[prefix + "instance_id"] = mapping.instance_field;
        const std::string family = mapping.family;
        result[prefix + "channel"] = (family == "Event" ? "event_" :
            family == "State" ? "state_" : "periodic_") + topic_priorities.at(topic);
        for (bool source : {true, false}) {
            const auto& bindings = source ? sources : destinations;
            const auto found = bindings.find(topic);
            if (found == bindings.end()) continue;
            const std::string direction = source ? "tx" : "rx";
            std::vector<std::string> selectors;
            bool enabled = false;
            for (const auto& binding : found->second) {
                if (!binding.enabled) continue;
                enabled = true;
                for (const auto& selector : split(recipients(binding.interest, wan, config.node_name, source))) {
                    if (std::find(selectors.begin(), selectors.end(), selector) == selectors.end()) {
                        selectors.push_back(selector);
                    }
                }
            }
            if (selectors.empty()) selectors.push_back("none");
            selectors.erase(std::remove(selectors.begin(), selectors.end(), "none"), selectors.end());
            if (selectors.empty()) selectors.push_back("none");
            result[prefix + direction + ".port"] = source ? input_port(topic) : output_port(topic);
            result[prefix + direction + ".recipients"] = join(selectors);
            result[prefix + direction + ".enabled"] = enabled ? "true" : "false";
        }
    }
    return result;
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
        if (family != transport_mapping(topic.first).family) continue;
        if (mission_policy() && topic.first != "Mission" && topic.first != "Team" &&
            !(receive_profile() == "ActiveMission" &&
              (topic.first == "PlatformMissionStatus" || topic.first == "ContactReport" ||
               topic.first == "PlatformStatus"))) continue;
        for (const auto& binding : topic.second) {
            if (binding.enabled) interests.push_back(binding.interest);
        }
    }
    transport_type(family);
    std::vector<router::WanMapping> mappings;
    if (!custom_mappings_.empty()) {
        for (auto mapping : codec_mappings()) {
            mapping = transport_mapping(mapping.topic);
            if (mapping.family == "Periodic") mapping.family = "Telemetry";
            mappings.push_back(std::move(mapping));
        }
    }
    auto filter = router::wan_receive_filter(family == "Periodic" ? "Telemetry" : family,
                                            config.node_name, interests, three_topic(), mappings);
    if (mission_policy()) {
        // Preserve the qualified discriminator-first audience clauses. An outer
        // role/topic disjunction changes Connext's sequence-filter evaluation.
        for (const auto& topic : {"Mission", "Team", "PlatformMissionStatus", "ContactReport",
                                  "PlatformStatus"}) {
            const auto discriminator = "original_topic = '" + std::string(topic) + "'";
            const auto guarded = "(" + discriminator + " AND source_role = '" +
                (std::string(topic) == "Mission" || std::string(topic) == "Team" ? "control" : "platform") +
                "')";
            size_t position = 0;
            while ((position = filter.expression.find(discriminator, position)) != std::string::npos) {
                filter.expression.replace(position, discriminator.size(), guarded);
                position += guarded.size();
            }
        }
    }
    if (!config.presence_participant.empty()) {
        const auto internal = family == "Event" ? "RouterConfig" :
            (family == "Periodic" || family == "Status") ? "RouterHealth" : "";
        if (std::string(internal).size()) {
            filter.expression = "(" + filter.expression + ") OR (source_id <> %0 AND original_topic = '" +
                                internal + "')";
        }
    }
    size_t size = filter.expression.size();
    for (const auto& parameter : filter.parameters) size += parameter.size() + 1;
    if (size >= 16384) throw std::runtime_error("Routing Service filter exceeds 16384-byte budget");
    return filter;
}

router::WanMetadata GatewayModel::metadata(
        const std::string& topic, const dds::core::xtypes::DynamicData& data) const {
    router::WanMetadata result;
    bool initialized = false;
    for (const auto& binding : sources.at(topic)) {
        if (!binding.enabled) continue;
        router::WanMetadata current;
        if (custom_mappings_.count(topic)) {
            current.source_id = config.node_name;
            current.source_role = config.node_role;
            current.control = binding.interest.control;
            current.global = binding.interest.global;
            current.destination_ids = binding.interest.destination_ids;
        } else {
            current = router::wan_source_metadata(binding.interest, data,
                    config.node_name, config.router_name, config.node_role, 0);
        }
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
