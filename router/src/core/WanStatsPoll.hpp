// WanStatsPoll.hpp — the shared per-matched-endpoint protocol-status poll (Phase 9,
// D14/D81). Templated on the payload type so it works for a route's DynamicData WAN leg
// (RouteTopicRuntime) and for PresenceMonitor's typed RouterHealth pair (the mandatory
// idle-mesh bellwether) without duplicating the discovery-DB attribution + delta logic.
//
// The protocol-status getters are typed-only (AnyDataWriter/AnyDataReader expose none —
// verified), which is exactly why polling lives with the typed entity (D81 item 2). The
// call surface here is the one compile-verified in spikes/matched_endpoints/
// cpp_compile_check.cxx (P9-1/P9-2). Callers own the baseline maps (dropped with the
// endpoint) and invoke these only on the controller strand — no locking.

#pragma once

#include "LinkStatsSink.hpp"

#include <dds/dds.hpp>
#include <dds/sub/discovery.hpp>
#include <dds/pub/discovery.hpp>

#include <cstdint>
#include <map>
#include <sstream>
#include <string>

namespace router {

// Cumulative writer/reader protocol totals per matched-endpoint handle (D14 self-delta).
struct WriterTotals {
    std::uint64_t pushed = 0, pushed_bytes = 0, pulled = 0, pulled_bytes = 0;
    std::uint64_t nacks = 0, nack_frags = 0, heartbeats = 0, rejected = 0;
};
struct ReaderTotals {
    std::uint64_t received = 0, duplicates = 0, heartbeats = 0, nacks = 0;
    std::uint64_t out_of_range = 0, rejected = 0;
};

inline std::string wan_handle_key(const dds::core::InstanceHandle &h) {
    std::ostringstream os;
    os << h;
    return os.str();
}

// Poll this writer's matched subscriptions (peer readers). For each, resolve the peer name
// via the discovery DB, read the DataWriterProtocolStatus totals, self-compute the interval
// delta vs the baseline (a new handle or a counter reset = rematch, count from zero and
// flag the interval — D81 item 5), and fold into the sink. Handles no longer matched drop
// out of `prev` (baselines released).
// Non-const entity refs: the per-matched-endpoint protocol-status getters are non-const
// (they touch internal state), so operator-> onto a const handle would discard qualifiers.
template <typename T>
void poll_writer_wan_stats(dds::pub::DataWriter<T> &writer,
                           std::map<std::string, WriterTotals> &prev,
                           LinkStatsSink &sink) {
    dds::core::InstanceHandleSeq subs = dds::pub::matched_subscriptions(writer);
    std::map<std::string, WriterTotals> next;
    for (auto it = subs.begin(); it != subs.end(); ++it) {
        std::string peer;
        try {
            dds::topic::ParticipantBuiltinTopicData pd =
                    rti::pub::matched_subscription_participant_data(writer, *it);
            rti::core::optional_value<std::string> name = pd->participant_name().name();
            if (name.is_set()) {
                peer = name.get();
            }
        } catch (const std::exception &) {
            continue; // handle raced away between enumerate and lookup
        }
        if (peer.empty()) {
            continue; // unattributable — never happens between ACT routers (D74)
        }
        rti::core::status::DataWriterProtocolStatus st;
        try {
            st = writer->matched_subscription_datawriter_protocol_status(*it);
        } catch (const std::exception &) {
            continue;
        }
        WriterTotals tot;
        tot.pushed = static_cast<std::uint64_t>(st.pushed_sample_count().total());
        tot.pushed_bytes = static_cast<std::uint64_t>(st.pushed_fragment_bytes());
        tot.pulled = static_cast<std::uint64_t>(st.pulled_sample_count().total());
        tot.pulled_bytes = static_cast<std::uint64_t>(st.pulled_fragment_bytes());
        tot.nacks = static_cast<std::uint64_t>(st.received_nack_count().total());
        tot.nack_frags = static_cast<std::uint64_t>(st.received_nack_fragment_count());
        tot.heartbeats = static_cast<std::uint64_t>(st.sent_heartbeat_count().total());
        tot.rejected = static_cast<std::uint64_t>(st.rejected_sample_count().total());

        const std::string key = wan_handle_key(*it);
        std::map<std::string, WriterTotals>::iterator p = prev.find(key);
        WriterLinkDeltas d;
        bool rematch = false;
        if (p == prev.end() || tot.pushed < p->second.pushed) {
            rematch = true; // first sight or counter reset — re-baseline, deltas stay 0
        } else {
            const WriterTotals &b = p->second;
            d.pushed_samples = tot.pushed - b.pushed;
            d.pushed_fragment_bytes = tot.pushed_bytes - b.pushed_bytes;
            d.pulled_samples = tot.pulled - b.pulled;
            d.pulled_fragment_bytes = tot.pulled_bytes - b.pulled_bytes;
            d.nacks_received = tot.nacks - b.nacks;
            d.nack_frags_received = tot.nack_frags - b.nack_frags;
            d.heartbeats_sent = tot.heartbeats - b.heartbeats;
            d.samples_rejected_remote = tot.rejected - b.rejected;
        }
        next[key] = tot;
        sink.add_writer(peer, d, rematch);
    }
    prev.swap(next);
}

// Symmetric reader-side poll (peer writers -> this reader).
template <typename T>
void poll_reader_wan_stats(dds::sub::DataReader<T> &reader,
                           std::map<std::string, ReaderTotals> &prev,
                           LinkStatsSink &sink) {
    dds::core::InstanceHandleSeq pubs = dds::sub::matched_publications(reader);
    std::map<std::string, ReaderTotals> next;
    for (auto it = pubs.begin(); it != pubs.end(); ++it) {
        std::string peer;
        try {
            dds::topic::ParticipantBuiltinTopicData pd =
                    rti::sub::matched_publication_participant_data(reader, *it);
            rti::core::optional_value<std::string> name = pd->participant_name().name();
            if (name.is_set()) {
                peer = name.get();
            }
        } catch (const std::exception &) {
            continue;
        }
        if (peer.empty()) {
            continue;
        }
        rti::core::status::DataReaderProtocolStatus st;
        try {
            st = reader->matched_publication_datareader_protocol_status(*it);
        } catch (const std::exception &) {
            continue;
        }
        ReaderTotals tot;
        tot.received = static_cast<std::uint64_t>(st.received_sample_count().total());
        tot.duplicates = static_cast<std::uint64_t>(st.duplicate_sample_count().total());
        tot.heartbeats = static_cast<std::uint64_t>(st.received_heartbeat_count().total());
        tot.nacks = static_cast<std::uint64_t>(st.sent_nack_count().total());
        tot.out_of_range =
                static_cast<std::uint64_t>(st.out_of_range_rejected_sample_count());
        tot.rejected = static_cast<std::uint64_t>(st.rejected_sample_count().total());

        const std::string key = wan_handle_key(*it);
        std::map<std::string, ReaderTotals>::iterator p = prev.find(key);
        ReaderLinkDeltas d;
        bool rematch = false;
        if (p == prev.end() || tot.received < p->second.received) {
            rematch = true;
        } else {
            const ReaderTotals &b = p->second;
            d.samples_received = tot.received - b.received;
            d.duplicates_received = tot.duplicates - b.duplicates;
            d.heartbeats_received = tot.heartbeats - b.heartbeats;
            d.nacks_sent = tot.nacks - b.nacks;
            d.out_of_range_rejected = tot.out_of_range - b.out_of_range;
            d.samples_rejected_local = tot.rejected - b.rejected;
        }
        d.uncommitted_samples =
                static_cast<std::uint32_t>(st.uncommitted_sample_count()); // gauge
        next[key] = tot;
        sink.add_reader(peer, d, rematch);
    }
    prev.swap(next);
}

} // namespace router
