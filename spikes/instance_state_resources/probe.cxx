#include "probe_support.hpp"
#include <rti/config/Logger.hpp>
#include <algorithm>
#include <iostream>

int main(int argc, char **argv)
{
    try {
        require(argc == 4, "usage: resources qos.xml profile mode");
        rti::config::Logger::instance().output_file(stderr);
        std::cout << std::setprecision(12);
        const std::string profile = argv[2], mode = argv[3];
        const bool churn = mode == "churn";
        const bool invalid_only = mode == "invalid_only";
        dds::core::QosProvider qos(argv[1]);
        const auto type = qos->type("contact_report");
        dds::domain::DomainParticipant producer(218, qos.participant_qos("ResourceState::participant"));
        dds::domain::DomainParticipant consumer(218, qos.participant_qos("ResourceState::participant"));
        dds::topic::Topic<Data> input(producer, "ContactReport", type);
        dds::topic::Topic<Data> output(consumer, "ContactReport", type);
        dds::pub::Publisher pub(producer);
        dds::sub::Subscriber sub(consumer);
        Reader reader(sub, output, qos.datareader_qos("ResourceState::" + profile));
        Writer writer(pub, input, qos.datawriter_qos("ResourceState::writer"));
        wait_for([&] { return writer.publication_matched_status().current_count() == 1
                          && reader.subscription_matched_status().current_count() == 1; },
                 15, "native reader/writer match");
        const auto take_valid = [&](const Data& expected) {
            bool received = false;
            writer.write(expected);
            wait_for([&] {
                const auto samples = reader.take();
                for (const auto& item : samples) {
                    if (!item.info().valid()) continue;
                    require(item.data() == expected, "full native contact payload mismatch");
                    received = true;
                }
                return received;
            }, 5, "native contact payload");
        };
        Data sentinel = contact(type, -1);
        take_valid(sentinel);
        require(reader.lookup_instance(sentinel) != dds::core::null, "sentinel instance missing");
        if (churn) {
            int disposed_received = 0;
            int64_t max_attached = 0, max_detached = 0, max_total = 0, max_samples = 0;
            for (int i = 0; i < 2000; ++i) {
                Data sample = contact(type, i);
                take_valid(sample);
                writer.dispose_instance(writer.lookup_instance(sample));
                bool disposed = false;
                wait_for([&] {
                    const auto samples = reader.take();
                    for (const auto& item : samples) {
                        if (item.info().state().instance_state() != State::not_alive_disposed()) continue;
                        require(!item.info().valid(), "expected invalid churn disposal notification");
                        Data key(type);
                        reader.key_value(key, item.info().instance_handle());
                        require(key.value<std::string>("contact_id") == sample.value<std::string>("contact_id"),
                                "dispose key differs from churn contact");
                        require(key.value<std::string>("source") == sample.value<std::string>("source"),
                                "dispose source differs from churn contact");
                        disposed = true;
                    }
                    return disposed;
                }, 5, "every churn contact disposed");
                ++disposed_received;
                if ((i + 1) % 32 == 0) writer.wait_for_acknowledgments(dds::core::Duration(5));
                const auto cache = reader->datareader_cache_status();
                const int64_t attached = cache.alive_instance_count() + cache.disposed_instance_count();
                max_attached = std::max(max_attached, attached);
                max_detached = std::max(max_detached, cache.detached_instance_count());
                max_total = std::max(max_total, attached + cache.detached_instance_count());
                max_samples = std::max(max_samples, cache.sample_count());
                require(attached <= 4 && attached + cache.detached_instance_count() <= 8,
                        "native instance limits exceeded");
                require(reader.lookup_instance(sentinel) != dds::core::null,
                        "alive sentinel evicted by disposed-instance replacement");
                if ((i + 1) % 500 == 0) std::cerr << "processed_contacts=" << i + 1 << std::endl;
            }
            require(disposed_received == 2000, "missing churn dispose notifications");
            std::cout << "{\"status\":\"PASS\",\"profile\":" << quote(profile)
                      << ",\"mode\":\"churn\",\"valid_contacts\":2000,\"disposals\":2000"
                      << ",\"max_attached\":" << max_attached << ",\"max_detached\":" << max_detached
                      << ",\"max_total\":" << max_total << ",\"max_samples\":" << max_samples
                      << ",\"alive_sentinel_preserved\":true"
                      << ",\"sample_lost\":" << reader.sample_lost_status().total_count()
                      << ",\"sample_rejected\":" << reader.sample_rejected_status().total_count()
                      << "}" << std::endl;
        } else {
            Data target = contact(type, 5), pressure = contact(type, 6);
            if (invalid_only) {
                take_valid(target);
            } else {
                writer.write(target);
                wait_for([&] { return reader->datareader_cache_status().alive_instance_count() == 2; },
                         5, "unread target queued");
            }
            writer.dispose_instance(writer.lookup_instance(target));
            wait_for([&] { return reader->datareader_cache_status().disposed_instance_count() == 1; },
                     5, "target native disposed state");
            const auto start = Clock::now();
            bool held_loan_blocked_replacement = false;
            if (mode == "read_returned" || mode == "read_held") {
                const auto loan = reader.read();
                require(loan.length() >= 1, "read-only processing control has no target samples");
                if (mode == "read_held") {
                    writer.write(pressure);
                    std::this_thread::sleep_for(std::chrono::milliseconds(300));
                    held_loan_blocked_replacement = reader.lookup_instance(pressure) == dds::core::null;
                    require(held_loan_blocked_replacement, "outstanding read loan did not protect instance");
                }
            }
            const double hold_seconds = mode == "before_expiry" ? 8.5
                : ((mode == "read_returned" || mode == "read_held") ? 0.5 : 11.0);
            std::this_thread::sleep_for(std::chrono::milliseconds(int(hold_seconds * 1000)));
            const auto before = reader->datareader_cache_status();
            writer.write(pressure);
            std::this_thread::sleep_for(std::chrono::seconds(1));
            const auto pressured = reader->datareader_cache_status();
            const bool target_known_before_take = reader.lookup_instance(target) != dds::core::null;
            const bool pressure_known_before_take = reader.lookup_instance(pressure) != dds::core::null;
            bool target_dispose_available = false, pressure_available = false;
            int target_valid_samples = 0, target_invalid_samples = 0;
            {
                const auto samples = reader.take();
                for (const auto& item : samples) {
                    Data key(type);
                    reader.key_value(key, item.info().instance_handle());
                    const auto id = key.value<std::string>("contact_id");
                    if (id == target.value<std::string>("contact_id")) {
                        if (item.info().valid()) ++target_valid_samples;
                        else ++target_invalid_samples;
                        if (item.info().state().instance_state() == State::not_alive_disposed())
                            target_dispose_available = true;
                    }
                    if (item.info().valid() && item.data() == pressure) pressure_available = true;
                }
            }
            if ((profile == "slow_infinite" && mode != "read_returned" && mode != "read_held")
                    || mode == "before_expiry")
                require(target_dispose_available, "dispose lost before permitted expiry");
            if (mode == "read_returned" || mode == "read_held")
                require(pressure_known_before_take && !target_known_before_take,
                        "read and returned loan did not permit fully-processed replacement");
            if (profile == "slow_ten" && (mode == "unread" || mode == "invalid_only")) {
                require(before.expired_dropped_sample_count() == 0,
                        "sample expiry occurred without pressure");
                require(pressured.expired_dropped_sample_count() == (invalid_only ? 1 : 2),
                        "ten-second pressure expiry did not account for every target sample");
                require(!target_dispose_available && !target_known_before_take && pressure_available,
                        "expired target was not replaced by incoming key");
            }
            require(reader.lookup_instance(sentinel) != dds::core::null, "alive sentinel evicted");
            if (!pressure_available) take_valid(pressure);
            require(reader.lookup_instance(sentinel) != dds::core::null,
                    "alive sentinel evicted while reusing processed target slot");
            const int lost_before_alive_pressure = reader.sample_lost_status().total_count();
            writer.write(contact(type, 7));
            std::this_thread::sleep_for(std::chrono::milliseconds(300));
            require(reader.lookup_instance(contact(type, 7)) == dds::core::null
                        && reader.lookup_instance(sentinel) != dds::core::null
                        && reader.lookup_instance(pressure) != dds::core::null,
                    "all-alive capacity pressure evicted an existing key");
            require(reader.sample_lost_status().total_count() == lost_before_alive_pressure + 1,
                    "all-alive admission failure was not reported as sample loss");
            const auto cache = reader->datareader_cache_status();
            std::cout << "{\"status\":\"PASS\",\"profile\":" << quote(profile)
                      << ",\"mode\":" << quote(mode) << ",\"hold_seconds\":" << hold_seconds
                      << ",\"observed_ms\":" << ms(start)
                      << ",\"target_dispose_available\":" << (target_dispose_available ? "true" : "false")
                      << ",\"target_valid_samples\":" << target_valid_samples
                      << ",\"target_invalid_samples\":" << target_invalid_samples
                      << ",\"held_loan_blocked_replacement\":"
                      << (held_loan_blocked_replacement ? "true" : "false")
                      << ",\"target_known_before_take\":" << (target_known_before_take ? "true" : "false")
                      << ",\"pressure_admitted_before_take\":" << (pressure_known_before_take ? "true" : "false")
                      << ",\"pressure_available_before_take\":" << (pressure_available ? "true" : "false")
                      << ",\"expired_dropped_before_pressure\":" << before.expired_dropped_sample_count()
                      << ",\"expired_dropped_after_pressure\":" << pressured.expired_dropped_sample_count()
                      << ",\"sample_lost\":" << reader.sample_lost_status().total_count()
                      << ",\"sample_lost_before_all_alive_pressure\":" << lost_before_alive_pressure
                      << ",\"all_alive_admission_rejected\":true"
                      << ",\"sample_rejected\":" << reader.sample_rejected_status().total_count()
                      << ",\"final_alive\":" << cache.alive_instance_count()
                      << ",\"alive_sentinel_preserved\":true}" << std::endl;
        }
        producer.close();
        consumer.close();
        return 0;
    } catch (const std::exception& error) {
        std::cout << "{\"status\":\"FAIL\",\"error\":" << quote(error.what()) << "}" << std::endl;
        return 1;
    }
}
