// ParticipantRegistry.hpp — creates DDS participants from config (Phase 2.5).
//
// Creates UDPv4-only participants with the act.router user_data tag (D15).
// Phase 2.5 creates participants enabled. The builtin readers are KEEP_LAST(1)
// current-state caches, so the DiscoveryDispatcher can attach after participant
// construction and still observe live discovery state.

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

    // Creates participants enabled.
    explicit ParticipantRegistry(const std::vector<Config> &configs);
    ~ParticipantRegistry();

    // Returns the participant handle by name (handle copy, shared ownership).
    dds::domain::DomainParticipant get(const std::string &name) const;

    const std::vector<std::string> &names() const { return names_; }

private:
    std::vector<std::string> names_;
    std::map<std::string, dds::domain::DomainParticipant> participants_;
};

} // namespace router
