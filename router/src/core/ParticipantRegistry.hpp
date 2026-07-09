// ParticipantRegistry.hpp — creates DDS participants from config (Phase 2.5).
//
// Creates UDPv4-only participants with the act.router user_data tag (D15).
// Participant startup is intentionally sequential: registry creates participants
// disabled so the DiscoveryDispatcher can attach ReadConditions before enabling,
// ensuring no discovery events are lost on startup (D12).

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

    // Creates participants DISABLED. Call enable_all() after conditions are attached.
    explicit ParticipantRegistry(const std::vector<Config> &configs);
    ~ParticipantRegistry();

    // Returns the participant handle by name (handle copy, shared ownership).
    dds::domain::DomainParticipant get(const std::string &name) const;

    const std::vector<std::string> &names() const { return names_; }

    // Enable all participants. Call this after DiscoveryDispatcher has attached
    // its ReadConditions so no discovery events are lost.
    void enable_all();

private:
    std::vector<std::string> names_;
    std::map<std::string, dds::domain::DomainParticipant> participants_;
};

} // namespace router
