// RouteConfigParser.hpp — full route/participant YAML parsing (Phase 4, D36).
//
// Parses the routes:/participants: sections (Phase 0's RouterIdentity reader stays
// identity-only) with yaml-cpp, and performs role-aware side selection: each route
// declares source/destination roles and a source_side/destination_side pair; the local
// node.role picks which side this instance materializes, producing a concrete active-side
// RouterRouteSpec (D7/D10). Filter parameters have ${node.name} substituted and string
// values single-quoted for the SQL filter (validated 7.7).

#pragma once

#include "core/RouterState.hpp" // ParticipantState
#include "RouterAdminTypes.hpp"

#include <string>
#include <vector>

namespace router {

struct RouteConfig {
    std::string node_name;
    std::string node_role;
    std::string router_name;
    std::int32_t router_id = 0;
    std::vector<ParticipantState> participants;
    // Concrete active-side routes for this node (routes where node.role is source or
    // destination); each already reduced to the selected side's input/output.
    std::vector<RouterRouteSpec> routes;
};

// Parse the config at path. Optional role_override/name_override let one config file be
// loaded "as" either node role for testing both sides; empty => use the file's node.*.
// Returns false and sets error on open/parse failure.
bool parse_route_config(const std::string &path, RouteConfig &out, std::string &error,
                        const std::string &role_override = "",
                        const std::string &name_override = "");

// Check every route's endpoint QoS aliases against the resolvable set (D44 —
// QosAliasPolicy.hpp, the same rule QosResolver enforces at route-build time). NOT called
// by parse_route_config itself (parsing stays purely syntactic); a caller that is about to
// hand this config to the real route-building pipeline should call this first, so an
// unresolvable alias fails once with one clear message instead of as N per-topic sticky
// errors discovered piecemeal at runtime. Returns false and sets error on the first
// unresolvable alias found.
bool validate_qos_aliases(const RouteConfig &cfg, std::string &error);

} // namespace router
