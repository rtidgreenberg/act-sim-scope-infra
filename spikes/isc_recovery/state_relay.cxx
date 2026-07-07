// -----------------------------------------------------------------------------
// state_relay — the router leg under test: reader -> (mirror) -> writer.
//
//     origin (dom A) --> [ ISC reader (leg 1) ]--(mirror)-->[ ISC writer (leg 2) ] --> downstream (dom B)
//
// This is the relay/cpp/isc_relay.cxx mirror, extended to answer the actual question:
// does instance state RELAYED from the reader reach the writer so the DOWNSTREAM
// reader sees it — including the case where leg-1 native ISC recovers an instance
// back to ALIVE?
//
// isc_relay.cxx only mirrors:
//     valid sample            -> writer.write(sample)
//     NOT_ALIVE_DISPOSED      -> writer.dispose_instance(handle)
//     NOT_ALIVE_NO_WRITERS    -> writer.unregister_instance(handle)
// It has NO case for an *invalid* sample reporting the instance back to ALIVE, which
// is exactly how leg-1 ISC recovery is delivered. Without handling it, the relay drives
// downstream to NO_WRITERS on an origin blip and never brings it back — the CORE-13337
// intermediary gap.
//
// The fix (Connext-confirmed: register_instance alone will NOT drive a reader to ALIVE;
// only a written sample does) is to cache the last value per key and re-write it when
// leg-1 reports the instance ALIVE again. Toggle with --no-reassert to demonstrate the
// baseline gap vs the fix.
//
// A background thread asserts writer liveliness (MANUAL_BY_TOPIC QoS) so the downstream
// leg stays alive while the relay is up; only the *origin* drops liveliness in tests.
// -----------------------------------------------------------------------------

#include <atomic>
#include <chrono>
#include <csignal>
#include <iostream>
#include <map>
#include <mutex>
#include <string>
#include <thread>

#include <dds/dds.hpp>
#include <dds/core/QosProvider.hpp>
#include <dds/core/cond/WaitSet.hpp>
#include <dds/sub/cond/ReadCondition.hpp>

#include "IscState.hpp"

static volatile std::sig_atomic_t g_stop = 0;
static void handle_signal(int) { g_stop = 1; }

int main(int argc, char **argv)
{
    int up_domain = -1, down_domain = -1;
    std::string topic = "IscState";
    std::string qos_file;
    std::string profile = "Recovery::Recover";
    bool reassert = true;   // re-write cached value when leg-1 recovers an instance to ALIVE
    bool verbose = true;

    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        auto next = [&](const char *name) -> std::string {
            if (i + 1 >= argc) { std::cerr << name << " requires a value\n"; std::exit(2); }
            return argv[++i];
        };
        if (a == "--upstream-domain") up_domain = std::stoi(next("--upstream-domain"));
        else if (a == "--downstream-domain") down_domain = std::stoi(next("--downstream-domain"));
        else if (a == "--topic") topic = next("--topic");
        else if (a == "--qos-file") qos_file = next("--qos-file");
        else if (a == "--profile") profile = next("--profile");
        else if (a == "--no-reassert") reassert = false;
        else if (a == "-q") verbose = false;
        else if (a == "-h" || a == "--help") {
            std::cerr << "usage: " << argv[0]
                      << " --upstream-domain N --downstream-domain M [--topic IscState]\n"
                      << "         [--qos-file PATH] [--profile LIB::PROFILE] [--no-reassert] [-q]\n";
            return 0;
        } else { std::cerr << "unknown arg: " << a << "\n"; return 2; }
    }
    if (up_domain < 0 || down_domain < 0) {
        std::cerr << "error: --upstream-domain and --downstream-domain required\n"; return 2;
    }

    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    try {
        dds::core::QosProvider provider =
            qos_file.empty() ? dds::core::QosProvider::Default()
                             : dds::core::QosProvider("file://" + qos_file, profile);

        dds::domain::DomainParticipant up_dp(up_domain, provider.participant_qos(profile));
        dds::domain::DomainParticipant down_dp(down_domain, provider.participant_qos(profile));
        dds::topic::Topic<IscState> up_topic(up_dp, topic);
        dds::topic::Topic<IscState> down_topic(down_dp, topic);
        dds::sub::DataReader<IscState> reader(
            dds::sub::Subscriber(up_dp), up_topic, provider.datareader_qos(profile));
        dds::pub::DataWriter<IscState> writer(
            dds::pub::Publisher(down_dp), down_topic, provider.datawriter_qos(profile));

        dds::sub::cond::ReadCondition read_cond(reader, dds::sub::status::DataState::any());
        dds::core::cond::WaitSet waitset;
        waitset += read_cond;

        std::mutex writer_mtx;
        std::map<std::string, IscState> last_value;   // key_id -> last valid sample (for reassert)

        auto handle_for = [&](const std::string &key_id) {
            IscState k; k.key_id = key_id;
            auto h = writer.lookup_instance(k);
            if (h.is_nil()) h = writer.register_instance(k);
            return h;
        };

        // Keep the downstream leg alive while the relay is up (MANUAL_BY_TOPIC liveliness).
        std::thread live_thread([&] {
            while (!g_stop) {
                try { std::lock_guard<std::mutex> lk(writer_mtx); writer.assert_liveliness(); }
                catch (const std::exception &) {}
                std::this_thread::sleep_for(std::chrono::milliseconds(500));
            }
        });

        try { reader.wait_for_historical_data(dds::core::Duration::from_secs(5)); }
        catch (const std::exception &) {}

        std::cout << "[relay] up: dom " << up_domain << " -> " << down_domain
                  << " topic '" << topic << "' reassert-alive=" << (reassert ? "on" : "OFF")
                  << ". Ctrl-C to stop." << std::endl;

        while (!g_stop) {
            try {
                auto active = waitset.wait(dds::core::Duration::from_secs(1));
                for (const auto &cond : active) {
                    if (cond != read_cond) continue;
                    auto samples = reader.select().condition(read_cond).take();
                    for (const auto &sample : samples) {
                        const dds::sub::SampleInfo &info = sample.info();

                        if (info.valid()) {
                            const IscState &data = sample.data();
                            std::lock_guard<std::mutex> lk(writer_mtx);
                            writer.write(data);
                            last_value[data.key_id] = data;   // cache for later reassert
                            if (verbose)
                                std::cout << "[relay] fwd key=" << data.key_id
                                          << " seq=" << data.seq << std::endl;
                            continue;
                        }

                        // Invalid sample: instance-state change only. Recover the key.
                        std::string key_id;
                        try {
                            IscState k; reader.key_value(k, info.instance_handle());
                            key_id = k.key_id;
                        } catch (const std::exception &) { continue; }
                        if (key_id.empty()) continue;

                        const auto state = info.state().instance_state();
                        std::lock_guard<std::mutex> lk(writer_mtx);

                        if (state == dds::sub::status::InstanceState::not_alive_disposed()) {
                            writer.dispose_instance(handle_for(key_id));
                            if (verbose) std::cout << "[relay] DISPOSE key=" << key_id << std::endl;
                        } else if (state == dds::sub::status::InstanceState::not_alive_no_writers()) {
                            writer.unregister_instance(handle_for(key_id));
                            if (verbose) std::cout << "[relay] NO_WRITERS key=" << key_id << std::endl;
                        } else if (state == dds::sub::status::InstanceState::alive()) {
                            // leg-1 ISC recovered this instance to ALIVE (no data sample).
                            // The intermediary cannot forward "state without data" — it must
                            // re-assert by WRITING the cached value, or downstream stays stuck.
                            auto it = last_value.find(key_id);
                            if (reassert && it != last_value.end()) {
                                writer.write(it->second);
                                if (verbose)
                                    std::cout << "[relay] REASSERT-ALIVE(write) key=" << key_id
                                              << " seq=" << it->second.seq << std::endl;
                            } else if (verbose) {
                                std::cout << "[relay] ALIVE-recovery NOT forwarded key=" << key_id
                                          << (reassert ? " (no cached value)" : " (reassert off)")
                                          << std::endl;
                            }
                        }
                    }
                }
            } catch (const dds::core::TimeoutError &) {
            } catch (const std::exception &e) {
                if (verbose) std::cout << "[relay] pump error (continuing): " << e.what() << std::endl;
            }
        }

        g_stop = 1;
        live_thread.join();
        std::cout << "[relay] stopping." << std::endl;
        waitset -= read_cond;
        up_dp.close();
        down_dp.close();
    } catch (const std::exception &e) {
        std::cerr << "fatal: " << e.what() << std::endl;
        return 1;
    }
    return 0;
}
