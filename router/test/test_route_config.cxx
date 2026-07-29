// test_route_config.cxx — Phase 4 step 3: role-aware route selection from real YAML (D36).
//
// No DDS: parses config/control-platform.yaml and asserts each node role selects the
// correct legs and that the platform-side content-filter parameter is substituted +
// quoted. The same file is parsed once as the platform node (its own node.*) and once
// with a control-role override, covering both source-side and destination-side selection.

#include "config/RouteConfigParser.hpp"
#include "config/Sha256.hpp"

#include <cstdio>
#include <fstream>
#include <string>
#include <unistd.h>

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

    // --- config_hash (D80): SHA-256 over the file's raw bytes, full lowercase hex ---
    {
        // NIST FIPS 180-4 test vector pins the algorithm itself.
        CHECK(sha256_hex(std::string("abc"))
              == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        RouteConfig cfg;
        std::string err;
        CHECK(parse_route_config(path, cfg, err));
        std::ifstream in(path, std::ios::binary);
        std::string bytes((std::istreambuf_iterator<char>(in)),
                          std::istreambuf_iterator<char>());
        CHECK(cfg.config_hash.size() == 64);
        CHECK(cfg.config_hash == sha256_hex(bytes));
    }

    // --- Stale-config guard (D79, E-R4): router.id is a hard, labeled parse error ---
    {
        std::string stale = "/tmp/router_route_cfg_stale_" + std::to_string(getpid())
                            + ".yaml";
        {
            std::ofstream f(stale);
            f << "node:\n  name: X\n  role: control\n"
              << "router:\n  id: 30\n  name: r\n"
              << "participants:\n  control_lan:\n    domain: 0\n";
        }
        RouteConfig cfg;
        std::string err;
        CHECK(!parse_route_config(stale, cfg, err));
        CHECK(err.find("router.id") != std::string::npos);
        std::remove(stale.c_str());
    }

    // --- router.name is optional: absent -> the fleet-wide default "router" (D79/D80) ---
    {
        std::string bare = "/tmp/router_route_cfg_bare_" + std::to_string(getpid())
                           + ".yaml";
        {
            std::ofstream f(bare);
            f << "node:\n  name: X\n  role: control\n"
              << "participants:\n  control_lan:\n    domain: 0\n";
        }
        RouteConfig cfg;
        std::string err;
        CHECK(parse_route_config(bare, cfg, err));
        CHECK(cfg.router_name == "router");
        std::remove(bare.c_str());
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

    // --- D83/D103: platform_wan (team_wan's role absorbed under D103) carries the
    // protected node-identity partition entry by default, control_wan carries a standing
    // protected wildcard instead (needed to keep matching platform_wan now that it's off
    // the free default partition), and the team routes (folded into control-platform.yaml
    // by D80, retargeted to platform_wan by D103) parse in role-pair form and always
    // resolve to source_side (same-role pair) ---
    {
        RouteConfig cfg;
        std::string err;
        CHECK(parse_route_config(path, cfg, err));
        const ParticipantState *platform_wan = nullptr;
        const ParticipantState *control_wan = nullptr;
        for (std::size_t i = 0; i < cfg.participants.size(); ++i) {
            if (cfg.participants[i].name == "platform_wan") platform_wan = &cfg.participants[i];
            if (cfg.participants[i].name == "control_wan") control_wan = &cfg.participants[i];
        }
        CHECK(platform_wan != nullptr);
        if (platform_wan) {
            // is_wan decomposition: platform_wan is BOTH team-partition-scoped and on the
            // WAN (D103: absorbed team_wan's role).
            CHECK(platform_wan->team_scoped);
            CHECK(platform_wan->on_wan);
            CHECK(platform_wan->participant_partition.size() == 1);
            if (platform_wan->participant_partition.size() == 1) {
                CHECK(platform_wan->participant_partition.at(0) == "Platform_30"); // ${node.name}
            }
        }
        CHECK(control_wan != nullptr);
        if (control_wan) {
            // D103: control_wan is on the WAN, NOT team-partition-scoped, but carries a
            // standing PROTECTED wildcard entry — required now that platform_wan is off
            // the free default partition, and non-removable via REMOVE_PARTICIPANT_PARTITION
            // (is_protected_partition_name checks protected_partition_entries too).
            CHECK(control_wan->on_wan);
            CHECK(!control_wan->team_scoped);
            CHECK(control_wan->participant_partition.size() == 1);
            if (control_wan->participant_partition.size() == 1) {
                CHECK(control_wan->participant_partition.at(0) == "*");
            }
            CHECK(control_wan->protected_partition_entries.size() == 1);
            if (control_wan->protected_partition_entries.size() == 1) {
                CHECK(control_wan->protected_partition_entries.at(0) == "*");
            }
            CHECK(is_protected_partition_name(*control_wan, cfg.node_name, "*"));
        }
        const RouterRouteSpec *t1 = find_route(cfg, "platform_team_to_wan");
        const RouterRouteSpec *t2 = find_route(cfg, "wan_team_to_platform");
        CHECK(t1 != nullptr);
        CHECK(t2 != nullptr);
        if (t1) {
            CHECK(t1->input.participant == "platform_lan");
            CHECK(t1->output.participant == "platform_wan");
        }
        if (t2) {
            CHECK(t2->input.participant == "platform_wan");
            CHECK(t2->output.participant == "platform_lan");
        }
        // LAN participants are neither team-scoped nor on the WAN.
        for (std::size_t i = 0; i < cfg.participants.size(); ++i) {
            if (cfg.participants[i].name == "platform_lan"
                || cfg.participants[i].name == "control_lan") {
                CHECK(!cfg.participants[i].team_scoped);
                CHECK(!cfg.participants[i].on_wan);
            }
        }
    }

    // --- D80: the retired flat input:/output: route shape is a hard parse error, not a
    // silently skipped route ---
    {
        std::string flat = "/tmp/router_route_cfg_flat_" + std::to_string(getpid())
                           + ".yaml";
        {
            std::ofstream f(flat);
            f << "node:\n  name: Platform_30\n  role: platform\n"
              << "participants:\n"
              << "  platform_lan:\n    role: platform\n    domain: 0\n"
              << "  team_wan:\n    role: platform\n    domain: 1\n"
              << "routes:\n"
              << "  - name: flat_route\n"
              << "    enabled: true\n"
              << "    input:\n      participant: platform_lan\n"
              << "    output:\n      participant: team_wan\n"
              << "    topics:\n      - name: PlatformData\n";
        }
        RouteConfig cfg;
        std::string err;
        CHECK(!parse_route_config(flat, cfg, err));
        CHECK(err.find("flat_route") != std::string::npos);
        CHECK(err.find("source/destination") != std::string::npos);
        std::remove(flat.c_str());
    }

    // --- D73/D83: the retired inherit_participant sentinel is a hard parse error ---
    {
        std::string sentinel = "/tmp/router_route_cfg_sentinel_" + std::to_string(getpid())
                               + ".yaml";
        {
            std::ofstream f(sentinel);
            f << "node:\n  name: Platform_30\n  role: platform\n"
              << "participants:\n"
              << "  team_wan:\n"
              << "    role: platform\n    domain: 1\n"
              << "    participant_partition: inherit_participant\n";
        }
        RouteConfig cfg;
        std::string err;
        CHECK(!parse_route_config(sentinel, cfg, err));
        CHECK(err.find("inherit_participant") != std::string::npos);
        std::remove(sentinel.c_str());
    }

    // --- D83: participant_partition accepts a YAML sequence too, with ${node.name}
    // substitution applied per entry ---
    {
        std::string seq = "/tmp/router_route_cfg_partseq_" + std::to_string(getpid())
                          + ".yaml";
        {
            std::ofstream f(seq);
            f << "node:\n  name: Platform_31\n  role: platform\n"
              << "participants:\n"
              << "  team_wan:\n"
              << "    role: platform\n    domain: 1\n    team_scoped: true\n"
              << "    participant_partition: [\"TEAM_A\", \"${node.name}\"]\n";
        }
        RouteConfig cfg;
        std::string err;
        CHECK(parse_route_config(seq, cfg, err));
        if (!err.empty()) std::fprintf(stderr, "partseq parse error: %s\n", err.c_str());
        CHECK(cfg.participants.size() == 1);
        if (cfg.participants.size() == 1) {
            const std::vector<std::string> &names = cfg.participants[0].participant_partition;
            CHECK(names.size() == 2); // ${node.name} already present -> no auto-add
            CHECK(names.size() == 2 && names.at(0) == "TEAM_A");
            CHECK(names.size() == 2 && names.at(1) == "Platform_31");
        }
        std::remove(seq.c_str());
    }

    // --- participant_partition over the RouterAdminTypes.idl sequence<string, 16>
    // bound is a hard parse error, not an accepted config that overflows on the first
    // status publish ---
    {
        std::string over = "/tmp/router_route_cfg_partover_" + std::to_string(getpid())
                           + ".yaml";
        {
            std::ofstream f(over);
            f << "node:\n  name: Platform_30\n  role: platform\n"
              << "participants:\n"
              << "  team_wan:\n"
              << "    role: platform\n    domain: 1\n    team_scoped: true\n"
              << "    participant_partition: [";
            for (int i = 0; i < 16; ++i) { // 17 explicit entries + the auto-added
                f << "\"TEAM_" << i << "\", ";               // protected "${node.name}"
            }
            f << "\"TEAM_LAST\"]\n";                         // entry — well past the cap

        }
        RouteConfig cfg;
        std::string err;
        CHECK(!parse_route_config(over, cfg, err));
        CHECK(err.find("participant_partition") != std::string::npos);
        std::remove(over.c_str());
    }

    if (g_failures == 0) {
        std::printf("test_route_config: OK role selection + filter substitution\n");
        return 0;
    }
    std::fprintf(stderr, "test_route_config: %d failure(s)\n", g_failures);
    return 1;
}
