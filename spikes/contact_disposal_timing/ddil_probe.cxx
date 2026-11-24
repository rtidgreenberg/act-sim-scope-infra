#include "probe_support.hpp"
#include <rti/config/Logger.hpp>
#include <fstream>
#include <iostream>

int main(int argc, char **argv)
{
    try {
        require(argc == 5, "usage: ddil_probe qos.xml control-directory scenario observation-seconds");
        rti::config::Logger::instance().output_file(stderr);
        std::cout << std::setprecision(12);
        const std::string directory = argv[2], scenario = argv[3];
        const int observation_seconds = std::stoi(argv[4]);
        const auto exists = [&](const std::string& name) {
            return std::ifstream(directory + "/" + name).good();
        };
        dds::core::QosProvider qos(argv[1]);
        const auto type = qos->type("contact_report");
        dds::domain::DomainParticipant platform(211, qos.participant_qos("ContactDisposal::probe"));
        dds::domain::DomainParticipant c2(213, qos.participant_qos("ContactDisposal::probe"));
        dds::topic::Topic<Data> input(platform, "ContactReport", type);
        dds::topic::Topic<Data> output(c2, "ContactReport", type);
        dds::pub::Publisher publisher(platform);
        dds::sub::Subscriber subscriber(c2);
        const auto reader_qos = qos.datareader_qos("ContactDisposal::delay_ten");
        Reader reader(subscriber, output, reader_qos);
        Writer writer(publisher, input, qos.datawriter_qos("ContactDisposal::delay_ten"));
        wait_for([&] { return writer.publication_matched_status().current_count() == 1
                          && reader.subscription_matched_status().current_count() == 1; },
                 25, "application LAN discovery");
        Data original = contact(type, 0), expected(original);
        expected.value<std::string>("source", "Platform_30");
        const auto handle = writer.register_instance(original);
        auto next_write = Clock::now();
        wait_for([&] {
            if (Clock::now() >= next_write) {
                writer.write(original);
                next_write = Clock::now() + std::chrono::milliseconds(100);
            }
            const auto samples = reader.take();
            for (const auto& item : samples) {
                if (!item.info().valid()) continue;
                require(item.data() == expected, "initial full ContactReport mismatch");
                return true;
            }
            return false;
        }, 25, "initial contact reaches C2");
        writer.wait_for_acknowledgments(dds::core::Duration(5));
        std::this_thread::sleep_for(std::chrono::milliseconds(150));
        { const auto pending = reader.take(); (void) pending; }
        std::ofstream(directory + "/ready") << "ready\n";
        wait_for([&] { return exists("go"); }, 30, "WAN impairment ready");
        const auto disposed_at = Clock::now();
        const auto stamp = std::chrono::duration_cast<std::chrono::nanoseconds>(
            disposed_at.time_since_epoch()).count();
        std::cout << "{\"event\":\"dispose\",\"monotonic_ns\":" << stamp << "}" << std::endl;
        writer.dispose_instance(handle);
        const bool revive = scenario == "revive" || scenario == "revive_dispose";
        Data updated(original), updated_expected(expected), canary(original);
        updated.value<std::string>("classification", "REVIVED");
        updated.value<uint64_t>("audit_sequence", 2234567890123ULL);
        updated_expected.value<std::string>("classification", "REVIVED");
        updated_expected.value<uint64_t>("audit_sequence", 2234567890123ULL);
        canary.value<std::string>("contact_id", "recovery-canary");
        canary.value<std::string>("classification", "CANARY");
        Data canary_expected(canary);
        canary_expected.value<std::string>("source", "Platform_30");
        if (revive) {
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
            writer.write(updated);
            std::cout << "{\"event\":\"revive\",\"since_dispose_ms\":" << ms(disposed_at) << "}"
                      << std::endl;
            if (scenario == "revive_dispose") {
                std::this_thread::sleep_for(std::chrono::milliseconds(250));
                writer.dispose_instance(writer.lookup_instance(updated));
                std::cout << "{\"event\":\"dispose_again\",\"since_dispose_ms\":"
                          << ms(disposed_at) << "}" << std::endl;
            }
        }
        bool restarted = false, canary_received = false, saw_disposed = false;
        bool saw_updated = false, regressed = false, terminal_observed = false;
        double first_disposed_ms = -1, first_updated_ms = -1, canary_ms = -1;
        std::string last_state = "ALIVE_ORIGINAL";
        auto next_canary = Clock::now();
        const auto deadline = disposed_at + std::chrono::seconds(observation_seconds);
        while (Clock::now() < deadline) {
            if (exists("restart_reader") && !restarted) {
                reader.close();
                reader = Reader(subscriber, output, reader_qos);
                restarted = true;
                last_state = "NO_DATA";
                std::cout << "{\"event\":\"reader_restarted\",\"since_dispose_ms\":"
                          << ms(disposed_at) << "}" << std::endl;
            }
            if (exists("link_restored") && !canary_received && Clock::now() >= next_canary) {
                writer.write(canary);
                next_canary = Clock::now() + std::chrono::seconds(1);
            }
            const auto samples = reader.take();
            for (const auto& item : samples) {
                if (item.info().valid()) {
                    if (item.data().value<std::string>("contact_id") == "recovery-canary") {
                        require(item.data() == canary_expected, "recovery canary payload mismatch");
                        if (!canary_received) {
                            canary_received = true;
                            canary_ms = ms(disposed_at);
                        }
                        continue;
                    }
                    require(item.data() == expected || item.data() == updated_expected,
                            "target payload changed unexpectedly");
                    const bool fresh = item.data() == updated_expected;
                    last_state = fresh ? "ALIVE_REVIVED" : "ALIVE_ORIGINAL";
                    if (fresh) {
                        saw_updated = true;
                        if (first_updated_ms < 0) first_updated_ms = ms(disposed_at);
                    }
                    if (item.info().state().instance_state() == State::not_alive_disposed()) {
                        last_state = "DISPOSED";
                        saw_disposed = true;
                        if (first_disposed_ms < 0) first_disposed_ms = ms(disposed_at);
                    }
                } else {
                    Data key(type);
                    reader.key_value(key, item.info().instance_handle());
                    if (key.value<std::string>("contact_id") == "recovery-canary") continue;
                    require(key.value<std::string>("contact_id") == original.value<std::string>("contact_id")
                                && key.value<std::string>("source") == "Platform_30",
                            "lifecycle authoritative key mismatch");
                    if (item.info().state().instance_state() == State::not_alive_disposed()) {
                        last_state = "DISPOSED";
                        saw_disposed = true;
                        if (first_disposed_ms < 0) first_disposed_ms = ms(disposed_at);
                    } else if (item.info().state().instance_state() == State::not_alive_no_writers()) {
                        last_state = "NO_WRITERS";
                    }
                }
                const std::string desired = scenario == "revive" ? "ALIVE_REVIVED" : "DISPOSED";
                const bool final_window = scenario != "revive_dispose"
                    || ms(disposed_at) >= (observation_seconds - 15) * 1000;
                if (final_window && terminal_observed && last_state != desired) regressed = true;
                if (final_window && last_state == desired) terminal_observed = true;
                std::cout << "{\"event\":\"target_state\",\"since_dispose_ms\":"
                          << ms(disposed_at) << ",\"state\":" << quote(last_state) << "}" << std::endl;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
        require(canary_received, "no live canary after restoration; recovery observation invalid");
        if (scenario == "dispose" || revive) {
            require(last_state == (scenario == "revive" ? "ALIVE_REVIVED" : "DISPOSED"),
                    "incorrect final target state");
            require(!regressed, "late lifecycle/data regressed terminal target state");
        }
        if (scenario == "restart") require(restarted, "requested application reader restart not executed");
        std::cout << "{\"event\":\"result\",\"status\":\"PASS\",\"scenario\":" << quote(scenario)
                  << ",\"final_state\":" << quote(last_state)
                  << ",\"saw_disposed\":" << (saw_disposed ? "true" : "false")
                  << ",\"saw_revived\":" << (saw_updated ? "true" : "false")
                  << ",\"terminal_regressed\":" << (regressed ? "true" : "false")
                  << ",\"reader_restarted\":" << (restarted ? "true" : "false")
                  << ",\"first_disposed_ms\":" << first_disposed_ms
                  << ",\"first_revived_ms\":" << first_updated_ms
                  << ",\"canary_arrival_ms\":" << canary_ms
                  << ",\"observed_ms\":" << ms(disposed_at) << "}" << std::endl;
        platform.close();
        c2.close();
        return 0;
    } catch (const std::exception& error) {
        std::cout << "{\"event\":\"result\",\"status\":\"FAIL\",\"error\":"
                  << quote(error.what()) << "}" << std::endl;
        return 1;
    }
}
