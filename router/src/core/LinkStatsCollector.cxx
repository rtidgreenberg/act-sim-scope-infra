// LinkStatsCollector.cxx — Phase 9 link-metrics capture (D14/D18/D81).

#include "LinkStatsCollector.hpp"
#include "Log.hpp"

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

// Collector-owned sink: folds per-source contributions into a per-peer RouterLinkStats
// (counter fields only; the collector stamps the invariants at emit time). rematch OR'd.
class Accumulator : public LinkStatsSink {
public:
    std::map<std::string, RouterLinkStats> records;

    RouterLinkStats &record_for(const std::string &peer) {
        std::map<std::string, RouterLinkStats>::iterator it = records.find(peer);
        if (it != records.end()) {
            return it->second;
        }

        RouterLinkStats r; // value-initialized: all counters zero, flag false
        r.peer_router = peer;
        return records.emplace(peer, r).first->second;
    }

    void add_rtt(const std::string &peer, const RttLinkStats &d) override {
        RouterLinkStats &r = record_for(peer);
        r.rtt_count = d.count;
        if (d.count) {
            r.rtt_min_us = d.min_us;
            r.rtt_max_us = d.max_us;
            r.rtt_mean_us = static_cast<std::uint32_t>(d.sum_us / d.count);
        }
        r.rtt_valid = d.valid;
        r.rtt_last_sample_age_ms = d.last_sample_age_ms;
        r.rtt_last_ack_age_us = d.last_ack_age_us;
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

    void add_writer_topic(const std::string &peer, const std::string &route_name,
                          const std::string &topic_name, const WriterLinkDeltas &d,
                          bool rematch) override {
        RouterLinkTopicStats stats;
        stats.route_name = route_name;
        stats.topic_name = topic_name;
        stats.direction = "writer";
        stats.rediscovery_in_interval = rematch;
        stats.pushed_samples = d.pushed_samples;
        stats.pushed_fragment_bytes = d.pushed_fragment_bytes;
        stats.pulled_samples = d.pulled_samples;
        stats.pulled_fragment_bytes = d.pulled_fragment_bytes;
        stats.nacks_received = d.nacks_received;
        stats.nack_frags_received = d.nack_frags_received;
        stats.heartbeats_sent = d.heartbeats_sent;
        stats.samples_rejected_remote = d.samples_rejected_remote;
        record_for(peer).topic_stats.push_back(stats);
    }

    void add_reader_topic(const std::string &peer, const std::string &route_name,
                          const std::string &topic_name, const ReaderLinkDeltas &d,
                          bool rematch) override {
        RouterLinkTopicStats stats;
        stats.route_name = route_name;
        stats.topic_name = topic_name;
        stats.direction = "reader";
        stats.rediscovery_in_interval = rematch;
        stats.samples_received = d.samples_received;
        stats.duplicates_received = d.duplicates_received;
        stats.heartbeats_received = d.heartbeats_received;
        stats.nacks_sent = d.nacks_sent;
        stats.out_of_range_rejected = d.out_of_range_rejected;
        stats.samples_rejected_local = d.samples_rejected_local;
        stats.uncommitted_samples = d.uncommitted_samples;
        record_for(peer).topic_stats.push_back(stats);
    }
};

} // namespace

LinkStatsCollector::LinkStatsCollector(dds::domain::DomainParticipant lan_participant,
                                       const std::string &observer_router,
                                       const std::string &network,
                                       const dds::pub::qos::DataWriterQos &stats_writer_qos,
                                       int period_ms,
                                       const std::string &stats_topic)
        : observer_router_(observer_router),
          network_(network),
          period_ms_(period_ms > 0 ? period_ms : kLinkStatsPeriodMs),
          lan_publisher_(lan_participant),
          stats_topic_(lan_participant, stats_topic),
          stats_writer_(lan_publisher_, stats_topic_, stats_writer_qos),
          shut_down_(false) {
    Log::info("link_stats_collector_ready",
              {{"stats_topic", stats_topic},
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

    // Emit one ActRouterLinkStats per peer (+ structured log line). A peer known only
    //    from RTT still gets a record (its route/health counters are simply zero).
    const std::int64_t capture_ts = now_unix_ns();
    ++capture_seq_;
    for (std::map<std::string, RouterLinkStats>::iterator it = acc.records.begin();
         it != acc.records.end(); ++it) {
        RouterLinkStats &s = it->second;
        s.observer_router = observer_router_;
        s.network = network_;
        s.capture_seq = capture_seq_;
        s.capture_timestamp = capture_ts;
        s.interval_ms = interval_ms;
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
                   {"rtt_mean_us", std::to_string(s.rtt_mean_us)},
                   {"rtt_valid", s.rtt_valid ? "1" : "0"},
                   {"rtt_last_sample_age_ms", std::to_string(s.rtt_last_sample_age_ms)},
                   {"rtt_last_ack_age_us", std::to_string(s.rtt_last_ack_age_us)}});
    }

}

} // namespace router
