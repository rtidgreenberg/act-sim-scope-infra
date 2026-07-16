// PresenceMonitor.hpp — Phase 8 presence & health (D75; design:
// docs/cpp_router/presence-and-health.md).
//
// Publishes this router's compact RouterHealth heartbeat on the WAN participant (the ONE
// liveliness-bearing WAN topic), subscribes to peers', maintains the ALIVE/STALE/DEAD
// roster, and republishes the aggregated connected-router list on the LAN participant as
// ActRouterMeshStatus whenever the roster changes.
//
// Roster signals (spike-proven, spikes/presence/):
//   valid heartbeat sample                  -> ALIVE (+summary update)
//   REQUESTED_DEADLINE_MISSED on the reader -> STALE (policy flag, never a teardown)
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

#include "RouterAdminTypes.hpp"

#include <rti/core/cond/AsyncWaitSet.hpp>
#include <dds/dds.hpp>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <map>
#include <mutex>
#include <string>
#include <vector>

namespace router {

// The D75-pinned demo numbers.
const int kHeartbeatPeriodMs = 1000;
const int kHealthDeadlineMs = 2000;       // 2x heartbeat period
const int kHealthLivelinessLeaseMs = 3000; // 3x heartbeat period (AUTOMATIC)

class PresenceMonitor : public IPresencePublisher {
public:
    // wan_participant carries the RouterHealth pair; lan_participant (the admin
    // participant) carries the ActRouterMeshStatus aggregate. Conditions attach to aws
    // here — construct BEFORE aws.start()/enable_all() like every other condition owner
    // (D52 edge-trigger ordering).
    PresenceMonitor(rti::core::cond::AsyncWaitSet &aws,
                    dds::domain::DomainParticipant wan_participant,
                    dds::domain::DomainParticipant lan_participant,
                    const std::string &node_name,
                    const std::string &router_name,
                    std::uint32_t router_id,
                    const std::string &health_topic = "RouterHealth",
                    const std::string &mesh_topic = "ActRouterMeshStatus");

    ~PresenceMonitor();

    // IPresencePublisher — called from the controller strand on each PresenceTick.
    void publish_heartbeat(const RouterHealth &hb) override;

    // Roster correlation router_id -> current participant GUID (D74/D75), for future
    // consumers (Phase 9 rollup join, Phase 12 reset). Empty if unknown.
    std::string participant_guid_of(std::uint32_t router_id) const;

    // Detach the conditions from the AsyncWaitSet (D32 barriers). Call before aws.stop().
    void shutdown();

private:
    struct PeerEntry {
        RouterHealth last_summary;
        RouterPresenceState presence = RouterPresenceState::PRESENCE_ALIVE;
        std::chrono::steady_clock::time_point last_seen;
        std::string participant_guid; // from the heartbeat writer's publication data
    };

    void on_health_data();
    void on_health_reader_status();
    // Rebuild + write the LAN mesh aggregate. Caller holds roster_mutex_.
    void republish_mesh_locked();

    rti::core::cond::AsyncWaitSet &aws_;
    std::string node_name_;
    std::string router_name_;
    std::uint32_t router_id_;

    dds::pub::Publisher wan_publisher_;
    dds::sub::Subscriber wan_subscriber_;
    dds::topic::Topic<RouterHealth> health_topic_;
    dds::pub::DataWriter<RouterHealth> health_writer_;
    dds::sub::DataReader<RouterHealth> health_reader_;
    dds::pub::Publisher lan_publisher_;
    dds::topic::Topic<RouterMeshStatus> mesh_topic_;
    dds::pub::DataWriter<RouterMeshStatus> mesh_writer_;

    mutable std::mutex roster_mutex_;
    std::map<std::uint32_t, PeerEntry> roster_;
    std::map<std::string, std::uint32_t> handle_to_id_; // health instance handle -> id
    std::uint64_t mesh_revision_ = 0;
    std::int32_t deadline_missed_total_ = 0;

    std::vector<dds::core::cond::Condition> conditions_;
    std::atomic<bool> shut_down_;
};

} // namespace router
