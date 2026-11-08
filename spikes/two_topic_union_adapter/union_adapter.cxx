#include <dds/dds.hpp>
#include <fnmatch.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using Data = dds::core::xtypes::DynamicData;
using Reader = dds::sub::DataReader<Data>;
using Writer = dds::pub::DataWriter<Data>;
using Topic = dds::topic::Topic<Data>;
using Handle = dds::core::InstanceHandle;
using State = dds::sub::status::InstanceState;

struct AdapterError : std::runtime_error {
    explicit AdapterError(const std::string& message) : std::runtime_error(message) {}
};

void require(bool condition, const std::string& message)
{
    if (!condition) {
        throw AdapterError(message);
    }
}

struct Rule {
    std::string pattern, type, family, branch;
    uint32_t label;
    std::string profile, key;
};

std::vector<Rule> load_rules(const std::string& path)
{
    std::ifstream input(path);
    require(input.good(), "Cannot open mapping: " + path);
    std::vector<Rule> rules;
    std::string line;
    while (std::getline(input, line)) {
        if (line.empty() || line[0] == '#') {
            continue;
        }
        Rule rule;
        std::string extra;
        std::istringstream fields(line);
        require(bool(fields >> rule.pattern >> rule.type >> rule.family >> rule.branch
                            >> rule.label >> rule.profile >> rule.key)
                    && !(fields >> extra),
                "Invalid mapping row: " + line);
        require(rule.family == "Event" || rule.family == "Status", "Unknown WAN family");
        rules.push_back(rule);
    }
    require(input.eof() && !rules.empty(), "Unreadable or empty mapping: " + path);
    return rules;
}

const Rule& select_rule(const std::vector<Rule>& rules, const std::string& topic)
{
    const Rule* selected = nullptr;
    for (const auto& rule : rules) {
        if (fnmatch(rule.pattern.c_str(), topic.c_str(), 0) == 0) {
            require(selected == nullptr, "Ambiguous mapping for " + topic);
            selected = &rule;
        }
    }
    require(selected != nullptr, "Unmapped topic: " + topic);
    return *selected;
}

struct Metadata {
    std::string node, router, destination;
    uint64_t session, sequence;
};

class Codec {
public:
    Codec(dds::core::QosProvider types, const std::vector<Rule>& rules)
        : types_(types), rules_(rules)
    {
        for (const auto& rule : rules_) {
            Data frame(types_->type("Adapter" + rule.family));
            auto payload = frame.loan_value("payload");
            {
                auto branch = payload.get().loan_value(rule.branch);
                require(branch.get().type() == types_->type(rule.type),
                        "Mapping branch/type mismatch: " + rule.pattern);
            }
            payload.get().value<Data>(rule.branch, Data(types_->type(rule.type)));
            require(payload.get().discriminator_value() == rule.label,
                    "Mapping discriminator mismatch: " + rule.pattern);
            Data key_probe(types_->type(rule.type));
            key_probe.value<std::string>(rule.key);
        }
    }

    Data key_frame(const std::string& topic, const Data& local, const Metadata& metadata) const
    {
        const auto& rule = select_rule(rules_, topic);
        require(local.type() == types_->type(rule.type), "Wrong input type for " + topic);
        const auto local_key = local.value<std::string>(rule.key);
        require(!topic.empty() && topic.size() <= 128, "Original topic exceeds envelope bound");
        require(!local_key.empty(), "Empty local instance key");
        // A length prefix prevents ambiguous topic/key concatenations.
        const auto id = rule.branch + "/" + std::to_string(topic.size()) + ":" + topic
            + "/" + local_key;
        require(id.size() <= 256, "Variant-qualified instance ID exceeds envelope bound");
        require(!metadata.node.empty() && metadata.node.size() <= 64
                    && !metadata.router.empty() && metadata.router.size() <= 64,
                "Invalid envelope source identity");
        Data frame(types_->type("Adapter" + rule.family));
        frame.value<std::string>("source_node", metadata.node);
        frame.value<std::string>("source_router", metadata.router);
        frame.value<std::string>("instance_id", id);
        return frame;
    }

    Data pack(const std::string& topic, const Data& local, const Metadata& metadata) const
    {
        const auto& rule = select_rule(rules_, topic);
        auto frame = key_frame(topic, local, metadata);
        require(!metadata.destination.empty() && metadata.destination.size() <= 64,
                "Invalid envelope destination");
        frame.value<std::string>("original_topic", topic);
        frame.value<uint64_t>("session_id", metadata.session);
        frame.value<uint64_t>("audit_sequence", metadata.sequence);
        frame.value<std::string>("destination", metadata.destination);
        if (rule.family == "Status") {
            frame.value<std::string>("team_id", "TEAM_A");
        }
        auto payload = frame.loan_value("payload");
        payload.get().value<Data>(rule.branch, local);
        return frame;
    }

    Data unpack(const Data& frame) const
    {
        const auto topic = frame.value<std::string>("original_topic");
        const auto& rule = select_rule(rules_, topic);
        require(frame.type() == types_->type("Adapter" + rule.family),
                "Wrong union envelope for " + topic);
        auto payload = frame.value<Data>("payload");
        require(payload.discriminator_value() == rule.label,
                "Inactive or unknown union branch for " + topic);
        auto local = payload.value<Data>(rule.branch);
        require(local.type() == types_->type(rule.type), "Wrong decoded type for " + topic);
        Metadata metadata{frame.value<std::string>("source_node"),
                          frame.value<std::string>("source_router"),
                          frame.value<std::string>("destination"),
                          frame.value<uint64_t>("session_id"),
                          frame.value<uint64_t>("audit_sequence")};
        const auto keys = key_frame(topic, local, metadata);
        require(keys.value<std::string>("instance_id") == frame.value<std::string>("instance_id"),
                "Envelope and original payload instance keys disagree");
        return local;
    }

private:
    dds::core::QosProvider types_;
    const std::vector<Rule>& rules_;
};

Data fixture(dds::core::QosProvider types, const Rule& rule, uint64_t sequence)
{
    Data data(types->type(rule.type));
    const bool command = rule.branch == "command";
    const std::string node = command ? "Control20" : "Boat30";
    if (rule.branch == "health") {
        data.value<std::string>("router", node + "/router");
        data.value<std::string>("role", "platform");
        data.value<std::string>("config_hash", "adapter-fixture-hash");
        data.value<uint64_t>("heartbeat_seq", sequence);
        data.value<uint64_t>("session_id", 42);
        data.value<int64_t>("send_timestamp", 9876543210123LL);
        data.value<uint64_t>("state_revision", 17);
        data.value<uint32_t>("n_routes", 2);
        data.value<uint32_t>("n_degraded", 1);
        data.value<uint32_t>("n_error", 0);
        data.value<int32_t>("overall_state", 1);
        {
            auto teams = data.loan_value("team_partition");
            for (uint32_t i = 1; i <= 16; ++i) {
                teams.get().value<std::string>(i, "TEAM_" + std::to_string(i));
            }
        }
        {
            auto peers = data.loan_value("peers_seen");
            for (uint32_t i = 1; i <= 2; ++i) {
                auto peer = peers.get().loan_value(i);
                peer.get().value<std::string>("router", "Boat3" + std::to_string(i) + "/router");
                peer.get().value<int32_t>("presence", 0);
                peer.get().value<int64_t>("last_seen_delta_ms", i * 123);
                peer.get().value<uint64_t>("ack_session_id", 900 + i);
                peer.get().value<uint64_t>("ack_heartbeat_seq", 1000 + i);
                peer.get().value<uint64_t>("ack_age_at_send_us", 5000 + i);
            }
        }
        {
            auto routes = data.loan_value("routes");
            for (uint32_t i = 1; i <= 2; ++i) {
                auto route = routes.get().loan_value(i);
                route.get().value<std::string>("route_name", "route-" + std::to_string(i));
                route.get().value<int32_t>("state", 1);
                route.get().value<int32_t>("discovery_state", 1);
                route.get().value<uint64_t>("samples_forwarded", 10000 + i);
            }
        }
        return data;
    }
    data.value<std::string>("source", node);
    data.value<std::string>("run_id", "adapter-fixture-run");
    data.value<std::string>("source_node", node);
    data.value<uint64_t>("audit_sequence", sequence);
    data.value<int64_t>("sent_at_ns", 1234567890123LL);
    data.value<int64_t>("timestamp", 1234567890);
    if (rule.branch == "navigation") {
        data.value<float>("latitude", 51.25f);
        data.value<float>("longitude", -1.75f);
        data.value<float>("altitude_m", 3.5f);
        data.value<float>("heading_deg", 173.25f);
        data.value<float>("speed_knots", 12.5f);
        data.value<float>("time_scale", 1.25f);
        data.value<float>("minimum_separation_m", 40.5f);
        data.value<std::string>("navigation_state", "UNDERWAY");
        data.value<std::string>("mission_id", std::string(64, 'm'));
        data.value<std::string>("mission_phase", "TRANSIT");
        data.value<uint32_t>("heartbeat_seq", 123);
    } else {
        data.value<std::string>("destination", "Boat31");
        data.value<std::string>("command_id", std::string(64, 'c'));
        if (command) {
            data.value<std::string>("command_type", "SET_MISSION");
            std::vector<uint8_t> bytes(8192);
            for (size_t i = 0; i < bytes.size(); ++i) {
                bytes[i] = static_cast<uint8_t>((i * 37 + sequence) % 256);
            }
            data.set_values<uint8_t>("payload", bytes);
        } else {
            data.value<bool>("accepted", true);
            data.value<std::string>("message", std::string(256, 'a'));
        }
    }
    return data;
}

void wait_until(const std::function<bool()>& predicate, const std::string& description)
{
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (std::chrono::steady_clock::now() < deadline) {
        if (predicate()) {
            return;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    throw AdapterError("Timed out: " + description);
}

void reject(const std::string& name, const std::function<void()>& action)
{
    try {
        action();
    } catch (const AdapterError& error) {
        std::cout << "EXPECTED REJECTION " << name << ": " << error.what() << '\n';
        return;
    }
    throw AdapterError("Invalid input was accepted: " + name);
}

Metadata metadata(const Rule& rule, const Data& data)
{
    return Metadata{rule.branch == "command" ? "Control20" : "Boat30",
                    "adapter-A", "Boat31", 42,
                    data.value<uint64_t>(rule.branch == "health" ? "heartbeat_seq" : "audit_sequence")};
}

struct Leg {
    std::string name;
    Rule rule;
    Data expected;
    Topic source_topic, input_topic, destination_topic, observed_topic;
    Writer source_writer, destination_writer;
    Reader source_reader, destination_reader;
    int input_count = 0, received = 0, disposed = 0, unregistered = 0;

    Leg(const std::string& name_arg, Rule rule_arg, dds::core::QosProvider types,
        dds::core::QosProvider qos, dds::pub::Publisher source_app,
        dds::sub::Subscriber adapter_in, dds::pub::Publisher adapter_out,
        dds::sub::Subscriber destination_app)
        : name(name_arg), rule(rule_arg), expected(fixture(types, rule, 1)),
          source_topic(source_app.participant(), name, types->type(rule.type)),
          input_topic(adapter_in.participant(), name, types->type(rule.type)),
          destination_topic(adapter_out.participant(), name, types->type(rule.type)),
          observed_topic(destination_app.participant(), name, types->type(rule.type)),
          source_writer(source_app, source_topic,
                        qos.datawriter_qos("ACT_QOS_LIB::" + rule.profile)),
          destination_writer(adapter_out, destination_topic,
                             qos.datawriter_qos("ACT_QOS_LIB::" + rule.profile)),
          source_reader(adapter_in, input_topic,
                        qos.datareader_qos("ACT_QOS_LIB::" + rule.profile)),
          destination_reader(destination_app, observed_topic,
                             qos.datareader_qos("ACT_QOS_LIB::" + rule.profile))
    {
    }
};

struct Binding {
    size_t leg;
    Handle incoming, output;
    std::string node, router, id;
};

int run(const std::string& xml, const std::string& qos_xml, const std::string& mapping)
{
    dds::core::QosProvider types(xml), qos(qos_xml);
    auto rules = load_rules(mapping);
    Codec codec(types, rules);
    const std::string navigation = "PlatformInitStatus";
    const auto nav = fixture(types, select_rule(rules, navigation), 1);
    const auto nav_meta = metadata(select_rule(rules, navigation), nav);
    auto frame = codec.pack(navigation, nav, nav_meta);
    require(codec.unpack(frame) == nav, "Whole-structure local copy changed data");
    reject("unknown topic", [&] { codec.pack("UnsupportedTopic", nav, nav_meta); });
    reject("wrong original type", [&] { codec.pack("ControlCommand", nav, nav_meta); });
    reject("ambiguous patterns", [&] {
        auto ambiguous = rules;
        ambiguous.push_back(rules.front());
        select_rule(ambiguous, navigation);
    });
    reject("wrong branch schema", [&] {
        auto invalid = rules;
        invalid.front().branch = "health";
        Codec bad(types, invalid);
    });
    reject("wrong discriminator mapping", [&] {
        auto invalid = rules;
        invalid.front().label = 2;
        Codec bad(types, invalid);
    });
    reject("wrong envelope family", [&] {
        auto command = fixture(types, select_rule(rules, "ControlCommand"), 1);
        auto event = codec.pack("ControlCommand", command, nav_meta);
        event.value<std::string>("original_topic", navigation);
        codec.unpack(event);
    });
    reject("inactive branch", [&] {
        auto invalid = frame;
        auto health = fixture(types, select_rule(rules, "RouterHealth"), 1);
        {
            auto payload = invalid.loan_value("payload");
            payload.get().value<Data>("health", health);
        }
        codec.unpack(invalid);
    });
    reject("unmapped schema branch", [&] {
        auto invalid = frame;
        {
            auto payload = invalid.loan_value("payload");
            payload.get().set_discriminator(3);
        }
        codec.unpack(invalid);
    });
    reject("mismatched instance identity", [&] {
        auto invalid = frame;
        invalid.value<std::string>("instance_id", "health/incorrect");
        codec.unpack(invalid);
    });
    reject("oversized topic", [&] {
        codec.pack(navigation + std::string(128, 'x'), nav, nav_meta);
    });
    std::cout << "PASS mapping/schema/error checks (10 explicit rejections)\n";

    dds::domain::DomainParticipant source_app(201, qos.participant_qos("ACT_QOS_LIB::lan_router_participant"));
    dds::domain::DomainParticipant adapter_input(201, qos.participant_qos("ACT_QOS_LIB::lan_router_participant"));
    dds::domain::DomainParticipant wan_source(203, qos.participant_qos("ACT_QOS_LIB::wan_router_participant"));
    dds::domain::DomainParticipant wan_destination(203, qos.participant_qos("ACT_QOS_LIB::wan_router_participant"));
    dds::domain::DomainParticipant adapter_output(202, qos.participant_qos("ACT_QOS_LIB::lan_router_participant"));
    dds::domain::DomainParticipant destination_app(202, qos.participant_qos("ACT_QOS_LIB::lan_router_participant"));
    dds::pub::Publisher source_publisher(source_app), wan_publisher(wan_source), output_publisher(adapter_output);
    dds::sub::Subscriber input_subscriber(adapter_input), wan_subscriber(wan_destination), output_subscriber(destination_app);

    std::vector<std::unique_ptr<Leg>> legs;
    for (const auto& topic : {navigation, std::string("RouterHealth"), std::string("ControlCommand"),
                             std::string("PlatformCommandAck"), std::string("PlatformInitStatusAlias")}) {
        legs.emplace_back(new Leg(topic, select_rule(rules, topic), types, qos,
                                  source_publisher, input_subscriber, output_publisher, output_subscriber));
    }
    Topic status_out(wan_source, "Status", types->type("AdapterStatus"));
    Topic event_out(wan_source, "Event", types->type("AdapterEvent"));
    Topic status_in(wan_destination, "Status", types->type("AdapterStatus"));
    Topic event_in(wan_destination, "Event", types->type("AdapterEvent"));
    dds::topic::ContentFilteredTopic<Data> status_filter(
        status_in, "selected-status",
        dds::topic::Filter("destination = %0 AND ((payload._d = 1 AND "
                          "payload.navigation.speed_knots > 5) OR payload._d = 2)", {"'Boat31'"}));
    dds::topic::ContentFilteredTopic<Data> event_filter(
        event_in, "selected-event", dds::topic::Filter("destination = %0", {"'Boat31'"}));
    Reader status_reader(wan_subscriber, status_filter, qos.datareader_qos("ACT_QOS_LIB::wan_status"));
    Reader event_reader(wan_subscriber, event_filter, qos.datareader_qos("ACT_QOS_LIB::wan_event"));
    std::map<std::string, Writer> wan_writers;
    for (const auto& rule : rules) {
        if (wan_writers.find(rule.profile) == wan_writers.end()) {
            wan_writers.emplace(rule.profile, Writer(wan_publisher,
                rule.family == "Status" ? status_out : event_out,
                qos.datawriter_qos("ACT_QOS_LIB::" + rule.profile)));
        }
    }
    require(wan_writers.size() == 3, "Expected Event writer and two Status QoS classes");
    std::vector<Binding> bindings;
    int wan_received = 0, wan_writes = 0, lifecycle_out = 0;
    auto pushed = [&] {
        int64_t count = 0;
        for (auto& entry : wan_writers) {
            count += entry.second->datawriter_protocol_status().pushed_sample_count().total();
        }
        return count;
    };
    auto pump = [&] {
        for (auto& pointer : legs) {
            auto& leg = *pointer;
            auto& wan_writer = wan_writers.at(leg.rule.profile);
            for (const auto& sample : leg.source_reader.take()) {
                if (sample.info().valid()) {
                    auto envelope = codec.pack(leg.name, sample.data(), metadata(leg.rule, sample.data()));
                    wan_writer.write(envelope);
                    ++leg.input_count;
                    ++wan_writes;
                } else {
                    Data key(types->type(leg.rule.type));
                    leg.source_reader.key_value(key, sample.info().instance_handle());
                    const auto envelope_key = codec.key_frame(
                        leg.name, key, Metadata{leg.rule.branch == "command" ? "Control20" : "Boat30",
                                               "adapter-A", "Boat31", 42, 0});
                    const auto handle = wan_writer.lookup_instance(envelope_key);
                    require(handle != Handle::nil(), "No WAN handle for original lifecycle key");
                    if (sample.info().state().instance_state() == State::not_alive_disposed()) {
                        wan_writer.dispose_instance(handle);
                    } else {
                        require(sample.info().state().instance_state() == State::not_alive_no_writers(),
                                "Unexpected original lifecycle state");
                        wan_writer.unregister_instance(handle);
                    }
                }
            }
        }
        for (auto* reader : {&status_reader, &event_reader}) {
            for (const auto& sample : reader->take()) {
                auto found = std::find_if(bindings.begin(), bindings.end(), [&](const Binding& binding) {
                    return binding.incoming == sample.info().instance_handle();
                });
                if (sample.info().valid()) {
                    const auto topic = sample.data().value<std::string>("original_topic");
                    size_t index = 0;
                    while (index < legs.size() && legs[index]->name != topic) {
                        ++index;
                    }
                    require(index < legs.size(), "No original destination topic configured: " + topic);
                    auto& leg = *legs[index];
                    auto decoded = codec.unpack(sample.data());
                    require(decoded == leg.expected, "Entire decoded payload differs: " + topic);
                    require(sample.data() == codec.pack(topic, leg.expected, metadata(leg.rule, leg.expected)),
                            "Envelope metadata changed over WAN: " + topic);
                    leg.destination_writer.write(decoded);
                    const auto handle = leg.destination_writer.lookup_instance(decoded);
                    require(handle != Handle::nil(), "Missing restored original instance");
                    Binding binding{
                        index, sample.info().instance_handle(), handle, sample.data().value<std::string>("source_node"),
                        sample.data().value<std::string>("source_router"),
                        sample.data().value<std::string>("instance_id")};
                    if (found == bindings.end()) {
                        bindings.push_back(binding);
                    } else {
                        *found = binding;
                    }
                    ++wan_received;
                } else {
                    Data key(reader == &status_reader ? types->type("AdapterStatus") : types->type("AdapterEvent"));
                    reader->key_value(key, sample.info().instance_handle());
                    require(found != bindings.end(), "Lifecycle arrived without a previously decoded binding");
                    const auto binding = *found;
                    require(key.value<std::string>("source_node") == binding.node
                                && key.value<std::string>("source_router") == binding.router
                                && key.value<std::string>("instance_id") == binding.id,
                            "Recovered WAN lifecycle keys disagree with original binding");
                    auto& writer = legs[binding.leg]->destination_writer;
                    if (sample.info().state().instance_state() == State::not_alive_disposed()) {
                        writer.dispose_instance(binding.output);
                    } else {
                        require(sample.info().state().instance_state() == State::not_alive_no_writers(),
                                "Unexpected WAN lifecycle state");
                        writer.unregister_instance(binding.output);
                        bindings.erase(found);
                    }
                    ++lifecycle_out;
                }
            }
        }
        for (auto& pointer : legs) {
            auto& leg = *pointer;
            for (const auto& sample : leg.destination_reader.take()) {
                if (sample.info().valid()) {
                    require(sample.data() == leg.expected, "Original-topic payload changed: " + leg.name);
                    ++leg.received;
                } else {
                    Data key(types->type(leg.rule.type));
                    leg.destination_reader.key_value(key, sample.info().instance_handle());
                    require(key.value<std::string>(leg.rule.key) == leg.expected.value<std::string>(leg.rule.key),
                            "Restored lifecycle has wrong original key");
                    if (sample.info().state().instance_state() == State::not_alive_disposed()) {
                        ++leg.disposed;
                    } else {
                        require(sample.info().state().instance_state() == State::not_alive_no_writers(),
                                "Unexpected restored lifecycle state");
                        ++leg.unregistered;
                    }
                }
            }
        }
    };
    wait_until([&] {
        for (const auto& pointer : legs) {
            if (pointer->source_writer.publication_matched_status().current_count() != 1
                || pointer->destination_writer.publication_matched_status().current_count() != 1) {
                return false;
            }
        }
        for (auto& entry : wan_writers) {
            if (entry.second.publication_matched_status().current_count() != 1) {
                return false;
            }
        }
        return true;
    }, "all local and WAN endpoints matched");

    for (auto& pointer : legs) {
        pointer->source_writer.write(pointer->expected);
    }
    wait_until([&] {
        pump();
        for (const auto& pointer : legs) {
            if (pointer->received != 1) {
                return false;
            }
        }
        return true;
    }, "five original -> union -> original deliveries");
    require(wan_received == 5 && wan_writes == 5 && pushed() == 5,
            "A selected original sample did not produce exactly one WAN DATA and output");
    require(bindings.size() == 5, "Variant/topic instances collapsed into shared handles");
    const auto initial_bindings = bindings;
    std::cout << "PASS five exact round trips: navigation, health, command, ack, wildcard alias\n";
    std::cout << "PASS complete equality: 8192-byte payload, bounded strings, nested health sequences, metadata\n";
    std::cout << "PASS only Event/Status on WAN; three writers, two readers; no duplicate forwarding\n";

    const auto baseline = pushed();
    for (uint64_t i = 0; i < 20; ++i) {
        auto unwanted = fixture(types, legs[0]->rule, 300 + i);
        auto context = metadata(legs[0]->rule, unwanted);
        if (i % 2 == 0) {
            unwanted.value<float>("speed_knots", 1.0f);
        } else {
            context.destination = "OtherBoat";
        }
        wan_writers.at("wan_status").write(codec.pack(navigation, unwanted, context));
    }
    const auto quiet_until = std::chrono::steady_clock::now() + std::chrono::milliseconds(500);
    while (std::chrono::steady_clock::now() < quiet_until) {
        pump();
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    require(pushed() == baseline && wan_received == 5 && legs[0]->received == 1,
            "Rejected union content/audience was transmitted or decoded");
    legs[0]->expected = fixture(types, legs[0]->rule, 2);
    wan_writers.at("wan_status").write(codec.pack(navigation, legs[0]->expected,
                                                metadata(legs[0]->rule, legs[0]->expected)));
    wait_until([&] { pump(); return legs[0]->received == 2; }, "selected union after filtering");
    require(pushed() == baseline + 1 && wan_received == 6, "Selected union did not add exactly one WAN DATA");
    require(bindings.size() == 5, "Payload updates changed the instance set");
    std::cout << "PASS writer filtering: 20 unwanted unions -> 0 pushed DATA; selected union -> 1\n";

    for (auto& pointer : legs) {
        auto& leg = *pointer;
        auto handle = leg.source_writer.lookup_instance(leg.expected);
        require(handle != Handle::nil(), "Missing original dispose handle");
        const auto original_handle = handle;
        leg.source_writer.dispose_instance(handle);
        wait_until([&] { pump(); return leg.disposed == 1; }, "original -> union -> original dispose");
        leg.expected = fixture(types, leg.rule, 3);
        const int next_received = leg.received + 1;
        leg.source_writer.write(leg.expected);
        wait_until([&] { pump(); return leg.received == next_received; }, "revive original instance");
        handle = leg.source_writer.lookup_instance(leg.expected);
        require(handle != Handle::nil(), "Missing original unregister handle");
        require(handle == original_handle, "Revived original instance changed identity");
        const auto initial = std::find_if(initial_bindings.begin(), initial_bindings.end(),
                                         [&](const Binding& binding) { return legs[binding.leg].get() == &leg; });
        const auto current = std::find_if(bindings.begin(), bindings.end(),
                                         [&](const Binding& binding) { return legs[binding.leg].get() == &leg; });
        require(initial != initial_bindings.end() && current != bindings.end()
                    && initial->incoming == current->incoming && initial->output == current->output,
                "Revived WAN/restored instance changed identity");
        leg.source_writer.unregister_instance(handle);
        wait_until([&] { pump(); return leg.unregistered == 1; }, "original -> union -> original no-writers");
    }
    require(lifecycle_out == 10 && bindings.empty(), "Lifecycle forwarding or binding cleanup incomplete");
    std::cout << "COUNTERS valid WAN receives=" << wan_received
              << " original-derived WAN writes=" << wan_writes
              << " sender pushed samples=" << pushed()
              << " lifecycle outputs=" << lifecycle_out << '\n';
    require(wan_received == 11 && wan_writes == 10 && pushed() == wan_received + lifecycle_out,
            "Unexpected forwarding counts after lifecycle exercise");
    for (const auto& pointer : legs) {
        require(pointer->input_count == 2 && pointer->disposed == 1 && pointer->unregistered == 1,
                "Duplicate/missing original input or lifecycle state: " + pointer->name);
        require(pointer->received == (pointer->name == navigation ? 3 : 2),
                "Duplicate/missing original-topic output: " + pointer->name);
    }
    std::cout << "PASS five disposals and five NO_WRITERS states; key/handle recovery, no invalid union reads\n";
    std::cout << "PASS revive after dispose; variant/topic-qualified identities remain independent\n";
    std::cout << "PASS final counts: 10 original inputs + 1 selected filter probe = 11 WAN DATA/outputs; 0 live bindings\n";
    std::cout << "PASS sender counter accounting: 11 valid + 10 lifecycle = 21 pushed DDS samples\n";
    return 0;
}

int main(int argc, char** argv)
{
    try {
        require(argc == 4, "Usage: union_adapter TYPES.xml QOS.xml mapping.tsv");
        return run(argv[1], argv[2], argv[3]);
    } catch (const std::exception& error) {
        std::cout << "FAIL " << error.what() << std::endl;
        std::cerr << "Adapter spike failed: " << error.what() << std::endl;
        return 1;
    }
}
