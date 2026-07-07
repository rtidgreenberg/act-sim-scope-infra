// router_main.cxx — ACT C++ router entry point (Phase 0 skeleton).
//
// Phase 0 scope (docs/cpp_router/implementation-plan.md): parse a config path and an
// optional router instance name, validate the config, print the router identity, and
// exit cleanly. NO DDS entities are created here yet — that begins in later phases,
// where main constructs the RouterController (docs/cpp_router/code-architecture.md).
//
// Usage:
//   router_main --config <path> [--name <router-instance-name>]

#include <string>

#include "config/RouterIdentity.hpp"
#include "core/Log.hpp"

using namespace router;

namespace {

void print_usage() {
    Log::info("router.usage",
              {{"invocation", "router_main --config <path> [--name <router-instance-name>]"}});
}

} // namespace

int main(int argc, char **argv) {
    std::string config_path;
    std::string name_override;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if ((arg == "--config" || arg == "-c") && i + 1 < argc) {
            config_path = argv[++i];
        } else if ((arg == "--name" || arg == "-n") && i + 1 < argc) {
            name_override = argv[++i];
        } else if (arg == "--help" || arg == "-h") {
            print_usage();
            return 0;
        } else {
            Log::error("router.args.unknown", {{"arg", arg}});
            print_usage();
            return 2;
        }
    }

    if (config_path.empty()) {
        Log::error("router.config.missing", {{"reason", "--config <path> is required"}});
        print_usage();
        return 2;
    }

    RouterIdentity identity;
    std::string error;
    if (!load_identity(config_path, identity, error)) {
        Log::error("router.config.invalid", {{"config", config_path}, {"error", error}});
        return 2;
    }

    if (!name_override.empty()) {
        identity.router_name = name_override;
    }

    Log::info("router.identity", {{"config", config_path},
                                  {"node", identity.node_name},
                                  {"role", identity.node_role},
                                  {"router", identity.router_name},
                                  {"router_id", std::to_string(identity.router_id)},
                                  {"config_set", identity.config_set},
                                  {"forwarding_mode", identity.default_forwarding_mode}});
    Log::info("router.start.ok",
              {{"phase", "0"}, {"note", "skeleton: config validated, no DDS entities created"}});
    return 0;
}
