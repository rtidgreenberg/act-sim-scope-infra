// RouteConfigParser.cxx — yaml-cpp route/participant parsing + role-aware selection (D36).

#include "config/RouteConfigParser.hpp"
#include "config/Sha256.hpp"
#include "core/QosAliasPolicy.hpp"

#include <yaml-cpp/yaml.h>

#include <fstream>
#include <sstream>
#include <stdexcept>

namespace router {

bool has_participant(const std::vector<ParticipantState> &participants,
                    const std::string &name) {
    for (const auto &ps : participants) {
        if (ps.name == name) return true;
    }
    return false;
}

namespace {

std::string get_str(const YAML::Node &n, const std::string &key,
                    const std::string &dflt = "") {
    return (n && n[key]) ? n[key].as<std::string>() : dflt;
}

// True for a plain SQL numeric literal: [+-]digits[.digits][(e|E)[+-]digits].
bool is_numeric_literal(const std::string &v) {
    std::size_t i = 0;
    if (i < v.size() && (v[i] == '+' || v[i] == '-')) ++i;
    std::size_t digits = 0;
    while (i < v.size() && v[i] >= '0' && v[i] <= '9') { ++i; ++digits; }
    if (i < v.size() && v[i] == '.') {
        ++i;
        while (i < v.size() && v[i] >= '0' && v[i] <= '9') { ++i; ++digits; }
    }
    if (digits == 0) return false;
    if (i < v.size() && (v[i] == 'e' || v[i] == 'E')) {
        ++i;
        if (i < v.size() && (v[i] == '+' || v[i] == '-')) ++i;
        std::size_t exp_digits = 0;
        while (i < v.size() && v[i] >= '0' && v[i] <= '9') { ++i; ++exp_digits; }
        if (exp_digits == 0) return false;
    }
    return i == v.size();
}

// Substitute ${node.name}, then make the value a valid SQL filter parameter. The member
// type is unknown at the YAML layer (it lives with the DDS type; typed resolution is a
// Phase 5+ concern), so quoting follows how the author wrote the YAML scalar (D43): an
// explicitly quoted parameter (`Tag() == "!"` for single/double-quoted scalars, vs `"?"`
// for a plain/bare one — verified against yaml-cpp 0.8.0) is always a string, regardless
// of shape, so a numeric-looking id like "101" or "PLATFORM232" written in quotes is never
// misquoted. Only a plain/bare scalar (an actual number in the config) falls back to the
// numeric-shape check (string comparison requires quotes — validated 7.7).
std::string substitute_node_name(std::string v, const std::string &node_name) {
    const std::string token = "${node.name}";
    std::string::size_type pos;
    while ((pos = v.find(token)) != std::string::npos) {
        v.replace(pos, token.size(), node_name);
    }
    return v;
}

std::string resolve_filter_param(const YAML::Node &param, const std::string &node_name) {
    std::string v = substitute_node_name(param.as<std::string>(), node_name);
    if (v.size() >= 2 && v[0] == '\'' && v[v.size() - 1] == '\'') {
        return v; // author embedded the SQL quotes directly
    }
    bool explicitly_quoted = (param.Tag() == "!");
    if (explicitly_quoted || !is_numeric_literal(v)) {
        return "'" + v + "'";
    }
    return v;
}

// Fill a RouterRouteEndpointSpec from a side's input/output YAML node.
void fill_endpoint(const YAML::Node &ep, RouterRouteEndpointSpec &out,
                   const std::string &node_name, bool is_input) {
    if (!ep) {
        return;
    }
    out.participant = get_str(ep, "participant");
    out.reader_qos = get_str(ep, "reader_qos");
    out.writer_qos = get_str(ep, "writer_qos");
    out.subscriber_partition = get_str(ep, "subscriber_partition");
    out.publisher_partition = get_str(ep, "publisher_partition");
    if (is_input && ep["filter"]) {
        const YAML::Node &f = ep["filter"];
        out.filter_expression = get_str(f, "expression");
        if (f["parameters"]) {
            for (std::size_t i = 0; i < f["parameters"].size(); ++i) {
                out.filter_parameters.push_back(
                    resolve_filter_param(f["parameters"][i], node_name));
            }
        }
    }
}

} // namespace

bool parse_route_config(const std::string &path, RouteConfig &out, std::string &error,
                        const std::string &role_override,
                        const std::string &name_override) {
    // config_hash (D80): SHA-256 over the file's raw bytes, computed once at load —
    // the same bytes every node of the fleet is supposed to be running.
    {
        std::ifstream in(path, std::ios::binary);
        if (!in.is_open()) {
            error = "cannot open config file: " + path;
            return false;
        }
        std::ostringstream bytes;
        bytes << in.rdbuf();
        out.config_hash = sha256_hex(bytes.str());
    }

    YAML::Node root;
    try {
        root = YAML::LoadFile(path);
    } catch (const std::exception &e) {
        error = std::string("cannot load config: ") + e.what();
        return false;
    }

    try {
        const YAML::Node &node = root["node"];
        out.node_name = name_override.empty() ? get_str(node, "name") : name_override;
        out.node_role = role_override.empty() ? get_str(node, "role") : role_override;
        if (out.node_role.empty()) {
            error = "missing node.role";
            return false;
        }
        if (out.node_name.empty()) {
            error = "missing node.name";
            return false;
        }

        const YAML::Node &router = root["router"];
        // Stale-config guard (D79 addendum, same posture as D73's unknown-sentinel
        // rule): router.id is retired — a silently ignored identity field is exactly
        // how a stale fleet config would hide.
        if (router && router["id"]) {
            error = "router.id is retired (D79: the router name is the only identity) "
                    "— remove it from the config";
            return false;
        }
        // The router half of the identity is a fleet-wide constant under D80's one
        // system-wide config; absent => default "router" (D79 addendum).
        out.router_name = get_str(router, "name", "router");
        // router.type_name retired (7c, D70): topic types are wire-learned. A leftover
        // type_name: key in a config is silently ignored.
        out.admin_participant = get_str(router, "admin_participant");

        // router.presence_participant (Phase 8, D75): which participant carries the
        // RouterHealth heartbeat pair — normally this role's WAN participant. Two forms:
        //   presence_participant: platform_wan            # scalar (single-role config)
        //   presence_participant: {control: control_wan,  # per-role map (one shared
        //                          platform: platform_wan} # config file, two roles)
        // Absent (or no entry for this role) = presence disabled for this process.
        if (router && router["presence_participant"]) {
            const YAML::Node &pp = router["presence_participant"];
            if (pp.IsMap()) {
                if (pp[out.node_role]) {
                    out.presence_participant = pp[out.node_role].as<std::string>();
                }
            } else {
                out.presence_participant = pp.as<std::string>();
            }
        }

        // router.link_stats_period_ms (Phase 9, D14/D81): config-fixed poll cadence. Must
        // be > 0 — a 0/negative value would build the collector's WAN entities but leave it
        // silently inert (the DrainThread tick guard is period > 0), so reject it up front
        // (D79 fail-fast posture) rather than ship a half-live collector.
        if (router && router["link_stats_period_ms"]) {
            out.link_stats_period_ms = router["link_stats_period_ms"].as<int>();
            if (out.link_stats_period_ms <= 0) {
                error = "router.link_stats_period_ms must be a positive integer (ms); got "
                        + std::to_string(out.link_stats_period_ms);
                return false;
            }
        }

        out.types_xml_path = get_str(root["types"], "xml");
        for (std::size_t i = 0; i < root["qos_libraries"].size(); ++i) {
            out.qos_library_paths.push_back(root["qos_libraries"][i].as<std::string>());
        }
        if (root["qos_profiles"]) {
            for (auto it = root["qos_profiles"].begin(); it != root["qos_profiles"].end();
                 ++it) {
                out.qos_profiles[it->first.as<std::string>()] = it->second.as<std::string>();
            }
        }

        for (auto it = root["participants"].begin(); it != root["participants"].end(); ++it) {
            ParticipantState ps;
            ps.name = it->first.as<std::string>();
            const YAML::Node &p = it->second;
            if (p["domain"]) {
                ps.domain = p["domain"].as<std::int32_t>();
            }
            ps.role = get_str(p, "role");
            ps.qos_profile_alias = get_str(p, "qos");
            ps.is_wan = p["wan"] && p["wan"].as<bool>();
            // D78 (reinstated): SPDP2 discovery for WAN participants, decoupled from `wan`
            // (is_wan is the D83 team-scope flag; SPDP2 applies to every WAN participant).
            ps.use_spdp2 = p["spdp2"] && p["spdp2"].as<bool>();

            // participant_partition (D83): a sequence of names, or a single scalar name
            // for convenience — either way the ${node.name} token is substituted (same
            // rule as filter parameters). An unknown sentinel (the retired
            // "inherit_participant") is a hard parse error (D73), not a silent no-op.
            if (p["participant_partition"]) {
                const YAML::Node &pp = p["participant_partition"];
                std::vector<YAML::Node> entries;
                if (pp.IsSequence()) {
                    for (std::size_t i = 0; i < pp.size(); ++i) entries.push_back(pp[i]);
                } else {
                    entries.push_back(pp);
                }
                for (const YAML::Node &entry : entries) {
                    std::string v = entry.as<std::string>();
                    if (v == "inherit_participant") {
                        error = "participant '" + ps.name + "' participant_partition: "
                                "'inherit_participant' is retired (D73) — team scope is "
                                "the participant partition alone; route endpoints use "
                                "the default partition";
                        return false;
                    }
                    ps.participant_partition.push_back(substitute_node_name(v, out.node_name));
                }
            }
            // Every WAN-facing participant's set always contains its own protected
            // identity entry (D83), config-time only — never removable by command.
            if (ps.is_wan) {
                if (!has_protected_partition_entry(ps, out.node_name)) {
                    ps.participant_partition.insert(ps.participant_partition.begin(),
                                                    out.node_name);
                }
            }
            // The wire status field is a bounded sequence<string, 16>
            // (RouterAdminTypes.idl) — reject an oversized config at parse time rather
            // than let it overflow silently on the first status publish.
            if (ps.participant_partition.size() > kMaxParticipantPartitionEntries) {
                error = "participant '" + ps.name + "' participant_partition: " +
                        std::to_string(ps.participant_partition.size()) + " entries "
                        "exceeds the " + std::to_string(kMaxParticipantPartitionEntries) +
                        "-entry bound (RouterParticipantStatus.participant_partition, "
                        "RouterAdminTypes.idl)";
                return false;
            }
            out.participants.push_back(ps);
        }

        for (std::size_t r = 0; r < root["routes"].size(); ++r) {
            const YAML::Node &rt = root["routes"][r];
            // D80: the flat top-level input:/output: route shape is retired before it
            // ever shipped — every route declares a source/destination role pair. A
            // route missing either key is a hard parse error, not a silently skipped
            // route (the bug this superseded: a flat route just fell through the old
            // role-match `continue`, invisible on every node that loaded the config).
            if (!rt["source"] || !rt["destination"]) {
                error = "route '" + get_str(rt, "name") + "' is missing source/destination "
                        "— the flat input:/output: route shape is retired (D80); every "
                        "route must declare a source/destination role pair";
                return false;
            }
            const std::string source = get_str(rt, "source");
            const std::string dest = get_str(rt, "destination");

            // Role-aware side selection: this node materializes the side matching its
            // role. A same-role pair (e.g. platform<->platform team routes) always
            // resolves to source_side — destination_side is reserved for a future
            // symmetric-route need and is never read while source == destination.
            std::string side_key;
            if (out.node_role == source) {
                side_key = "source_side";
            } else if (out.node_role == dest) {
                side_key = "destination_side";
            } else {
                continue; // route not for this node's role
            }
            const YAML::Node &side = rt[side_key];
            if (!side) {
                error = "route '" + get_str(rt, "name") + "' missing " + side_key;
                return false;
            }

            RouterRouteSpec spec;
            spec.route_name = get_str(rt, "name");
            spec.desired_enabled = rt["enabled"] ? rt["enabled"].as<bool>() : false;
            spec.forwarding_mode = get_str(rt, "forwarding_mode");
            fill_endpoint(side["input"], spec.input, out.node_name, /*is_input=*/true);
            fill_endpoint(side["output"], spec.output, out.node_name, /*is_input=*/false);
            if (!has_participant(out.participants, spec.input.participant)) {
                error = "route '" + spec.route_name + "' " + side_key
                        + ".input.participant '" + spec.input.participant
                        + "' is not a declared participant";
                return false;
            }
            if (!has_participant(out.participants, spec.output.participant)) {
                error = "route '" + spec.route_name + "' " + side_key
                        + ".output.participant '" + spec.output.participant
                        + "' is not a declared participant";
                return false;
            }
            for (std::size_t t = 0; t < rt["topics"].size(); ++t) {
                RouterRouteTopicSpec topic;
                topic.name = get_str(rt["topics"][t], "name");
                spec.topics.push_back(topic);
            }
            out.routes.push_back(spec);
        }
    } catch (const std::exception &e) {
        error = std::string("parse error: ") + e.what();
        return false;
    }

    return true;
}

bool validate_qos_aliases(const RouteConfig &cfg, std::string &error) {
    for (std::size_t r = 0; r < cfg.routes.size(); ++r) {
        const RouterRouteSpec &spec = cfg.routes[r];
        if (!is_resolvable_qos_alias(spec.input.reader_qos, cfg.qos_profiles)) {
            error = "route '" + spec.route_name + "' input.reader_qos '"
                    + spec.input.reader_qos
                    + "' is not \"\", \"default\", or a declared qos_profiles: key";
            return false;
        }
        if (!is_resolvable_qos_alias(spec.output.writer_qos, cfg.qos_profiles)) {
            error = "route '" + spec.route_name + "' output.writer_qos '"
                    + spec.output.writer_qos
                    + "' is not \"\", \"default\", or a declared qos_profiles: key";
            return false;
        }
    }
    for (std::size_t p = 0; p < cfg.participants.size(); ++p) {
        const ParticipantState &ps = cfg.participants[p];
        if (!is_resolvable_qos_alias(ps.qos_profile_alias, cfg.qos_profiles)) {
            error = "participant '" + ps.name + "' qos '" + ps.qos_profile_alias
                    + "' is not \"\", \"default\", or a declared qos_profiles: key";
            return false;
        }
    }
    return true;
}

} // namespace router
