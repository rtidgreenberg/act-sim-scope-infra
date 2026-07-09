// ParticipantRegistry.cxx — DDS participant creation with UDPv4 + user-data tag.

#include "ParticipantRegistry.hpp"
#include "Log.hpp"

#include <rti/core/policy/CorePolicy.hpp>   // rti::core::policy::TransportBuiltin
#include <dds/core/policy/CorePolicy.hpp>   // dds::core::policy::UserData

namespace router {

namespace {

dds::domain::qos::DomainParticipantQos make_participant_qos(
    const ParticipantRegistry::Config &cfg) {
    dds::domain::qos::DomainParticipantQos qos =
        dds::domain::DomainParticipant::default_participant_qos();
    // UDPv4 only — no shared memory segments on /dev/shm per vboxsf safety rules.
    qos << rti::core::policy::TransportBuiltin::UDPv4();
    if (!cfg.user_data_tag.empty()) {
        dds::core::ByteSeq ud(cfg.user_data_tag.begin(), cfg.user_data_tag.end());
        qos << dds::core::policy::UserData(ud);
    }
    return qos;
}

} // namespace

ParticipantRegistry::ParticipantRegistry(const std::vector<Config> &configs) {
    // Phase 2.5: participants are created enabled. KEEP_LAST builtin caches ensure no
    // discovery events are missed even if conditions attach after the participant
    // is live). Phase 3 should add the disabled-startup optimisation (D12).
    for (const Config &cfg : configs) {
        dds::domain::DomainParticipant dp(cfg.domain, make_participant_qos(cfg));
        participants_.emplace(cfg.name, dp);
        names_.push_back(cfg.name);
        Log::info("participant_created",
                  {{"name", cfg.name},
                   {"domain", std::to_string(cfg.domain)},
                   {"tag", cfg.user_data_tag}});
    }
}

ParticipantRegistry::~ParticipantRegistry() {}

dds::domain::DomainParticipant ParticipantRegistry::get(const std::string &name) const {
    return participants_.at(name);
}

} // namespace router
