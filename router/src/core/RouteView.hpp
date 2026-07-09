// RouteView.hpp — the immutable resolved active-side route spec (D6/D23).
//
// Runtimes make forwarding decisions from this; they never read lifecycle state. Minted
// by the controller (Phase 1: once at construction; later: on any spec-affecting change)
// with a stamp from the global entity-generation counter (D23).

#pragma once

#include "RouterAdminTypes.hpp"

#include <cstdint>

namespace router {

struct RouteView {
    RouterRouteSpec spec;                // concrete active-side spec (D7/D10)
    std::uint64_t entity_generation = 0; // stamp from the global counter at mint (D23)
};

} // namespace router
