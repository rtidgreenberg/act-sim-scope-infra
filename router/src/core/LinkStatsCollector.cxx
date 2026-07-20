// LinkStatsCollector.cxx — Phase 9 link-metrics capture (D14/D18/D81).

#include "LinkStatsCollector.hpp"
#include "Log.hpp"

#include <rti/core/policy/CorePolicy.hpp>

#include <algorithm>
#include <limits>
#include <sstream>

namespace router {

namespace {

std::int64_t now_unix_ns() {
    return static_cast<std::int64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(
                    std::chrono::system_clock::now().time_since_epoch())
                    .count());
}

// Probe QoS (link-health.md, D14): RELIABLE + APPLICATION_AUTO (both sides, RxO),
// VOLATILE, KEEP_LAST(1), a fixed 1-sample send window with a per-sample piggyback
// heartbeat (writer), zero heartbeat_response_delay (reader). No liveliness — presence
// stays RouterHealth's job. Validated end-to-end by spikes/link_probe/ (D81 gate).
dds::pub::qos::DataWriterQos probe_writer_qos(const dds::pub::Publisher &pub) {
    dds::pub::qos::DataWriterQos qos = pub.default_datawriter_qos();
    dds::core::policy::Reliability rel = dds::core::policy::Reliability::Reliable();
    rel->acknowledgment_kind(rti::core::policy::AcknowledgmentKind::APPLICATION_AUTO);
    qos << rel;
    qos << dds::core::policy::Durability::Volatile();
    qos << dds::core::policy::History::KeepLast(1);
    rti::core::policy::DataWriterProtocol dwp =
            qos.policy<rti::core::policy::DataWriterProtocol>();
    dwp.rtps_reliable_writer().min_send_window_size(1);
    dwp.rtps_reliable_writer().max_send_window_size(1);
    dwp.rtps_reliable_writer().heartbeats_per_max_samples(1);
    qos << dwp;
    return qos;
}

dds::sub::qos::DataReaderQos probe_reader_qos(const dds::sub::Subscriber &sub) {
    dds::sub::qos::DataReaderQos qos = sub.default_datareader_qos();
    dds::core::policy::Reliability rel = dds::core::policy::Reliability::Reliable();
    rel->acknowledgment_kind(rti::core::policy::AcknowledgmentKind::APPLICATION_AUTO);
    qos << rel;
    qos << dds::core::policy::Durability::Volatile();
    qos << dds::core::policy::History::KeepLast(1);
    rti::core::policy::DataReaderProtocol drp =
            qos.policy<rti::core::policy::DataReaderProtocol>();
    drp.rtps_reliable_reader().min_heartbeat_response_delay(dds::core::Duration::zero());
    drp.rtps_reliable_reader().max_heartbeat_response_delay(dds::core::Duration::zero());
    qos << drp;
    return qos;
}

// LAN telemetry stream, keyed by (observer, peer): RELIABLE + TRANSIENT_LOCAL +
// KEEP_LAST(8) so a slightly-late reader still catches recent intervals per pair (the
// ActRouterMeshStatus shape, deeper history since this is a stream not a snapshot).
dds::pub::qos::DataWriterQos stats_writer_qos(const dds::pub::Publisher &pub) {
    dds::pub::qos::DataWriterQos qos = pub.default_datawriter_qos();
    qos << dds::core::policy::Reliability::Reliable();
    qos << dds::core::policy::Durability::TransientLocal();
    qos << dds::core::policy::History::KeepLast(8);
    return qos;
}

// The codebase's first (and only) DataWriterListener — the app-ack RTT sink, on the probe
// writer alone (D81 item 3). The callback (middleware thread) does the minimum: stamp a
// clock, push (subscription_handle, RTPS seq, recv_time). It shares ownership of the
// accumulator via shared_ptr, so a callback that races teardown is still safe.
class ProbeAckListener : public dds::pub::NoOpDataWriterListener<RouterLinkProbe> {
public:
    explicit ProbeAckListener(std::shared_ptr<ProbeAckAccumulator> acc)
            : acc_(std::move(acc)) {}

    void on_application_acknowledgment(
            dds::pub::DataWriter<RouterLinkProbe> &,
            const rti::pub::AcknowledgmentInfo &info) override {
        ProbeAckAccumulator::Ack a;
        a.subscription_handle = info.subscription_handle();
        a.rtps_seq = static_cast<std::uint64_t>(
                info.sample_identity().sequence_number().value());
        a.recv_unix_ns = now_unix_ns();
        std::lock_guard<std::mutex> lk(acc_->mutex);
        acc_->acks.push_back(a);
    }

private:
    std::shared_ptr<ProbeAckAccumulator> acc_;
};

// Per-peer RTT running stats for this interval.
struct RttAccum {
    std::uint32_t count = 0;
    std::uint32_t min_us = std::numeric_limits<std::uint32_t>::max();
    std::uint32_t max_us = 0;
    std::uint64_t sum_us = 0;
    void add(std::uint32_t us) {
        ++count;
        min_us = std::min(min_us, us);
        max_us = std::max(max_us, us);
        sum_us += us;
    }
};

// Collector-owned sink: folds per-source contributions into a per-peer RouterLinkStats
// (counter fields only; the collector stamps the invariants at emit time). rematch OR'd.
class Accumulator : public LinkStatsSink {
public:
    std::map<std::string, RouterLinkStats> records;
    std::map<std::string, RttAccum> rtt;

    RouterLinkStats &record_for(const std::string &peer) {
        std::map<std::string, RouterLinkStats>::iterator it = records.find(peer);
        if (it != records.end()) {
            return it->second;
        }
        RouterLinkStats r; // value-initialized: all counters zero, flag false
        r.peer_router = peer;
        return records.emplace(peer, r).first->second;
    }

    void add_writer(const std::string &peer, const WriterLinkDeltas &d,
                    bool rematch) override {
        RouterLinkStats &r = record_for(peer);
        r.pushed_samples = r.pushed_samples + d.pushed_samples;
        r.pushed_fragment_bytes = r.pushed_fragment_bytes + d.pushed_fragment_bytes;
        r.pulled_samples = r.pulled_samples + d.pulled_samples;
        r.pulled_fragment_bytes = r.pulled_fragment_bytes + d.pulled_fragment_bytes;
        r.nacks_received = r.nacks_received + d.nacks_received;
        r.nack_frags_received = r.nack_frags_received + d.nack_frags_received;
        r.heartbeats_sent = r.heartbeats_sent + d.heartbeats_sent;
        r.samples_rejected_remote = r.samples_rejected_remote + d.samples_rejected_remote;
        if (rematch) {
            r.rediscovery_in_interval = true;
        }
    }

    void add_reader(const std::string &peer, const ReaderLinkDeltas &d,
                    bool rematch) override {
        RouterLinkStats &r = record_for(peer);
        r.samples_received = r.samples_received + d.samples_received;
        r.duplicates_received = r.duplicates_received + d.duplicates_received;
        r.heartbeats_received = r.heartbeats_received + d.heartbeats_received;
        r.nacks_sent = r.nacks_sent + d.nacks_sent;
        r.out_of_range_rejected = r.out_of_range_rejected + d.out_of_range_rejected;
        r.samples_rejected_local = r.samples_rejected_local + d.samples_rejected_local;
        r.uncommitted_samples = std::max(r.uncommitted_samples, d.uncommitted_samples);
        if (rematch) {
            r.rediscovery_in_interval = true;
        }
    }
};

} // namespace

LinkStatsCollector::LinkStatsCollector(rti::core::cond::AsyncWaitSet &aws,
                                       dds::domain::DomainParticipant wan_participant,
                                       dds::domain::DomainParticipant lan_participant,
                                       const std::string &observer_router,
                                       const std::string &network,
                                       int period_ms,
                                       const std::string &probe_topic,
                                       const std::string &stats_topic)
        : aws_(aws),
          observer_router_(observer_router),
          network_(network),
          period_ms_(period_ms > 0 ? period_ms : kLinkStatsPeriodMs),
          wan_publisher_(wan_participant),
          wan_subscriber_(wan_participant),
          probe_topic_(wan_participant, probe_topic),
          probe_writer_(wan_publisher_, probe_topic_, probe_writer_qos(wan_publisher_)),
          probe_reader_(wan_subscriber_, probe_topic_, probe_reader_qos(wan_subscriber_)),
          lan_publisher_(lan_participant),
          stats_topic_(lan_participant, stats_topic),
          stats_writer_(lan_publisher_, stats_topic_, stats_writer_qos(lan_publisher_)),
          acks_(std::make_shared<ProbeAckAccumulator>()),
          shut_down_(false) {
    // Install the app-ack listener on the probe writer alone, with exactly the app-ack
    // mask (D81 item 3 containment). It shares the accumulator with the collector.
    std::shared_ptr<ProbeAckListener> listener =
            std::make_shared<ProbeAckListener>(acks_);
    probe_writer_.set_listener(
            listener,
            dds::core::status::StatusMask::datawriter_application_acknowledgment());

    // Take-and-discard peers' probes to emit APPLICATION_AUTO acks back to them (their
    // RTT measurement). ReadCondition on the shared AWS, like PresenceMonitor (D52).
    dds::sub::cond::ReadCondition probe_cond(
            probe_reader_, dds::sub::status::DataState::any(),
            [this]() { on_probe_data(); });
    aws_.attach_condition(probe_cond);
    conditions_.push_back(probe_cond);

    Log::info("link_stats_collector_ready",
              {{"probe_topic", probe_topic},
               {"stats_topic", stats_topic},
               {"observer", observer_router_},
               {"network", network_}});
}

LinkStatsCollector::~LinkStatsCollector() {
    shutdown();
}

void LinkStatsCollector::shutdown() {
    if (shut_down_.exchange(true)) {
        return;
    }
    // Reset the listener BEFORE closing the writer (D31/D32 discipline extended to the
    // listener): no callback fires after this. The accumulator outlives any in-flight
    // callback via its shared_ptr.
    try {
        probe_writer_.set_listener(nullptr);
    } catch (const std::exception &e) {
        Log::warn("link_stats_listener_reset_failed", {{"error", e.what()}});
    }
    for (const auto &cond : conditions_) {
        try {
            aws_.detach_condition(cond); // D32 blocking barrier per condition
        } catch (const std::exception &e) {
            Log::warn("link_stats_detach_condition_failed", {{"error", e.what()}});
        }
    }
    conditions_.clear();
    // Drop every registered source (route legs the dispatcher didn't unregister, and the
    // PresenceMonitor pair which is registered for the collector's whole life) so no raw
    // source pointer outlives its registration — symmetric with unregister_source, and
    // robust to a future teardown/destruction reorder in router_main.
    sources_.clear();
}

void LinkStatsCollector::register_source(IWanStatsSource *source) {
    if (source == nullptr) {
        return;
    }
    // Strand-only; dedupe defensively (register is idempotent).
    if (std::find(sources_.begin(), sources_.end(), source) == sources_.end()) {
        sources_.push_back(source);
    }
}

void LinkStatsCollector::unregister_source(IWanStatsSource *source) {
    sources_.erase(std::remove(sources_.begin(), sources_.end(), source),
                   sources_.end());
}

std::string LinkStatsCollector::probe_peer_name(const dds::core::InstanceHandle &handle) {
    try {
        dds::topic::ParticipantBuiltinTopicData pd =
                rti::pub::matched_subscription_participant_data(probe_writer_, handle);
        rti::core::optional_value<std::string> name = pd->participant_name().name();
        return name.is_set() ? name.get() : std::string();
    } catch (const std::exception &) {
        return std::string();
    }
}

void LinkStatsCollector::on_probe_data() {
    try {
        probe_reader_.take(); // loan returned at end of statement -> APPLICATION_AUTO ack
    } catch (const std::exception &e) {
        Log::warn("link_probe_take_failed", {{"error", e.what()}});
    }
}

void LinkStatsCollector::write_probe() {
    RouterLinkProbe p;
    p.router = observer_router_;
    p.probe_seq = probe_seq_;
    p.send_timestamp = now_unix_ns();
    try {
        probe_writer_.write(p);
    } catch (const dds::core::NotEnabledError &) {
        Log::debug("link_probe_skipped_not_enabled", {}); // ticks start after enable_all()
        return; // no RTPS seq consumed — do NOT advance the counter (would desync acks)
    } catch (const std::exception &e) {
        Log::warn("link_probe_write_failed", {{"error", e.what()}});
        return; // ditto: a failed write assigns no sequence number
    }
    // Only now that write() succeeded and the writer consumed exactly one RTPS sequence
    // number: record its send-time keyed by that seq and advance. Recording/incrementing
    // on a throwing write would leave next_rtps_seq_ ahead of the writer's real sequence,
    // so every later ack would join the wrong send-time (corrupted RTT).
    send_times_[next_rtps_seq_] = p.send_timestamp;
    ++probe_seq_;
    ++next_rtps_seq_;
    // Bound the send-time table: acks arrive within an interval, so a small ring is ample.
    while (send_times_.size() > 32) {
        send_times_.erase(send_times_.begin());
    }
}

void LinkStatsCollector::on_link_stats_tick() {
    if (shut_down_.load()) {
        return;
    }
    const auto now = std::chrono::steady_clock::now();
    // First tick has no prior sample to diff against: report the CONFIGURED cadence, not the
    // 1 s default, so a non-default link_stats_period_ms isn't misreported on sample 1.
    std::uint32_t interval_ms = static_cast<std::uint32_t>(period_ms_);
    if (have_last_tick_) {
        interval_ms = static_cast<std::uint32_t>(
                std::chrono::duration_cast<std::chrono::milliseconds>(now - last_tick_)
                        .count());
    }
    last_tick_ = now;
    have_last_tick_ = true;

    // 1. Poll every registered WAN endpoint source into the per-peer accumulator.
    Accumulator acc;
    for (IWanStatsSource *source : sources_) {
        source->collect_wan_stats(acc);
    }

    // 2. Drain the app-ack accumulator and fold RTTs in (attributing via the discovery DB).
    std::vector<ProbeAckAccumulator::Ack> acks;
    {
        std::lock_guard<std::mutex> lk(acks_->mutex);
        acks.swap(acks_->acks);
    }
    for (const ProbeAckAccumulator::Ack &a : acks) {
        std::map<std::uint64_t, std::int64_t>::iterator sit =
                send_times_.find(a.rtps_seq);
        if (sit == send_times_.end()) {
            continue; // send-time evicted or seq unknown
        }
        std::int64_t rtt_ns = a.recv_unix_ns - sit->second;
        if (rtt_ns < 0) {
            rtt_ns = 0;
        }
        const std::string peer = probe_peer_name(a.subscription_handle);
        if (peer.empty()) {
            continue;
        }
        acc.rtt[peer].add(static_cast<std::uint32_t>(rtt_ns / 1000));
    }

    // 3. Emit one ActRouterLinkStats per peer (+ structured log line). A peer known only
    //    from RTT still gets a record (its route/health counters are simply zero).
    const std::int64_t capture_ts = now_unix_ns();
    ++capture_seq_;
    for (std::map<std::string, RttAccum>::iterator it = acc.rtt.begin();
         it != acc.rtt.end(); ++it) {
        acc.record_for(it->first); // ensure a record exists for RTT-only peers
    }
    for (std::map<std::string, RouterLinkStats>::iterator it = acc.records.begin();
         it != acc.records.end(); ++it) {
        RouterLinkStats &s = it->second;
        s.observer_router = observer_router_;
        s.network = network_;
        s.capture_seq = capture_seq_;
        s.capture_timestamp = capture_ts;
        s.interval_ms = interval_ms;
        std::map<std::string, RttAccum>::iterator r = acc.rtt.find(it->first);
        if (r != acc.rtt.end() && r->second.count > 0) {
            const RttAccum &ra = r->second;
            s.rtt_count = ra.count;
            s.rtt_min_us = ra.min_us;
            s.rtt_max_us = ra.max_us;
            s.rtt_mean_us = static_cast<std::uint32_t>(ra.sum_us / ra.count);
        }
        try {
            stats_writer_.write(s);
        } catch (const dds::core::NotEnabledError &) {
            Log::debug("link_stats_skipped_not_enabled", {});
        } catch (const std::exception &e) {
            Log::warn("link_stats_write_failed", {{"peer", it->first},
                                                  {"error", e.what()}});
        }
        Log::info("link_stats",
                  {{"observer", observer_router_},
                   {"peer", it->first},
                   {"network", network_},
                   {"interval_ms", std::to_string(s.interval_ms)},
                   {"rediscovery", s.rediscovery_in_interval ? "1" : "0"},
                   {"pushed_samples", std::to_string(s.pushed_samples)},
                   {"heartbeats_sent", std::to_string(s.heartbeats_sent)},
                   {"samples_received", std::to_string(s.samples_received)},
                   {"nacks_received", std::to_string(s.nacks_received)},
                   {"rtt_count", std::to_string(s.rtt_count)},
                   {"rtt_mean_us", std::to_string(s.rtt_mean_us)}});
    }

    // 4. One probe on the WAN (the only new WAN traffic this phase adds).
    write_probe();
}

} // namespace router
