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
    // Repo-root-relative paths as literally written in the YAML (router_main resolves
    // them against its cwd, which must be the repo root — see router/README.md).
    std::string types_xml_path;               // types.xml
    std::vector<std::string> qos_library_paths; // qos_libraries (not yet applied to
                                                // entity QoS — Phase 7, D45 — carried
                                                // here only so router_main can fail fast
                                                // on a missing file).
    // router.type_name: the single DynamicData type (registered in types_xml_path) this
    // process's DynamicRouteFactory is bound to. DynamicRouteFactory binds one type name
    // for its whole lifetime (D34/D35 — multi-type dispatch is deferred), so a config
    // whose active routes span more than one type cannot be served by one router_main
    // process yet; router_main fails fast rather than silently mis-typing a route.
    std::string type_name;
    // router.admin_participant: which participant (by name, must exist in participants)
    // carries the command/status admin channel — command-status.md's "admin rides the
    // local LAN participant" decision, made explicit instead of inferred from a "_lan"
    // name-suffix heuristic (D50 follow-up). Empty is allowed only when participants
    // has exactly one entry.
    std::string admin_participant;
};

// True if some participant in the list has this exact name. Shared by parse_route_config's
// own endpoint-existence checks and by router_main.cxx's admin_participant validation —
// one definition of "does this participant exist" for both callers.
bool has_participant(const std::vector<ParticipantState> &participants,
                    const std::string &name);

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
