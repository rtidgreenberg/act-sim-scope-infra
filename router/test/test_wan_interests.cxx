#include "config/RouteConfigParser.hpp"
#include "core/WanInterests.hpp"

#include <algorithm>
#include <cstdio>
#include <stdexcept>

using namespace router;

static int failures = 0;
#define CHECK(value) do { if (!(value)) { \
    std::fprintf(stderr, "FAIL %d: %s\n", __LINE__, #value); ++failures; } } while (0)

int main(int argc, char** argv) {
    if (argc != 4) {
        return 2;
    }
    dds::core::QosProvider model(argv[1]);
    dds::core::QosProvider profiles(argv[2]);
    profiles.participant_qos("ACT_QOS_LIB::wan_two_topic_participant");
    profiles.participant_qos("ACT_QOS_LIB::wan_two_topic_participant_emane");
    RouteConfig cfg;
    std::string error;
    CHECK(parse_route_config(argv[3], cfg, error));
    CHECK(cfg.wan_contract == "legacy");
    cfg.wan_contract = "two_topic_union";
    cfg.presence_participant.clear();
    cfg.qos_profiles["wan_participant"] = "ACT_QOS_LIB::wan_two_topic_participant";
    cfg.qos_profiles["wan_event"] = "ACT_QOS_LIB::wan_event";
    cfg.qos_profiles["wan_status"] = "ACT_QOS_LIB::wan_union_status";
    CHECK(validate_wan_contract(cfg, error));
    cfg.presence_participant = "platform_wan";
    CHECK(validate_wan_contract(cfg, error));
    RouteConfig events_only = cfg;
    for (auto& configured_route : events_only.routes) {
        auto& topics = configured_route.topics;
        for (auto topic = topics.begin(); topic != topics.end();) {
            if (std::string(wan_mapping(topic->name).family) == "Status") {
                topic = topics.erase(topic);
            } else {
                ++topic;
            }
        }
    }
    events_only.routes.erase(std::remove_if(events_only.routes.begin(), events_only.routes.end(),
        [](const RouterRouteSpec& configured_route) { return configured_route.topics.empty(); }),
        events_only.routes.end());
    CHECK(!events_only.routes.empty());
    CHECK(validate_wan_contract(events_only, error));
    events_only.qos_profiles["wan_status"] = "ACT_QOS_LIB::wan_status";
    CHECK(!validate_wan_contract(events_only, error));
    CHECK(error.find("wan_status") != std::string::npos);
    events_only.presence_participant.clear();
    CHECK(validate_wan_contract(events_only, error));
    events_only.presence_participant = "platform_wan";
    events_only.qos_profiles.erase("wan_status");
    CHECK(!validate_wan_contract(events_only, error));
    CHECK(error.find("wan_status") != std::string::npos);
    events_only.qos_profiles["wan_status"] = "ACT_QOS_LIB::wan_union_status";
    CHECK(validate_wan_contract(events_only, error));
    events_only.routes.clear();
    for (const auto& role : {"platform", "control"}) {
        events_only.node_role = role;
        events_only.presence_participant = std::string(role) + "_wan";
        events_only.qos_profiles["wan_status"] = "ACT_QOS_LIB::wan_union_status";
        CHECK(validate_wan_contract(events_only, error));
        events_only.qos_profiles["wan_status"] = "ACT_QOS_LIB::wan_status";
        CHECK(!validate_wan_contract(events_only, error));
        events_only.wan_contract = "legacy";
        CHECK(validate_wan_contract(events_only, error));
        events_only.wan_contract = "two_topic_union";
    }
    cfg.presence_participant = "platform_lan";
    CHECK(!validate_wan_contract(cfg, error));
    cfg.presence_participant = "control_wan";
    CHECK(!validate_wan_contract(cfg, error));
    cfg.presence_participant.clear();
    cfg.routes.front().topics.front().name = "RouterHealth";
    CHECK(!validate_wan_contract(cfg, error));
    cfg.routes.front().topics.front().name = "Unmapped";
    CHECK(!validate_wan_contract(cfg, error));

    ParticipantState wan;
    wan.team_scoped = true;
    wan.team_partitions_on_endpoints = true;
    wan.team_partition = {"Boat30", "TEAM_A"};
    RouterRouteSpec route;
    route.output.publisher_partition = "PLATFORM";
    auto control = wan_interest(route, wan, "Boat30", true, "PlatformInitStatus");
    CHECK(control.control && !control.global && control.teams.empty());
    route.output.publisher_partition = "NAVIGATION";
    auto global = wan_interest(route, wan, "Boat30", true, "PlatformInitStatus");
    CHECK(global.global && !global.control);
    route.output.publisher_partition.clear();
    auto team = wan_interest(route, wan, "Boat30", true, "PlatformData");
    CHECK(team.teams.size() == 2);
    auto command = wan_interest(route, wan, "Boat30", true, "ControlCommand");
    CHECK(command.addressed && !command.global);
    auto assignment = wan_interest(route, wan, "Boat30", true, "ActTeamAssignment");
    CHECK(assignment.global && !assignment.addressed);

    auto filter = wan_receive_filter("Status", "Boat30", {control, global, team});
    CHECK(filter.expression.find("audience.control = TRUE") != std::string::npos);
    CHECK(filter.expression.find("audience.global = TRUE") != std::string::npos);
    CHECK(filter.expression.find("payload._d = 10") == std::string::npos);
    CHECK(filter.parameters.size() == 3);
    CHECK(wan_receive_filter("Event", "Boat30", {}).expression ==
          "source_id <> %0 AND (payload._d = 0)");
    for (const auto& invalid : std::vector<std::string>{
            "O'BRIEN", std::string(256, 'x'), std::string("TEAM\0A", 6)}) {
        try {
            team.teams = {invalid};
            wan_receive_filter("Status", "Boat30", {team});
            CHECK(false);
        } catch (const std::runtime_error&) {}
        try {
            route.output.publisher_partition = invalid;
            wan_interest(route, wan, "Boat30", true, "PlatformData");
            CHECK(false);
        } catch (const std::runtime_error&) {}
    }
    std::vector<WanInterest> full;
    for (const auto& mapping : wan_mappings()) {
        if (std::string(mapping.family) != "Status" || std::string(mapping.branch) == "health") {
            continue;
        }
        WanInterest selected;
        selected.topic = mapping.topic;
        selected.control = selected.global = selected.addressed = true;
        for (size_t index = 0; index < 16; ++index) {
            selected.teams.push_back(std::to_string(index) + std::string(250, 'x'));
        }
        full.push_back(selected);
    }
    filter = wan_receive_filter("Status", std::string(255, 'n'), full);
    CHECK(filter.parameters.size() == 17);
    CHECK(filter.expression.find("audience.team_ids[15]") != std::string::npos);
    auto alias = full.front();
    validate_wan_source_interests({full.front(), alias});
    alias.teams = {"EXTRA"};
    try {
        validate_wan_source_interests({full.front(), alias});
        CHECK(false);
    } catch (const std::runtime_error&) {}
    alias.topic = "PlatformData";
    if (full.front().topic == alias.topic) {
        alias.topic = "PlatformInitStatus";
    }
    validate_wan_source_interests({full.front(), alias});
    dds::core::xtypes::DynamicData contact(model->type("contact_report"));
    contact.value<std::string>("source", "Boat30");
    contact.value<std::string>("contact_id", "contact-101");
    contact.value<std::string>("run_id", "contact-run");
    contact.value<uint64_t>("audit_sequence", 23);
    contact.value<int64_t>("sent_at_ns", 123456789);
    WanInterest contact_interest;
    contact_interest.topic = "ContactReport";
    const auto contact_metadata = wan_source_metadata(
            contact_interest, contact, "Boat30", "router", "platform", 7);
    CHECK(contact_metadata.source_id == "Boat30");
    CHECK(contact_metadata.run_id == "contact-run");
    CHECK(contact_metadata.audit_sequence == 23);
    CHECK(contact_metadata.sent_at_ns == 123456789);
    for (const auto& mapping : wan_mappings()) {
        if (std::string(mapping.branch) == "health") {
            continue;
        }
        dds::core::xtypes::DynamicData data(model->type(mapping.type));
        WanInterest selected;
        selected.topic = mapping.topic;
        const auto metadata = wan_source_metadata(selected, data, "Boat30", "router", "platform", 7);
        CHECK(metadata.source_id == "Boat30" && metadata.membership_revision == 7);
    }
    try {
        team.teams = {"TEAM_*"};
        wan_receive_filter("Status", "Boat30", {team});
        CHECK(false);
    } catch (const std::runtime_error&) {}
    std::printf("WAN interest checks: %d failures\n", failures);
    return failures ? 1 : 0;
}
