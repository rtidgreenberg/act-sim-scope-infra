#include "core/WanCodec.hpp"
#include "ActTypes.hpp"

#include <cstdio>
#include <functional>
#include <stdexcept>

using Data = dds::core::xtypes::DynamicData;
using Type = dds::core::xtypes::DynamicType;
using Kind = dds::core::xtypes::TypeKind;

static int failures = 0;
#define CHECK(condition) do { \
    if (!(condition)) { \
        std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #condition); \
        ++failures; \
    } \
} while (0)

static void rejected(const std::function<void()>& operation) {
    bool threw = false;
    try {
        operation();
    } catch (const std::runtime_error&) {
        threw = true;
    }
    CHECK(threw);
}

template<class Index>
static void fill_member(Data& data, const Index& index, const Type& type, uint32_t seed);

static void fill(Data& data, uint32_t seed) {
    if (data.type().kind() == Kind::STRUCTURE_TYPE) {
        const auto& type = static_cast<const dds::core::xtypes::StructType&>(data.type());
        uint32_t index = 0;
        for (const auto& member : type.members()) {
            fill_member(data, member.name(), member.type(), seed + index++);
        }
    } else if (data.type().kind() == Kind::SEQUENCE_TYPE) {
        const auto& type = static_cast<const dds::core::xtypes::SequenceType&>(data.type());
        for (uint32_t index = 1; index <= 2; ++index) {
            fill_member(data, index, type.content_type(), seed + index);
        }
    } else {
        throw std::runtime_error("Unexpected fixture aggregate");
    }
}

template<class Index>
static void fill_member(Data& data, const Index& index, const Type& type, uint32_t seed) {
    switch (type.kind().underlying()) {
    case Kind::STRING_TYPE: data.value<std::string>(index, "value/" + std::to_string(seed)); break;
    case Kind::BOOLEAN_TYPE: data.value<bool>(index, true); break;
    case Kind::UINT_8_TYPE:
    case Kind::OCTET_TYPE: data.value<uint8_t>(index, static_cast<uint8_t>(seed)); break;
    case Kind::UINT_16_TYPE: data.value<uint16_t>(index, static_cast<uint16_t>(seed)); break;
    case Kind::INT_32_TYPE: data.value<int32_t>(index, -static_cast<int32_t>(seed)); break;
    case Kind::UINT_32_TYPE: data.value<uint32_t>(index, seed); break;
    case Kind::INT_64_TYPE: data.value<int64_t>(index, -9876543210123LL - seed); break;
    case Kind::UINT_64_TYPE: data.value<uint64_t>(index, (uint64_t(1) << 60) + seed); break;
    case Kind::FLOAT_32_TYPE: data.value<float>(index, 12.5f + seed); break;
    case Kind::ENUMERATION_TYPE: data.value<int32_t>(index, 1); break;
    case Kind::STRUCTURE_TYPE:
    case Kind::SEQUENCE_TYPE: {
        auto child = data.loan_value(index);
        fill(child.get(), seed);
        break;
    }
    default: throw std::runtime_error("Unhandled fixture member " + type.name());
    }
}

int main(int argc, char** argv) {
    if (argc != 2) {
        std::fprintf(stderr, "Usage: test_wan_codec ActTypes.xml\n");
        return 2;
    }
    dds::core::QosProvider types(argv[1]);
    router::WanCodec codec(types);
    router::WanMetadata metadata;
    metadata.source_id = std::string(255, 'n');
    metadata.source_role = "platform";
    metadata.run_id = "run-42";
    metadata.session_id = (uint64_t(1) << 60) + 1;
    metadata.audit_sequence = (uint64_t(1) << 40) + 2;
    metadata.sent_at_ns = 9876543210123LL;
    metadata.membership_revision = 17;
    metadata.control = true;
    metadata.global = true;
    metadata.destination = "Boat31";
    for (uint32_t index = 0; index < 16; ++index) {
        metadata.team_ids.push_back("TEAM_" + std::to_string(index));
    }
    CHECK(router::wan_mappings().size() == 15);
    std::vector<std::string> ids;
    for (const auto& mapping : router::wan_mappings()) {
        Data local(types->type(mapping.type));
        fill(local, 20);
        local.value<std::string>(mapping.key,
                                std::string(mapping.key == std::string("source") ? 100 : 255, 'k'));
        const auto frame = codec.pack(mapping.topic, local, metadata);
        const auto decoded = codec.unpack(frame);
        CHECK(decoded.topic == mapping.topic);
        CHECK(decoded.data == local);
        CHECK(frame.value<uint32_t>("schema_version") == 1);
        CHECK(frame.value<std::string>("source_id") == metadata.source_id);
        CHECK(frame.value<std::string>("source_role") == metadata.source_role);
        CHECK(frame.value<std::string>("run_id") == metadata.run_id);
        CHECK(frame.value<uint64_t>("session_id") == metadata.session_id);
        CHECK(frame.value<uint64_t>("audit_sequence") == metadata.audit_sequence);
        CHECK(frame.value<int64_t>("sent_at_ns") == metadata.sent_at_ns);
        CHECK(frame.value<uint64_t>("membership_revision") == metadata.membership_revision);
        const auto audience = frame.value<Data>("audience");
        CHECK(audience.value<bool>("control"));
        CHECK(audience.value<bool>("global"));
        CHECK(audience.value<std::string>("destination") == metadata.destination);
        const auto teams = audience.value<Data>("team_ids");
        CHECK(teams.member_count() == 16);
        CHECK(teams.value<std::string>(16) == "TEAM_15");
        const auto id = frame.value<std::string>("instance_id");
        for (const auto& previous : ids) {
            CHECK(previous != id);
        }
        ids.push_back(id);
        auto changed_metadata = metadata;
        changed_metadata.session_id++;
        changed_metadata.audit_sequence++;
        changed_metadata.membership_revision++;
        changed_metadata.team_ids.clear();
        changed_metadata.control = false;
        changed_metadata.global = false;
        changed_metadata.destination.clear();
        Data changed(types->type(mapping.type));
        fill(changed, 40);
        changed.value<std::string>(mapping.key, local.value<std::string>(mapping.key));
        const auto changed_frame = codec.pack(mapping.topic, changed, changed_metadata);
        CHECK(changed_frame.value<std::string>("instance_id") == id);
        CHECK(codec.unpack(changed_frame).data == changed);
        CHECK(changed_frame.value<Data>("audience").value<Data>("team_ids").member_count() == 0);
        auto key_frame = codec.key_frame(mapping.topic, local, metadata);
        CHECK(key_frame.value<uint32_t>("schema_version") == 0);
        CHECK(key_frame.value<std::string>("original_topic").empty());
        const auto recovered = codec.unpack_key(key_frame);
        CHECK(recovered.topic == mapping.topic);
        CHECK(recovered.data.value<std::string>(mapping.key)
                == local.value<std::string>(mapping.key));
        auto corrupt = frame;
        corrupt.value<uint32_t>("schema_version", 2);
        rejected([&] { codec.unpack(corrupt); });
        corrupt = frame;
        corrupt.value<std::string>("original_topic", "RemoteArbitraryTopic");
        rejected([&] { codec.unpack(corrupt); });
        corrupt = frame;
        corrupt.value<std::string>("instance_id", id + "/trailing");
        rejected([&] { codec.unpack_key(corrupt); });
        corrupt = frame;
        {
            auto payload = corrupt.loan_value("payload");
            auto branch = payload.get().loan_value(mapping.branch);
            branch.get().value<std::string>(mapping.key, "wrong-key");
        }
        rejected([&] { codec.unpack(corrupt); });
        local.value<std::string>(mapping.key, "");
        CHECK(codec.unpack(codec.pack(mapping.topic, local, metadata)).data == local);
        local.value<std::string>(mapping.key, "7:/key/:0:");
        CHECK(codec.unpack(codec.pack(mapping.topic, local, metadata)).data == local);
    }
    Data nav(types->type("platform_init_status"));
    rejected([&] { codec.unpack_key(nav); });
    rejected([&] { codec.pack("Unknown", nav, metadata); });
    rejected([&] { codec.pack("PlatformPowerStatus", nav, metadata); });
    auto invalid_metadata = metadata;
    invalid_metadata.team_ids.push_back("TEAM_17");
    rejected([&] { codec.pack("PlatformInitStatus", nav, invalid_metadata); });
    invalid_metadata = metadata;
    invalid_metadata.source_id.clear();
    rejected([&] { codec.key_frame("PlatformInitStatus", nav, invalid_metadata); });
    auto frame = codec.pack("PlatformInitStatus", nav, metadata);
    for (const auto& id : {"", "navigation/999999999999999999999999999:",
                          "navigation/999:short", "wrong/18:PlatformInitStatus/0:",
                          "navigation/018:PlatformInitStatus/0:"}) {
        auto corrupt = frame;
        corrupt.value<std::string>("instance_id", id);
        rejected([&] { codec.unpack_key(corrupt); });
    }
    {
        auto payload = frame.loan_value("payload");
        payload.get().value<Data>("health", Data(types->type("RouterHealth")));
    }
    rejected([&] { codec.unpack(frame); });
    Data command(types->type("control_command"));
    command.set_values<uint8_t>("payload", std::vector<uint8_t>(8192, 255));
    CHECK(codec.unpack(codec.pack("ControlCommand", command, metadata)).data == command);
    // Compiled and XML-loaded envelope types must agree before either lane is wired.
    CHECK(types->type("WanEvent") == rti::topic::dynamic_type<WanEvent>::get());
    CHECK(types->type("WanStatus") == rti::topic::dynamic_type<WanStatus>::get());
    std::printf("WAN codec: 15 complete payloads, metadata, keys and rejections; %d failures\n",
                failures);
    return failures ? 1 : 0;
}
