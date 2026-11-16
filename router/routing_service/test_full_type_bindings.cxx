#include "GatewayModel.hpp"

#include <cstdio>
#include <functional>

using Data = dds::core::xtypes::DynamicData;
using Properties = std::map<std::string, std::string>;

static void check(bool result) {
    if (!result) throw std::runtime_error("full-type binding check failed");
}

static void rejected(const std::function<void()>& operation) {
    try { operation(); }
    catch (const std::exception&) { return; }
    throw std::runtime_error("invalid full-type binding accepted");
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    try {
        Properties properties{
            {"act.version", "1"}, {"act.types_uri", argv[1]},
            {"act.node", "DeviceGateway"}, {"act.role", "platform"}, {"act.router", "rs"},
            {"act.receive_policy", "static"}, {"act.wan_contract", "three_topic_union"},
            {"act.monitoring", "false"}, {"act.admin_participant", "lan"},
            {"act.lan_participant", "lan"}, {"act.lan_domain", "103"},
            {"act.wan_participant", "wan"}, {"act.wan_domain", "102"}};
        const auto add = [&](const std::string& id, const std::string& topic,
                             const std::string& type, const std::string& branch,
                             const std::string& key, const std::string& channel) {
            const auto prefix = "act.binding." + id + ".";
            for (const auto& entry : Properties{
                    {"topic_name", topic}, {"type", type}, {"branch", branch}, {"instance_id", key},
                    {"channel", channel}, {"tx.port", "input_" + id}, {"tx.recipients", "global"},
                    {"rx.port", "output_" + id}, {"rx.recipients", "global"}}) {
                properties[prefix + entry.first] = entry.second;
            }
        };
        add("command", "DeviceCommands", "DeviceCommand", "device_command", "request_id", "event_high");
        add("status", "DeviceUpdates", "DeviceStatus", "device_status", "device_id", "periodic_normal");
        act_rs::GatewayModel model(properties);
        dds::core::QosProvider types(argv[1]);
        router::WanCodec codec(types, model.codec_mappings());
        check(codec.mapping("DeviceCommands").discriminator == 41);
        check(codec.mapping("DeviceUpdates").discriminator == 73);
        check(model.transport_mapping("DeviceUpdates").family == "Periodic");
        check(model.publication_priority("DeviceCommands") == 600);
        check(model.receive_filter("Event").expression.find("DeviceCommands") != std::string::npos);
        check(model.receive_filter("Periodic").expression.find("DeviceUpdates") != std::string::npos);
        check(model.receive_filter("State").expression.find("original_topic = ''") != std::string::npos);
        check(act_rs::GatewayModel(model.properties()).properties() == model.properties());
        Data command(types->type("DeviceCommand"));
        command.value<std::string>("request_id", "request-9");
        command.value<int32_t>("opcode", -123);
        {
            auto bytes = command.loan_value("bytes");
            bytes.get().value<uint8_t>(1, 0);
            bytes.get().value<uint8_t>(2, 255);
        }
        Data status(types->type("DeviceStatus"));
        status.value<std::string>("device_id", "sensor-8");
        status.value<uint64_t>("serial", (uint64_t(1) << 60) + 13);
        {
            auto readings = status.loan_value("readings");
            auto reading = readings.get().loan_value(1);
            reading.get().value<std::string>("label", "voltage");
            auto values = reading.get().loan_value("values");
            values.get().value<double>(1, -0.125);
            values.get().value<double>(2, 123456.75);
        }
        for (const auto& entry : std::vector<std::pair<std::string, Data>>{
                {"DeviceCommands", command}, {"DeviceUpdates", status}}) {
            const auto metadata = model.metadata(entry.first, entry.second);
            check(metadata.source_id == "DeviceGateway" && metadata.global);
            const auto frame = codec.pack(entry.first, entry.second, metadata);
            const auto decoded = codec.unpack(frame);
            check(decoded.topic == entry.first && decoded.data == entry.second);
            auto key = codec.unpack_key(codec.key_frame(entry.first, entry.second, metadata));
            const auto field = codec.mapping(entry.first).instance_field;
            check(key.data.value<std::string>(field) == entry.second.value<std::string>(field));
            auto wrong = frame;
            wrong.value<std::string>("instance_id", "wrong");
            rejected([&] { codec.unpack(wrong); });
            wrong = frame;
            wrong.value<uint32_t>("schema_version", 99);
            rejected([&] { codec.unpack(wrong); });
            wrong = frame;
            wrong.value<std::string>("source_id", "");
            rejected([&] { codec.unpack(wrong); });
            wrong = frame;
            {
                auto payload = wrong.loan_value("payload");
                auto value = payload.get().loan_value(codec.mapping(entry.first).branch);
                value.get().value<std::string>(field, "wrong-payload-key");
            }
            rejected([&] { codec.unpack(wrong); });
        }
        {
            auto next = properties;
            next["act.binding.command.topic_name"] = "devices.commands";
            // Deliberately reuse an ACT topic as an unrelated local binding ID.
            for (const auto& entry : properties) {
                const std::string prefix = "act.binding.status.";
                if (entry.first.compare(0, prefix.size(), prefix) != 0) continue;
                next.erase(entry.first);
                next["act.binding.Mission." + entry.first.substr(prefix.size())] = entry.second;
            }
            act_rs::GatewayModel dotted(next);
            const auto saved = dotted.properties();
            check(saved.at("act.binding.command.topic_name") == "devices.commands");
            check(saved.at("act.binding.Mission.topic_name") == "DeviceUpdates");
            check(act_rs::GatewayModel(saved).properties() == saved);
        }
        {
            auto next = properties;
            const std::string topic(255, 't');
            next["act.node"] = std::string(255, 'n');
            next["act.binding.command.topic_name"] = topic;
            act_rs::GatewayModel boundary(next);
            router::WanCodec boundary_codec(types, boundary.codec_mappings());
            auto sample = command;
            const auto& structure = static_cast<const dds::core::xtypes::StructType&>(sample.type());
            const auto key_bound = static_cast<const dds::core::xtypes::StringType&>(
                structure.member("request_id").type()).bounds();
            sample.value<std::string>("request_id", std::string(key_bound, 'k'));
            auto metadata = boundary.metadata(topic, sample);
            metadata.source_role = std::string(255, 'r');
            metadata.run_id = std::string(255, 'a');
            for (int index = 0; index < 16; ++index) {
                metadata.destination_ids.push_back(std::string(252, 'd') + std::to_string(100 + index));
            }
            const auto frame = boundary_codec.pack(topic, sample, metadata);
            const auto decoded = boundary_codec.unpack(frame);
            check(decoded.topic == topic && decoded.data == sample);
            check(frame.value<std::string>("source_id") == metadata.source_id);
            check(frame.value<std::string>("source_role") == metadata.source_role);
            check(frame.value<std::string>("run_id") == metadata.run_id);
            const auto audience = frame.value<Data>("audience");
            const auto destinations = audience.value<Data>("destination_ids");
            for (size_t index = 0; index < metadata.destination_ids.size(); ++index) {
                check(destinations.value<std::string>(static_cast<uint32_t>(index + 1)) ==
                      metadata.destination_ids[index]);
            }
            const auto key = boundary_codec.unpack_key(boundary_codec.key_frame(topic, sample, metadata));
            check(key.data.value<std::string>("request_id") == sample.value<std::string>("request_id"));
        }
        const auto invalid = [&](const std::string& key, const std::string& value) {
            auto next = properties;
            next[key] = value;
            rejected([&] { act_rs::GatewayModel bad(next); });
        };
        invalid("act.binding.command.type", "WrongCommand");
        invalid("act.binding.command.type", "MissingType");
        invalid("act.binding.command.branch", "missing_branch");
        invalid("act.binding.command.instance_id", "missing_key");
        invalid("act.binding.command.instance_id", "opcode");
        invalid("act.binding.command.topic_name", "DeviceUpdates");
        invalid("act.binding.command.tx.port", "input_status");
        invalid("act.binding.command.tx.recipients", "addressed");
        invalid("act.binding.command.channel", "periodic_high");
        invalid("act.receive_policy", "mission");
        invalid("act.monitoring", "true");
        invalid("act.node", "");
        auto mappings = model.codec_mappings();
        mappings.front().discriminator += 1;
        rejected([&] { router::WanCodec bad(types, mappings); });
        mappings = model.codec_mappings();
        mappings.push_back(mappings.front());
        rejected([&] { router::WanCodec bad(types, mappings); });
        rejected([&] { router::WanCodec bad(types, {}); });
        rejected([&] { codec.pack("DeviceCommands", status, model.metadata("DeviceUpdates", status)); });
        Data assignable(types->type("WrongCommand"));
        assignable.value<std::string>("request_id", "request-9");
        rejected([&] { codec.pack("DeviceCommands", assignable, model.metadata("DeviceCommands", command)); });
        auto frame = codec.pack("DeviceCommands", command, model.metadata("DeviceCommands", command));
        frame.value<std::string>("original_topic", "DeviceUpdates");
        rejected([&] { codec.unpack(frame); });
        frame.value<std::string>("original_topic", "Unknown");
        rejected([&] { codec.unpack(frame); });
        std::puts("full-type registry/model/codec checks passed (no DDS entities)");
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "%s\n", error.what());
        return 1;
    }
}
