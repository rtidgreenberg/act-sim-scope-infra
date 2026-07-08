// test_admin_types.cxx — proves the generated admin command/status types compile and are
// usable in a standalone program (Phase 0 evidence: "generated command/status types
// compile in a standalone test"). This links the Connext Modern C++ type support but
// creates NO participants/entities — it exercises construction and field access only.
//
// rtiddsgen generated these types as plain structs with public data members and
// vector-like bounded_sequence fields, so access is by field, not fluent accessors.

#include "RouterAdminTypes.hpp"

#include <cstdio>
#include <string>

static int g_failures = 0;

#define CHECK(cond)                                                                      \
    do {                                                                                 \
        if (!(cond)) {                                                                   \
            std::fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);         \
            ++g_failures;                                                                \
        }                                                                                \
    } while (0)

int main() {
    // RouterStatus: keyed identity + a route table entry.
    RouterStatus status;
    status.target_node = "Platform_30";
    status.target_router = "platform-30-control-platform";
    status.router_id = 30;
    status.status_id = "status-1";
    status.state_revision = 1; // uint64 monotonic counter (design-decisions.md D5)

    RouterRouteStatus route;
    route.route_name = "control_command";
    route.state = RouterRouteOperationalState::ROUTE_DISABLED;
    route.discovery_state = RouterRouteDiscoveryState::DISCOVERY_NONE; // D1 second field
    route.state_revision = 1;
    route.samples_forwarded = 0;

    // Per-topic status sequence (D11): route active when >= 1 topic forwards.
    RouterRouteTopicStatus topic;
    topic.name = "ControlCommand";
    topic.discovery_state = RouterRouteDiscoveryState::DISCOVERY_READY;
    topic.topic_state = RouterRouteTopicState::TOPIC_FORWARDING;
    topic.samples_forwarded = 3;
    route.topic_status.push_back(topic);
    status.routes.push_back(route);

    RouterParticipantStatus part;
    part.name = "platform_wan";
    part.domain = 200;
    part.participant_partition = "PLATFORM_30";
    status.participants.push_back(part);

    CHECK(status.target_node == "Platform_30");
    CHECK(status.router_id == 30u);
    CHECK(status.routes.size() == 1);
    CHECK(status.routes.at(0).route_name == "control_command");
    CHECK(status.routes.at(0).state == RouterRouteOperationalState::ROUTE_DISABLED);
    CHECK(status.routes.at(0).discovery_state == RouterRouteDiscoveryState::DISCOVERY_NONE);
    CHECK(status.routes.at(0).topic_status.size() == 1);
    CHECK(status.routes.at(0).topic_status.at(0).topic_state
          == RouterRouteTopicState::TOPIC_FORWARDING);
    CHECK(status.routes.at(0).topic_status.at(0).samples_forwarded == 3u);
    CHECK(status.state_revision == 1u);
    CHECK(status.participants.size() == 1);

    // RouterCommand / RouterCommandAck round-trip of scalar fields.
    RouterCommand cmd;
    cmd.target_node = "Platform_30";
    cmd.command_id = "cmd-42";
    cmd.kind = RouterCommandKind::ENABLE_ROUTE;
    cmd.route_name = "platform_detail_status";
    CHECK(cmd.kind == RouterCommandKind::ENABLE_ROUTE);
    CHECK(cmd.command_id == "cmd-42");

    RouterCommandAck ack;
    ack.command_id = cmd.command_id;
    ack.route_name = cmd.route_name;
    ack.accepted = true;
    ack.message = "enabled; waiting for discovery";
    CHECK(ack.accepted);
    CHECK(ack.command_id == "cmd-42");

    // RouterRouteSpec nested endpoint + sequence fields.
    RouterRouteSpec spec;
    spec.route_name = "control_command";
    spec.desired_enabled = true;
    spec.forwarding_mode = "dynamic_data";
    spec.topics.resize(1);
    spec.topics.at(0).name = "ControlCommand";
    spec.input.filter_parameters.push_back("Platform_30");
    CHECK(spec.topics.size() == 1);
    CHECK(spec.input.filter_parameters.size() == 1);

    if (g_failures == 0) {
        std::printf("test_admin_types: OK\n");
        return 0;
    }
    std::fprintf(stderr, "test_admin_types: %d failure(s)\n", g_failures);
    return 1;
}
