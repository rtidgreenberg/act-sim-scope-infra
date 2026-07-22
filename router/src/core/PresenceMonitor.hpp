// PresenceMonitor.hpp — Phase 8 presence & health (D75; design:
// docs/cpp_router/presence-and-health.md), re-keyed by the router NAME (D79).
//
// Publishes this router's compact RouterHealth heartbeat on the WAN participant (the ONE
// liveliness-bearing WAN topic), subscribes to peers', maintains the ALIVE/STALE/DEAD
// roster, and republishes the aggregated connected-router list on the LAN participant as
// ActRouterMeshStatus whenever the roster changes.
//
// Identity (D79): the D74 participant name "<node>/<router>" is the ONLY router
// identity — RouterHealth is keyed by it, the roster maps it, and peers_seen edges carry
// it. router_id is retired; consumers needing a GUID resolve it through the middleware
// discovery database (D81), so the old participant_guid_of correlation is gone too —
// the roster is purely the presence authority.
//
// Roster signals (spike-proven, spikes/presence/):
//   valid heartbeat sample                  -> ALIVE (+summary update)
//   REQUESTED_DEADLINE_MISSED on the reader -> STALE (policy flag, never a teardown).
//     The status carries only last_instance_handle, so the handler also sweeps every
//     ALIVE peer's last_seen against the deadline — coalesced misses (several peers
//     silent in one dispatch window) all surface in the same pass.
//   instance NOT_ALIVE_NO_WRITERS           -> DEAD  (liveliness lost / participant purge)
// A real crash cascades STALE -> DEAD (the 2s deadline fires before the 3s liveliness
// lease) — normal, not an anomaly (D75).
//
// Pinned demo numbers (D75; D16 ordering: the WAN participant lease must stay LONGER
// than kLivelinessLease — the participant lease comes from the WAN participant profile,
// so record both knobs when retuning): heartbeat 1 s (router_main passes
// kHeartbeatPeriodMs to DrainThread), DEADLINE 2 s, AUTOMATIC liveliness lease 3 s.
//
// Threading: the ReadCondition/StatusCondition handlers run on AsyncWaitSet worker
// threads; publish_heartbeat() runs on the controller strand (PresenceTick). The roster
// is mutex-guarded; the DDS writers are internally thread-safe. shutdown() detaches the
// conditions (D32 blocking barriers) before destruction.

#pragma once

#include "Interfaces.hpp"
#include "WanStatsPoll.hpp" // Phase 9: the RouterHealth pair is a WAN-stats source (D81)

#include "RouterAdminTypes.hpp"

#include <rti/core/cond/AsyncWaitSet.hpp>
#include <dds/dds.hpp>

#include <atomic>
#include <chrono>
#include <map>
#include <mutex>
#include <string>
#include <vector>

namespace router {

// The D75-pinned demo numbers.
const int kHeartbeatPeriodMs = 1000;
const int kHealthDeadlineMs = 2000;       // 2x heartbeat period
const int kHealthLivelinessLeaseMs = 3000; // 3x heartbeat period (AUTOMATIC)

// Also an IWanStatsSource (Phase 9, D81): the RouterHealth writer/reader pair is the
// mandatory idle-mesh bellwether — a known-rate WAN pair the LinkStatsCollector polls so
// per-peer protocol counters advance even when every data route is idle. router_main
// registers the monitor with the collector when both are active.
class PresenceMonitor : public IPresencePublisher, public IWanStatsSource {
public:
    // wan_participant carries the RouterHealth pair; lan_participant (the admin
    // participant) carries the ActRouterMeshStatus aggregate. Conditions attach to aws
    // here — construct BEFORE aws.start()/enable_all() like every other condition owner
    // (D52 edge-trigger ordering).
    //
    // team_wan_participant (D93, mesh-dashboard team-grouping follow-on): the team-scoped
    // WAN participant (config-convention name "team_wan"), whose LIVE
    // DomainParticipantQos.partition this router polls at each heartbeat and copies into
    // RouterHealth.team_partition (see publish_heartbeat). Deliberately a live poll of the
    // real entity, not a read of the controller's config-mirrored ParticipantState —
    // ground truth, no risk of drift between the two. router_main looks it up directly by
    // name; the old is_wan flag it once sidestepped is now split into team_scoped + on_wan
    // (see design-decisions.md D93 and the is_wan-decomposition entry). dds::core::null when this process
    // has no team_wan participant (a control node, or any config without one) — then
    // team_partition is always empty, same as "no team".
    PresenceMonitor(rti::core::cond::AsyncWaitSet &aws,
                    dds::domain::DomainParticipant wan_participant,
                    dds::domain::DomainParticipant lan_participant,
                    const std::string &node_name,
                    const std::string &router_name,
                    dds::domain::DomainParticipant team_wan_participant = dds::core::null,
                    const std::string &health_topic = "RouterHealth",
                    const std::string &mesh_topic = "ActRouterMeshStatus");

    ~PresenceMonitor();

    // IPresencePublisher — called from the controller strand on each PresenceTick.
    void publish_heartbeat(const RouterHealth &hb) override;

    // IWanStatsSource — poll the RouterHealth pair's per-matched-endpoint protocol
    // statuses (Phase 9, D81). Called on the controller strand (the LinkStatsTick),
    // single-threaded with publish_heartbeat; the DDS reads are thread-safe and the
    // baseline maps are strand-only.
    void collect_wan_stats(LinkStatsSink &sink) override;

    // Detach the conditions from the AsyncWaitSet (D32 barriers). Call before aws.stop().
    void shutdown();

private:
    struct PeerEntry {
        RouterHealth last_summary;
        RouterPresenceState presence = RouterPresenceState::PRESENCE_ALIVE;
        std::chrono::steady_clock::time_point last_seen;
    };

    void on_health_data();
    void on_health_reader_status();
    // Snapshot the roster into a mesh sample. Caller holds roster_mutex_.
    RouterMeshStatus build_mesh_locked();
    // Build (briefly under roster_mutex_) + write (off it) the LAN mesh aggregate,
    // serialized under mesh_write_mutex_ so revisions reach the wire in order.
    void publish_mesh();

    rti::core::cond::AsyncWaitSet &aws_;
    std::string node_name_;
    std::string router_name_;
    std::string identity_; // "<node>/<router>" — the D74/D79 name, our RouterHealth key
    // D93: polled live (qos().policy<Partition>().name()) at each heartbeat, never
    // cached — see the constructor comment and publish_heartbeat. dds::core::null if this
    // process has no team_wan participant.
    dds::domain::DomainParticipant team_wan_participant_;

    dds::pub::Publisher wan_publisher_;
    dds::sub::Subscriber wan_subscriber_;
    dds::topic::Topic<RouterHealth> health_topic_;
    dds::pub::DataWriter<RouterHealth> health_writer_;
    dds::sub::DataReader<RouterHealth> health_reader_;
    dds::pub::Publisher lan_publisher_;
    dds::topic::Topic<RouterMeshStatus> mesh_topic_;
    dds::pub::DataWriter<RouterMeshStatus> mesh_writer_;

    mutable std::mutex roster_mutex_;
    std::map<std::string, PeerEntry> roster_; // keyed by the peer's router name (D79)
    std::map<std::string, std::string> handle_to_name_; // health instance handle -> name
    std::uint64_t mesh_revision_ = 0;
    std::int32_t deadline_missed_total_ = 0;
    // Guarded by roster_mutex_: duplicate-identity offender already warned about, and
    // edge-trigger flags for the bounded_sequence(100) truncation warnings.
    bool duplicate_identity_warned_ = false;
    bool heartbeat_peers_truncated_ = false;
    bool mesh_peers_truncated_ = false;
    // Serializes build+write of the mesh aggregate (see publish_mesh()). Always taken
    // BEFORE roster_mutex_; publish_heartbeat takes only roster_mutex_.
    std::mutex mesh_write_mutex_;

    std::vector<dds::core::cond::Condition> conditions_;
    std::atomic<bool> shut_down_;

    // Phase 9 (D81) link-stats delta baselines for the RouterHealth pair. Strand-only
    // (collect_wan_stats), so no lock — separate from roster_mutex_.
    std::map<std::string, WriterTotals> health_writer_prev_;
    std::map<std::string, ReaderTotals> health_reader_prev_;
};

} // namespace router
