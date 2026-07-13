// RouteConfigParser.cxx — yaml-cpp route/participant parsing + role-aware selection (D36).

#include "config/RouteConfigParser.hpp"
#include "core/QosAliasPolicy.hpp"

#include <yaml-cpp/yaml.h>

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
std::string resolve_filter_param(const YAML::Node &param, const std::string &node_name) {
    std::string v = param.as<std::string>();
    const std::string token = "${node.name}";
    std::string::size_type pos;
    while ((pos = v.find(token)) != std::string::npos) {
        v.replace(pos, token.size(), node_name);
    }
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
        out.router_name = get_str(router, "name");
        if (out.router_name.empty()) {
            error = "missing router.name";
            return false;
        }
        if (router && router["id"]) {
            out.router_id = router["id"].as<std::int32_t>();
        }
        out.type_name = get_str(router, "type_name");
        out.admin_participant = get_str(router, "admin_participant");

        out.types_xml_path = get_str(root["types"], "xml");
        for (std::size_t i = 0; i < root["qos_libraries"].size(); ++i) {
            out.qos_library_paths.push_back(root["qos_libraries"][i].as<std::string>());
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
            out.participants.push_back(ps);
        }

        for (std::size_t r = 0; r < root["routes"].size(); ++r) {
            const YAML::Node &rt = root["routes"][r];
            const std::string source = get_str(rt, "source");
            const std::string dest = get_str(rt, "destination");

            // Role-aware side selection: this node materializes the side matching its role.
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
        if (!is_resolvable_qos_alias(spec.input.reader_qos)) {
            error = "route '" + spec.route_name + "' input.reader_qos '"
                    + spec.input.reader_qos
                    + "' is unresolvable until Phase 7 QoS-library lookup lands (D45) "
                      "(only \"\"/\"default\" supported)";
            return false;
        }
        if (!is_resolvable_qos_alias(spec.output.writer_qos)) {
            error = "route '" + spec.route_name + "' output.writer_qos '"
                    + spec.output.writer_qos
                    + "' is unresolvable until Phase 7 QoS-library lookup lands (D45) "
                      "(only \"\"/\"default\" supported)";
            return false;
        }
    }
    return true;
}

} // namespace router
