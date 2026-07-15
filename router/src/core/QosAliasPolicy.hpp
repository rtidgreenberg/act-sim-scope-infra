// QosAliasPolicy.hpp — the single source of truth for which QoS aliases are currently
// resolvable (D41/D44/D60).
//
// An alias is resolvable iff it is "" (auto — the D39 asymmetric profiles), "default" (the
// built-in profile), or a key in the config's qos_profiles: map (Phase 7a, D60 — XML-alias
// resolution via a QosProvider over qos_libraries). QosResolver enforces this at route-build
// time (RouteEntityFactory); RouteConfigParser's validate_qos_aliases() enforces the identical
// rule at config-load time so a config using an unresolvable alias fails once, loudly, and
// early, instead of as N per-topic sticky errors discovered piecemeal at runtime. Both call
// this one predicate so the two checks cannot drift apart.
//
// This predicate only checks whether the alias is *declared*, not whether the profile it
// points to actually exists in the loaded QoS libraries — that requires a real QosProvider
// (DDS-dependent) and is checked separately, as a router_main startup preflight (D60/D65).
//
// Deliberately dependency-free (no DDS headers): config-layer code needs this rule without
// pulling in Connext.

#pragma once

#include <map>
#include <string>

namespace router {

inline bool is_resolvable_qos_alias(const std::string &alias,
                                    const std::map<std::string, std::string> &qos_profiles) {
    return alias.empty() || alias == "default" || qos_profiles.count(alias) > 0;
}

} // namespace router
