#include "core/WanAdapterFactory.hpp"
#include "core/AsyncWaitSetDispatcher.hpp"
#include "core/ParticipantRegistry.hpp"
#include "core/QosResolver.hpp"

#include <rti/config/Logger.hpp>
#include <atomic>
#include <cstdio>
#include <iostream>
#include <map>
#include <set>
#include <sstream>

using Factory = router::WanAdapterFactory;
using Data = dds::core::xtypes::DynamicData;

struct Stats : router::IWanStatsRegistry {
    std::set<router::IWanStatsSource*> sources;
    void register_source(router::IWanStatsSource* source) override { sources.insert(source); }
    void unregister_source(router::IWanStatsSource* source) override { sources.erase(source); }
};

struct Binding {
    Factory* factory;
    std::string topic;
    uint64_t generation;
};

static Factory::MetadataProvider metadata(const std::string& node, bool control, bool global) {
    return [node, control, global](const Data& data) {
        router::WanMetadata result;
        result.source_id = node;
        result.source_role = node == "Control20" ? "control" : "platform";
        result.run_id = data.value<std::string>("run_id");
        result.audit_sequence = data.value<uint64_t>("audit_sequence");
        result.sent_at_ns = data.value<int64_t>("sent_at_ns");
        result.session_id = 42;
        result.control = control;
        result.global = global;
        if (node == "Control20") {
            result.destination = data.value<std::string>("destination");
        }
        return result;
    };
}

static void print_factory(const Factory& factory) {
    std::cout << "{\"writers\":" << factory.wan_writers()
              << ",\"readers\":" << factory.wan_readers()
              << ",\"source_readers\":" << factory.source_readers()
              << ",\"destination_writers\":" << factory.destination_writers() << "}";
}

int main(int argc, char** argv) {
    if (argc != 6) {
        std::cerr << "Usage: wan_adapter_pilot types.xml qos.xml control_domain wan_domain platform_domain\n";
        return 2;
    }
    try {
        rti::config::Logger::instance().output_file(stderr);
        auto qos_provider = std::make_shared<dds::core::QosProvider>(argv[2]);
        dds::core::QosProvider types(argv[1]);
        router::QosResolver qos(qos_provider, {
            {"wan_event", "ACT_QOS_LIB::wan_event"},
            {"wan_status", "ACT_QOS_LIB::wan_union_status"},
            {"router_health", "ACT_QOS_LIB::router_health"},
            {"lan_event", "ACT_QOS_LIB::lan_event"},
            {"lan_status", "ACT_QOS_LIB::lan_status_1s"}
        });
        const int control_domain = std::stoi(argv[3]);
        const int wan_domain = std::stoi(argv[4]);
        const int platform_domain = std::stoi(argv[5]);
        for (const int domain : {control_domain, wan_domain, platform_domain}) {
            if (domain < 0 || domain > 232) {
                throw std::runtime_error("invalid pilot domain");
            }
        }
        rti::core::cond::AsyncWaitSet aws;
        aws.start();
        router::ParticipantRegistry::Config control_lan, control_wan, platform_lan, platform_wan;
        control_lan.name = "control_lan";
        control_lan.domain = control_domain;
        control_lan.participant_name = "Control20/pilot";
        control_lan.qos_provider_profile = "ACT_QOS_LIB::lan_transport_loopback";
        control_wan = control_lan;
        control_wan.name = "control_wan";
        control_wan.domain = wan_domain;
        control_wan.on_wan = true;
        control_wan.qos_provider_profile = "ACT_QOS_LIB::wan_two_topic_participant";
        platform_lan = control_lan;
        platform_lan.name = "platform_lan";
        platform_lan.domain = platform_domain;
        platform_lan.participant_name = "Boat30/pilot";
        platform_wan = control_wan;
        platform_wan.name = "platform_wan";
        platform_wan.participant_name = "Boat30/pilot";
        router::ParticipantRegistry participants(
            {control_lan, control_wan, platform_lan, platform_wan}, true, qos_provider);
        Stats stats;
        router::AsyncWaitSetDispatcher control_dispatcher(aws), platform_dispatcher(aws);
        control_dispatcher.set_stats_registry(&stats);
        platform_dispatcher.set_stats_registry(&stats);
        std::atomic<uint64_t> errors{0};
        std::atomic<uint64_t> warnings{0};
        router::WanAdapterCallbacks callbacks;
        callbacks.error = [&errors](
                const std::string&, const std::string&, uint64_t, const std::string&) {
            errors.fetch_add(1);
        };
        callbacks.warning = [&warnings](
                const std::string&, const std::string&, uint64_t, const std::string&) {
            warnings.fetch_add(1);
        };
        Factory control(participants, qos, control_dispatcher, types,
                        "control_wan", "Control20", "pilot", callbacks);
        Factory platform(participants, qos, platform_dispatcher, types,
                         "platform_wan", "Boat30", "pilot", callbacks);
        std::map<std::string, Binding> bindings;
        const router::WanReceiveFilter nav_filter{"payload._d <> 10", {}};
        const router::WanReceiveFilter command_filter{
            "payload._d = 1 AND audience.destination = %0", {"'Boat30'"}};
        auto bind = [&](const std::string& name, uint64_t generation) {
            if (name == "command_source") {
                control.bind_source(name, "ControlCommand", generation, "control_lan",
                                    "lan_event", metadata("Control20", false, false));
                bindings.emplace(name, Binding{&control, "ControlCommand", generation});
            } else if (name == "command_destination") {
                platform.bind_destination(name, "ControlCommand", generation, "platform_lan",
                                          "lan_event", command_filter);
                bindings.emplace(name, Binding{&platform, "ControlCommand", generation});
            } else if (name == "nav_c2" || name == "nav_global") {
                platform.bind_source(name, "PlatformInitStatus", generation, "platform_lan",
                                     "lan_status", metadata("Boat30", name == "nav_c2",
                                                           name == "nav_global"));
                bindings.emplace(name, Binding{&platform, "PlatformInitStatus", generation});
            } else if (name == "nav_destination" || name == "nav_destination_alias") {
                control.bind_destination(name, "PlatformInitStatus", generation, "control_lan",
                                         "lan_status", nav_filter);
                bindings.emplace(name, Binding{&control, "PlatformInitStatus", generation});
            } else if (name == "power_source") {
                platform.bind_source(name, "PlatformPowerStatus", generation, "platform_lan",
                                     "lan_status", metadata("Boat30", true, false));
                bindings.emplace(name, Binding{&platform, "PlatformPowerStatus", generation});
            } else if (name == "power_destination") {
                control.bind_destination(name, "PlatformPowerStatus", generation, "control_lan",
                                         "lan_status", nav_filter);
                bindings.emplace(name, Binding{&control, "PlatformPowerStatus", generation});
            } else if (name == "nav_local_destination") {
                platform.bind_destination(name, "PlatformInitStatus", generation, "platform_lan",
                                          "lan_status", nav_filter);
                bindings.emplace(name, Binding{&platform, "PlatformInitStatus", generation});
            } else {
                throw std::runtime_error("unknown pilot binding");
            }
        };
        uint64_t generation = 1;
        for (const auto& name : {"command_source", "command_destination", "nav_c2", "nav_global",
                                 "nav_destination", "nav_destination_alias"}) {
            bind(name, generation++);
        }
        std::cout << "{\"ready\":true}" << std::endl;
        std::string line;
        while (std::getline(std::cin, line)) {
            try {
                std::istringstream input(line);
                std::string operation, name;
                input >> operation;
                if (operation == "stats") {
                    std::cout << "{\"control\":";
                    print_factory(control);
                    std::cout << ",\"platform\":";
                    print_factory(platform);
                    std::cout << ",\"stats_sources\":" << stats.sources.size()
                              << ",\"errors\":" << errors.load()
                              << ",\"warnings\":" << warnings.load()
                              << ",\"counts\":{";
                    bool first = true;
                    for (const auto& binding : bindings) {
                        std::cout << (first ? "" : ",") << "\"" << binding.first << "\":"
                                  << binding.second.factory->forwarded(binding.first,
                                                                      binding.second.topic);
                        first = false;
                    }
                    std::cout << "}}" << std::endl;
                    continue;
                }
                if (operation == "bind" || operation == "unbind") {
                    uint64_t stamp;
                    if (!(input >> name >> stamp)) {
                        throw std::runtime_error("binding operation needs name and generation");
                    }
                    if (operation == "bind") {
                        bind(name, stamp);
                    } else {
                        const auto binding = bindings.at(name);
                        binding.factory->unbind(name, binding.topic, stamp);
                        bindings.erase(name);
                    }
                } else if (operation == "bad_source") {
                    platform.bind_source("bad_source", "PlatformPowerStatus", 100,
                                         "platform_lan", "missing_alias", metadata("Boat30", true, false));
                } else if (operation == "bad_destination") {
                    control.bind_destination("bad_destination", "ActRouterDiagnostics", 100,
                                             "control_lan", "missing_alias", nav_filter);
                } else if (operation == "bad_filter") {
                    control.bind_destination("bad_filter", "ControlCommand", 100,
                                             "control_lan", "lan_event",
                                             {"INVALID_FIELD = 1", {}});
                } else if (operation == "filter_command") {
                    if (!(input >> name) || (name != "Boat30" && name != "OtherBoat")) {
                        throw std::runtime_error("filter_command needs Boat30 or OtherBoat");
                    }
                    platform.update_receive_filter("ControlCommand",
                        {"payload._d = 1 AND audience.destination = %0", {"'" + name + "'"}});
                } else if (operation == "shutdown") {
                    platform.shutdown();
                    control.shutdown();
                    bindings.clear();
                } else {
                    throw std::runtime_error("unknown pilot operation");
                }
                std::cout << "{\"ok\":true}" << std::endl;
            } catch (const std::exception& e) {
                std::cerr << "pilot operation failed: " << e.what() << std::endl;
                std::cout << "{\"ok\":false}" << std::endl;
            }
        }
        platform.shutdown();
        control.shutdown();
        control_dispatcher.shutdown();
        platform_dispatcher.shutdown();
        aws.stop();
    } catch (const std::exception& e) {
        std::cerr << "pilot failed: " << e.what() << std::endl;
        return 1;
    }
}
