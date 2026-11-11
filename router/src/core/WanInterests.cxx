#include "WanInterests.hpp"

#include <algorithm>
#include <map>
#include <stdexcept>

namespace router {
namespace {

std::string sql_string(const std::string& value) {
    if (value.size() > 255) {
        throw std::runtime_error("two-topic audience names exceed the 255-byte bound");
    }
    if (value.find('\'') != std::string::npos || value.find('\0') != std::string::npos) {
        throw std::runtime_error("two-topic audience names cannot contain apostrophes or NULs");
    }
    return "'" + value + "'";
}

void add_destination(std::vector<std::string>& destinations, const std::string& value) {
    if (value.empty() || value.find_first_of("*?[]") != std::string::npos) {
        throw std::runtime_error("two-topic interests require nonempty exact names, not patterns");
    }
    sql_string(value);
    if (std::find(destinations.begin(), destinations.end(), value) == destinations.end()) {
        destinations.push_back(value);
    }
    if (destinations.size() > kMaxParticipantPartitionEntries) {
        throw std::runtime_error("two-topic aggregate receive interests exceed 16 names");
    }
}

} // namespace

WanInterest wan_interest(const RouterRouteSpec& route, const ParticipantState& wan,
                         const std::string& node, bool source, const std::string& topic) {
    sql_string(node);
    WanInterest result;
    result.topic = topic;
    const auto& endpoint = source ? route.output : route.input;
    const auto& partition = source ? endpoint.publisher_partition : endpoint.subscriber_partition;
    if ((topic == "ControlCommand" || topic == "ActPlatformStatusMode" ||
         topic == "ActTeamAssignment") && !partition.empty() && partition != "CONTROL") {
        throw std::runtime_error("addressed/broadcast control topics require the CONTROL audience");
    }
    if (topic == "ControlCommand" || topic == "ActPlatformStatusMode") {
        result.addressed = true;
        if (!source) {
            const auto& names = wan.team_partitions_on_endpoints
                    ? wan.team_partition : wan.participant_partition;
            for (const auto& name : names) {
                if (std::find(wan.protected_partition_entries.begin(),
                              wan.protected_partition_entries.end(), name)
                        == wan.protected_partition_entries.end()) {
                    add_destination(result.destination_ids, name);
                }
            }
            add_destination(result.destination_ids, node);
        }
    } else if (topic == "ActTeamAssignment") {
        result.global = true;
    } else if (partition == "PLATFORM" || (!wan.team_scoped && partition.empty())) {
        result.control = true;
    } else if (partition == "NAVIGATION") {
        result.global = true;
    } else if (partition == "CONTROL") {
        throw std::runtime_error("unsupported CONTROL audience for topic " + topic);
    } else if (!partition.empty()) {
        add_destination(result.destination_ids, partition);
    } else {
        const auto& names = wan.team_partitions_on_endpoints
                ? wan.team_partition : wan.participant_partition;
        for (const auto& name : names) {
            if (std::find(wan.protected_partition_entries.begin(),
                          wan.protected_partition_entries.end(), name)
                    == wan.protected_partition_entries.end()) {
                add_destination(result.destination_ids, name);
            }
        }
        // Preserve the protected identity's direct-peer interest even before assignment.
        if (wan.team_scoped) {
            add_destination(result.destination_ids, node);
        }
    }
    return result;
}

WanReceiveFilter wan_receive_filter(const std::string& family, const std::string& node,
                                   const std::vector<WanInterest>& interests) {
    WanReceiveFilter filter;
    filter.parameters.push_back(sql_string(node));
    std::vector<std::string> destinations;
    for (const auto& interest : interests) {
        for (const auto& destination : interest.destination_ids) {
            if (destination != node) {
                add_destination(destinations, destination);
            }
        }
    }
    const auto cases_for = [&](size_t kind, const std::string& destination) {
        std::string cases;
        for (const auto& mapping : wan_mappings()) {
            if (family != mapping.family || std::string(mapping.branch) == "health") {
                continue;
            }
            bool enabled = false;
            for (const auto& interest : interests) {
                if (interest.topic == mapping.topic) {
                    enabled = enabled || (kind == 0 ? interest.control :
                        kind == 1 ? interest.global :
                        std::find(interest.destination_ids.begin(), interest.destination_ids.end(), destination)
                            != interest.destination_ids.end());
                }
            }
            if (!enabled) {
                continue;
            }
            if (!cases.empty()) {
                cases += " OR ";
            }
            cases += "original_topic = " + sql_string(mapping.topic);
        }
        return cases;
    };
    std::vector<std::pair<std::string, std::string>> clauses;
    const auto add_clause = [&](const std::string& selector, const std::string& cases) {
        if (!cases.empty()) {
            for (auto& clause : clauses) {
                if (clause.first == cases) {
                    clause.second += " OR (" + selector + ")";
                    return;
                }
            }
            clauses.emplace_back(cases, "(" + selector + ")");
        }
    };
    const std::vector<std::string> selectors = {
        "audience.control = TRUE", "audience.global = TRUE"};
    for (size_t kind = 0; kind < selectors.size(); ++kind) {
        add_clause(selectors[kind], cases_for(kind, ""));
    }
    const auto destination_selector = [](size_t parameter) {
        std::string matches;
        for (size_t outgoing = 0; outgoing < 16; ++outgoing) {
            if (!matches.empty()) {
                matches += " OR ";
            }
            matches += "audience.destination_ids[" + std::to_string(outgoing) + "] = %"
                       + std::to_string(parameter);
        }
        return matches;
    };
    add_clause(destination_selector(0), cases_for(2, node));
    for (const auto& destination : destinations) {
        const auto parameter = filter.parameters.size();
        filter.parameters.push_back(sql_string(destination));
        add_clause(destination_selector(parameter), cases_for(2, destination));
    }
    for (const auto& clause : clauses) {
        if (!filter.expression.empty()) {
            filter.expression += " OR ";
        }
        filter.expression += "((" + clause.first + ") AND (" + clause.second + "))";
    }
    if (filter.expression.empty()) {
        filter.expression = "original_topic = ''";
    }
    filter.expression = "source_id <> %0 AND (" + filter.expression + ")";
    size_t property_size = filter.expression.size();
    for (const auto& parameter : filter.parameters) {
        property_size += parameter.size() + 1;
    }
    if (property_size >= 16384) {
        throw std::runtime_error("two-topic filter exceeds the qualified 16384-byte property budget");
    }
    return filter;
}

void validate_wan_source_interests(const std::vector<WanInterest>& interests) {
    std::map<std::string, std::vector<std::string>> destinations;
    for (const auto& interest : interests) {
        for (const auto& destination : interest.destination_ids) {
            add_destination(destinations[interest.topic], destination);
        }
    }
}

WanMetadata wan_source_metadata(const WanInterest& interest,
                               const dds::core::xtypes::DynamicData& data,
                               const std::string& node, const std::string& router,
                               const std::string& role, uint64_t revision) {
    WanMetadata metadata;
    metadata.source_id = node;
    metadata.source_role = role;
    metadata.membership_revision = revision;
    metadata.control = interest.control;
    metadata.global = interest.global;
    metadata.destination_ids = interest.destination_ids;
    if (interest.addressed) {
        const auto destination = data.value<std::string>(
            interest.topic == "ActPlatformStatusMode" ? "platform_node" : "destination");
        if (!destination.empty()) {
            add_destination(metadata.destination_ids, destination);
        }
    }
    const auto& mapping = wan_mapping(interest.topic);
    if (std::string(mapping.instance_field) == "source" || interest.topic == "ContactReport") {
        metadata.run_id = data.value<std::string>("run_id");
        metadata.audit_sequence = data.value<uint64_t>("audit_sequence");
        metadata.sent_at_ns = data.value<int64_t>("sent_at_ns");
    } else if (interest.topic == "ActRouterDiagnostics") {
        metadata.audit_sequence = data.value<uint64_t>("snapshot_seq");
        metadata.sent_at_ns = data.value<int64_t>("send_timestamp");
    }
    return metadata;
}

} // namespace router
