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

void add_team(std::vector<std::string>& teams, const std::string& value) {
    if (value.empty() || value.find_first_of("*?[]") != std::string::npos) {
        throw std::runtime_error("two-topic interests require nonempty exact names, not patterns");
    }
    sql_string(value);
    if (std::find(teams.begin(), teams.end(), value) == teams.end()) {
        teams.push_back(value);
    }
    if (teams.size() > kMaxParticipantPartitionEntries) {
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
    } else if (topic == "ActTeamAssignment") {
        result.global = true;
    } else if (partition == "PLATFORM" || (!wan.team_scoped && partition.empty())) {
        result.control = true;
    } else if (partition == "NAVIGATION") {
        result.global = true;
    } else if (partition == "CONTROL") {
        throw std::runtime_error("unsupported CONTROL audience for topic " + topic);
    } else if (!partition.empty()) {
        add_team(result.teams, partition);
    } else {
        const auto& names = wan.team_partitions_on_endpoints
                ? wan.team_partition : wan.participant_partition;
        for (const auto& name : names) {
            if (std::find(wan.protected_partition_entries.begin(),
                          wan.protected_partition_entries.end(), name)
                    == wan.protected_partition_entries.end()) {
                add_team(result.teams, name);
            }
        }
        // Preserve the protected identity's direct-peer interest even before assignment.
        if (wan.team_scoped) {
            add_team(result.teams, node);
        }
    }
    return result;
}

WanReceiveFilter wan_receive_filter(const std::string& family, const std::string& node,
                                   const std::vector<WanInterest>& interests) {
    WanReceiveFilter filter;
    filter.parameters.push_back(sql_string(node));
    std::vector<std::string> teams;
    for (const auto& interest : interests) {
        for (const auto& team : interest.teams) {
            add_team(teams, team);
        }
    }
    const auto cases_for = [&](size_t kind, const std::string& team) {
        std::string cases;
        for (const auto& mapping : wan_mappings()) {
            if (family != mapping.family || std::string(mapping.branch) == "health") {
                continue;
            }
            bool enabled = false;
            for (const auto& interest : interests) {
                if (interest.topic == mapping.topic) {
                    enabled = enabled || (kind == 0 ? interest.control :
                        kind == 1 ? interest.global : kind == 2 ? interest.addressed :
                        std::find(interest.teams.begin(), interest.teams.end(), team)
                            != interest.teams.end());
                }
            }
            if (!enabled) {
                continue;
            }
            if (!cases.empty()) {
                cases += " OR ";
            }
            cases += "payload._d = " + std::to_string(mapping.discriminator);
        }
        return cases;
    };
    const auto add_clause = [&](const std::string& selector, const std::string& cases) {
        if (!cases.empty()) {
            if (!filter.expression.empty()) {
                filter.expression += " OR ";
            }
            // Discriminator-first sequence predicates are qualified on 7.7.
            filter.expression += "((" + cases + ") AND (" + selector + "))";
        }
    };
    const std::vector<std::string> selectors = {
        "audience.control = TRUE", "audience.global = TRUE", "audience.destination = %0"};
    for (size_t kind = 0; kind < selectors.size(); ++kind) {
        add_clause(selectors[kind], cases_for(kind, ""));
    }
    for (const auto& team : teams) {
        std::string matches;
        for (size_t outgoing = 0; outgoing < 16; ++outgoing) {
            if (!matches.empty()) {
                matches += " OR ";
            }
            matches += "audience.team_ids[" + std::to_string(outgoing) + "] = %"
                       + std::to_string(filter.parameters.size());
        }
        filter.parameters.push_back(sql_string(team));
        add_clause(matches, cases_for(3, team));
    }
    if (filter.expression.empty()) {
        filter.expression = "payload._d = 0";
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
    std::map<std::string, std::vector<std::string>> teams;
    for (const auto& interest : interests) {
        for (const auto& team : interest.teams) {
            add_team(teams[interest.topic], team);
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
    metadata.team_ids = interest.teams;
    if (interest.addressed) {
        metadata.destination = data.value<std::string>(
            interest.topic == "ActPlatformStatusMode" ? "platform_node" : "destination");
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
