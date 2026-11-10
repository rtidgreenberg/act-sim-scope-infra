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
    for (const auto& mapping : router::wan_mappings()) {
        auto sample_metadata = metadata;
        const bool contact_report = std::string(mapping.topic) == "ContactReport";
        if (contact_report) {
            sample_metadata.source_id = std::string(100, 'n');
        }
        const auto check_decoded = [&](const Data& actual, const Data& sent) {
            auto expected = sent;
            if (contact_report) {
                expected.value<std::string>("source", sample_metadata.source_id);
            }
            CHECK(actual == expected);
        };
        Data local(types->type(mapping.type));
        fill(local, 20);
        const auto bound = std::string(mapping.instance_field) == "contact_id" ? 32
                : std::string(mapping.instance_field) == "source" ? 100 : 255;
        local.value<std::string>(mapping.instance_field, std::string(bound, 'k'));
        const auto frame = codec.pack(mapping.topic, local, sample_metadata);
        const auto decoded = codec.unpack(frame);
        CHECK(decoded.topic == mapping.topic);
        check_decoded(decoded.data, local);
        CHECK(frame.value<Data>("payload").value<Data>(mapping.branch) == local);
        CHECK(frame.value<uint32_t>("schema_version") == 2);
        CHECK(frame.value<std::string>("original_topic") == mapping.topic);
        CHECK(frame.value<std::string>("source_id") == sample_metadata.source_id);
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
        CHECK(id == local.value<std::string>(mapping.instance_field));
        auto changed_metadata = sample_metadata;
        changed_metadata.session_id++;
        changed_metadata.audit_sequence++;
        changed_metadata.membership_revision++;
        changed_metadata.team_ids.clear();
        changed_metadata.control = false;
        changed_metadata.global = false;
        changed_metadata.destination.clear();
        Data changed(types->type(mapping.type));
        fill(changed, 40);
        changed.value<std::string>(mapping.instance_field,
                                   local.value<std::string>(mapping.instance_field));
        const auto changed_frame = codec.pack(mapping.topic, changed, changed_metadata);
        CHECK(changed_frame.value<std::string>("instance_id") == id);
        check_decoded(codec.unpack(changed_frame).data, changed);
        CHECK(changed_frame.value<Data>("audience").value<Data>("team_ids").member_count() == 0);
        auto key_frame = codec.key_frame(mapping.topic, local, sample_metadata);
        CHECK(key_frame.value<uint32_t>("schema_version") == 0);
        CHECK(key_frame.value<std::string>("original_topic") == mapping.topic);
        const auto recovered = codec.unpack_key(key_frame);
        CHECK(recovered.topic == mapping.topic);
        CHECK(recovered.data.value<std::string>(mapping.instance_field)
                == local.value<std::string>(mapping.instance_field));
        if (contact_report) {
            CHECK(recovered.data.value<std::string>("source") == sample_metadata.source_id);
        }
        auto corrupt = frame;
        corrupt.value<uint32_t>("schema_version", 1);
        rejected([&] { codec.unpack(corrupt); });
        corrupt = frame;
        corrupt.value<std::string>("original_topic", "RemoteArbitraryTopic");
        rejected([&] { codec.unpack(corrupt); });
        corrupt = frame;
        corrupt.value<std::string>("instance_id", "wrong-instance");
        rejected([&] { codec.unpack(corrupt); });
        corrupt = frame;
        {
            auto payload = corrupt.loan_value("payload");
            auto branch = payload.get().loan_value(mapping.branch);
            branch.get().value<std::string>(mapping.instance_field, "wrong-instance");
        }
        rejected([&] { codec.unpack(corrupt); });
        local.value<std::string>(mapping.instance_field, "");
        check_decoded(codec.unpack(codec.pack(mapping.topic, local, sample_metadata)).data, local);
        local.value<std::string>(mapping.instance_field, "7:/key/:0:");
        check_decoded(codec.unpack(codec.pack(mapping.topic, local, sample_metadata)).data, local);
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
    for (const auto& topic : {"", "Unknown", "ControlCommand"}) {
        auto corrupt = frame;
        corrupt.value<std::string>("original_topic", topic);
        rejected([&] { codec.unpack_key(corrupt); });
    }
    Data contact(types->type("contact_report"));
    contact.value<std::string>("source", "untrusted-producer");
    contact.value<std::string>("contact_id", "contact-101");
    auto contact_metadata = metadata;
    contact_metadata.source_id = "Boat30";
    const auto first_contact = codec.pack("ContactReport", contact, contact_metadata);
    CHECK(first_contact.value<std::string>("instance_id") == "contact-101");
    auto received_contact = contact;
    received_contact.value<std::string>("source", "Boat30");
    CHECK(codec.unpack(first_contact).data == received_contact);
    CHECK(contact.value<std::string>("source") == "untrusted-producer");
    auto other_origin = first_contact;
    other_origin.value<std::string>("source_id", "Boat31");
    CHECK(codec.unpack(other_origin).data.value<std::string>("source") == "Boat31");
    CHECK(codec.unpack_key(other_origin).data.value<std::string>("source") == "Boat31");
    other_origin.value<std::string>("source_id", std::string(101, 'n'));
    rejected([&] { codec.unpack(other_origin); });
    rejected([&] { codec.unpack_key(other_origin); });
    contact.value<std::string>("contact_id", "contact-102");
    const auto second_contact = codec.pack("ContactReport", contact, contact_metadata);
    CHECK(second_contact.value<std::string>("instance_id") == "contact-102");
    CHECK(first_contact.value<std::string>("source_id")
            == second_contact.value<std::string>("source_id"));
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
