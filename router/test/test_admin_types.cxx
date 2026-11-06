// test_admin_types.cxx — proves the generated admin command/status types compile and are
// usable in a standalone program (Phase 0 evidence: "generated command/status types
// compile in a standalone test"). This links the Connext Modern C++ type support but
// creates NO participants/entities — it exercises construction and field access only.
//
// rtiddsgen generated these types as plain structs with public data members and
// vector-like bounded_sequence fields, so access is by field, not fluent accessors.

#include "ActTypes.hpp"
#include <rti/topic/cdr.hpp>

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
    part.participant_partition.push_back("Platform_30"); // D83: multi-valued set
    status.participants.push_back(part);

    CHECK(status.target_node == "Platform_30");
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

    RouterHealth health;
    health.router = "Platform_30/router";
    health.heartbeat_seq = (std::uint64_t(1) << 40) + 1;
    health.session_id = (std::uint64_t(1) << 60) + 3;
    RouterCompactRouteHealth compact;
    compact.route_name = "platform_debug_status";
    compact.state = RouterRouteOperationalState::ROUTE_ERROR;
    compact.discovery_state = RouterRouteDiscoveryState::DISCOVERY_READY;
    compact.samples_forwarded = (std::uint64_t(1) << 40) + 7;
    health.routes.push_back(compact);
    for (unsigned int i = 0; i < 70; ++i) {
        RouterPeerRef peer;
        peer.router = "Platform_" + std::to_string(i + 30) + "/platform-router";
        peer.presence = RouterPresenceState::PRESENCE_STALE;
        peer.last_seen_delta_ms = 12345;
        peer.ack_session_id = health.session_id;
        peer.ack_heartbeat_seq = health.heartbeat_seq;
        peer.ack_age_at_send_us = 680000;
        health.peers_seen.push_back(peer);
    }
    std::vector<char> buffer;
    rti::topic::to_cdr_buffer(buffer, health);
    const auto restored = rti::topic::from_cdr_buffer<RouterHealth>(buffer);
    CHECK(restored.heartbeat_seq == health.heartbeat_seq);
    CHECK(restored.session_id == health.session_id);
    CHECK(restored.peers_seen.at(0).ack_session_id == health.session_id);
    CHECK(restored.peers_seen.at(0).ack_heartbeat_seq == health.heartbeat_seq);
    CHECK(restored.peers_seen.at(0).ack_age_at_send_us == 680000);
    CHECK(restored.routes.at(0).samples_forwarded == compact.samples_forwarded);
    CHECK(restored.routes.at(0).state == compact.state);
    CHECK(restored.peers_seen.size() == 70);
    CHECK(restored.peers_seen.at(69).router == health.peers_seen.at(69).router);
    CHECK(restored.peers_seen.at(69).presence == RouterPresenceState::PRESENCE_STALE);
    CHECK(restored.peers_seen.at(69).last_seen_delta_ms == 12345);
    std::printf("RouterHealth 70-peer CDR size: %zu bytes\n", buffer.size());
    health.peers_seen.resize(5);
    rti::topic::to_cdr_buffer(buffer, health);
    std::printf("RouterHealth 5-peer CDR size: %zu bytes\n", buffer.size());
    CHECK(buffer.size() < 1400);

    if (g_failures == 0) {
        std::printf("test_admin_types: OK\n");
        return 0;
    }
    std::fprintf(stderr, "test_admin_types: %d failure(s)\n", g_failures);
    return 1;
}
