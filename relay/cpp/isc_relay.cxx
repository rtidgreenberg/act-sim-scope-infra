// -----------------------------------------------------------------------------
// Modern C++ ISC relay — C++ port of relay/isc_relay.py (Phase 1 PoC).
//
// A standalone DomainParticipant-to-DomainParticipant relay:
//
//     upstream domain ──▶ [ ISC DataReader (leg 1) ] ──▶ [ ISC DataWriter (leg 2) ] ──▶ downstream domain
//
// Native Instance State Consistency runs *per leg* (source-writer↔relay-reader, and
// relay-writer↔dest-reader). The relay makes the two legs behave as one transparent
// hop by mirroring its reader's instance lifecycle onto its writer:
//
//     reader sample valid            ──▶ writer.write(sample)
//     reader NOT_ALIVE_DISPOSED      ──▶ writer.dispose_instance(handle)
//     reader NOT_ALIVE_NO_WRITERS    ──▶ writer.unregister_instance(handle)
//
// Why this C++ port exists: the Modern C++ API exposes rti::util::network_capture
// (enable/start/stop/disable), which the Python API (rti.connextdds) does NOT. Pass
// --capture <name> to record the relay's DDS traffic — including shared-memory
// traffic — to <name>*.pcap for offline Wireshark analysis. Everything else mirrors
// the Python relay's behaviour.
//
// QoS comes from the shared XML profile (relay/qos_isc.xml, profile
// "ActIscLibrary::ActIscProfile") — the same profile Routing Service loads — so the
// C++ and Python relays are QoS-identical. The XML token is
// RECOVER_INSTANCE_STATE_CONSISTENCY (the Python binding spells the enum RECOVER_STATE;
// both map to the same feature).
// -----------------------------------------------------------------------------

#include <atomic>
#include <csignal>
#include <cstring>
#include <iostream>
#include <string>

#include <dds/dds.hpp>
#include <dds/core/QosProvider.hpp>
#include <dds/core/cond/WaitSet.hpp>
#include <dds/sub/cond/ReadCondition.hpp>
#include <rti/util/network_capture.hpp>

#include "ActState.hpp"

namespace nc = rti::util::network_capture;

// SIGINT/SIGTERM flip this; the wait loop wakes every 1s to re-check it.
static volatile std::sig_atomic_t g_stop = 0;
static void handle_signal(int) { g_stop = 1; }


// One-topic, one-key-type ISC relay across two domains (or two topics).
class IscRelay {
public:
    IscRelay(int upstream_domain,
             int downstream_domain,
             const std::string &topic_name,
             const std::string &qos_file,
             const std::string &profile,
             bool verbose)
        : verbose_(verbose),
          // Empty qos_file → QosProvider::Default() (reads NDDS_QOS_PROFILES /
          // USER_QOS_PROFILES.xml); otherwise load the given file explicitly.
          provider_(qos_file.empty()
                        ? dds::core::QosProvider::Default()
                        : dds::core::QosProvider("file://" + qos_file, profile)),
          // Two participants so the two legs are genuinely separate DDS "hops".
          up_dp_(upstream_domain, provider_.participant_qos(profile)),
          down_dp_(downstream_domain, provider_.participant_qos(profile)),
          up_topic_(up_dp_, topic_name),
          down_topic_(down_dp_, topic_name),
          // Leg 1: ISC reader on the upstream side.
          reader_(dds::sub::Subscriber(up_dp_), up_topic_,
                  provider_.datareader_qos(profile)),
          // Leg 2: ISC writer on the downstream side.
          writer_(dds::pub::Publisher(down_dp_), down_topic_,
                  provider_.datawriter_qos(profile)),
          read_cond_(reader_, dds::sub::status::DataState::any())
    {
        // Read-condition-driven wakeups; take() drains on each notification.
        waitset_ += read_cond_;
    }

    // With TRANSIENT_LOCAL, wait for the source's durable state before forwarding so
    // the relay starts from a complete instance picture, not mid-stream.
    void start()
    {
        try {
            reader_.wait_for_historical_data(dds::core::Duration::from_secs(5));
        } catch (const std::exception &) {
            // no historical writer yet — fine, we'll get live data
        }
    }

    // Block until a signal arrives, draining and mirroring on every wakeup. A single
    // bad sample must never kill the loop, so faults are isolated per wake.
    void run()
    {
        while (!g_stop) {
            try {
                // 1s tick so we re-check g_stop even with no traffic.
                dds::core::cond::WaitSet::ConditionSeq active =
                    waitset_.wait(dds::core::Duration::from_secs(1));
                for (const auto &cond : active) {
                    if (cond == read_cond_) {
                        pump();
                    }
                }
            } catch (const dds::core::TimeoutError &) {
                // no condition triggered within 1s — loop and re-check g_stop
            } catch (const std::exception &e) {
                if (verbose_) {
                    std::cout << "[relay] pump error (continuing): " << e.what()
                              << std::endl;
                }
            }
        }
    }

    // Explicit teardown so participants are gone before network_capture::disable().
    void close()
    {
        waitset_ -= read_cond_;  // release the ReadCondition before closing entities
        up_dp_.close();
        down_dp_.close();
    }

private:
    // Drain the reader and mirror every sample / state change onto the writer.
    void pump()
    {
        // Consume through the same condition the WaitSet triggers on.
        auto samples = reader_.select().condition(read_cond_).take();
        for (const auto &sample : samples) {
            const dds::sub::SampleInfo &info = sample.info();

            if (info.valid()) {
                // Live data — forward as-is (preserves the key, so the instance maps 1:1).
                const ActState &data = sample.data();
                writer_.write(data);
                if (verbose_) {
                    std::cout << "[relay] fwd  key=" << data.key_id
                              << " seq=" << data.seq << std::endl;
                }
                continue;
            }

            // Invalid sample == instance-state change only; recover the key.
            std::string key_id;
            if (!resolve_key_id(info, key_id)) {
                continue;  // key genuinely unrecoverable — nothing we can faithfully mirror
            }
            const auto state = info.state().instance_state();

            // dispose/unregister are by InstanceHandle, so look the handle up on the
            // writer (registering it if the writer hasn't seen the key).
            ActState key;
            key.key_id = key_id;
            dds::core::InstanceHandle handle = writer_.lookup_instance(key);
            if (handle.is_nil()) {
                handle = writer_.register_instance(key);
            }

            if (state == dds::sub::status::InstanceState::not_alive_disposed()) {
                writer_.dispose_instance(handle);
                if (verbose_) {
                    std::cout << "[relay] DISPOSE key=" << key_id << std::endl;
                }
            } else if (state == dds::sub::status::InstanceState::not_alive_no_writers()) {
                writer_.unregister_instance(handle);
                if (verbose_) {
                    std::cout << "[relay] NO_WRITERS(unregister) key=" << key_id
                              << std::endl;
                }
            }
        }
    }

    // Return true and set out=key_id, or false if unrecoverable.
    //
    // Unlike the Python relay (which caches handle→key_id from valid samples), this
    // relies on reader.key_value(): the shared QoS is tuned to make it succeed —
    // serialize_key_with_dispose lets a dispose-only sample carry its key, and
    // keep_minimum_state_for_instances retains the key mapping for instances seen
    // alive. It fails (→ false) only for the case DDS genuinely can't recover: a
    // NOT_ALIVE_NO_WRITERS for an instance this reader never saw alive.
    bool resolve_key_id(const dds::sub::SampleInfo &info, std::string &out)
    {
        try {
            ActState key;
            reader_.key_value(key, info.instance_handle());
            out = key.key_id;
            return !out.empty();
        } catch (const std::exception &) {
            return false;
        }
    }

    bool verbose_;
    dds::core::QosProvider provider_;
    dds::domain::DomainParticipant up_dp_;
    dds::domain::DomainParticipant down_dp_;
    dds::topic::Topic<ActState> up_topic_;
    dds::topic::Topic<ActState> down_topic_;
    dds::sub::DataReader<ActState> reader_;
    dds::pub::DataWriter<ActState> writer_;
    dds::sub::cond::ReadCondition read_cond_;
    dds::core::cond::WaitSet waitset_;
};


namespace {

void usage(const char *prog)
{
    std::cerr
        << "usage: " << prog << " --upstream-domain N --downstream-domain M\n"
        << "         [--topic ActState] [--qos-file PATH] [--profile LIB::PROFILE]\n"
        << "         [--capture NAME] [-v|--verbose]\n\n"
        << "  --capture NAME   enable RTI Network Capture; writes NAME*.pcap\n"
        << "                   (Modern C++ only — not available in the Python relay)\n";
}

}  // namespace


int main(int argc, char **argv)
{
    int upstream_domain = -1;
    int downstream_domain = -1;
    std::string topic = "ActState";
    std::string qos_file;  // empty → QosProvider::Default()
    std::string profile = "ActIscLibrary::ActIscProfile";
    std::string capture_file;
    bool verbose = false;

    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        auto next = [&](const char *name) -> std::string {
            if (i + 1 >= argc) {
                std::cerr << name << " requires a value\n";
                std::exit(2);
            }
            return argv[++i];
        };
        if (a == "--upstream-domain") {
            upstream_domain = std::stoi(next("--upstream-domain"));
        } else if (a == "--downstream-domain") {
            downstream_domain = std::stoi(next("--downstream-domain"));
        } else if (a == "--topic") {
            topic = next("--topic");
        } else if (a == "--qos-file") {
            qos_file = next("--qos-file");
        } else if (a == "--profile") {
            profile = next("--profile");
        } else if (a == "--capture") {
            capture_file = next("--capture");
        } else if (a == "-v" || a == "--verbose") {
            verbose = true;
        } else if (a == "-h" || a == "--help") {
            usage(argv[0]);
            return 0;
        } else {
            std::cerr << "unknown argument: " << a << "\n";
            usage(argv[0]);
            return 2;
        }
    }

    if (upstream_domain < 0 || downstream_domain < 0) {
        std::cerr << "error: --upstream-domain and --downstream-domain are required\n\n";
        usage(argv[0]);
        return 2;
    }

    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    const bool capture = !capture_file.empty();

    // Network Capture MUST be enabled before creating any DomainParticipant, and
    // disabled only after all participants are deleted (and after stop()).
    if (capture && !nc::enable()) {
        std::cerr << "warning: network_capture::enable() failed; continuing without capture\n";
    }

    try {
        {
            IscRelay relay(upstream_domain, downstream_domain, topic, qos_file,
                           profile, verbose);

            // Participants now exist → start capturing all their traffic.
            if (capture && !nc::start(capture_file)) {
                std::cerr << "warning: network_capture::start() failed\n";
            }

            relay.start();
            std::cout << "ISC relay running: domain " << upstream_domain << " -> "
                      << downstream_domain << " topic '" << topic << "'"
                      << (capture ? " [capturing to " + capture_file + "*.pcap]" : "")
                      << ". Ctrl-C to stop." << std::endl;

            relay.run();  // blocks until SIGINT/SIGTERM

            std::cout << "\nstopping relay..." << std::endl;
            if (capture) {
                nc::stop();  // stop before disable / before participants are deleted
            }
            relay.close();
        }  // relay (participants) destroyed here

        if (capture) {
            nc::disable();  // must be the last network-capture call
        }
    } catch (const std::exception &e) {
        std::cerr << "fatal: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}
