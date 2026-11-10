#include <dds/dds.hpp>
#include <rti/config/Logger.hpp>

#include <chrono>
#include <cstdio>
#include <cstdint>
#include <functional>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using Data = dds::core::xtypes::DynamicData;
using Reader = dds::sub::DataReader<Data>;
using Writer = dds::pub::DataWriter<Data>;
using Handle = dds::core::InstanceHandle;
using State = dds::sub::status::InstanceState;
using Clock = std::chrono::steady_clock;

std::string json(const std::string& value)
{
    std::ostringstream result;
    result << '"';
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') {
            result << '\\' << c;
        } else if (c < 32) {
            result << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c);
        } else {
            result << c;
        }
    }
    result << '"';
    return result.str();
}

void require(bool condition, const std::string& message)
{
    if (!condition) throw std::runtime_error(message);
}

void wait_for(const std::function<bool()>& ready, const std::string& description)
{
    const auto deadline = Clock::now() + std::chrono::seconds(8);
    while (Clock::now() < deadline) {
        if (ready()) return;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    throw std::runtime_error("Timeout: " + description);
}

struct Probe {
    std::string phase;
    int64_t elapsed_ms;
    bool ok;
    std::string key;
    std::string error;
};

int main(int argc, char** argv)
{
    if (argc != 5) {
        std::cerr << "Usage: reader_instance_purge qos.xml profile operation mode\n";
        return 2;
    }
    const std::string profile = argv[2], operation = argv[3], mode = argv[4];
    std::string phase = "arguments", status = "failed", error;
    std::vector<Probe> probes;
    bool relayed = false, destination_state = false, revived = false;
    bool revival_same_handle = false;
    try {
        rti::config::Logger::instance().output_file(stderr);
        require(operation == "dispose" || operation == "unregister" || operation == "qos_only",
                "Unknown operation");
        require(mode == "take" || mode == "read_then_take" || mode == "hold_take",
                "Unknown mode");
        phase = "provider_load";
        dds::core::QosProvider provider(argv[1]);
        phase = "profile_load";
        const auto reader_qos = provider.datareader_qos("Purge::" + profile);
        const auto participant_qos = provider.participant_qos("Purge::base");
        const auto writer_qos = provider.datawriter_qos("Purge::base");
        const auto type = provider->type("PurgeSample");
        phase = "participant_create";
        dds::domain::DomainParticipant producer(210, participant_qos);
        dds::domain::DomainParticipant input(210, participant_qos);
        dds::domain::DomainParticipant output(211, participant_qos);
        dds::domain::DomainParticipant consumer(211, participant_qos);
        dds::topic::Topic<Data> source_topic(producer, "PurgeInput", type);
        dds::topic::Topic<Data> input_topic(input, "PurgeInput", type);
        dds::topic::Topic<Data> output_topic(output, "PurgeOutput", type);
        dds::topic::Topic<Data> destination_topic(consumer, "PurgeOutput", type);
        dds::pub::Publisher producer_pub(producer), output_pub(output);
        dds::sub::Subscriber input_sub(input), consumer_sub(consumer);
        phase = "reader_create";
        Reader reader(input_sub, input_topic, reader_qos);
        phase = "endpoint_create";
        Writer source(producer_pub, source_topic, writer_qos);
        Writer destination(output_pub, output_topic, writer_qos);
        Reader observer(consumer_sub, destination_topic,
                        provider.datareader_qos("Purge::base"));
        if (operation != "qos_only") {
            phase = "discovery";
            wait_for([&] {
                return source.publication_matched_status().current_count() == 1
                    && destination.publication_matched_status().current_count() == 1;
            }, "both independent UDPv4 legs match");
            const std::string expected_key =
                "a-long-key-that-cannot-be-reconstructed-from-sixteen-handle-bytes";
            Data sample(type);
            sample.value<std::string>("id", expected_key);
            sample.value<int32_t>("sequence", 1);
            Handle input_handle = dds::core::null;
            phase = "initial_data";
            source.write(sample);
            wait_for([&] {
                const auto samples = reader.take();
                for (const auto& item : samples) {
                    if (!item.info().valid()) continue;
                    require(item.data().value<std::string>("id") == expected_key, "Wrong input key");
                    input_handle = item.info().instance_handle();
                    destination.write(item.data());
                    return true;
                }
                return false;
            }, "initial input data");
            Handle observer_handle = dds::core::null;
            wait_for([&] {
                const auto samples = observer.take();
                for (const auto& item : samples) {
                    if (!item.info().valid()) continue;
                    require(item.data().value<std::string>("id") == expected_key, "Wrong output key");
                    observer_handle = item.info().instance_handle();
                    return true;
                }
                return false;
            }, "initial destination data");
            const auto state = operation == "dispose"
                ? State::not_alive_disposed() : State::not_alive_no_writers();
            phase = "source_lifecycle";
            const auto source_handle = source.lookup_instance(sample);
            require(source_handle != dds::core::null, "Missing source registration");
            if (operation == "dispose") source.dispose_instance(source_handle);
            else source.unregister_instance(source_handle);

            auto taken_at = Clock::now();
            const auto probe = [&](const std::string& label, bool allow_relay) {
                Probe observation{label, std::chrono::duration_cast<std::chrono::milliseconds>(
                    Clock::now() - taken_at).count(), false, "", ""};
                Data key(type);
                try {
                    reader.key_value(key, input_handle);
                    observation.key = key.value<std::string>("id");
                    require(observation.key == expected_key, "Lookup returned the wrong key");
                    observation.ok = true;
                } catch (const std::exception& e) {
                    observation.error = e.what();
                }
                probes.push_back(observation);
                if (observation.ok && allow_relay && !relayed) {
                    const auto handle = destination.lookup_instance(key);
                    require(handle != dds::core::null, "Missing destination registration");
                    if (operation == "dispose") destination.dispose_instance(handle);
                    else destination.unregister_instance(handle);
                    relayed = true;
                }
            };
            const auto find_notification = [&](bool read) {
                const auto samples = read ? reader.read() : reader.take();
                for (const auto& item : samples) {
                    if (item.info().valid() || item.info().state().instance_state() != state) continue;
                    require(item.info().instance_handle() == input_handle,
                            "Lifecycle instance handle changed");
                    if (!read) taken_at = Clock::now();
                    probe(read ? "read_loan" : "take_loan", true);
                    if (!read && mode == "hold_take") {
                        std::this_thread::sleep_until(taken_at + std::chrono::milliseconds(2100));
                        probe("take_loan_after_2100ms", false);
                    }
                    return true;
                }
                return false;
            };
            phase = "input_lifecycle";
            if (mode == "read_then_take") {
                wait_for([&] { return find_notification(true); }, "read lifecycle");
            }
            wait_for([&] { return find_notification(false); }, "take lifecycle");
            probe("after_return_loan", false);
            for (int delay : {100, 1000, 2500, 5500}) {
                if (mode == "hold_take" && delay < 2100) continue;
                std::this_thread::sleep_until(taken_at + std::chrono::milliseconds(delay));
                probe("after_" + std::to_string(delay) + "ms", false);
            }
            phase = "destination_lifecycle";
            if (relayed) {
                wait_for([&] {
                    const auto samples = observer.take();
                    for (const auto& item : samples) {
                        if (!item.info().valid()
                                && item.info().instance_handle() == observer_handle
                                && item.info().state().instance_state() == state) {
                            destination_state = true;
                            return true;
                        }
                    }
                    return false;
                }, "native destination lifecycle");
            }
            phase = "revival";
            sample.value<int32_t>("sequence", 2);
            source.write(sample);
            wait_for([&] {
                const auto samples = reader.take();
                for (const auto& item : samples) {
                    if (!item.info().valid()) continue;
                    require(item.info().state().instance_state() == State::alive(), "Not ALIVE");
                    require(item.data().value<std::string>("id") == expected_key, "Wrong revival key");
                    revival_same_handle = item.info().instance_handle() == input_handle;
                    destination.write(item.data());
                    return true;
                }
                return false;
            }, "input revival");
            wait_for([&] {
                const auto samples = observer.take();
                for (const auto& item : samples) {
                    if (item.info().valid() && item.data().value<int32_t>("sequence") == 2) {
                        require(item.info().state().instance_state() == State::alive(),
                                "Destination revival not ALIVE");
                        revived = true;
                        return true;
                    }
                }
                return false;
            }, "destination revival");
        }
        phase = "cleanup";
        producer.close();
        input.close();
        output.close();
        consumer.close();
        status = "completed";
    } catch (const std::exception& e) {
        error = e.what();
        if (phase == "provider_load" || phase == "profile_load" || phase == "reader_create") {
            status = "rejected";
        }
    }
    std::cout << "RESULT {\"profile\":" << json(profile)
              << ",\"operation\":" << json(operation) << ",\"mode\":" << json(mode)
              << ",\"status\":" << json(status) << ",\"phase\":" << json(phase)
              << ",\"error\":" << json(error) << ",\"relayed\":" << (relayed ? "true" : "false")
              << ",\"destination_state\":" << (destination_state ? "true" : "false")
              << ",\"revived\":" << (revived ? "true" : "false")
              << ",\"revival_same_handle\":" << (revival_same_handle ? "true" : "false")
              << ",\"probes\":[";
    bool first = true;
    for (const auto& probe : probes) {
        if (!first) std::cout << ',';
        first = false;
        std::cout << "{\"phase\":" << json(probe.phase) << ",\"elapsed_ms\":" << probe.elapsed_ms
                  << ",\"ok\":" << (probe.ok ? "true" : "false")
                  << ",\"key\":" << json(probe.key) << ",\"error\":" << json(probe.error) << '}';
    }
    std::cout << "]}" << std::endl;
    return status == "failed" ? 1 : 0;
}
