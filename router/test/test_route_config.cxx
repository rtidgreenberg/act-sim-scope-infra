// test_route_config.cxx — Phase 4 step 3: role-aware route selection from real YAML (D36).
//
// No DDS: parses config/control-platform.yaml and asserts each node role selects the
// correct legs and that the platform-side content-filter parameter is substituted +
// quoted. The same file is parsed once as the platform node (its own node.*) and once
// with a control-role override, covering both source-side and destination-side selection.

#include "config/RouteConfigParser.hpp"

#include <cstdio>
#include <string>

using namespace router;

static int g_failures = 0;
#define CHECK(cond)                                                                    \
    do {                                                                               \
        if (!(cond)) {                                                                 \
            std::fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);       \
            ++g_failures;                                                              \
        }                                                                              \
    } while (0)

#ifndef ROUTER_CONFIG_DIR
#define ROUTER_CONFIG_DIR "."
#endif

static const RouterRouteSpec *find_route(const RouteConfig &cfg, const std::string &name) {
    for (std::size_t i = 0; i < cfg.routes.size(); ++i) {
        if (cfg.routes[i].route_name == name) {
            return &cfg.routes[i];
        }
    }
    return nullptr;
}

int main() {
    const std::string path = std::string(ROUTER_CONFIG_DIR) + "/control-platform.yaml";

    // --- Platform node (uses the file's node.* : Platform_30 / platform) ---
    {
        RouteConfig cfg;
        std::string err;
        CHECK(parse_route_config(path, cfg, err));
        if (!err.empty()) std::fprintf(stderr, "platform parse error: %s\n", err.c_str());
        CHECK(cfg.node_role == "platform");

        // platform is the DESTINATION on control_command -> destination_side selected.
        const RouterRouteSpec *cc = find_route(cfg, "control_command");
        CHECK(cc != nullptr);
        if (cc) {
            CHECK(cc->input.participant == "platform_wan");
            CHECK(cc->output.participant == "platform_lan");
            // Content filter substituted + quoted to this node's name.
            CHECK(cc->input.filter_expression == "msg.destination = %0");
            CHECK(cc->input.filter_parameters.size() == 1);
            if (cc->input.filter_parameters.size() == 1) {
                CHECK(cc->input.filter_parameters.at(0) == "'Platform_30'");
            }
        }

        // platform is the SOURCE on platform_primary_status -> source_side selected.
        const RouterRouteSpec *ps = find_route(cfg, "platform_primary_status");
        CHECK(ps != nullptr);
        if (ps) {
            CHECK(ps->input.participant == "platform_lan");
            CHECK(ps->output.participant == "platform_wan");
        }
    }

    // --- Control node (role override on the same file) ---
    {
        RouteConfig cfg;
        std::string err;
        CHECK(parse_route_config(path, cfg, err, "control", "Control"));
        CHECK(cfg.node_role == "control");

        // control is the SOURCE on control_command -> source_side selected.
        const RouterRouteSpec *cc = find_route(cfg, "control_command");
        CHECK(cc != nullptr);
        if (cc) {
            CHECK(cc->input.participant == "control_lan");
            CHECK(cc->output.participant == "control_wan");
            // source side carries no destination filter.
            CHECK(cc->input.filter_expression.empty());
        }

        // control is the DESTINATION on platform_primary_status -> destination_side.
        const RouterRouteSpec *ps = find_route(cfg, "platform_primary_status");
        CHECK(ps != nullptr);
        if (ps) {
            CHECK(ps->input.participant == "control_wan");
            CHECK(ps->output.participant == "control_lan");
        }
    }

    // --- QoS alias resolution (Phase 7a, D60) ---
    // control-platform.yaml declares every alias it uses (wan_event, wan_status,
    // lan_status_1hz, control_wan_udpv4_qos, platform_wan_udpv4_qos) in its own
    // qos_profiles: map, so validate_qos_aliases now passes (it once rejected this file
    // during the Phase-5-interim gap, before qos_profiles: parsing existed).
    {
        RouteConfig cfg;
        std::string err;
        CHECK(parse_route_config(path, cfg, err));
        std::string qos_err;
        CHECK(validate_qos_aliases(cfg, qos_err));
        CHECK(qos_err.empty());

        CHECK(cfg.qos_profiles.size() == 5);
        CHECK(cfg.qos_profiles.at("wan_event") == "WAN_QOS_LIB::event_qos");
        CHECK(cfg.qos_profiles.at("wan_status") == "WAN_QOS_LIB::status_qos");
        // Regression guard for the now-fixed broken alias (spikes/qos_alias/ PLAN.md
        // finding 3): must point at the profile the lib actually defines.
        CHECK(cfg.qos_profiles.at("lan_status_1hz") == "LAN_QOS_LIB::status_1sec_qos");
        CHECK(cfg.qos_profiles.at("control_wan_udpv4_qos")
              == "WAN_QOS_LIB::control_participant_udpv4_qos");
        CHECK(cfg.qos_profiles.at("platform_wan_udpv4_qos")
              == "WAN_QOS_LIB::platform_participant_udpv4_qos");
    }

    // --- Numeric-looking node name: quoted YAML scalar must still be quoted (D43) ---
    // A node named "101" substituted into the quoted "${node.name}" parameter must not be
    // misread as a numeric SQL literal — the author's explicit quoting in the YAML source
    // is authoritative, regardless of what the substituted text looks like.
    {
        RouteConfig cfg;
        std::string err;
        CHECK(parse_route_config(path, cfg, err, "platform", "101"));
        const RouterRouteSpec *cc = find_route(cfg, "control_command");
        CHECK(cc != nullptr);
        if (cc && cc->input.filter_parameters.size() == 1) {
            CHECK(cc->input.filter_parameters.at(0) == "'101'");
        }
    }

    if (g_failures == 0) {
        std::printf("test_route_config: OK role selection + filter substitution\n");
        return 0;
    }
    std::fprintf(stderr, "test_route_config: %d failure(s)\n", g_failures);
    return 1;
}
