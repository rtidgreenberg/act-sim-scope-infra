#include "common.hpp"

using namespace spike;

Data command(dds::core::QosProvider& types, bool move, uint32_t phase, uint32_t index)
{
    Data value(types->type(move ? "SpikeMoveCommand" : "SpikeModeCommand"));
    value.value<std::string>("command_id",
        std::to_string(phase) + "/" + (move ? "move/" : "mode/") + std::to_string(index));
    value.value<std::string>("destination", index < 3 ? "Platform_30" : "Platform_31");
    value.value<uint32_t>("sequence", phase * 100 + index);
    value.value<int64_t>("issued_at", 9876543210000LL + phase * 100 + index);
    if (move) {
        {
            auto points = value.loan_value("waypoints");
            for (uint32_t i = 1; i <= (index == 0 ? 0 : index == 1 ? 2 : 8); ++i) {
                auto point = points.get().loan_value(i);
                point.get().value<double>("north", -42.125 + i + phase);
                point.get().value<double>("east", 19.5 * i);
            }
        }
        auto limits = value.loan_value("limits");
        for (uint32_t i = 1; i <= 3; ++i) limits.get().value<double>(i, i * 0.125);
    } else {
        value.value<std::string>("mode", index == 2 ? std::string(64, 'M') : "ARM_AND_EXECUTE");
        value.value<bool>("enabled", index % 2 == 0);
        auto arguments = value.loan_value("arguments");
        for (uint32_t i = 1; i <= (index == 0 ? 0 : index == 1 ? 2 : 8); ++i)
            arguments.get().value<int32_t>(i, -int32_t(i * phase));
    }
    return value;
}

int main(int argc, char** argv)
{
    try {
        const bool discovery_only = argc == 5 && std::string(argv[4]) == "--discovery-only";
        require(argc == 4 || discovery_only,
                "Usage: application application_types.xml bootstrap.xml qos.xml "
                "[--discovery-only]");
        dds::core::QosProvider types(argv[1]), bootstrap(argv[2]), qos(argv[3]);
        auto source = participant(source_domain, qos);
        std::unique_ptr<Participant> destination;
        if (!discovery_only) destination.reset(new Participant(participant(destination_domain, qos)));
        auto wan = participant(wan_domain, qos);
        Progress progress(wan, bootstrap, qos);
        const std::vector<std::string> topics{"Cmd_Move", "Cmd_Mode"};
        const std::vector<std::string> type_names{"SpikeMoveCommand", "SpikeModeCommand"};
        std::vector<Topic> source_topics, destination_topics;
        std::vector<Writer> writers;
        std::vector<Reader> readers;
        std::vector<size_t> reader_families;
        for (size_t i = 0; i < topics.size(); ++i) {
            source_topics.emplace_back(source, topics[i], types->type(type_names[i]));
            writers.push_back(writer(source, source_topics.back(), qos));
            if (!discovery_only) {
                destination_topics.emplace_back(*destination, topics[i], types->type(type_names[i]));
                readers.push_back(reader(*destination, destination_topics.back(), qos));
                reader_families.push_back(i);
            }
        }
        progress.wait("gateway", 0, "ready");
        progress.wait("platform", 0, "ready");
        const uint32_t phases = discovery_only ? 5 : 3;
        uint32_t restored = 0;
        for (uint32_t phase = 1; phase <= phases; ++phase) {
            std::set<size_t> selected_families;
            if (phase == 1 || phase == 5) selected_families.insert(0);
            if (phase == 2 || phase == 5) selected_families.insert(1);
            if (discovery_only) {
                readers.clear();
                destination_topics.clear();
                reader_families.clear();
                if (destination) destination->close();
                destination.reset(new Participant(participant(destination_domain, qos)));
                auto add_reader = [&](const std::string& topic, const Type& type, size_t family) {
                    destination_topics.emplace_back(*destination, topic, type);
                    readers.push_back(reader(*destination, destination_topics.back(), qos,
                                             "lan_event", phase_reader_name(phase)));
                    reader_families.push_back(family);
                };
                for (auto family : selected_families)
                    add_reader(topics[family], types->type(type_names[family]), family);
                if (phase == 4) {
                    dds::core::xtypes::StructType wrong("SpikeModeCommand");
                    wrong.add_member(dds::core::xtypes::Member(
                        "unexpected", dds::core::xtypes::primitive_type<int32_t>()));
                    add_reader(topics[1], wrong, 1);
                }
                add_reader("Cmd_Unmapped", types->type(type_names[0]), 2);
            }
            const auto selection = discovery_only || selected_families.empty()
                ? "" : topics[*selected_families.begin()];
            const uint32_t expected = uint32_t(selected_families.size() * 3);
            progress.put("application", phase, "run", selection, 12, expected, 0,
                         uint32_t(readers.size()));
            progress.wait("gateway", phase, "ready");
            for (size_t family = 0; family < writers.size(); ++family) {
                until("source LAN reader match", [&] {
                    return writers[family].publication_matched_status().current_count() == 1;
                });
                for (uint32_t i = 0; i < 6; ++i)
                    writers[family].write(command(types, family == 0, phase, i));
            }
            auto result = progress.wait("platform", phase, "done");
            require(result.value<uint32_t>("count") == expected, "Wrong selected receive count");
            auto gateway = progress.wait("gateway", phase, "forwarded");
            require(gateway.value<uint32_t>("count") == 12
                        && gateway.value<int64_t>("pushed") == expected,
                    "Wrong input/native push counts");
            std::set<std::string> received;
            auto drain = [&] {
                for (size_t observation = 0; observation < readers.size(); ++observation) {
                    const auto family = reader_families[observation];
                    for (const auto& sample : readers[observation].take()) {
                        require(sample.info().valid(), "Unexpected restored lifecycle sample");
                        require(selected_families.count(family), "Unselected LAN topic delivered");
                        const auto& value = sample.data();
                        const auto sequence = value.value<uint32_t>("sequence");
                        require(sequence >= phase * 100 && sequence < phase * 100 + 3,
                                "Wrong phase, destination or sequence on destination LAN");
                        require(value == command(types, family == 0, phase,
                                                 sequence - phase * 100),
                                "Full original command differs after discovery/union/LAN roundtrip");
                        require(received.insert(value.value<std::string>("command_id")).second,
                                "Duplicate command on destination LAN");
                    }
                }
            };
            until("exact original LAN command equality", [&] {
                drain();
                return received.size() == expected;
            });
            const auto quiet_end = Clock::now() + std::chrono::milliseconds(500);
            while (Clock::now() < quiet_end) {
                drain();
                std::this_thread::sleep_for(std::chrono::milliseconds(20));
            }
            require(received.size() == expected, "Extra restored commands");
            restored += uint32_t(received.size());
            std::cout << "PASS phase " << phase << ": full original LAN equality, "
                      << received.size() << " unique commands; no other family/destination"
                      << std::endl;
        }
        if (discovery_only) require_no_state(wan);
        progress.put("application", phases + 1, "complete");
        std::this_thread::sleep_for(std::chrono::seconds(1));
        std::cout << "PASS ALL: " << phases * 12 << " source commands, "
                  << restored << " selected/restored, " << restored
                  << " native WAN pushes; three separate processes; "
                  << (discovery_only ? "no State or selection messages" : "State catalog mode")
                  << std::endl;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL application: " << error.what() << std::endl;
        return 1;
    }
}
