// router_main.cxx — ACT C++ router entry point.
//
// Builds a real, long-running router process from a route config: parses the YAML
// (RouteConfigParser), creates the configured DDS participants, and wires discovery ->
// controller -> entity factory -> forwarding. Route topic types are learned FROM THE
// WIRE (7c, D64/D70): the DiscoveryDispatcher reads each endpoint's inline SEDP type
// object and the controller builds a topic's entities once its type arrives — the
// router carries no local type objects for forwarded payloads and one
// DynamicRouteFactory serves all types. Runs until SIGINT/SIGTERM.
//
// Usage:
//   router_main --config <path> [--name <router-instance-name>] [--role <node-role>]
//               [--node-name <node-name>] [--admin-participant <name>]
//
// Must be run with cwd == repo root: types.xml/qos_libraries paths in the config are
// repo-root-relative (see router/README.md).
//
// Test isolation is domain-id-only for now (router/test_e2e/conftest.py's unique_domains
// fixture) — a DomainParticipant-level PARTITION per test run (stronger than domain-id
// spreading, since mismatched participant partitions block discovery entirely) is
// deferred to a later pass, after the basic router_main wiring + e2e suite is confirmed
// working end-to-end (see docs/cpp_router/design-decisions.md D50).

#include "config/RouteConfigParser.hpp"
#include "core/AsyncWaitSetDispatcher.hpp"
#include "core/CommandReader.hpp"
#include "core/ControllerJournalPublisher.hpp"
#include "core/DdsStatusPublisher.hpp"
#include "core/DiscoveryDispatcher.hpp"
#include "core/DrainThread.hpp"
#include "core/DynamicRouteFactory.hpp"
#include "core/Log.hpp"
#include "core/ParticipantRegistry.hpp"
#include "core/PresenceMonitor.hpp"
#include "core/QosResolver.hpp"
#include "core/RouterController.hpp"
#include "core/TypeResolver.hpp"

#include "RouterAdminTypes.hpp"

#include <rti/core/cond/AsyncWaitSet.hpp>
#include <rti/core/policy/CorePolicy.hpp>
#include <rti/core/QosProviderParams.hpp>
#include <dds/core/QosProvider.hpp>
#include <dds/dds.hpp>

#include <csignal>
#include <chrono>
#include <fstream>
#include <memory>
#include <string>
#include <thread>
#include <unistd.h>

using namespace router;

namespace {

void print_usage() {
    Log::info("router.usage",
              {{"invocation",
                "router_main --config <path> [--name <router-instance-name>] "
                "[--role <node-role>] [--node-name <node-name>] "
                "[--admin-participant <name>] [--presence-participant <name>] "
                "[--router-id <n>]"}});
}

// SIGINT/SIGTERM flip this; the run loop wakes every 200ms to re-check it (mirrors
// relay/cpp/isc_relay.cxx's existing idiom in this repo).
volatile std::sig_atomic_t g_stop = 0;
void handle_signal(int) { g_stop = 1; }

bool file_exists(const std::string &path) {
    std::ifstream f(path);
    return f.good();
}

// A participant with no declared role is always needed (back-compat for configs that
// don't tag participants by role at all); one with a role only matters to a process
// running that same role — command/status's admin participant is force-included
// separately regardless of its role tag.
bool participant_needed(const ParticipantState &p, const std::string &node_role) {
    return p.role.empty() || p.role == node_role;
}

} // namespace

int main(int argc, char **argv) {
    std::string config_path;
    std::string name_override;
    std::string role_override;
    std::string node_name_override;
    std::string admin_participant_override;
    std::string presence_participant_override;
    std::string router_id_override;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if ((arg == "--config" || arg == "-c") && i + 1 < argc) {
            config_path = argv[++i];
        } else if ((arg == "--name" || arg == "-n") && i + 1 < argc) {
            name_override = argv[++i];
        } else if (arg == "--role" && i + 1 < argc) {
            role_override = argv[++i];
        } else if (arg == "--node-name" && i + 1 < argc) {
            node_name_override = argv[++i];
        } else if (arg == "--admin-participant" && i + 1 < argc) {
            admin_participant_override = argv[++i];
        } else if (arg == "--presence-participant" && i + 1 < argc) {
            presence_participant_override = argv[++i];
        } else if (arg == "--router-id" && i + 1 < argc) {
            // Test/deployment override: two processes launched from one shared config
            // file (e2e router_pair) must not share the RouterHealth key (Phase 8).
            router_id_override = argv[++i];
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

    try {
        RouteConfig cfg;
        std::string error;
        if (!parse_route_config(config_path, cfg, error, role_override, node_name_override)) {
            Log::error("router.config.invalid", {{"config", config_path}, {"error", error}});
            return 2;
        }
        if (!name_override.empty()) {
            cfg.router_name = name_override;
        }
        if (!admin_participant_override.empty()) {
            cfg.admin_participant = admin_participant_override;
        }
        if (!presence_participant_override.empty()) {
            cfg.presence_participant = presence_participant_override;
        }
        if (!router_id_override.empty()) {
            // Full-string, non-negative parse: stoi alone would accept "20x" as 20,
            // wrap "-1" to a huge uint32 at the cast sites, and surface garbage as an
            // unlabeled router.fatal instead of a config error naming the flag.
            try {
                std::size_t consumed = 0;
                int parsed = std::stoi(router_id_override, &consumed);
                if (consumed != router_id_override.size() || parsed < 0) {
                    throw std::invalid_argument("not a non-negative integer");
                }
                cfg.router_id = parsed;
            } catch (const std::exception &) {
                Log::error("router.config.router_id_invalid",
                          {{"value", router_id_override},
                           {"reason", "--router-id must be a non-negative integer"}});
                return 2;
            }
        }
        if (!validate_qos_aliases(cfg, error)) {
            Log::error("router.config.qos_alias", {{"config", config_path}, {"error", error}});
            return 2;
        }
        if (cfg.participants.empty()) {
            Log::error("router.config.no_participants", {{"config", config_path}});
            return 2;
        }

        // command-status.md's "admin rides the local LAN participant" decision, made
        // explicit here instead of inferred from a name-suffix heuristic that picked the
        // wrong node's participant when more than one config-wide "_lan" name existed.
        std::string admin_participant_name = cfg.admin_participant;
        if (admin_participant_name.empty()) {
            if (cfg.participants.size() != 1) {
                Log::error("router.config.admin_participant_missing",
                          {{"config", config_path},
                           {"reason", "router.admin_participant required when more than "
                                      "one participant is declared"}});
                return 2;
            }
            admin_participant_name = cfg.participants.front().name;
        } else if (!has_participant(cfg.participants, admin_participant_name)) {
            Log::error("router.config.admin_participant_unknown",
                      {{"config", config_path}, {"name", admin_participant_name}});
            return 2;
        }

        // The admin participant is always built regardless of role (see the filtering
        // loop below), so a role tag inconsistent with this node's own role would
        // otherwise be accepted silently — catch it here instead.
        for (const auto &p : cfg.participants) {
            if (p.name == admin_participant_name && !p.role.empty()
                && p.role != cfg.node_role) {
                Log::error("router.config.admin_participant_role_mismatch",
                          {{"config", config_path},
                           {"admin_participant", admin_participant_name},
                           {"participant_role", p.role},
                           {"node_role", cfg.node_role}});
                return 2;
            }
        }

        // router.type_name is retired (7c, D70): each topic's DynamicType is learned
        // from the wire (inline SEDP type objects) and one DynamicRouteFactory serves
        // all types. The old D50 Gap-C single-type guard is gone with it.

        if (cfg.routes.empty()) {
            Log::warn("router.routes.none_for_role",
                      {{"config", config_path},
                       {"role", cfg.node_role},
                       {"reason", "no route in this config selected this node's role — "
                                  "check --role/node.role against the routes' source/"
                                  "destination, unless this node is intentionally "
                                  "routeless"}});
        }

        Log::info("router.identity", {{"config", config_path},
                                      {"node", cfg.node_name},
                                      {"role", cfg.node_role},
                                      {"router", cfg.router_name},
                                      {"router_id", std::to_string(cfg.router_id)}});

        if (!cfg.types_xml_path.empty() && !file_exists(cfg.types_xml_path)) {
            Log::error("router.config.types_xml_missing",
                      {{"path", cfg.types_xml_path},
                       {"reason", "not found relative to cwd; router_main must be run "
                                  "with cwd == repo root (see router/README.md)"}});
            return 2;
        }
        for (const auto &qos_path : cfg.qos_library_paths) {
            if (!file_exists(qos_path)) {
                Log::error("router.config.qos_library_missing",
                          {{"path", qos_path},
                           {"reason", "not found relative to cwd; router_main must be run "
                                      "with cwd == repo root (see router/README.md)"}});
                return 2;
            }
        }

        // Phase 7a (D60): one QosProvider over every qos_libraries: file, shared by
        // QosResolver (endpoint aliases) and ParticipantRegistry (participant aliases)
        // below. The production QoS libs are templated with env vars (WAN_TIMEOUT_SEC,
        // peer locators, ...) that the deployment must supply before launch — a missing
        // one makes Connext's XML loader throw naming it; caught here (not left to
        // surface as an unlabeled router.fatal) with the file list attached for context.
        std::shared_ptr<dds::core::QosProvider> qos_provider;
        if (!cfg.qos_library_paths.empty()) {
            try {
                rti::core::QosProviderParams params;
                params.url_profile(dds::core::StringSeq(cfg.qos_library_paths.begin(),
                                                        cfg.qos_library_paths.end()));
                qos_provider = std::make_shared<dds::core::QosProvider>(
                        rti::core::create_qos_provider_ex(params));
            } catch (const std::exception &e) {
                std::string paths;
                for (const auto &p : cfg.qos_library_paths) {
                    paths += (paths.empty() ? "" : ",") + p;
                }
                Log::error("router.config.qos_provider_load_failed",
                          {{"config", config_path}, {"paths", paths}, {"error", e.what()}});
                return 2;
            }
        }

        // Route topic types are wire-learned (D70); the XML load remains only as a
        // legacy/debug path for configs that still name one — it is NOT on the
        // route-build path.
        TypeResolver types;
        if (!cfg.types_xml_path.empty()) {
            types.load_types(cfg.types_xml_path);
        }

        // D74 identity: participant_name = "<node>/<router>" with the act.router
        // role_name sentinel (set by ParticipantRegistry); the same string is what
        // DiscoveryDispatcher reads off peers' builtin data. user_data is retired.
        const std::string router_identity = cfg.node_name + "/" + cfg.router_name;

        // Presence participant (Phase 8, D75): optional; when named it must exist and,
        // like the admin participant, a role tag inconsistent with this node's role is a
        // config bug (it would be filtered out below and PresenceMonitor would fail on a
        // missing participant deep in wiring).
        if (!cfg.presence_participant.empty()) {
            if (!has_participant(cfg.participants, cfg.presence_participant)) {
                Log::error("router.config.presence_participant_unknown",
                          {{"config", config_path}, {"name", cfg.presence_participant}});
                return 2;
            }
            for (const auto &p : cfg.participants) {
                if (p.name == cfg.presence_participant && !p.role.empty()
                    && p.role != cfg.node_role) {
                    Log::error("router.config.presence_participant_role_mismatch",
                              {{"config", config_path},
                               {"presence_participant", cfg.presence_participant},
                               {"participant_role", p.role},
                               {"node_role", cfg.node_role}});
                    return 2;
                }
            }
        }

        // Only build participants this node's role actually needs (plus the admin
        // participant, always) — previously every participant in the file was built
        // unconditionally, regardless of role, wasting a full set of sockets/discovery
        // threads per process and making it easy for the wrong one to get picked as admin.
        std::vector<ParticipantRegistry::Config> participant_configs;
        std::vector<ParticipantState> filtered_participants;
        for (const auto &p : cfg.participants) {
            if (p.name != admin_participant_name && !participant_needed(p, cfg.node_role)) {
                continue;
            }
            ParticipantRegistry::Config pc;
            pc.name = p.name;
            pc.domain = p.domain;
            pc.participant_name = router_identity;
            if (!p.qos_profile_alias.empty()) {
                auto it = cfg.qos_profiles.find(p.qos_profile_alias);
                if (it != cfg.qos_profiles.end()) {
                    pc.qos_provider_profile = it->second;
                }
            }
            participant_configs.push_back(pc);
            filtered_participants.push_back(p);
        }

        // Defense: every active route's endpoint participants must be in the filtered
        // set (should always hold given consistent role-tagging in the YAML; if it
        // doesn't, that's a config bug — fail loudly now rather than let
        // ParticipantRegistry::get() throw deep inside route-entity creation later).
        for (const auto &route : cfg.routes) {
            for (const auto &endpoint_name : {route.input.participant, route.output.participant}) {
                if (!has_participant(filtered_participants, endpoint_name)) {
                    Log::error("router.config.route_participant_role_mismatch",
                              {{"config", config_path},
                               {"route", route.route_name},
                               {"participant", endpoint_name},
                               {"node_role", cfg.node_role},
                               {"reason", "this route's participant was filtered out — "
                                          "its declared role doesn't match this node's "
                                          "role and it isn't the admin participant"}});
                    return 2;
                }
            }
        }

        // QosResolver built before ParticipantRegistry so its preflight (below) runs
        // before any DDS entity exists (D60/D65).
        QosResolver qos(qos_provider, cfg.qos_profiles);

        // Preflight: eagerly resolve every route's declared reader_qos/writer_qos alias
        // against the loaded QosProvider. is_resolvable_qos_alias/validate_qos_aliases
        // already confirmed each alias is *declared*; this confirms the profile it names
        // actually *exists* in the loaded XML — the class of bug the historical
        // lan_status_1hz -> status_1hz_qos typo was (D60) — at startup instead of only
        // when that route's topic later happens to build entities.
        for (const auto &route : cfg.routes) {
            try {
                qos.reader_qos(route.input.reader_qos);
                qos.writer_qos(route.output.writer_qos);
            } catch (const std::exception &e) {
                Log::error("router.config.qos_profile_unresolvable",
                          {{"config", config_path},
                           {"route", route.route_name},
                           {"error", e.what()}});
                return 2;
            }
        }

        // Same preflight for the participant leg: a declared participant qos: alias whose
        // LIB::Profile doesn't exist in the loaded XML would otherwise only throw inside
        // ParticipantRegistry's constructor as an unlabeled fatal.
        for (const auto &pc : participant_configs) {
            if (pc.qos_provider_profile.empty()) {
                continue;
            }
            try {
                if (!qos_provider) {
                    throw std::runtime_error(
                            "participant qos alias is declared but no QosProvider is "
                            "loaded (qos_libraries: missing?)");
                }
                qos_provider->participant_qos(pc.qos_provider_profile);
            } catch (const std::exception &e) {
                Log::error("router.config.qos_profile_unresolvable",
                          {{"config", config_path},
                           {"participant", pc.name},
                           {"profile", pc.qos_provider_profile},
                           {"error", e.what()}});
                return 2;
            }
        }

        // Disabled startup (D52): create participants DISABLED so no discovery traffic
        // flows until the builtin-reader conditions are attached and the AsyncWaitSet is
        // running. registry.enable_all() below (after aws.start()) turns discovery on.
        ParticipantRegistry registry(participant_configs, /*autoenable=*/false, qos_provider);

        dds::domain::DomainParticipant admin_dp = registry.get(admin_participant_name);
        DdsStatusPublisher status_pub(admin_dp, "ActRouterStatus");

        rti::core::cond::AsyncWaitSet aws;
        AsyncWaitSetDispatcher route_disp(aws);
        DynamicRouteFactory factory(registry, types, qos, route_disp);

        // Phase 6 slice 6b: controller journal (debug analysis). Its backlog StatusCondition
        // attaches to the AWS here, before aws.start()/enable_all() (D52). The writer is
        // always created; it emits no data traffic until a recorder reader matches. Declared
        // before ctrl so ctrl can hold the IControllerJournal seam (D55).
        ControllerJournalPublisher journal_pub(admin_dp, aws);

        // Phase 8 (D75): presence monitor — RouterHealth heartbeat pair on the presence
        // (WAN) participant, ActRouterMeshStatus aggregate on the admin (LAN)
        // participant. Optional: no presence_participant in the config = no presence.
        // Conditions attach to the AWS here, before aws.start()/enable_all() (D52).
        std::unique_ptr<PresenceMonitor> presence;
        if (!cfg.presence_participant.empty()) {
            presence.reset(new PresenceMonitor(
                    aws, registry.get(cfg.presence_participant), admin_dp,
                    cfg.node_name, cfg.router_name,
                    static_cast<std::uint32_t>(cfg.router_id)));
        }

        RouterIdentityInfo identity;
        identity.node_name = cfg.node_name;
        identity.router_name = cfg.router_name;
        identity.router_id = static_cast<std::uint32_t>(cfg.router_id);
        identity.status_id = cfg.router_name + "-" + std::to_string(::getpid());
        identity.node_role = cfg.node_role;

        RouterController ctrl(identity, cfg.routes, filtered_participants, &factory,
                              &status_pub, &journal_pub, presence.get());
        factory.set_controller(&ctrl);

        DiscoveryDispatcher discovery(aws, ctrl, registry, router_identity, types);

        // Phase 6 slice 6a: LAN admin command channel. Attached to the AWS BEFORE
        // aws.start()/enable_all() (same D52 reason as discovery — an edge-triggered
        // condition attached after enable could strand a command that arrives in the gap).
        // The D47 CFT keys on this router's own identity, so only commands addressed here
        // reach the callback; the controller runs the state machine and DdsStatusPublisher
        // writes the ack.
        CommandReader command_reader(aws, ctrl, admin_dp, cfg.node_name, cfg.router_name);
        aws.start();

        // D52: only now, with every builtin-reader condition attached and the
        // AsyncWaitSet dispatching, enable the participants. Discovery samples from here
        // on arrive as genuine post-start condition transitions and are never stranded.
        registry.enable_all();

        // The controller's constructor-time startup snapshot was written while the admin
        // participant was still disabled (a no-op on a disabled writer), so re-publish it
        // now that the writer is live. Done before the DrainThread starts so it cannot
        // race the controller state the drain thread mutates.
        ctrl.republish_status();

        // Create-and-observe (D64/D66): build every startup-enabled route's entities
        // now — creation gates on nothing. After enable_all() (entity ignores/instance
        // handles need enabled participants) and before the DrainThread (single-threaded
        // here, so no strand race).
        ctrl.activate();

        // 7d (D63): the drain loop doubles as the status-refresh tick — every second it
        // posts RefreshCounters, so per-route samples_forwarded is observable in steady
        // state (republish without a state_revision bump). Config-fixed cadence, D14
        // precedent. Phase 8 (D75): with presence configured it also carries the 1 s
        // PresenceTick — the RouterHealth heartbeat (separate knob: the heartbeat must
        // stay inside its 2 s DEADLINE offer regardless of refresh retuning).
        DrainThread drain(ctrl, std::chrono::milliseconds(1000),
                          std::chrono::milliseconds(
                                  presence ? kHeartbeatPeriodMs : 0));

        Log::info("router.start.ok",
                  {{"admin_participant", admin_participant_name},
                   {"participants", std::to_string(participant_configs.size())},
                   {"routes", std::to_string(cfg.routes.size())}});

        std::signal(SIGINT, handle_signal);
        std::signal(SIGTERM, handle_signal);
        while (!g_stop) {
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }

        Log::info("router.stop.begin", {});
        route_disp.shutdown();
        discovery.shutdown();
        command_reader.shutdown();
        drain.stop(); // stop the ticks before detaching the presence conditions
        if (presence) {
            presence->shutdown();
        }
        journal_pub.shutdown();
        aws.stop();
        Log::info("router.stop.ok", {});
        return 0;

    } catch (const std::exception &e) {
        Log::error("router.fatal", {{"error", e.what()}});
        return 1;
    }
}
