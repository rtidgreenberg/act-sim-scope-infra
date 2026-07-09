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

} // namespace router
