#include "common.hpp"
using namespace spike;

void source(dds::core::QosProvider& envelope, dds::core::QosProvider& qos)
{
    auto lan = participant(111, qos), wan = participant(112, qos);
    auto discovery = builtin<dds::topic::PublicationBuiltinTopicData>(lan, "DCPSPublication");
    const auto outer = envelope->type("SerializedWanEnvelope");
    Topic topic(wan, "OpaqueWan", outer);
    auto output = writer(wan, topic, qos);
    std::map<std::string, Type> local;
    std::vector<Topic> input_topics;
    std::map<std::string, Reader> inputs;
    uint64_t payload_bytes = 0, envelope_bytes = 0;
    for (uint32_t phase = 1; phase <= 2; ++phase) {
        wait("app-" + std::to_string(phase));
        until("discovered source types", [&] {
            learn(discovery, local);
            return local.size() == (phase == 1 ? 2U : 3U);
        });
        for (const auto& item : local) {
            if (inputs.count(item.first)) continue;
            input_topics.emplace_back(lan, item.first, item.second);
            inputs.emplace(item.first, reader(lan, input_topics.back(), qos));
            std::cout << "DISCOVER source " << item.first << " " << schema_id(item.second) << std::endl;
        }
        wait("destination-ready-" + std::to_string(phase));
        until("one WAN filtered reader", [&] {
            return output.publication_matched_status().current_count() == 1;
        });
        const auto before = output->datawriter_protocol_status().pushed_sample_count().total();
        mark("source-ready-" + std::to_string(phase));
        uint32_t count = 0, offered = 0;
        until("all source samples", [&] {
            for (auto& input : inputs) {
                for (const auto& sample : input.second.take()) {
                    require(sample.info().valid(), "Unexpected lifecycle before lifecycle test");
                    const auto bytes = serialize(sample.data());
                    Data local_copy(local.at(input.first));
                    rti::topic::from_cdr_buffer_no_alloc(local_copy, bytes);
                    require(local_copy == sample.data(), "Local Connext CDR equality failed");
                    Data frame(outer);
                    frame.value<std::string>("source", "Control");
                    frame.value<std::string>("destination", "Platform");
                    frame.value<std::string>("original_topic", input.first);
                    frame.value<std::string>("schema_name", local.at(input.first).name());
                    frame.value<std::string>("schema_identity", schema_id(local.at(input.first)));
                    frame.value<std::string>("instance_id", instance_id(sample.data()));
                    frame.value<std::string>("encoding", "connext-cdr-xcdr1");
                    frame.value<std::string>("operation", "WRITE");
                    frame.set_values("payload", octets(bytes));
                    payload_bytes += bytes.size();
                    envelope_bytes += serialize(frame).size();
                    output.write(frame);
                    ++offered;
                    auto wrong = frame;
                    wrong.value<std::string>("destination", "Other");
                    output.write(wrong);
                    wrong = frame;
                    wrong.value<std::string>("original_topic", "NotSelected");
                    output.write(wrong);
                    wrong = frame;
                    wrong.value<std::string>("source", "Other");
                    output.write(wrong);
                    offered += 3;
                    if (phase == 1 && input.first == "App_Command"
                        && sample.data().value<uint32_t>("sequence") == 100) {
                        auto bad = frame;
                        bad.value<std::string>("original_topic", "App_Unknown");
                        output.write(bad);
                        bad = frame;
                        bad.value<std::string>("original_topic", "App_Mismatch");
                        output.write(bad);
                        bad = frame;
                        bad.value<std::string>("schema_identity", "wrong-schema");
                        output.write(bad);
                        bad = frame;
                        bad.value<std::string>("encoding", "unknown-encoding");
                        output.write(bad);
                        bad = frame;
                        bad.set_values("payload", std::vector<uint8_t>{0, 1});
                        output.write(bad);
                        offered += 5;
                    }
                    ++count;
                }
            }
            return count == (phase == 1 ? 6U : 9U);
        });
        output.wait_for_acknowledgments(dds::core::Duration(10));
        const auto pushed = output->datawriter_protocol_status().pushed_sample_count().total() - before;
        require(pushed == (phase == 1 ? 11 : 9), "Native WAN push count differs");
        require(outer == envelope->type("SerializedWanEnvelope"), "Outer schema changed");
        std::cout << "PASS source phase=" << phase << " originals=" << count
                  << " offered=" << offered << " native_pushes=" << pushed << std::endl;
        mark("source-done-" + std::to_string(phase));
        wait("verified-" + std::to_string(phase));
    }
    until("source dispose observation", [&] {
        for (auto& input : inputs) {
            for (const auto& sample : input.second.take()) {
                if (!sample.info().valid()) {
                    require(input.first == "App_Command"
                                && sample.info().state().instance_state()
                                    == dds::sub::status::InstanceState::not_alive_disposed(),
                            "Expected explicit original command dispose");
                    std::cout << "PASS source lifecycle observed; explicitly NOT forwarded" << std::endl;
                    return true;
                }
            }
        }
        return false;
    });
    mark("lifecycle-observed");
    wait("complete");
    std::cout << "MEASURE serialized_original_bytes=" << payload_bytes
              << " serialized_envelope_bytes=" << envelope_bytes
              << " (15 selected originals; excludes RTPS/discovery/retransmission)" << std::endl;
}

void destination(dds::core::QosProvider& envelope, dds::core::QosProvider& qos)
{
    auto lan = participant(113, qos), wan = participant(112, qos);
    auto discovery = builtin<dds::topic::SubscriptionBuiltinTopicData>(lan, "DCPSSubscription");
    auto wan_publications = builtin<dds::topic::PublicationBuiltinTopicData>(wan, "DCPSPublication");
    const auto outer = envelope->type("SerializedWanEnvelope");
    Topic topic(wan, "OpaqueWan", outer);
    dds::topic::ContentFilteredTopic<Data> filter(topic, "metadata_only",
        dds::topic::Filter("source = 'Control' AND destination = 'Platform' AND "
                          "(original_topic = 'App_Command' OR original_topic = 'App_Status' OR "
                          "original_topic = 'App_Added' OR original_topic = 'App_Unknown' OR "
                          "original_topic = 'App_Mismatch')"));
    Reader input(dds::sub::Subscriber(wan), filter, qos.datareader_qos(lib + "sample"));
    std::map<std::string, Type> local;
    std::vector<Topic> outputs;
    std::map<std::string, Writer> writers;
    std::map<std::string, uint32_t> rejections;
    for (uint32_t phase = 1; phase <= 2; ++phase) {
        wait("app-" + std::to_string(phase));
        until("discovered destination subscription types", [&] {
            learn(discovery, local);
            return local.size() == (phase == 1 ? 3U : 4U);
        });
        until("WAN only discovers fixed envelope", [&] {
            for (const auto& sample : wan_publications.read()) {
                if (!sample.info().valid()) continue;
                require(sample.data().topic_name() == "OpaqueWan", "Application type on WAN");
                auto type = sample.data()->type();
                if (type) {
                    require(type.get() == outer, "WAN discovered application-specific type graph");
                    return true;
                }
            }
            return false;
        });
        mark("destination-ready-" + std::to_string(phase));
        uint32_t received = 0, restored = 0;
        until("WAN selected samples", [&] {
            for (const auto& sample : input.take()) {
                require(sample.info().valid(), "WAN lifecycle unqualified");
                ++received;
                const auto& frame = sample.data();
                try {
                    validate(frame, local);
                } catch (const std::runtime_error& error) {
                    const std::string reason = error.what();
                    require(reason == "unknown_local_type" || reason == "schema_mismatch"
                                || reason == "unsupported_encoding"
                                || reason == "invalid_cdr_encapsulation",
                            "Unexpected rejection: " + reason);
                    ++rejections[reason];
                    std::cout << "REJECT " << reason << " topic="
                              << frame.value<std::string>("original_topic") << std::endl;
                    continue;
                }
                const auto name = frame.value<std::string>("original_topic");
                Data restored_sample(local.at(name));
                rti::topic::from_cdr_buffer_no_alloc(restored_sample, cdr(frame));
                require(instance_id(restored_sample) == frame.value<std::string>("instance_id"),
                        "Envelope key disagrees with restored original key");
                if (!writers.count(name)) {
                    outputs.emplace_back(lan, name, local.at(name));
                    writers.emplace(name, writer(lan, outputs.back(), qos));
                    until("original application reader", [&] {
                        return writers.at(name).publication_matched_status().current_count() == 1;
                    });
                }
                writers.at(name).write(restored_sample);
                ++restored;
            }
            require(received <= (phase == 1 ? 11U : 9U), "Unselected metadata arrived");
            return received == (phase == 1 ? 11U : 9U);
        });
        require(restored == (phase == 1 ? 6U : 9U), "Wrong restoration count");
        require(rejections["unknown_local_type"] == 1 && rejections["schema_mismatch"] == 2
                    && rejections["unsupported_encoding"] == 1
                    && rejections["invalid_cdr_encapsulation"] == 1, "Negative cases missing");
        std::cout << "PASS destination phase=" << phase << " selected=" << received
                  << " restored=" << restored << " cumulative_rejected=5" << std::endl;
        mark("destination-done-" + std::to_string(phase));
        wait("verified-" + std::to_string(phase));
    }
    wait("complete");
}

int main(int argc, char** argv)
{
    try {
        require(argc == 4, "gateway source|destination envelope.xml qos.xml");
        dds::core::QosProvider envelope(argv[2]), qos(argv[3]);
        if (std::string(argv[1]) == "source") source(envelope, qos);
        else if (std::string(argv[1]) == "destination") destination(envelope, qos);
        else throw std::runtime_error("Unknown role");
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL gateway: " << error.what() << std::endl;
        return 1;
    }
}
