// RouterIdentity.hpp — Phase-0 minimal config identity reader.
//
// Phase 0 only needs to validate the config path and print the router's identity, so
// this reads a SMALL, FIXED subset of the config YAML: node.{name,role} and
// router.{id,name,config_set,default_forwarding_mode}. It is deliberately NOT a general
// YAML parser (no external yaml-cpp dependency in this environment) — full route/QoS
// parsing (RouteConfigParser) arrives in a later phase per docs/cpp_router/code-architecture.md.
//
// It has NO DDS dependency, which keeps the Phase-0 unit test a true no-DDS target.

#pragma once

#include <string>

namespace router {

struct RouterIdentity {
    std::string node_name;
    std::string node_role;
    long router_id = -1;
    std::string router_name;
    std::string config_set;
    std::string default_forwarding_mode;
};

// Reads the identity fields from the flat, 2-space-indented config at `path`.
// Returns true on success. On failure returns false and sets `error` to a concise reason
// (cannot open file, or missing required node.name / router.name).
bool load_identity(const std::string &path, RouterIdentity &out, std::string &error);

} // namespace router
