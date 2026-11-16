#include "common.hpp"

using namespace spike;

int main(int argc, char** argv)
{
    try {
        const bool discovery_only = argc == 4 && std::string(argv[3]) == "--discovery-only";
        require(argc == 3 || discovery_only,
                "Usage: gateway bootstrap.xml qos.xml [--discovery-only]");
        dds::core::QosProvider bootstrap(argv[1]), qos(argv[2]);
        auto lan = participant(source_domain, qos);
        auto wan = participant(wan_domain, qos);
        auto discovery = publications(lan);
        Progress progress(wan, bootstrap, qos);
        std::map<std::string, Type> learned;
        until("two LAN command types, solely from discovery", [&] {
            for (const auto& sample : discovery.read()) {
                if (!sample.info().valid()) continue;
                const std::string name = sample.data().topic_name();
                if (name.compare(0, 4, "Cmd_") != 0) continue;
                auto type = sample.data()->type();
                if (type) {
                    learned.erase(name);
                    learned.emplace(name, type.get());
                }
            }
            return learned.size() == 2;
        });

        using namespace dds::core::xtypes;
        UnionType payload("DiscoveredCommandUnion", primitive_type<int32_t>());
        std::vector<Entry> catalog;
        int32_t label = discovery_only ? 17 : 1;
        for (const auto& item : learned) {
            Entry entry{item.first, item.second.name(),
                        discovery_only ? item.first : "branch_" + std::to_string(label), label};
            payload.add_member(UnionMember(entry.branch, item.second, label));
            catalog.push_back(entry);
            label += discovery_only ? 25 : 1;
        }
        StructType envelope("DiscoveredCommandEnvelope");
        envelope.add_member(Member("source", StringType(64)));
        if (!discovery_only) envelope.add_member(Member("original_topic", StringType(128)));
        envelope.add_member(Member("destination", StringType(64)));
        envelope.add_member(Member("payload", payload));
        Topic event_topic(wan, "Event", envelope);
        auto event_writer = writer(wan, event_topic, qos, "wan_event");
        std::vector<Topic> input_topics;
        std::vector<Reader> inputs;
        for (const auto& entry : catalog) {
            input_topics.emplace_back(lan, entry.topic, learned.at(entry.topic));
            inputs.push_back(reader(lan, input_topics.back(), qos));
            require(branch_type(envelope, entry) == learned.at(entry.topic),
                    "Runtime branch did not preserve whole discovered type");
        }
        Topic state_topic(dds::core::null);
        Writer state_writer(dds::core::null);
        if (!discovery_only) {
            state_topic = Topic(wan, "State", bootstrap->type("CatalogState"));
            state_writer = writer(wan, state_topic, qos, "router_admin_status");
            Data state(bootstrap->type("CatalogState"));
            state.value<std::string>("source", "Control_20");
            state.value<uint32_t>("revision", 1);
            state.value<std::string>("wan_topic", "Event");
            state.value<std::string>("envelope_type", envelope.name());
            {
                auto values = state.loan_value("entries");
                for (size_t i = 0; i < catalog.size(); ++i) {
                    auto value = values.get().loan_value(uint32_t(i + 1));
                    value.get().value<std::string>("original_topic", catalog[i].topic);
                    value.get().value<std::string>("type_name", catalog[i].type_name);
                    value.get().value<std::string>("branch", catalog[i].branch);
                    value.get().value<int32_t>("label", catalog[i].label);
                }
            }
            state_writer.write(state);
            std::cout << "CATALOG_PUBLISHED (once, before platform process starts)" << std::endl;
        }
        std::cout << "PASS discovered LAN structures and constructed runtime union\n"
                  << "UNION_PUBLISHED (before platform process starts)" << std::endl;
        progress.put("gateway", 0, "ready");

        const uint32_t phases = discovery_only ? 5 : 3;
        for (uint32_t phase = 1; phase <= phases; ++phase) {
            auto request = progress.wait("application", phase, "run");
            if (discovery_only) {
                require(request.value<std::string>("selection").empty(),
                        "Discovery-only request must not carry a topic selection");
                require_no_state(wan);
            }
            progress.wait("platform", phase, "ready");
            until("one filtered WAN reader", [&] {
                return event_writer.publication_matched_status().current_count() == 1;
            });
            const auto before = event_writer->datawriter_protocol_status()
                                    .pushed_sample_count().total();
            progress.put("gateway", phase, "ready");
            uint32_t count = 0;
            until("forward all phase inputs", [&] {
                for (size_t i = 0; i < inputs.size(); ++i) {
                    for (const auto& sample : inputs[i].take()) {
                        require(sample.info().valid(), "Unexpected input lifecycle sample");
                        Data frame(envelope);
                        frame.value<std::string>("source", "Control_20");
                        if (!discovery_only)
                            frame.value<std::string>("original_topic", catalog[i].topic);
                        frame.value<std::string>("destination",
                            sample.data().value<std::string>("destination"));
                        {
                            auto value = frame.loan_value("payload");
                            value.get().value<Data>(catalog[i].branch, sample.data());
                        }
                        event_writer.write(frame);
                        ++count;
                    }
                }
                require(count <= request.value<uint32_t>("count"), "Extra source commands");
                return count == request.value<uint32_t>("count");
            });
            event_writer.wait_for_acknowledgments(dds::core::Duration(10));
            const auto pushed = event_writer->datawriter_protocol_status()
                                    .pushed_sample_count().total() - before;
            require(pushed == request.value<uint32_t>("expected"),
                    "Sender filtering push delta: expected "
                        + std::to_string(request.value<uint32_t>("expected"))
                        + ", actual " + std::to_string(pushed));
            progress.put("gateway", phase, "forwarded", "", count, 0, pushed);
            std::cout << "PASS phase " << phase << ": forwarded=" << count
                      << " native WAN pushes=" << pushed << std::endl;
        }
        progress.wait("application", phases + 1, "complete");
        if (discovery_only) require_no_state(wan);
        std::cout << "PASS gateway complete" << std::endl;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL gateway: " << error.what() << std::endl;
        return 1;
    }
}
