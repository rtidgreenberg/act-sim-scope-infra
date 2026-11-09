#pragma once

#include <dds/dds.hpp>

#include <cstdint>
#include <string>
#include <vector>

namespace router {

struct WanMapping {
    const char* topic;
    const char* type;
    const char* family;
    const char* branch;
    uint32_t discriminator;
    const char* key;
    const char* qos_profile;
};

const std::vector<WanMapping>& wan_mappings();
const WanMapping& wan_mapping(const std::string& topic);

struct WanMetadata {
    std::string source_id;
    std::string source_role;
    std::string run_id;
    uint64_t session_id = 0;
    uint64_t audit_sequence = 0;
    int64_t sent_at_ns = 0;
    uint64_t membership_revision = 0;
    bool control = false;
    bool global = false;
    std::string destination;
    std::vector<std::string> team_ids;
};

struct WanDecoded {
    std::string topic;
    dds::core::xtypes::DynamicData data;
};

// Entity-free conversion. The caller owns QoS, audiences, logging and lifecycle.
// Unsupported schemas/mappings and inconsistent keys throw std::runtime_error.
class WanCodec {
public:
    explicit WanCodec(dds::core::QosProvider types);

    dds::core::xtypes::DynamicData pack(
            const std::string& topic,
            const dds::core::xtypes::DynamicData& local,
            const WanMetadata& metadata) const;
    dds::core::xtypes::DynamicData key_frame(
            const std::string& topic,
            const dds::core::xtypes::DynamicData& local_key,
            const WanMetadata& metadata) const;
    WanDecoded unpack(const dds::core::xtypes::DynamicData& frame) const;
    WanDecoded unpack_key(const dds::core::xtypes::DynamicData& frame) const;

private:
    dds::core::QosProvider types_;
    void validate_frame_type(const dds::core::xtypes::DynamicData& frame,
                             const WanMapping& mapping) const;
};

} // namespace router
