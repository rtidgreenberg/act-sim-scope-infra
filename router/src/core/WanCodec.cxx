#include "core/WanCodec.hpp"

#include <stdexcept>
#include <set>
#include <utility>

namespace router {
namespace {

using Data = dds::core::xtypes::DynamicData;
using Struct = dds::core::xtypes::StructType;
using Kind = dds::core::xtypes::TypeKind;

void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error("WAN codec: " + message);
    }
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

void validate_string_capacity(const dds::core::xtypes::DynamicType& type,
                              uint32_t minimum, const std::string& field) {
    require(type.kind() == Kind::STRING_TYPE, "expected string field " + field);
    require(static_cast<const dds::core::xtypes::StringType&>(type).bounds() >= minimum,
            field + " capacity must be at least " + std::to_string(minimum));
}

void validate_envelope(const dds::core::xtypes::DynamicType& type) {
    validate_keys(type, {"source_id", "original_topic", "instance_id"});
    const auto& structure = static_cast<const Struct&>(type);
    for (const auto& name : {"source_id", "original_topic", "instance_id", "source_role", "run_id"}) {
        validate_string_capacity(structure.member(name).type(),
                std::string(name) == "instance_id" ? 512 : 255, type.name() + "." + name);
    }
    for (const auto& name : {"session_id", "audit_sequence", "membership_revision"}) {
        require(structure.member(name).type().kind() == Kind::UINT_64_TYPE,
                "expected uint64 envelope field " + std::string(name));
    }
    require(structure.member("schema_version").type().kind() == Kind::UINT_32_TYPE,
            "expected uint32 schema_version");
    require(structure.member("sent_at_ns").type().kind() == Kind::INT_64_TYPE,
            "expected int64 sent_at_ns");
    const auto& audience = structure.member("audience").type();
    require(audience.kind() == Kind::STRUCTURE_TYPE, "expected audience structure");
    const auto& fields = static_cast<const Struct&>(audience);
    require(fields.member("control").type().kind() == Kind::BOOLEAN_TYPE &&
            fields.member("global").type().kind() == Kind::BOOLEAN_TYPE,
            "expected boolean audience flags");
    const auto& destinations = fields.member("destination_ids").type();
    require(destinations.kind() == Kind::SEQUENCE_TYPE &&
            static_cast<const dds::core::xtypes::SequenceType&>(destinations)
                .content_type().kind() == Kind::STRING_TYPE,
            "expected string destination sequence");
    const auto& sequence = static_cast<const dds::core::xtypes::SequenceType&>(destinations);
    require(sequence.bounds() >= 16, "audience.destination_ids capacity must be at least 16");
    validate_string_capacity(sequence.content_type(), 255, "audience.destination_ids element");
}

} // namespace

const std::vector<WanMapping>& wan_mappings() {
    static const std::vector<WanMapping> mappings{
        {"Mission", "control_command", "Event", "command", 1, "source", "wan_event"},
        {"Team", "TeamAssignment", "Event", "team_assignment", 2,
         "platform_node", "wan_event"},
        {"ActPlatformStatusMode", "PlatformStatusMode", "Event", "status_mode", 3,
         "platform_node", "wan_event"},
        {"PlatformCommandAck", "control_command_ack", "Event", "command_ack", 4,
         "source", "wan_event"},
        {"ContactReport", "contact_report", "Event", "contact_report", 5, "contact_id", "wan_event"},
        {"RouterConfig", "RouterConfig", "Event", "router_config", 6, "router", "wan_event"},
        {"PlatformStatus", "platform_init_status", "Status", "navigation", 1,
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

WanMapping wan_transport_mapping(const std::string& topic, bool separate_contacts) {
    auto mapping = wan_mapping(topic);
    if (separate_contacts && topic == "ContactReport") {
        mapping.family = "State";
        mapping.qos_profile = "wan_contacts";
    } else if (separate_contacts && std::string(mapping.family) == "Status") {
        mapping.family = "Telemetry";
    }
    return mapping;
}

std::string wan_transport_type(const std::string& family) {
    if (family == "Event" || family == "State") return "WanEvent";
    if (family == "Status" || family == "Telemetry") return "WanStatus";
    throw std::runtime_error("WAN codec: unknown transport family " + family);
}

const WanMapping& wan_mapping(const std::string& topic) {
    for (const auto& mapping : wan_mappings()) {
        if (topic == mapping.topic) {
            return mapping;
        }
    }
    throw std::runtime_error("WAN codec: unmapped topic " + topic);
}

WanMapping wan_full_type_mapping(dds::core::QosProvider types,
        const std::string& topic, const std::string& type, const std::string& family,
        const std::string& branch, const std::string& instance_field) {
    require(family == "Event" || family == "Status", "unsupported codec family " + family);
    const auto envelope = types->type("Wan" + family);
    require(envelope.kind() == dds::core::xtypes::TypeKind::STRUCTURE_TYPE,
            "expected envelope structure");
    const auto& payload = static_cast<const Struct&>(envelope).member("payload").type();
    require(payload.kind() == dds::core::xtypes::TypeKind::UNION_TYPE,
            "expected payload union");
    const auto& member = static_cast<const dds::core::xtypes::UnionType&>(payload).member(branch);
    require(member.type() == types->type(type), "branch/type mismatch for " + topic);
    const auto labels = member.labels();
    require(labels.size() == 1 && labels.front() >= 0 &&
            labels.front() != dds::core::xtypes::UnionMember::DEFAULT_LABEL,
            "branch requires one nonnegative, non-default label: " + branch);
    std::set<int32_t> ids;
    std::set<int32_t> discriminators;
    for (const auto& item : static_cast<const dds::core::xtypes::UnionType&>(payload).members()) {
        require(!item.has_id() || ids.insert(item.get_id()).second,
                "duplicate union member ID in " + payload.name());
        for (const auto label : item.labels()) {
            require(discriminators.insert(label).second,
                    "duplicate union label in " + payload.name());
        }
    }
    return {topic, type, family, branch, static_cast<uint32_t>(labels.front()),
            instance_field, family == "Event" ? "wan_event" : "wan_status"};
}

WanCodec::WanCodec(dds::core::QosProvider types)
    : WanCodec(std::move(types), wan_mappings()) {}

WanCodec::WanCodec(dds::core::QosProvider types, std::vector<WanMapping> mappings)
    : types_(std::move(types)), mappings_(std::move(mappings)) {
    require(!mappings_.empty(), "empty binding registry");
    std::set<std::string> topics;
    std::set<std::pair<std::string, std::string>> branches;
    for (const auto& mapping : mappings_) {
        require(!mapping.topic.empty() && !mapping.type.empty() &&
                !mapping.branch.empty() && !mapping.instance_field.empty(),
                "incomplete binding");
        require(mapping.topic.size() <= 255, "topic name exceeds 255-byte envelope capacity");
        require(mapping.family == "Event" || mapping.family == "Status",
                "unsupported codec family for " + mapping.topic);
        require(topics.insert(mapping.topic).second, "duplicate topic " + mapping.topic);
        require(branches.emplace(mapping.family, mapping.branch).second,
                "duplicate union branch " + mapping.branch);
        const auto prepared = wan_full_type_mapping(types_, mapping.topic, mapping.type,
                mapping.family, mapping.branch, mapping.instance_field);
        require(prepared.discriminator == mapping.discriminator,
                "discriminator mismatch for " + mapping.topic);
        for (const auto& builtin : wan_mappings()) {
            if (mapping.topic != builtin.topic) continue;
            require(mapping.type == builtin.type && mapping.family == builtin.family &&
                    mapping.branch == builtin.branch &&
                    mapping.discriminator == builtin.discriminator &&
                    mapping.instance_field == builtin.instance_field,
                    "binding disagrees with fixed ACT catalog for " + mapping.topic);
        }
        const auto type = types_->type(mapping.type);
        validate_keys(type, std::string(mapping.topic) == "ContactReport"
                ? std::vector<std::string>{"source", "contact_id"}
                : std::vector<std::string>{mapping.instance_field});
        const auto& key_type = static_cast<const Struct&>(type).member(mapping.instance_field).type();
        require(key_type.kind() == Kind::STRING_TYPE,
                "expected string instance key for " + mapping.topic);
        require(static_cast<const dds::core::xtypes::StringType&>(key_type).bounds() <= 512,
                "instance key capacity exceeds 512-byte envelope limit for " + mapping.topic);
        Data local(type);
        local.value<std::string>(mapping.instance_field);
        Data frame(types_->type(std::string("Wan") + mapping.family));
        validate_envelope(frame.type());
        auto payload = frame.loan_value("payload");
        payload.get().value<Data>(mapping.branch, local);
        require(payload.get().value<Data>(mapping.branch).type() == type,
                "branch/type mismatch for " + std::string(mapping.topic));
        require(payload.get().discriminator_value() == mapping.discriminator,
                "discriminator mismatch for " + std::string(mapping.topic));
    }
}

const WanMapping& WanCodec::mapping(const std::string& topic) const {
    for (const auto& mapping : mappings_) {
        if (mapping.topic == topic) return mapping;
    }
    throw std::runtime_error("WAN codec: unmapped topic " + topic);
}

void WanCodec::validate_frame_type(const Data& frame, const WanMapping& mapping) const {
    require(frame.type() == types_->type(std::string("Wan") + mapping.family),
            "wrong envelope family/type for " + std::string(mapping.topic));
}

Data WanCodec::key_frame(const std::string& topic, const Data& local,
                        const WanMetadata& metadata) const {
    const auto& mapping = this->mapping(topic);
    require(local.type() == types_->type(mapping.type), "wrong input type for " + topic);
    require(!metadata.source_id.empty(), "empty source identity");
    Data frame(types_->type(std::string("Wan") + mapping.family));
    frame.value<std::string>("source_id", metadata.source_id);
    frame.value<std::string>("original_topic", topic);
    frame.value<std::string>("instance_id",
                            local.value<std::string>(mapping.instance_field));
    return frame;
}

Data WanCodec::pack(const std::string& topic, const Data& local,
                   const WanMetadata& metadata) const {
    require(metadata.destination_ids.size() <= 16, "destination set exceeds 16-entry limit");
    const auto& mapping = this->mapping(topic);
    auto frame = key_frame(topic, local, metadata);
    frame.value<uint32_t>("schema_version", 3);
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
        auto destinations = audience.get().loan_value("destination_ids");
        for (size_t index = 0; index < metadata.destination_ids.size(); ++index) {
            destinations.get().value<std::string>(static_cast<uint32_t>(index + 1),
                                                 metadata.destination_ids[index]);
        }
    }
    auto payload = frame.loan_value("payload");
    payload.get().value<Data>(mapping.branch, local);
    return frame;
}

WanDecoded WanCodec::unpack_key(const Data& frame) const {
    require(frame.type() == types_->type("WanEvent") || frame.type() == types_->type("WanStatus"),
            "unsupported envelope type");
    const auto topic = frame.value<std::string>("original_topic");
    const auto& mapping = this->mapping(topic);
    validate_frame_type(frame, mapping);
    require(!frame.value<std::string>("source_id").empty(), "empty source identity");
    Data local(types_->type(mapping.type));
    local.value<std::string>(mapping.instance_field, frame.value<std::string>("instance_id"));
    if (topic == "ContactReport") {
        const auto source = frame.value<std::string>("source_id");
        require(source.size() <= 100, "ContactReport source identity exceeds LAN source bound");
        local.value<std::string>("source", source);
    }
    return {topic, local};
}

WanDecoded WanCodec::unpack(const Data& frame) const {
    auto decoded = unpack_key(frame);
    const auto& mapping = this->mapping(decoded.topic);
    require(frame.value<uint32_t>("schema_version") == 3, "unsupported schema version");
    const auto payload = frame.value<Data>("payload");
    require(payload.discriminator_value() == mapping.discriminator,
            "inactive or unknown branch for " + decoded.topic);
    const auto local = payload.value<Data>(mapping.branch);
    require(local.type() == decoded.data.type(), "decoded type mismatch");
    require(local.value<std::string>(mapping.instance_field)
                == decoded.data.value<std::string>(mapping.instance_field),
            "payload instance ID disagrees with envelope key");
    decoded.data = local;
    if (decoded.topic == "ContactReport") {
        decoded.data.value<std::string>("source", frame.value<std::string>("source_id"));
    }
    return decoded;
}

} // namespace router
