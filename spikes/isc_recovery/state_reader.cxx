// -----------------------------------------------------------------------------
// state_reader — instance-state observer for the ISC recovery spike.
//
// Prints every instance-state transition it sees, tagged with HOW the transition
// arrived, which is the whole point of the spike:
//
//   via=DATA(seq=N)   a valid data sample  -> state came from a (re)written / replayed
//                     sample (durability replay or an imperative re-write)
//   via=STATE         an invalid sample (valid_data=false), instance-state change only
//                     -> state came from the native ISC recovery exchange, no data
//
// So Scenario A (native ISC recovers) shows up as `via=STATE ... ALIVE`, while a
// Scenario B recovery that only comes back through replay shows up as `via=DATA`.
//
// SIGUSR1 prints a full per-key snapshot of the current instance states.
// SIGINT/SIGTERM exits cleanly.
//
// Modern C++ entity/QoS/key_value patterns follow relay/cpp/isc_relay.cxx.
// -----------------------------------------------------------------------------

#include <atomic>
#include <csignal>
#include <iostream>
#include <map>
#include <string>

#include <dds/dds.hpp>
#include <dds/core/QosProvider.hpp>
#include <dds/core/cond/WaitSet.hpp>
#include <dds/sub/cond/ReadCondition.hpp>

#include "IscState.hpp"

static volatile std::sig_atomic_t g_stop = 0;
static volatile std::sig_atomic_t g_snapshot = 0;
static void handle_stop(int) { g_stop = 1; }
static void handle_snapshot(int) { g_snapshot = 1; }

static const char *state_name(const dds::sub::status::InstanceState &s)
{
    if (s == dds::sub::status::InstanceState::alive()) return "ALIVE";
    if (s == dds::sub::status::InstanceState::not_alive_disposed()) return "NOT_ALIVE_DISPOSED";
    if (s == dds::sub::status::InstanceState::not_alive_no_writers()) return "NOT_ALIVE_NO_WRITERS";
    return "UNKNOWN";
}

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
                      << " --domain N [--topic IscState] [--qos-file PATH] [--profile LIB::PROFILE]\n";
            return 0;
        } else { std::cerr << "unknown arg: " << a << "\n"; return 2; }
    }
    if (domain < 0) { std::cerr << "error: --domain required\n"; return 2; }

    std::signal(SIGINT, handle_stop);
    std::signal(SIGTERM, handle_stop);
    std::signal(SIGUSR1, handle_snapshot);

    try {
        dds::core::QosProvider provider =
            qos_file.empty() ? dds::core::QosProvider::Default()
                             : dds::core::QosProvider("file://" + qos_file, profile);

        dds::domain::DomainParticipant dp(domain, provider.participant_qos(profile));
        dds::topic::Topic<IscState> t(dp, topic);
        dds::sub::DataReader<IscState> reader(
            dds::sub::Subscriber(dp), t, provider.datareader_qos(profile));

        dds::sub::cond::ReadCondition read_cond(reader, dds::sub::status::DataState::any());
        dds::core::cond::WaitSet waitset;
        waitset += read_cond;

        std::map<std::string, std::string> current;  // key_id -> last state name
        long tick = 0;

        std::cout << "[reader] up on domain " << domain << " topic '" << topic
                  << "' profile '" << profile << "'. Ctrl-C to stop." << std::endl;

        while (!g_stop) {
            if (g_snapshot) {
                g_snapshot = 0;
                std::cout << "[reader] SNAPSHOT t=" << tick << " {";
                bool first = true;
                for (const auto &kv : current) {
                    std::cout << (first ? "" : ", ") << kv.first << "=" << kv.second;
                    first = false;
                }
                std::cout << "}" << std::endl;
            }
            try {
                auto active = waitset.wait(dds::core::Duration::from_secs(1));
                for (const auto &cond : active) {
                    if (cond != read_cond) continue;
                    auto samples = reader.select().condition(read_cond).take();
                    for (const auto &sample : samples) {
                        ++tick;
                        const dds::sub::SampleInfo &info = sample.info();
                        const auto st = info.state().instance_state();
                        const char *sname = state_name(st);

                        std::string key_id;
                        bool valid = info.valid();
                        long long seq = -1;
                        if (valid) {
                            key_id = sample.data().key_id;
                            seq = sample.data().seq;
                        } else {
                            try {
                                IscState k;
                                reader.key_value(k, info.instance_handle());
                                key_id = k.key_id;
                            } catch (const std::exception &) {
                                key_id = "<unrecoverable>";
                            }
                        }

                        current[key_id] = sname;
                        std::cout << "[reader] t=" << tick << " key=" << key_id
                                  << " " << sname
                                  << " via=" << (valid ? "DATA" : "STATE");
                        if (valid) std::cout << "(seq=" << seq << ")";
                        std::cout << std::endl;
                    }
                }
            } catch (const dds::core::TimeoutError &) {
                // no data within 1s — loop, re-check signals
            } catch (const std::exception &e) {
                std::cerr << "[reader] error (continuing): " << e.what() << std::endl;
            }
        }

        std::cout << "[reader] stopping." << std::endl;
        waitset -= read_cond;
        dp.close();
    } catch (const std::exception &e) {
        std::cerr << "fatal: " << e.what() << std::endl;
        return 1;
    }
    return 0;
}
