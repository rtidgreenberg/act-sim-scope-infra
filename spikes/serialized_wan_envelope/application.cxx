#include "common.hpp"
using namespace spike;

const std::vector<std::string> names{"App_Command", "App_Status", "App_Added"};
const std::vector<std::string> types{"MoveCommand", "NestedStatus", "AddedPayload"};

Data fixture(dds::core::QosProvider& model, size_t family, uint32_t phase, uint32_t i)
{
    Data value(model->type(types.at(family)));
    value.value<std::string>("id", names[family] + "/key/" + std::to_string(i % 2));
    value.value<uint32_t>("sequence", phase * 100 + i);
    value.value<int64_t>("timestamp", 9999999990000LL + phase * 100 + i);
    if (family == 0) {
        {
            auto points = value.loan_value("waypoints");
            for (uint32_t j = 1; j <= (i == 0 ? 0 : i == 1 ? 2 : 8); ++j) {
                auto point = points.get().loan_value(j);
                point.get().value<double>("north", -42.125 + j);
                point.get().value<double>("east", 19.5 * j);
            }
        }
        auto limits = value.loan_value("limits");
        for (uint32_t j = 1; j <= 3; ++j) limits.get().value<double>(j, j * 0.125);
    } else if (family == 1) {
        {
            auto position = value.loan_value("position");
            position.get().value<double>("north", -123.125 + i);
            position.get().value<double>("east", 172.5);
        }
        value.value<std::string>("notes", i == 0 ? "" : i == 1 ? "nested status" : std::string(128, 'Z'));
        value.value<bool>("enabled", i % 2 == 0);
        std::vector<int32_t> readings;
        for (uint32_t j = 0; j < (i == 0 ? 0 : i == 1 ? 2 : 16); ++j)
            readings.push_back(-int32_t(j * 11));
        value.set_values("readings", readings);
    } else {
        std::vector<uint8_t> blob;
        for (uint32_t j = 0; j < (i == 0 ? 0 : i == 1 ? 2 : 256); ++j)
            blob.push_back(uint8_t(j));
        value.set_values("blob", blob);
    }
    return value;
}

int main(int argc, char** argv)
{
    try {
        require(argc == 3, "application model.xml qos.xml");
        dds::core::QosProvider model(argv[1]), qos(argv[2]);
        auto source = participant(111, qos), sink = participant(113, qos);
        std::vector<Topic> input_topics, output_topics;
        std::vector<Writer> producers;
        std::vector<Reader> consumers;
        auto add = [&](size_t i) {
            input_topics.emplace_back(source, names[i], model->type(types[i]));
            producers.push_back(writer(source, input_topics.back(), qos));
            output_topics.emplace_back(sink, names[i], model->type(types[i]));
            consumers.push_back(reader(sink, output_topics.back(), qos));
        };
        add(0);
        add(1);
        // Same topic and name as the incoming command schema, deliberately different structure.
        dds::core::xtypes::StructType wrong("MoveCommand");
        wrong.add_member(dds::core::xtypes::Member(
            "unexpected", dds::core::xtypes::primitive_type<int32_t>()));
        auto wrong_participant = participant(113, qos);
        Topic wrong_topic(wrong_participant, "App_Mismatch", wrong);
        auto wrong_reader = reader(wrong_participant, wrong_topic, qos);
        std::map<std::string, dds::core::InstanceHandle> handles;
        for (uint32_t phase = 1; phase <= 2; ++phase) {
            if (phase == 2) add(2);
            mark("app-" + std::to_string(phase));
            wait("source-ready-" + std::to_string(phase));
            wait("destination-ready-" + std::to_string(phase));
            for (size_t family = 0; family < producers.size(); ++family) {
                until("LAN matching", [&] {
                    return producers[family].publication_matched_status().current_count() == 1;
                });
                for (uint32_t i = 0; i < 3; ++i)
                    producers[family].write(fixture(model, family, phase, i));
            }
            std::set<std::string> seen;
            auto drain = [&] {
                for (size_t family = 0; family < consumers.size(); ++family) {
                    for (const auto& sample : consumers[family].take()) {
                        require(sample.info().valid(), "Unexpected destination lifecycle");
                        const auto seq = sample.data().value<uint32_t>("sequence");
                        require(seq >= phase * 100 && seq < phase * 100 + 3, "Unexpected sequence");
                        require(sample.data() == fixture(model, family, phase, seq - phase * 100),
                                "Complete original payload equality failed");
                        const auto key = instance_id(sample.data());
                        const auto found = handles.find(key);
                        if (found != handles.end())
                            require(found->second == sample.info().instance_handle(),
                                    "Original key did not retain instance handle");
                        else handles.emplace(key, sample.info().instance_handle());
                        require(seen.insert(names[family] + std::to_string(seq)).second,
                                "Duplicate original LAN sample");
                    }
                }
                require(wrong_reader.take().length() == 0, "Mismatch emitted a LAN sample");
            };
            until("independent app equality", [&] {
                drain();
                return seen.size() == producers.size() * 3;
            });
            wait("source-done-" + std::to_string(phase));
            wait("destination-done-" + std::to_string(phase));
            const auto end = Clock::now() + std::chrono::milliseconds(600);
            while (Clock::now() < end) {
                drain();
                std::this_thread::sleep_for(std::chrono::milliseconds(20));
            }
            std::cout << "PASS application phase=" << phase << " full_equality=" << seen.size()
                      << " stable_key_handles=" << handles.size() << std::endl;
            mark("verified-" + std::to_string(phase));
        }
        producers[0].dispose_instance(producers[0].lookup_instance(fixture(model, 0, 2, 0)));
        wait("lifecycle-observed");
        require(consumers[0].take().length() == 0,
                "Lifecycle must not silently cross unqualified bridge");
        mark("complete");
        std::cout << "PASS application complete: 15 full payloads; lifecycle explicitly not forwarded"
                  << std::endl;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL application: " << error.what() << std::endl;
        return 1;
    }
}
