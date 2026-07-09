// RouteConfigParser.cxx — yaml-cpp route/participant parsing + role-aware selection (D36).

#include "config/RouteConfigParser.hpp"

#include <yaml-cpp/yaml.h>

#include <stdexcept>

namespace router {

namespace {

std::string get_str(const YAML::Node &n, const std::string &key,
                    const std::string &dflt = "") {
    return (n && n[key]) ? n[key].as<std::string>() : dflt;
}

// Substitute ${node.name} and single-quote the value for a string SQL filter param.
std::string resolve_filter_param(const std::string &raw, const std::string &node_name) {
    std::string v = raw;
    const std::string token = "${node.name}";
    std::string::size_type pos;
    while ((pos = v.find(token)) != std::string::npos) {
        v.replace(pos, token.size(), node_name);
    }
    return "'" + v + "'"; // string comparison => quoted (validated 7.7)
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
                    resolve_filter_param(f["parameters"][i].as<std::string>(), node_name));
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

        const YAML::Node &router = root["router"];
        out.router_name = get_str(router, "name");
        if (router && router["id"]) {
            out.router_id = router["id"].as<std::int32_t>();
        }

        for (auto it = root["participants"].begin(); it != root["participants"].end(); ++it) {
            ParticipantState ps;
            ps.name = it->first.as<std::string>();
            const YAML::Node &p = it->second;
            if (p["domain"]) {
                ps.domain = p["domain"].as<std::int32_t>();
            }
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

} // namespace router
