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
    if (!cfg.partition_names.empty()) {
        // D83: initial partition name set (protected identity + optional team entries).
        qos << dds::core::policy::Partition(cfg.partition_names);
    }
    if (cfg.use_spdp2) {
        // D78 (reinstated): WAN participants use SPDP2 | SEDP. D87 had retracted this,
        // blaming a disabled-then-delayed-enable (D52) discovery failure — but the D92
        // CORRECTION (2026-07-22, design-decisions.md) shows that failure was a
        // rig/measurement artifact, not an SPDP2 defect: loopback has no multicast and the
        // captures were lo-only, the "failure" was largely a fragile success metric, and a
        // controlled cross-process harness discovers 66/66. A clean wire capture also
        // confirms SPDP2 periodically re-announces to unmatched peers (writer 0x00010082 at
        // participant_announcement_period) — i.e. it self-heals. D78's measured wins stand
        // (~30x faster retarget, lower idle bandwidth than plain SPDP). The one real
        // residual is SPDP2's probabilistic post-match settle window (D78's accepted
        // residual) — absorbed by D91's verification-gated (never timing-gated) route
        // enablement, not assumed away here. Not yet validated over a genuine multi-host WAN
        // (all evidence to date is single-host/loopback) — see design-decisions.md D92.
        //
        // Gated on `use_spdp2` (YAML `spdp2: true`), independent of team_scoped and on_wan:
        // team_scoped is the D83 protected-identity-partition flag (platform_wan-only post-
        // D103; was team_wan-only before D103 retired it); on_wan is
        // the link-stats WAN-leg flag (every WAN participant). SPDP2 tracks the same set as
        // on_wan today, but the choice of discovery protocol and the link-stats concern stay
        // separate flags so neither silently changes when the other is retuned.
        rti::core::policy::DiscoveryConfig discovery =
            qos.policy<rti::core::policy::DiscoveryConfig>();
        discovery.builtin_discovery_plugins(
            rti::core::policy::DiscoveryConfigBuiltinPluginKindMask::SPDP2() |
            rti::core::policy::DiscoveryConfigBuiltinPluginKindMask::SEDP());
        qos << discovery;
    }
    // Participants without `spdp2: true` stay on the default plain SPDP (D78): LAN
    // participants never retarget partition (D20), so there is no reason to take on SPDP2's
    // settle-time risk there.
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
            on_wan_.emplace(cfg.name, cfg.on_wan);
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

bool ParticipantRegistry::on_wan(const std::string &name) const {
    std::map<std::string, bool>::const_iterator it = on_wan_.find(name);
    return it != on_wan_.end() && it->second;
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
