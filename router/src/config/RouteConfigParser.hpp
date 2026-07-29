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
#include "ActTypes.hpp"

#include <map>
#include <string>
#include <vector>

namespace router {

struct RouteConfig {
    std::string node_name;
    std::string node_role;
    // The identity's router half (D79: the D74 "<node>/<router>" participant name is the
    // ONLY router identity; router_id is retired). Under D80's one system-wide config
    // this is a fleet-wide constant — optional in the YAML, default "router".
    std::string router_name;
    // SHA-256 (full lowercase hex, 64 chars) over the RAW BYTES of the loaded config
    // file, computed once at load (D80/D79-addendum). Stamped into every RouterHealth
    // heartbeat so C2 observes config drift mesh-wide.
    std::string config_hash;
    std::vector<ParticipantState> participants;
    // Concrete active-side routes for this node (routes where node.role is source or
    // destination); each already reduced to the selected side's input/output.
    std::vector<RouterRouteSpec> routes;
    // Repo-root-relative paths as literally written in the YAML (router_main resolves
    // them against its cwd, which must be the repo root — see router/README.md).
    std::string types_xml_path;               // types.xml
    std::vector<std::string> qos_library_paths; // qos_libraries: loaded into a QosProvider
                                                // by router_main (Phase 7a, D60).
    // qos_profiles: alias -> "LIB::Profile" (e.g. wan_event -> WAN_QOS_LIB::event_qos).
    // Endpoint reader_qos:/writer_qos: and participant qos: values are alias keys into this
    // map; router_main resolves them via a QosProvider built over qos_library_paths (D60).
    std::map<std::string, std::string> qos_profiles;
    // (router.type_name retired — 7c/D70: topic types are learned from the wire and one
    // DynamicRouteFactory serves all types.)
    // router.admin_participant: which participant (by name, must exist in participants)
    // carries the command/status admin channel — command-status.md's "admin rides the
    // local LAN participant" decision, made explicit instead of inferred from a "_lan"
    // name-suffix heuristic (D50 follow-up). Empty is allowed only when participants
    // has exactly one entry.
    std::string admin_participant;
    // router.presence_participant (Phase 8, D75): the participant carrying the
    // RouterHealth heartbeat pair (this role's WAN participant), already resolved for
    // this node's role (the YAML accepts a scalar or a per-role map). Empty = presence
    // disabled for this process — configs without it are unaffected.
    std::string presence_participant;
    // router.link_stats_period_ms (Phase 9, D14/D81): config-fixed link-metrics poll
    // cadence (constant per run so experiment sweeps stay comparable). Default 1000 ms.
    // Only takes effect when presence is active — the collector rides the WAN/presence
    // participant (D81 item 6). Sits beside the (currently constant) heartbeat period.
    int link_stats_period_ms = 1000;
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
