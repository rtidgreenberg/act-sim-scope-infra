#include "probe_support.hpp"
#include <rti/config/Logger.hpp>
#include <chrono>
#include <functional>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>

struct Observation {
    int64_t samples, alive, disposed, detached;
    bool lookup, key;
    double measured_ms;
};

Observation inspect(Reader& reader, const Data& expected, const Handle& handle,
                    const std::string& label, Clock::time_point disposed_at)
{
    const auto cache = reader->datareader_cache_status();
    Observation result{cache.sample_count(), cache.alive_instance_count(),
                       cache.disposed_instance_count(), cache.detached_instance_count(),
                       reader.lookup_instance(expected) != dds::core::null, false, ms(disposed_at)};
    try {
        Data key(expected.type());
        reader.key_value(key, handle);
        result.key = key.value<std::string>("source") == expected.value<std::string>("source")
            && key.value<std::string>("contact_id") == expected.value<std::string>("contact_id");
    } catch (const std::exception&) {
        // A purged handle is expected only in the isolated zero-delay controls.
    }
    std::cout << "{\"event\":\"observation\",\"phase\":" << quote(label)
              << ",\"since_dispose_ms\":" << result.measured_ms
              << ",\"sample_count\":" << result.samples
              << ",\"alive_instance_count\":" << result.alive
              << ",\"disposed_instance_count\":" << result.disposed
              << ",\"detached_instance_count\":" << result.detached
              << ",\"lookup_known\":" << (result.lookup ? "true" : "false")
              << ",\"authoritative_key_available\":" << (result.key ? "true" : "false")
              << "}" << std::endl;
    return result;
}

int main(int argc, char** argv)
{
    std::string phase = "arguments";
    try {
        require(argc == 4 || argc == 5,
                "usage: timing merged-qos.xml profile trial [delayed-control-directory]");
        rti::config::Logger::instance().output_file(stderr);
        std::cout << std::setprecision(12);
        const std::string profile = argv[2];
        const int trial = std::stoi(argv[3]);
        const bool delayed = argc == 5;
        const bool zero = profile == "zero" || profile == "zero_forget";
        const int holds[] = {250, 750, 1500};
        const int hold_ms = holds[trial % 3];
        phase = "provider";
        dds::core::QosProvider qos(argv[1]);
        const auto type = qos->type("contact_report");
        auto rqos = qos.datareader_qos("ContactDisposal::" + profile);
        const auto lifecycle = rqos.policy<dds::core::policy::ReaderDataLifecycle>();
        require(lifecycle->autopurge_disposed_instances_delay()
                    == (zero ? dds::core::Duration::zero()
                             : profile == "finite_2" ? dds::core::Duration(2)
                                                      : dds::core::Duration::infinite()),
                "effective instance delay differs from intended XML");
        std::cout << "{\"event\":\"qos\",\"profile\":" << quote(profile)
                  << ",\"disposed_instance_sec\":" << lifecycle->autopurge_disposed_instances_delay().sec()
                  << ",\"disposed_sample_sec\":" << lifecycle.autopurge_disposed_samples_delay().sec()
                  << ",\"keep_minimum_state\":"
                  << (rqos.policy<rti::core::policy::DataReaderResourceLimits>()
                              .keep_minimum_state_for_instances() ? "true" : "false")
                  << "}" << std::endl;
        dds::domain::DomainParticipant platform(211, qos.participant_qos("ContactDisposal::probe"));
        dds::domain::DomainParticipant c2(213, qos.participant_qos("ContactDisposal::probe"));
        dds::topic::Topic<Data> input(platform, "ContactReport", type);
        dds::topic::Topic<Data> output(c2, "ContactReport", type);
        dds::pub::Publisher publisher(platform);
        dds::sub::Subscriber subscriber(c2);
        phase = "reader_create";
        Reader reader(subscriber, output, rqos);
        require(profile != "finite_2", "finite nonzero instance delay unexpectedly accepted");
        Writer writer(publisher, input, qos.datawriter_qos(
            "ContactDisposal::" + (delayed ? profile : std::string("probe"))));
        if (delayed) {
            const auto cleanup = writer.qos().policy<dds::core::policy::WriterDataLifecycle>()
                ->autopurge_disposed_instances_delay();
            require(cleanup.sec() == (profile == "delay_ten" ? 10 : 0),
                    "effective source writer cleanup differs from requested value");
            require(lifecycle.autopurge_disposed_samples_delay() == dds::core::Duration::infinite(),
                    "delayed test must retain unread C2 disposal notifications");
        }
        phase = "discovery";
        wait_for([&] { return writer.publication_matched_status().current_count() == 1
                          && reader.subscription_matched_status().current_count() == 1; },
                 20, "platform input and C2 output match exactly once");
        Data sample = contact(type, trial);
        Data expected(sample);
        expected.value<std::string>("source", "Platform_30");
        Handle received = dds::core::null;
        const auto source_handle = writer.register_instance(sample);
        phase = "materialize";
        auto next_write = Clock::now();
        wait_for([&] {
            if (Clock::now() >= next_write) {
                writer.write(sample);
                next_write = Clock::now() + std::chrono::milliseconds(100);
            }
            const auto samples = reader.take();
            for (const auto& item : samples) {
                if (!item.info().valid()) continue;
                require(item.data() == expected, "complete original payload/authoritative source mismatch");
                require(item.info().state().instance_state() == State::alive(), "initial sample not ALIVE");
                received = item.info().instance_handle();
                return true;
            }
            return false;
        }, 20, "complete ContactReport reaches C2");
        // Drain repeated materialization writes before starting the one-dispose clock.
        writer.wait_for_acknowledgments(dds::core::Duration(5));
        std::this_thread::sleep_for(std::chrono::milliseconds(150));
        {
            const auto pending = reader.take();
            for (const auto& item : pending)
                require(item.info().valid() && item.data() == expected, "unexpected pre-dispose sample");
        }
        require(reader.lookup_instance(expected) == received, "authoritative two-field key lookup mismatch");
        Data wrong(expected);
        wrong.value<std::string>("source", "deliberately-wrong-producer");
        require(reader.lookup_instance(wrong) == dds::core::null, "producer source leaked into C2 key");
        if (delayed) {
            std::ofstream(std::string(argv[4]) + "/ready") << "ready\n";
            wait_for([&] { return std::ifstream(std::string(argv[4]) + "/go").good(); },
                     30, "WAN impairment configured");
        }
        phase = "dispose";
        const auto disposed_at = Clock::now();
        writer.dispose_instance(source_handle);
        const double dispose_call_ms = ms(disposed_at);
        if (delayed) {
            std::cout << "{\"event\":\"dispose\",\"monotonic_ns\":"
                      << std::chrono::duration_cast<std::chrono::nanoseconds>(
                             disposed_at.time_since_epoch()).count()
                      << "}" << std::endl;
        }
        double arrival_ms = -1;
        wait_for([&] {
            const auto samples = reader.read();
            for (const auto& item : samples) {
                require(!item.info().valid(), "unexpected valid data after dispose barrier");
                require(item.info().instance_handle() == received, "dispose changed C2 instance handle");
                require(item.info().state().instance_state() == State::not_alive_disposed(),
                        "C2 lifecycle is not DISPOSED");
                arrival_ms = ms(disposed_at);
                return true;
            }
            return false;
        }, delayed ? 40 : 5, "invalid C2 DISPOSED sample");
        if (delayed) {
            const auto observed = inspect(reader, expected, received,
                                          "delayed_invalid_disposed_received", disposed_at);
            require(observed.disposed == 1 && observed.key && observed.lookup,
                    "C2 dispose/key missing after impaired WAN");
            double source_purged_ms = -1;
            wait_for([&] {
                const auto cache = writer->datawriter_cache_status();
                const auto elapsed = ms(disposed_at);
                std::cout << "{\"event\":\"source_writer_cache\",\"since_dispose_ms\":"
                          << elapsed << ",\"disposed\":" << cache.disposed_instance_count()
                          << ",\"alive\":" << cache.alive_instance_count() << "}" << std::endl;
                if (cache.disposed_instance_count() == 0
                        && cache.alive_instance_count() == 0 && source_purged_ms < 0)
                    source_purged_ms = elapsed;
                if (profile == "delay_ten" && elapsed < 9900)
                    require(cache.disposed_instance_count() == 1,
                            "source writer reclaimed before ten-second retention");
                const bool complete = elapsed >= 12000 && source_purged_ms >= 0;
                if (!complete) std::this_thread::sleep_for(std::chrono::milliseconds(250));
                return complete;
            }, 25, "source writer cleanup after retained disposal");
            {
                const auto samples = reader.take();
                require(samples.length() >= 1, "retained C2 dispose disappeared");
                for (const auto& item : samples)
                    require(!item.info().valid()
                                && item.info().state().instance_state() == State::not_alive_disposed(),
                            "retained C2 sample is not DISPOSED");
            }
            std::cout << "{\"event\":\"result\",\"status\":\"PASS\",\"profile\":"
                      << quote(profile) << ",\"dispose_to_c2_invalid_ms\":" << arrival_ms
                      << ",\"source_first_purged_observation_ms\":" << source_purged_ms
                      << ",\"payload_all_fields_equal\":true,\"authoritative_source_and_key\":true"
                      << "}" << std::endl;
            platform.close();
            c2.close();
            return 0;
        }
        auto observed_at = Clock::now();
        inspect(reader, expected, received, "invalid_disposed_received", disposed_at);
        std::this_thread::sleep_until(observed_at + std::chrono::milliseconds(hold_ms));
        const auto before = inspect(reader, expected, received, "before_take", disposed_at);
        require(before.disposed == 1 && before.key && before.lookup,
                "disposed instance vanished while notification still queued");
        phase = "take_and_return_loan";
        double last_retained_ms = before.measured_ms;
        {
            const auto samples = reader.take();
            require(samples.length() >= 1, "dispose notification missing at take");
            for (const auto& item : samples) {
                require(!item.info().valid()
                            && item.info().state().instance_state() == State::not_alive_disposed()
                            && item.info().instance_handle() == received,
                        "wrong invalid sample/key at take");
            }
            const auto loan_start = Clock::now();
            std::this_thread::sleep_until(loan_start + std::chrono::milliseconds(200));
            const auto held = inspect(reader, expected, received, "take_loan_held_200ms", disposed_at);
            require(held.disposed == 1 && held.key && held.lookup,
                    "disposed active state/key lost before take loan returned");
            last_retained_ms = held.measured_ms;
        }
        const auto released_at = Clock::now();
        const double release_ms = ms(disposed_at);
        auto after = inspect(reader, expected, received, "after_return_loan", disposed_at);
        double first_purged_ms = -1;
        phase = "retention_or_purge";
        if (zero) {
            if (after.disposed == 0) {
                first_purged_ms = after.measured_ms;
            } else {
                last_retained_ms = after.measured_ms;
                wait_for([&] {
                    const auto cache = reader->datareader_cache_status();
                    if (cache.disposed_instance_count() == 0) {
                        first_purged_ms = ms(disposed_at);
                        return true;
                    }
                    last_retained_ms = ms(disposed_at);
                    return false;
                }, 2, "zero-delay active disposed instance purge");
            }
            after = inspect(reader, expected, received, "zero_active_state_purged", disposed_at);
            require(after.samples == 0 && after.alive == 0 && after.disposed == 0,
                    "zero-delay cache active state not purged");
            if (profile == "zero_forget")
                require(after.detached == 0 && !after.lookup && !after.key,
                        "minimum-state disabled but native instance still resolvable");
            else
                require(after.detached == 1 && !after.lookup && !after.key,
                        "zero-delay default minimum-state control differs from expected detached state");
            std::this_thread::sleep_until(released_at + std::chrono::milliseconds(500));
            inspect(reader, expected, received, "zero_after_500ms", disposed_at);
        } else {
            require(after.samples == 0 && after.disposed == 1 && after.key && after.lookup,
                    "take emptied samples but did not retain disposed instance");
            for (int delay : {100, 1000, 2500, 5500}) {
                std::this_thread::sleep_until(released_at + std::chrono::milliseconds(delay));
                const auto retained = inspect(reader, expected, received, "retained_" + std::to_string(delay),
                                              disposed_at);
                require(retained.samples == 0 && retained.disposed == 1
                            && retained.key && retained.lookup, "infinite-retention instance disappeared");
                last_retained_ms = retained.measured_ms;
            }
        }
        std::cout << "{\"event\":\"result\",\"status\":\"PASS\",\"profile\":" << quote(profile)
                  << ",\"trial\":" << trial << ",\"notification_hold_ms\":" << hold_ms
                  << ",\"take_loan_hold_ms\":200,\"dispose_call_ms\":" << dispose_call_ms
                  << ",\"dispose_to_c2_invalid_ms\":" << arrival_ms
                  << ",\"dispose_to_loan_release_ms\":" << release_ms
                  << ",\"last_retained_since_dispose_ms\":" << last_retained_ms
                  << ",\"first_active_purge_since_dispose_ms\":" << first_purged_ms
                  << ",\"first_active_purge_after_return_ms\":"
                  << (zero ? first_purged_ms - release_ms : -1)
                  << ",\"payload_all_fields_equal\":true,\"authoritative_source_and_key\":true"
                  << ",\"contact_id\":" << quote(sample.value<std::string>("contact_id"))
                  << ",\"producer_source\":" << quote(sample.value<std::string>("source"))
                  << ",\"c2_key_source\":\"Platform_30\",\"original_source_node_preserved\":true"
                  << ",\"retention\":\"" << (zero ? "zero_control" : "right_censored_no_purge")
                  << "\"}" << std::endl;
        platform.close();
        c2.close();
        return 0;
    } catch (const std::exception& error) {
        const bool expected_rejection = argc == 4 && std::string(argv[2]) == "finite_2"
            && phase == "reader_create"
            && std::string(error.what()) == "Failed to create DataReader";
        std::cout << "{\"event\":\"result\",\"status\":\""
                  << (expected_rejection ? "EXPECTED_REJECTION" : "FAIL")
                  << "\",\"phase\":" << quote(phase) << ",\"error\":" << quote(error.what())
                  << "}" << std::endl;
        return expected_rejection ? 0 : 1;
    }
}
