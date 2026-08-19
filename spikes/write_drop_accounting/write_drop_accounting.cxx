// write_drop_accounting.cxx — which Connext-native counter, if any, accounts for a
// sample the router failed to forward?
//
// Motivated by review item M6 (RouteTopicRuntime::pump() swallows every write()
// exception with no counter, no log, no status field). Before adding any router-side
// accounting, establish empirically what the middleware ALREADY counts, so we add
// nothing that duplicates an existing Connext counter.
//
// Deliberate non-goal: this spike does not reimplement or model any internal Connext
// mechanic. The only numbers it keeps of its own are a ledger of OUR OWN write() calls
// (attempted / returned normally / threw, by exception type) — the independent variable
// every native counter is compared against. Everything else is read straight off the
// middleware's own status getters.
//
// Four questions:
//   Q1  KEEP_ALL + RELIABLE, max_blocking_time exhausted -> write() throws.
//       Does ANY native writer counter move for that sample?
//   Q2  KEEP_LAST + RELIABLE, reader stalled -> writer replaces unacked samples.
//       Does replaced_unacknowledged_sample_count account for them exactly?
//   Q3  Is per-matched-subscription rejected_sample_count populated at all? The C
//       header annotates it "Only available for local DW status"
//       (dds_c_publication.h:460) — the router reads only the per-subscription variant
//       (WanStatsPoll.hpp:85), so if that annotation holds, samples_rejected_remote is
//       a permanently-zero field.
//   Q4  Does unacknowledged_sample_count rise measurably BEFORE the first throw, i.e.
//       is it usable as a leading indicator at a threshold?
//
// Scenario C is the control: a drained reader, nothing lost, every loss counter must
// stay 0 — it proves the rig does not manufacture drops.
//
// Rig: two participants per scenario on dedicated valid domain IDs (181-184 per repo
// guardrails), UDPv4-only so a killed process cannot leak /dev/shm segments. The reader
// is stalled by giving it RELIABLE + KEEP_ALL + a small max_samples and never calling
// take(): once its cache is full it stops ACKing, and the writer's cache backs up.

#include <dds/dds.hpp>
#include <dds/core/QosProvider.hpp>
#include <rti/core/status/Status.hpp>

#include <cstdio>
#include <cstdint>
#include <string>
#include <thread>
#include <vector>

#include "DropProbe.hpp"

namespace {

// Our own write() ledger — the ground truth. Not a model of anything internal.
struct WriteLedger {
    int attempted = 0;
    int returned_ok = 0;
    int threw_timeout = 0;   // dds::core::TimeoutError (max_blocking_time exhausted)
    int threw_other = 0;
    std::string first_other_what;

    // Q4 sampling: the middleware's own unacked gauge, observed per write.
    std::int32_t unacked_at_first_throw = -1; // -1 = never threw
    std::int32_t unacked_peak_during_loop = 0;
    int first_throw_at_sample = -1;
};

struct Scenario {
    const char *name;
    int domain;
    bool writer_keep_all;   // false => KEEP_LAST(writer_depth)
    int writer_depth;
    int writer_max_samples; // writer RESOURCE_LIMITS
    int reader_max_samples; // reader RESOURCE_LIMITS (the stall knob)
    int blocking_ms;        // RELIABILITY max_blocking_time
    int samples;
    bool drain_reader;      // control scenario: take() so nothing is ever lost
    int pace_ms;            // sleep between writes; 0 = as fast as possible
};

dds::domain::DomainParticipant make_participant(int domain)
{
    // UDPv4 only (repo guardrail): no shared-memory segments to leak.
    dds::domain::qos::DomainParticipantQos qos =
            dds::core::QosProvider::Default().participant_qos();
    qos << rti::core::policy::TransportBuiltin::UDPv4();
    return dds::domain::DomainParticipant(domain, qos);
}

void print_writer_global(dds::pub::DataWriter<DropProbe> &w, const char *when)
{
    rti::core::status::DataWriterProtocolStatus p = w->datawriter_protocol_status();
    rti::core::status::ReliableWriterCacheChangedStatus c =
            w->reliable_writer_cache_changed_status();
    std::printf("  [%s] writer-global DataWriterProtocolStatus:\n", when);
    std::printf("      pushed_sample_count        = %lld\n",
                (long long)p.pushed_sample_count().total());
    std::printf("      pulled_sample_count        = %lld\n",
                (long long)p.pulled_sample_count().total());
    std::printf("      sent_heartbeat_count       = %lld\n",
                (long long)p.sent_heartbeat_count().total());
    std::printf("      received_nack_count        = %lld\n",
                (long long)p.received_nack_count().total());
    std::printf("      rejected_sample_count      = %lld   <-- Q3 (writer-global)\n",
                (long long)p.rejected_sample_count().total());
    std::printf("      pushed_fragment_bytes      = %lld\n",
                (long long)p.pushed_fragment_bytes());
    std::printf("  [%s] ReliableWriterCacheChangedStatus:\n", when);
    std::printf("      unacknowledged_sample_count       = %d   <-- Q4 (gauge)\n",
                (int)c.unacknowledged_sample_count());
    std::printf("      unacknowledged_sample_count_peak  = %d\n",
                (int)c.unacknowledged_sample_count_peak());
    std::printf("      replaced_unacknowledged_sample_count = %lld   <-- Q2\n",
                (long long)c.replaced_unacknowledged_sample_count());
    std::printf("      full_reliable_writer_cache        = %d\n",
                (int)c.full_reliable_writer_cache().total());
    std::printf("      high_watermark_reliable_writer_cache = %d\n",
                (int)c.high_watermark_reliable_writer_cache().total());
}

void print_writer_per_sub(dds::pub::DataWriter<DropProbe> &w)
{
    dds::core::InstanceHandleSeq subs = dds::pub::matched_subscriptions(w);
    std::printf("  per-matched-subscription DataWriterProtocolStatus (%u peer(s)):\n",
                (unsigned)subs.size());
    for (auto it = subs.begin(); it != subs.end(); ++it) {
        rti::core::status::DataWriterProtocolStatus p =
                w->matched_subscription_datawriter_protocol_status(*it);
        std::printf("      peer: pushed=%lld  pulled=%lld  rejected=%lld   <-- Q3 "
                    "(per-sub; header says local-only)\n",
                    (long long)p.pushed_sample_count().total(),
                    (long long)p.pulled_sample_count().total(),
                    (long long)p.rejected_sample_count().total());
    }
}

void print_reader_per_pub(dds::sub::DataReader<DropProbe> &r)
{
    dds::core::InstanceHandleSeq pubs = dds::sub::matched_publications(r);
    std::printf("  per-matched-publication DataReaderProtocolStatus (%u peer(s)):\n",
                (unsigned)pubs.size());
    for (auto it = pubs.begin(); it != pubs.end(); ++it) {
        rti::core::status::DataReaderProtocolStatus p =
                r->matched_publication_datareader_protocol_status(*it);
        std::printf("      peer: received=%lld  duplicates=%lld  rejected=%lld  "
                    "out_of_range_rejected=%lld  uncommitted=%lld\n",
                    (long long)p.received_sample_count().total(),
                    (long long)p.duplicate_sample_count().total(),
                    (long long)p.rejected_sample_count().total(),
                    (long long)p.out_of_range_rejected_sample_count(),
                    (long long)p.uncommitted_sample_count());
    }
}

void run_scenario(const Scenario &s)
{
    std::printf("\n================================================================\n");
    std::printf("SCENARIO %s   (domain %d, history %s, %d samples)\n", s.name, s.domain,
                s.writer_keep_all ? "KEEP_ALL" : "KEEP_LAST", s.samples);
    std::printf("================================================================\n");

    dds::domain::DomainParticipant dp_pub = make_participant(s.domain);
    dds::domain::DomainParticipant dp_sub = make_participant(s.domain);

    dds::topic::Topic<DropProbe> t_pub(dp_pub, "DropProbeTopic");
    dds::topic::Topic<DropProbe> t_sub(dp_sub, "DropProbeTopic");

    // Reader: RELIABLE + KEEP_ALL + tight max_samples. Never drained (unless this is
    // the control scenario) so it stops ACKing once full.
    dds::sub::qos::DataReaderQos rqos =
            dds::core::QosProvider::Default().datareader_qos();
    rqos << dds::core::policy::Reliability::Reliable()
         << dds::core::policy::History::KeepAll()
         << dds::core::policy::ResourceLimits(s.reader_max_samples, 1,
                                             s.reader_max_samples);
    dds::sub::Subscriber sub(dp_sub);
    dds::sub::DataReader<DropProbe> reader(sub, t_sub, rqos);

    // Writer: the scenario's history shape, with max_blocking_time short enough that a
    // full cache surfaces as a timeout quickly.
    dds::pub::qos::DataWriterQos wqos =
            dds::core::QosProvider::Default().datawriter_qos();
    wqos << dds::core::policy::Reliability::Reliable(
            dds::core::Duration::from_millisecs(s.blocking_ms));
    if (s.writer_keep_all) {
        wqos << dds::core::policy::History::KeepAll();
    } else {
        wqos << dds::core::policy::History::KeepLast(s.writer_depth);
    }
    wqos << dds::core::policy::ResourceLimits(s.writer_max_samples, 1,
                                             s.writer_max_samples);
    dds::pub::Publisher pub(dp_pub);
    dds::pub::DataWriter<DropProbe> writer(pub, t_pub, wqos);

    // Wait for the match — DDS is the authority, poll its own matched status.
    for (int i = 0; i < 100; ++i) {
        if (writer.publication_matched_status().current_count() > 0) {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    if (writer.publication_matched_status().current_count() == 0) {
        std::printf("  ABORT: writer never matched a reader\n");
        return;
    }
    std::printf("  matched. writer max_samples=%d, reader max_samples=%d, "
                "max_blocking_time=%dms\n",
                s.writer_max_samples, s.reader_max_samples, s.blocking_ms);

    print_writer_global(writer, "before");

    WriteLedger led;
    for (int i = 0; i < s.samples; ++i) {
        DropProbe d;
        d.seq = i;
        d.filler = "x";

        // Observe the middleware's own gauge immediately before the attempt (Q4).
        std::int32_t unacked_now =
                writer->reliable_writer_cache_changed_status().unacknowledged_sample_count();
        if (unacked_now > led.unacked_peak_during_loop) {
            led.unacked_peak_during_loop = unacked_now;
        }

        led.attempted++;
        try {
            writer.write(d);
            led.returned_ok++;
        } catch (const dds::core::TimeoutError &) {
            led.threw_timeout++;
            if (led.first_throw_at_sample < 0) {
                led.first_throw_at_sample = i;
                led.unacked_at_first_throw = unacked_now;
            }
        } catch (const std::exception &e) {
            led.threw_other++;
            if (led.first_other_what.empty()) {
                led.first_other_what = e.what();
            }
            if (led.first_throw_at_sample < 0) {
                led.first_throw_at_sample = i;
                led.unacked_at_first_throw = unacked_now;
            }
        }

        if (s.drain_reader) {
            dds::sub::LoanedSamples<DropProbe> got = reader.take();
            (void)got; // drained and released — control scenario keeps the path clean
        }
        if (s.pace_ms > 0) {
            std::this_thread::sleep_for(std::chrono::milliseconds(s.pace_ms));
        }
    }

    // Let ACKs/NACKs/heartbeats settle before the final read.
    std::this_thread::sleep_for(std::chrono::milliseconds(800));
    if (s.drain_reader) {
        dds::sub::LoanedSamples<DropProbe> got = reader.take();
        (void)got;
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
    }

    std::printf("\n  OUR write() LEDGER (ground truth):\n");
    std::printf("      attempted        = %d\n", led.attempted);
    std::printf("      returned_ok      = %d\n", led.returned_ok);
    std::printf("      threw_timeout    = %d\n", led.threw_timeout);
    std::printf("      threw_other      = %d%s%s\n", led.threw_other,
                led.first_other_what.empty() ? "" : "   first: ",
                led.first_other_what.c_str());
    std::printf("      first throw at sample index = %d\n", led.first_throw_at_sample);
    std::printf("      unacked gauge AT first throw = %d   <-- Q4\n",
                led.unacked_at_first_throw);
    std::printf("      unacked gauge peak during loop = %d\n",
                led.unacked_peak_during_loop);

    std::printf("\n");
    print_writer_global(writer, "after");
    print_writer_per_sub(writer);
    print_reader_per_pub(reader);

    // Machine-readable one-liner for the README table.
    rti::core::status::DataWriterProtocolStatus gp = writer->datawriter_protocol_status();
    rti::core::status::ReliableWriterCacheChangedStatus gc =
            writer->reliable_writer_cache_changed_status();
    std::int64_t per_sub_rejected = -1;
    dds::core::InstanceHandleSeq subs = dds::pub::matched_subscriptions(writer);
    if (!subs.empty()) {
        per_sub_rejected = writer->matched_subscription_datawriter_protocol_status(
                                          subs[0]).rejected_sample_count().total();
    }
    std::printf("\nRESULT %s attempted=%d ok=%d timeout=%d other=%d "
                "pushed=%lld global_rejected=%lld persub_rejected=%lld "
                "replaced_unacked=%lld unacked_at_first_throw=%d unacked_peak=%d\n",
                s.name, led.attempted, led.returned_ok, led.threw_timeout,
                led.threw_other, (long long)gp.pushed_sample_count().total(),
                (long long)gp.rejected_sample_count().total(),
                (long long)per_sub_rejected,
                (long long)gc.replaced_unacknowledged_sample_count(),
                led.unacked_at_first_throw, led.unacked_peak_during_loop);
}

} // namespace

int main()
{
    // Q1: KEEP_ALL + stalled reader -> write() must throw TimeoutError.
    Scenario a;
    a.name = "A_keep_all_blocking_throw";
    a.domain = 181;
    a.writer_keep_all = true;
    a.writer_depth = 0;
    a.writer_max_samples = 10;
    a.reader_max_samples = 5;
    a.blocking_ms = 100;
    a.samples = 40;
    a.drain_reader = false;
    a.pace_ms = 0;

    // Q2: KEEP_LAST + stalled reader -> writer silently replaces unacked samples.
    Scenario b;
    b.name = "B_keep_last_replace_unacked";
    b.domain = 182;
    b.writer_keep_all = false;
    b.writer_depth = 5;
    // Bounded by KEEP_LAST depth, NOT by resource limits: max_samples must stay above
    // the default protocol.rtps_reliable_writer.heartbeats_per_max_samples (8) or the
    // writer QoS is rejected as inconsistent (verified: DDS_DataWriterQos_is_consistentI).
    b.writer_max_samples = 100;
    b.reader_max_samples = 5;
    b.blocking_ms = 100;
    b.samples = 40;
    b.drain_reader = false;
    b.pace_ms = 0;

    // Control: drained reader, nothing lost, every loss counter must stay 0.
    Scenario c;
    c.name = "C_control_drained_reader";
    c.domain = 183;
    c.writer_keep_all = false;
    c.writer_depth = 5;
    c.writer_max_samples = 100; // see scenario B note
    c.reader_max_samples = 50;
    c.blocking_ms = 100;
    c.samples = 40;
    c.drain_reader = true;
    c.pace_ms = 0;

    // D discriminates "loss" from "write faster than the ACK round trip". Identical to
    // C in every way except that writes are paced above the ACK latency. If
    // replaced_unacknowledged_sample_count is a LOSS counter it should read the same as
    // C (both lose nothing); if it is merely cache churn it should collapse toward 0.
    Scenario d;
    d.name = "D_control_drained_paced";
    d.domain = 184;
    d.writer_keep_all = false;
    d.writer_depth = 5;
    d.writer_max_samples = 100;
    d.reader_max_samples = 50;
    d.blocking_ms = 100;
    d.samples = 40;
    d.drain_reader = true;
    d.pace_ms = 20;

    try {
        run_scenario(a);
        run_scenario(b);
        run_scenario(c);
        run_scenario(d);
    } catch (const std::exception &e) {
        std::printf("FATAL: %s\n", e.what());
        return 1;
    }
    std::printf("\ndone\n");
    return 0;
}
