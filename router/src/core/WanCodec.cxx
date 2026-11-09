#include "core/WanCodec.hpp"

#include <limits>
#include <stdexcept>
#include <utility>

namespace router {
namespace {

using Data = dds::core::xtypes::DynamicData;
using Struct = dds::core::xtypes::StructType;

void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error("WAN codec: " + message);
    }
}

std::string segment(const std::string& value) {
    return std::to_string(value.size()) + ":" + value;
}

std::string instance_id(const WanMapping& mapping, const std::string& key) {
    return std::string(mapping.branch) + "/" + segment(mapping.topic) + "/" + segment(key);
}

std::string read_segment(const std::string& id, size_t& position) {
    size_t length = 0;
    const auto start = position;
    while (position < id.size() && id[position] >= '0' && id[position] <= '9') {
        const size_t digit = static_cast<size_t>(id[position++] - '0');
        require(length <= (std::numeric_limits<size_t>::max() - digit) / 10,
                "instance ID length overflow");
        length = length * 10 + digit;
    }
    require(position > start && position < id.size() && id[position++] == ':',
            "malformed instance ID length");
    require(length <= id.size() - position, "truncated instance ID");
    const auto value = id.substr(position, length);
    position += length;
    return value;
}

void validate_keys(const dds::core::xtypes::DynamicType& type,
                   const std::vector<std::string>& expected) {
    require(type.kind() == dds::core::xtypes::TypeKind::STRUCTURE_TYPE,
            "expected structure for " + type.name());
    const auto& structure = static_cast<const Struct&>(type);
    std::vector<std::string> keys;
    for (const auto& member : structure.members()) {
        if (member.is_key()) {
            keys.push_back(member.name());
        }
    }
    require(keys == expected, "unexpected DDS keys for " + type.name());
}

} // namespace

const std::vector<WanMapping>& wan_mappings() {
    static const std::vector<WanMapping> mappings{
        {"ControlCommand", "control_command", "Event", "command", 1, "source", "wan_event"},
        {"ActTeamAssignment", "TeamAssignment", "Event", "team_assignment", 2,
         "platform_node", "wan_event"},
        {"ActPlatformStatusMode", "PlatformStatusMode", "Event", "status_mode", 3,
         "platform_node", "wan_event"},
        {"PlatformCommandAck", "control_command_ack", "Event", "command_ack", 4,
         "source", "wan_event"},
        {"ContactReport", "contact_report", "Event", "contact_report", 5, "source", "wan_event"},
        {"PlatformInitStatus", "platform_init_status", "Status", "navigation", 1,
         "source", "wan_status"},
        {"PlatformData", "platform_data", "Status", "data", 2, "source", "wan_status"},
        {"PlatformMissionStatus", "platform_mission_status", "Status", "mission", 3,
         "source", "wan_status"},
        {"PlatformWaypointStatus", "platform_waypoint_status", "Status", "waypoint", 4,
         "source", "wan_status"},
        {"PlatformDetailStatus", "platform_detail_status", "Status", "detail", 5,
         "source", "wan_status"},
        {"PlatformDebugStatus", "platform_debug_status", "Status", "debug", 6,
         "source", "wan_status"},
        {"PlatformThrusterStatus", "platform_thruster_status", "Status", "thruster", 7,
         "source", "wan_status"},
        {"PlatformPowerStatus", "platform_power_status", "Status", "power", 8,
         "source", "wan_status"},
        {"ActRouterDiagnostics", "RouterDiagnostics", "Status", "diagnostics", 9,
         "router", "wan_status"},
        {"RouterHealth", "RouterHealth", "Status", "health", 10, "router", "wan_status"}
    };
    return mappings;
}

const WanMapping& wan_mapping(const std::string& topic) {
    for (const auto& mapping : wan_mappings()) {
        if (topic == mapping.topic) {
            return mapping;
        }
    }
    throw std::runtime_error("WAN codec: unmapped topic " + topic);
}

WanCodec::WanCodec(dds::core::QosProvider types) : types_(std::move(types)) {
    for (const auto& mapping : wan_mappings()) {
        const auto type = types_->type(mapping.type);
        validate_keys(type, {mapping.key});
        Data local(type);
        local.value<std::string>(mapping.key);
        Data frame(types_->type(std::string("Wan") + mapping.family));
        validate_keys(frame.type(), {"source_id", "instance_id"});
        auto payload = frame.loan_value("payload");
        payload.get().value<Data>(mapping.branch, local);
        require(payload.get().value<Data>(mapping.branch).type() == type,
                "branch/type mismatch for " + std::string(mapping.topic));
        require(payload.get().discriminator_value() == mapping.discriminator,
                "discriminator mismatch for " + std::string(mapping.topic));
    }
}

void WanCodec::validate_frame_type(const Data& frame, const WanMapping& mapping) const {
    require(frame.type() == types_->type(std::string("Wan") + mapping.family),
            "wrong envelope family/type for " + std::string(mapping.topic));
}

Data WanCodec::key_frame(const std::string& topic, const Data& local,
                        const WanMetadata& metadata) const {
    const auto& mapping = wan_mapping(topic);
    require(local.type() == types_->type(mapping.type), "wrong input type for " + topic);
    require(!metadata.source_id.empty(), "empty source identity");
    Data frame(types_->type(std::string("Wan") + mapping.family));
    frame.value<std::string>("source_id", metadata.source_id);
    frame.value<std::string>("instance_id",
                            instance_id(mapping, local.value<std::string>(mapping.key)));
    return frame;
}

Data WanCodec::pack(const std::string& topic, const Data& local,
                   const WanMetadata& metadata) const {
    require(metadata.team_ids.size() <= 16, "team set exceeds existing 16-entry limit");
    const auto& mapping = wan_mapping(topic);
    auto frame = key_frame(topic, local, metadata);
    frame.value<uint32_t>("schema_version", 1);
    frame.value<std::string>("original_topic", topic);
    frame.value<std::string>("source_role", metadata.source_role);
    frame.value<std::string>("run_id", metadata.run_id);
    frame.value<uint64_t>("session_id", metadata.session_id);
    frame.value<uint64_t>("audit_sequence", metadata.audit_sequence);
    frame.value<int64_t>("sent_at_ns", metadata.sent_at_ns);
    frame.value<uint64_t>("membership_revision", metadata.membership_revision);
    {
        auto audience = frame.loan_value("audience");
        audience.get().value<bool>("control", metadata.control);
        audience.get().value<bool>("global", metadata.global);
        audience.get().value<std::string>("destination", metadata.destination);
        auto teams = audience.get().loan_value("team_ids");
        for (size_t index = 0; index < metadata.team_ids.size(); ++index) {
            teams.get().value<std::string>(static_cast<uint32_t>(index + 1),
                                          metadata.team_ids[index]);
        }
    }
    auto payload = frame.loan_value("payload");
    payload.get().value<Data>(mapping.branch, local);
    return frame;
}

WanDecoded WanCodec::unpack_key(const Data& frame) const {
    require(frame.type() == types_->type("WanEvent") || frame.type() == types_->type("WanStatus"),
            "unsupported envelope type");
    const auto id = frame.value<std::string>("instance_id");
    const auto slash = id.find('/');
    require(slash != std::string::npos, "missing instance variant");
    size_t position = slash + 1;
    const auto topic = read_segment(id, position);
    require(position < id.size() && id[position++] == '/', "missing instance key separator");
    const auto key = read_segment(id, position);
    const auto& mapping = wan_mapping(topic);
    require(position == id.size() && id == instance_id(mapping, key),
            "noncanonical instance ID or variant mismatch");
    validate_frame_type(frame, mapping);
    require(!frame.value<std::string>("source_id").empty(), "empty source identity");
    Data local(types_->type(mapping.type));
    local.value<std::string>(mapping.key, key);
    return {topic, local};
}

WanDecoded WanCodec::unpack(const Data& frame) const {
    auto decoded = unpack_key(frame);
    const auto& mapping = wan_mapping(decoded.topic);
    require(frame.value<uint32_t>("schema_version") == 1, "unsupported schema version");
    require(frame.value<std::string>("original_topic") == decoded.topic,
            "original topic disagrees with instance ID");
    const auto payload = frame.value<Data>("payload");
    require(payload.discriminator_value() == mapping.discriminator,
            "inactive or unknown branch for " + decoded.topic);
    const auto local = payload.value<Data>(mapping.branch);
    require(local.type() == decoded.data.type(), "decoded type mismatch");
    require(local.value<std::string>(mapping.key)
                == decoded.data.value<std::string>(mapping.key),
            "payload key disagrees with envelope key");
    decoded.data = local;
    return decoded;
}

} // namespace router
