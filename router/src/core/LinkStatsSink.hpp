// LinkStatsSink.hpp — the DDS-free seam between WAN endpoints and the LinkStatsCollector
// (Phase 9, D14/D81). Kept dependency-light so RouteRuntime.hpp can inherit the source
// interface without pulling in the collector or the generated admin types.
//
// Registration model (D81 item 2): the protocol-status getters are typed-only, so polling
// happens where the payload type is known. A WAN endpoint pair (a route topic runtime's
// WAN leg, or PresenceMonitor's RouterHealth pair — the mandatory idle-mesh bellwether)
// implements IWanStatsSource and registers with the collector at build, unregisters at
// close. Both registration and polling run on the controller strand, so no locks are
// needed on the source set or the per-source delta baselines.
//
// The source resolves the peer NAME itself via the middleware discovery DB
// (matched_*_participant_data, D81 item 1 — no roster join) and folds interval deltas into
// the collector-owned sink; the collector stamps the network label (the local WAN
// participant, degenerate N=1 today — D18) and the per-peer rollup across sources.

#pragma once

#include <cstdint>
#include <string>

namespace router {

// Writer-side interval deltas (this router's WAN writers -> a peer's readers). Cumulative
// totals are read from the per-matched-subscription DataWriterProtocolStatus; the collector
// (via the source) self-computes deltas and never trusts the native *_change fields (D14).
struct WriterLinkDeltas {
    std::uint64_t pushed_samples = 0;
    std::uint64_t pushed_fragment_bytes = 0; // DATA_FRAG bytes only (0 for unfragmented)
    std::uint64_t pulled_samples = 0;
    std::uint64_t pulled_fragment_bytes = 0; // DATA_FRAG bytes only (0 for unfragmented)
    std::uint64_t nacks_received = 0;
    std::uint64_t nack_frags_received = 0;
    std::uint64_t heartbeats_sent = 0;
    std::uint64_t samples_rejected_remote = 0;
};

// Reader-side interval deltas (a peer's writers -> this router's WAN readers), plus one
// point-in-time gauge. From the per-matched-publication DataReaderProtocolStatus.
struct ReaderLinkDeltas {
    std::uint64_t samples_received = 0;
    std::uint64_t duplicates_received = 0;
    std::uint64_t heartbeats_received = 0;
    std::uint64_t nacks_sent = 0;
    std::uint64_t out_of_range_rejected = 0;
    std::uint64_t samples_rejected_local = 0;
    std::uint32_t uncommitted_samples = 0; // gauge: rolled up as the max across endpoints
};

// The collector-owned accumulator a source folds its per-peer contributions into. `rematch`
// marks that a (re)match happened for this peer this interval (new/reappeared matched
// handle, or a negative delta = counter reset) — the collector stamps the whole peer
// interval `rediscovery_in_interval` so analysis can down-weight the TRANSIENT_LOCAL replay
// burst (D81 item 5). A peer contributed to by several sources (multiple topics, or the
// health pair + a route) is summed by name.
class LinkStatsSink {
public:
    virtual ~LinkStatsSink() {}
    virtual void add_writer(const std::string &peer, const WriterLinkDeltas &d,
                            bool rematch) = 0;
    virtual void add_reader(const std::string &peer, const ReaderLinkDeltas &d,
                            bool rematch) = 0;
};

// A WAN endpoint pair the collector polls each tick. Implemented by RouteTopicRuntime<T>
// (its WAN leg) and by PresenceMonitor's RouterHealth pair.
struct IWanStatsSource {
    virtual ~IWanStatsSource() {}
    // Poll this source's WAN-side matched-endpoint statuses, resolve each peer via the
    // discovery DB, and fold interval deltas into the sink. Called on the controller
    // strand only.
    virtual void collect_wan_stats(LinkStatsSink &sink) = 0;
};

// The collector's registration face, held by the AsyncWaitSetDispatcher (route legs) and
// by router_main (PresenceMonitor's pair). Register/unregister on the controller strand.
struct IWanStatsRegistry {
    virtual ~IWanStatsRegistry() {}
    virtual void register_source(IWanStatsSource *source) = 0;
    virtual void unregister_source(IWanStatsSource *source) = 0;
};

} // namespace router
