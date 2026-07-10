// QosAliasPolicy.hpp — the single source of truth for which QoS aliases are currently
// resolvable (D41/D44).
//
// Until Phase 7 lands real XML-alias resolution (QosProvider profiles — deferred there
// because its routes are the first consumers of wan_event/wan_status, D45), the only
// aliases anything can honor are "" (auto — the D39 asymmetric profiles) and
// "default" (the built-in profile). QosResolver enforces this at route-build time
// (RouteEntityFactory); RouteConfigParser's validate_qos_aliases() enforces the identical
// rule at config-load time so a config using an unresolvable alias fails once, loudly, and
// early, instead of as N per-topic sticky errors discovered piecemeal at runtime. Both call
// this one predicate so the two checks cannot drift apart.
//
// Deliberately dependency-free (no DDS headers): config-layer code needs this rule without
// pulling in Connext.

#pragma once

#include <string>

namespace router {

inline bool is_resolvable_qos_alias(const std::string &alias) {
    return alias.empty() || alias == "default";
}

} // namespace router
