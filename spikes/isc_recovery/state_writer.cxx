// -----------------------------------------------------------------------------
// state_writer — imperative instance-state driver for the ISC recovery spike.
//
// Reads commands from stdin (one per line) and drives instance state on its
// DataWriter with exactly the calls the router's mirror would make:
//
//   write K [payload]   writer.write({K, ++seq, payload})   -> instance ALIVE
//   dispose K           writer.dispose_instance(handle)      -> NOT_ALIVE_DISPOSED
//   unregister K        writer.unregister_instance(handle)   -> NOT_ALIVE_NO_WRITERS
//   pause               stop asserting liveliness            -> (after lease) reader NO_WRITERS
//   resume              resume asserting liveliness           -> SAME physical GUID (Scenario A)
//   assert              assert liveliness once
//   quit                clean shutdown
//
// Liveliness is MANUAL_BY_TOPIC with a short lease (see QoS). A background thread
// asserts liveliness every 500ms while not paused; `pause` stops it so the reader
// loses liveliness *without this process dying* — that is the Scenario A trigger.
//
// Scenario B is driven externally by the test runner: SIGKILL this process, then
// launch it again with the SAME durable-writer-history file, so it resumes under the
// same virtual GUID but a new physical GUID.
//
// Modern C++ entity/QoS patterns follow relay/cpp/isc_relay.cxx.
// -----------------------------------------------------------------------------

#include <atomic>
#include <chrono>
#include <csignal>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>

#include <dds/dds.hpp>
#include <dds/core/QosProvider.hpp>

#include "IscState.hpp"

static volatile std::sig_atomic_t g_stop = 0;
static void handle_signal(int) { g_stop = 1; }

int main(int argc, char **argv)
{
    int domain = -1;
    std::string topic = "IscState";
    std::string qos_file;
    std::string profile = "Recovery::Recover";

    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        auto next = [&](const char *name) -> std::string {
            if (i + 1 >= argc) { std::cerr << name << " requires a value\n"; std::exit(2); }
            return argv[++i];
        };
        if (a == "--domain") domain = std::stoi(next("--domain"));
        else if (a == "--topic") topic = next("--topic");
        else if (a == "--qos-file") qos_file = next("--qos-file");
        else if (a == "--profile") profile = next("--profile");
        else if (a == "-h" || a == "--help") {
            std::cerr << "usage: " << argv[0]
                      << " --domain N [--topic IscState] [--qos-file PATH] [--profile LIB::PROFILE]\n"
                      << "commands (stdin): write K [payload] | dispose K | unregister K | "
                         "pause | resume | assert | quit\n";
            return 0;
        } else { std::cerr << "unknown arg: " << a << "\n"; return 2; }
    }
    if (domain < 0) { std::cerr << "error: --domain required\n"; return 2; }

    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    try {
        dds::core::QosProvider provider =
            qos_file.empty() ? dds::core::QosProvider::Default()
                             : dds::core::QosProvider("file://" + qos_file, profile);

        dds::domain::DomainParticipant dp(domain, provider.participant_qos(profile));
        dds::topic::Topic<IscState> t(dp, topic);
        dds::pub::DataWriter<IscState> writer(
            dds::pub::Publisher(dp), t, provider.datawriter_qos(profile));

        std::mutex writer_mtx;              // serialize writer ops vs the liveliness thread
        std::atomic<bool> paused(false);
        long long seq = 0;

        // Look up (or register) the instance handle for a key on the writer.
        auto handle_for = [&](const std::string &key_id) {
            IscState k; k.key_id = key_id;
            auto h = writer.lookup_instance(k);
            if (h.is_nil()) h = writer.register_instance(k);
            return h;
        };

        // Background liveliness assertion: keeps the reader seeing this writer ALIVE
        // while not paused. Stopping (pause) lets the lease expire -> reader NO_WRITERS.
        std::thread live_thread([&] {
            while (!g_stop) {
                if (!paused.load()) {
                    try {
                        std::lock_guard<std::mutex> lk(writer_mtx);
                        writer.assert_liveliness();
                    } catch (const std::exception &) { /* ignore transient */ }
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(500));
            }
        });

        std::cout << "[writer] up on domain " << domain << " topic '" << topic
                  << "' profile '" << profile << "'. Commands on stdin." << std::endl;

        std::string line;
        while (!g_stop && std::getline(std::cin, line)) {
            std::istringstream iss(line);
            std::string cmd, key, payload;
            iss >> cmd;
            if (cmd.empty()) continue;

            try {
                if (cmd == "write") {
                    iss >> key; std::getline(iss, payload);
                    IscState d; d.key_id = key; d.seq = ++seq; d.payload = payload;
                    std::lock_guard<std::mutex> lk(writer_mtx);
                    writer.write(d);
                    std::cout << "[writer] write key=" << key << " seq=" << seq << std::endl;
                } else if (cmd == "dispose") {
                    iss >> key;
                    std::lock_guard<std::mutex> lk(writer_mtx);
                    writer.dispose_instance(handle_for(key));
                    std::cout << "[writer] dispose key=" << key << std::endl;
                } else if (cmd == "unregister") {
                    iss >> key;
                    std::lock_guard<std::mutex> lk(writer_mtx);
                    writer.unregister_instance(handle_for(key));
                    std::cout << "[writer] unregister key=" << key << std::endl;
                } else if (cmd == "pause") {
                    paused.store(true);
                    std::cout << "[writer] paused (liveliness assertion off)" << std::endl;
                } else if (cmd == "resume") {
                    paused.store(false);
                    std::lock_guard<std::mutex> lk(writer_mtx);
                    writer.assert_liveliness();
                    std::cout << "[writer] resumed (liveliness asserted)" << std::endl;
                } else if (cmd == "assert") {
                    std::lock_guard<std::mutex> lk(writer_mtx);
                    writer.assert_liveliness();
                    std::cout << "[writer] asserted liveliness" << std::endl;
                } else if (cmd == "quit") {
                    break;
                } else {
                    std::cout << "[writer] unknown command: " << cmd << std::endl;
                }
            } catch (const std::exception &e) {
                std::cerr << "[writer] command error (continuing): " << e.what() << std::endl;
            }
        }

        g_stop = 1;
        live_thread.join();
        std::cout << "[writer] stopping." << std::endl;
        dp.close();
    } catch (const std::exception &e) {
        std::cerr << "fatal: " << e.what() << std::endl;
        return 1;
    }
    return 0;
}
