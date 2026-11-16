#pragma once

#include <dds/dds.hpp>

#include <cstdint>
#include <string>
#include <vector>

namespace router {

struct WanMapping {
    std::string topic;
    std::string type;
    std::string family;
    std::string branch;
    uint32_t discriminator;
    std::string instance_field; // Payload identity need not be a LAN DDS key.
    std::string qos_profile;
};

const std::vector<WanMapping>& wan_mappings();
const WanMapping& wan_mapping(const std::string& topic);
// Physical transport selection does not change the codec's union catalog.
WanMapping wan_transport_mapping(const std::string& topic, bool separate_contacts);
std::string wan_transport_type(const std::string& family);
// Resolve a single, non-default union label from the prepared type registry.
WanMapping wan_full_type_mapping(dds::core::QosProvider types,
        const std::string& topic, const std::string& type, const std::string& family,
        const std::string& branch, const std::string& instance_field);

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
    std::vector<std::string> destination_ids;
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
    // Prepared full-type bindings; labels are verified against the frozen XML.
    WanCodec(dds::core::QosProvider types, std::vector<WanMapping> mappings);
    const WanMapping& mapping(const std::string& topic) const;

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
    std::vector<WanMapping> mappings_;
    void validate_frame_type(const dds::core::xtypes::DynamicData& frame,
                             const WanMapping& mapping) const;
};

} // namespace router
