// ParticipantRegistry.hpp — creates DDS participants from config (Phase 2.5).
//
// Creates UDPv4-only participants with the act.router user_data tag (D15).
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

#include <map>
#include <string>
#include <vector>

namespace router {

class ParticipantRegistry {
public:
    struct Config {
        std::string name;
        int domain = 0;
        std::string user_data_tag; // "act.router=<node>/<router>" (D15); may be empty
    };

    // autoenable=false creates participants disabled for the D52 disabled-startup
    // ordering — the caller MUST call enable_all() after aws.start().
    explicit ParticipantRegistry(const std::vector<Config> &configs,
                                 bool autoenable = true);
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
