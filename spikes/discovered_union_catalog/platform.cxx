#include "common.hpp"

using namespace spike;

int main(int argc, char** argv)
{
    try {
        const bool discovery_only = argc == 4 && std::string(argv[3]) == "--discovery-only";
        require(argc == 3 || discovery_only,
                "Usage: platform bootstrap.xml qos.xml [--discovery-only]");
        dds::core::QosProvider bootstrap(argv[1]), qos(argv[2]);
        auto wan = participant(wan_domain, qos);
        auto lan = participant(destination_domain, qos);
        auto discovery = publications(wan);
        (void) discovery;
        auto local_discovery = subscriptions(lan);
        Progress progress(wan, bootstrap, qos);
        Topic state_topic(dds::core::null);
        Reader state_reader(dds::core::null);
        std::unique_ptr<Data> state;
        if (!discovery_only) {
            state_topic = Topic(wan, "State", bootstrap->type("CatalogState"));
            state_reader = reader(wan, state_topic, qos, "router_admin_status");
            until("retained State catalog for late platform", [&] {
                for (const auto& sample : state_reader.read()) {
                    if (sample.info().valid()) state.reset(new Data(sample.data()));
                }
                return bool(state);
            });
            require(state->value<uint32_t>("revision") == 1, "Unsupported catalog revision");
        }
        const auto wan_topic = discovery_only ? "Event" : state->value<std::string>("wan_topic");
        auto envelope = discover(wan, wan_topic);
        auto catalog = discovery_only ? union_entries(envelope) : entries(*state);
        if (!discovery_only) {
            require(envelope.name() == state->value<std::string>("envelope_type"),
                    "Discovered envelope disagrees with catalog");
        }
        std::vector<Topic> outputs;
        std::map<std::string, Writer> output_writers;
        for (const auto& entry : catalog) {
            const auto& original = branch_type(envelope, entry);
            require(original.kind() == dds::core::xtypes::TypeKind::STRUCTURE_TYPE,
                    "Expected discovered command structure");
            const auto& structure =
                static_cast<const dds::core::xtypes::StructType&>(original);
            std::cout << "DISCOVERED " << original.name() << ":";
            for (const auto& member : structure.members()) {
                const std::string name = member.name();
                require(!name.empty(), "Missing discovered member name");
                std::cout << " " << name;
            }
            std::cout << std::endl;
            if (!discovery_only) {
                outputs.emplace_back(lan, entry.topic, original);
                output_writers.emplace(entry.topic, writer(lan, outputs.back(), qos));
            }
        }
        if (!discovery_only) {
            try {
                select(catalog, "not-in-the-catalog");
                throw std::runtime_error("Unknown selection was accepted");
            } catch (const std::invalid_argument& error) {
                std::cout << "PASS explicit selection rejection: " << error.what() << std::endl;
            }
            auto invalid = catalog.front();
            invalid.type_name = "WrongType";
            bool rejected = false;
            try {
                branch_type(envelope, invalid);
            } catch (const std::runtime_error& error) {
                rejected = true;
                std::cout << "PASS catalog/discovery mismatch rejection: "
                          << error.what() << std::endl;
            }
            require(rejected, "Mismatched catalog type was accepted");
        }
        std::cout << (discovery_only ? "PASS remote union discovery without State; "
                                    : "PASS late State bootstrap and remote nested-union discovery; ")
                  << "no application type file" << std::endl;
        progress.put("platform", 0, "ready");
        Topic event_topic(wan, wan_topic, envelope);

        const uint32_t phases = discovery_only ? 5 : 3;
        uint32_t type_rejections = 0;
        for (uint32_t phase = 1; phase <= phases; ++phase) {
            auto request = progress.wait("application", phase, "run");
            const auto selection = request.value<std::string>("selection");
            std::set<std::string> selected_topics;
            if (discovery_only) {
                require(selection.empty(), "Discovery-only control carried a topic selection");
                require_no_state(wan);
                output_writers.clear();
                outputs.clear();
                auto local = local_types(local_discovery, phase,
                                         request.value<uint32_t>("local_readers"));
                for (const auto& entry : catalog) {
                    auto found = local.find(entry.topic);
                    if (found == local.end()) continue;
                    if (found->second != branch_type(envelope, entry)) {
                        std::cout << "REJECT local subscription " << entry.topic
                                  << ": discovered type structure differs (local="
                                  << found->second.name() << ", WAN=" << entry.type_name
                                  << ")" << std::endl;
                        ++type_rejections;
                        continue;
                    }
                    selected_topics.insert(entry.topic);
                    outputs.emplace_back(lan, entry.topic, found->second);
                    output_writers.emplace(entry.topic, writer(lan, outputs.back(), qos));
                }
            } else if (!selection.empty()) {
                select(catalog, selection);
                selected_topics.insert(selection);
            }
            std::string expression = "(";
            std::vector<std::string> parameters;
            for (const auto& entry : catalog) {
                if (!selected_topics.count(entry.topic)) continue;
                if (!parameters.empty()) expression += " OR ";
                expression += "payload._d = %" + std::to_string(parameters.size());
                parameters.push_back(std::to_string(entry.label));
            }
            if (parameters.empty()) {
                expression += "payload._d = %0";
                parameters.push_back("0");
            }
            expression += ") AND destination = 'Platform_30'";
            uint32_t count = 0;
            {
                dds::topic::ContentFilteredTopic<Data> filter(
                    event_topic, "selected_" + std::to_string(phase),
                    dds::topic::Filter(expression, parameters));
                Reader input(dds::sub::Subscriber(wan), filter,
                             qos.datareader_qos(library + "wan_event"));
                until("WAN match and destination LAN readers", [&] {
                    if (input.subscription_matched_status().current_count() != 1) return false;
                    for (auto& output : output_writers) {
                        if (output.second.publication_matched_status().current_count() != 1)
                            return false;
                    }
                    return true;
                });
                progress.put("platform", phase, "ready");
                auto drain = [&] {
                    for (const auto& sample : input.take()) {
                        require(sample.info().valid(), "Unexpected WAN lifecycle sample");
                        const auto& frame = sample.data();
                        auto payload = frame.value<Data>("payload");
                        const Entry* active = nullptr;
                        for (const auto& entry : catalog) {
                            if (uint32_t(entry.label) == payload.discriminator_value())
                                active = &entry;
                        }
                        require(active && selected_topics.count(active->topic),
                                "Unselected discriminator arrived");
                        const auto& entry = *active;
                        if (!discovery_only) {
                            require(frame.value<std::string>("original_topic") == entry.topic,
                                    "Envelope topic disagrees with discovered branch");
                        }
                        require(frame.value<std::string>("destination") == "Platform_30",
                                "Wrong destination passed native CFT");
                        auto original = payload.value<Data>(entry.branch);
                        require(original.type() == branch_type(envelope, entry),
                                "Decoded original type mismatch");
                        require(original.value<std::string>("destination") == "Platform_30",
                                "Payload and envelope destination disagree");
                        output_writers.at(entry.topic).write(original);
                        ++count;
                    }
                    require(count <= request.value<uint32_t>("expected"),
                            "Duplicate or unexpected selected command");
                };
                until("all inputs forwarded and exact selected receives", [&] {
                    drain();
                    return bool(progress.get("gateway", phase, "forwarded"))
                        && count == request.value<uint32_t>("expected");
                });
                const auto quiet_end = Clock::now() + std::chrono::seconds(1);
                while (Clock::now() < quiet_end) {
                    drain();
                    std::this_thread::sleep_for(std::chrono::milliseconds(20));
                }
                for (auto& output : output_writers)
                    output.second.wait_for_acknowledgments(dds::core::Duration(10));
            }
            progress.put("platform", phase, "done", selection, count);
            std::cout << "PASS phase " << phase << ": "
                      << (discovery_only ? "local-discovery interests=" : "catalog interests=");
            for (const auto& topic : selected_topics) std::cout << topic << " ";
            std::cout << "received/restored=" << count << std::endl;
        }
        progress.wait("application", phases + 1, "complete");
        if (discovery_only) {
            require(type_rejections == 1, "Expected exactly one structural type rejection");
            require_no_state(wan);
            std::cout << "PASS same-name/different-structure rejection and no State endpoints"
                      << std::endl;
        }
        std::cout << "PASS platform complete" << std::endl;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL platform: " << error.what() << std::endl;
        return 1;
    }
}
