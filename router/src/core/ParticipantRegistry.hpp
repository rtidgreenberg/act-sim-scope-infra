// ParticipantRegistry.hpp — creates DDS participants from config (Phase 2.5).
//
// Creates UDPv4-only participants carrying the router identity as EntityName (D74):
// participant_name.name = "<node>/<router>", role_name = the act.router sentinel. This
// replaced the D15 user_data tag in Phase 8 — same ignore/join mechanisms, new field —
// and is what RTI Admin Console displays natively.
//
// Startup ordering (D52). A participant enabled at construction begins discovery
// immediately. If the builtin-reader ReadConditions and the AsyncWaitSet that dispatches
// them are wired up AFTER that point, any discovery sample that lands in the
// enable -> aws.start() window sets a ReadCondition's trigger before the AsyncWaitSet is
// running; the AWS's handler dispatch is edge-triggered, so a condition already true at
// start() that never re-transitions is never dispatched and the sample is stranded (the
// flaky-discovery bug — two router_main processes each miss the other's SPDP). Pass
// autoenable=false to create participants DISABLED; the caller then attaches the
// conditions, starts the AsyncWaitSet, and finally calls enable_all(). enable()
// recursively enables the builtin subscriber/readers and any child entities created
// while the participant was disabled.
//
// autoenable defaults to true for callers that create every discoverable peer AFTER
// aws.start() (the C++ test mains), where no sample can arrive during the window.

#pragma once

#include <dds/dds.hpp>
#include <dds/core/QosProvider.hpp>

#include <map>
#include <memory>
#include <string>
#include <vector>

namespace router {

// The reserved participant_name.role_name sentinel identifying router participants in
// discovery (D74). Detection keys on this ALONE — an app's arbitrary display name can
// never collide; claiming this exact role_name is impersonation (accepted residual).
constexpr const char *kActRouterRoleName = "act.router";

class ParticipantRegistry {
public:
    struct Config {
        std::string name;
        int domain = 0;
        std::string participant_name; // "<node>/<router>" EntityName (D74); may be empty
        // Already-resolved "LIB::Profile" path for this participant's qos: alias (Phase
        // 7a, D60); empty = no named participant QoS (default_participant_qos()). Alias
        // resolution happens in router_main — this class only applies an already-resolved
        // profile path, so it stays alias-agnostic.
        std::string qos_provider_profile;
    };

    // autoenable=false creates participants disabled for the D52 disabled-startup
    // ordering — the caller MUST call enable_all() after aws.start(). provider may be
    // null (no qos_libraries: configured); then every Config's qos_provider_profile must
    // be empty (D60).
    explicit ParticipantRegistry(const std::vector<Config> &configs,
                                 bool autoenable = true,
                                 std::shared_ptr<dds::core::QosProvider> provider = nullptr);
    ~ParticipantRegistry();

    // Returns the participant handle by name (handle copy, shared ownership).
    dds::domain::DomainParticipant get(const std::string &name) const;

    const std::vector<std::string> &names() const { return names_; }

    // Enables every participant and, recursively, its builtin readers plus any child
    // entities created while disabled. Safe to call once after conditions are attached
    // and the AsyncWaitSet is started (D52); a no-op for already-enabled participants.
    void enable_all();

private:
    std::vector<std::string> names_;
    std::map<std::string, dds::domain::DomainParticipant> participants_;
};

} // namespace router
