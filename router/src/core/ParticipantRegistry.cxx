// ParticipantRegistry.cxx — DDS participant creation with UDPv4 + EntityName identity.

#include "ParticipantRegistry.hpp"
#include "Log.hpp"

#include <rti/core/policy/CorePolicy.hpp>   // TransportBuiltin, EntityName
#include <dds/core/policy/CorePolicy.hpp>   // dds::core::policy::EntityFactory

namespace router {

namespace {

dds::domain::qos::DomainParticipantQos make_participant_qos(
    const ParticipantRegistry::Config &cfg,
    const std::shared_ptr<dds::core::QosProvider> &provider) {
    dds::domain::qos::DomainParticipantQos qos =
        (provider && !cfg.qos_provider_profile.empty())
            ? provider->participant_qos(cfg.qos_provider_profile) // named alias (D60)
            : dds::domain::DomainParticipant::default_participant_qos();
    // UDPv4 only — no shared memory segments on /dev/shm per vboxsf safety rules.
    qos << rti::core::policy::TransportBuiltin::UDPv4();
    if (!cfg.participant_name.empty()) {
        // D74 identity: name = "<node>/<router>" (Admin Console display), role_name =
        // the act.router detection sentinel. Replaces the D15 user_data tag.
        rti::core::policy::EntityName entity_name(cfg.participant_name);
        entity_name.role_name(std::string(kActRouterRoleName));
        qos << entity_name;
    }
    return qos;
}

} // namespace

ParticipantRegistry::ParticipantRegistry(const std::vector<Config> &configs,
                                         bool autoenable,
                                         std::shared_ptr<dds::core::QosProvider> provider) {
    // For disabled startup (D52), flip the process-global DomainParticipantFactory
    // EntityFactory policy to ManuallyEnable for the duration of this construction loop
    // so each participant is created DISABLED, then restore it. The factory QoS is a
    // process-wide singleton, so it must be restored (even on exception) or later
    // participant creation elsewhere in the process would inherit the disabled setting.
    const dds::domain::qos::DomainParticipantFactoryQos saved_factory_qos =
        dds::domain::DomainParticipant::participant_factory_qos();
    if (!autoenable) {
        dds::domain::qos::DomainParticipantFactoryQos factory_qos = saved_factory_qos;
        factory_qos << dds::core::policy::EntityFactory::ManuallyEnable();
        dds::domain::DomainParticipant::participant_factory_qos(factory_qos);
    }

    try {
        for (const Config &cfg : configs) {
            dds::domain::DomainParticipant dp(cfg.domain, make_participant_qos(cfg, provider));
            participants_.emplace(cfg.name, dp);
            names_.push_back(cfg.name);
            Log::info("participant_created",
                      {{"name", cfg.name},
                       {"domain", std::to_string(cfg.domain)},
                       {"participant_name", cfg.participant_name},
                       {"enabled", autoenable ? "true" : "false"}});
        }
    } catch (...) {
        if (!autoenable) {
            dds::domain::DomainParticipant::participant_factory_qos(saved_factory_qos);
        }
        throw;
    }

    if (!autoenable) {
        dds::domain::DomainParticipant::participant_factory_qos(saved_factory_qos);
    }
}

ParticipantRegistry::~ParticipantRegistry() {}

dds::domain::DomainParticipant ParticipantRegistry::get(const std::string &name) const {
    return participants_.at(name);
}

void ParticipantRegistry::enable_all() {
    for (std::map<std::string, dds::domain::DomainParticipant>::iterator it =
             participants_.begin();
         it != participants_.end(); ++it) {
        it->second.enable(); // recursively enables builtin readers + children (D52)
        Log::info("participant_enabled", {{"name", it->first}});
    }
}

} // namespace router
