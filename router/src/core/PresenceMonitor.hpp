// PresenceMonitor.hpp — Phase 8 presence & health (D75; design:
// docs/cpp_router/presence-and-health.md), re-keyed by the router NAME (D79).
//
// Publishes this router's compact RouterHealth heartbeat on the WAN participant (the ONE
// liveliness-bearing WAN topic), subscribes to peers', maintains the ALIVE/STALE/DEAD
// roster, and republishes the aggregated connected-router list on the LAN participant as
// ActRouterMeshStatus. Mesh publish happens twice over: unconditionally on every MeshTick
// (its own independent cadence, kMeshPublishPeriodMs, for GUI/mesh-dashboard consumers
// that want a steady live feed rather than waiting on an event) AND immediately on a
// roster change (peer appears, presence transition, or state_revision advance), so a
// transition is never stuck behind the tick period.
//
// D98: MeshTick is deliberately a SEPARATE controller tick from PresenceTick (the WAN
// heartbeat) — D97 originally called publish_mesh() unconditionally from inside
// publish_heartbeat() itself, which coupled the LAN dashboard's refresh rate to the WAN
// heartbeat's tuning (kHeartbeatPeriodMs) with no independent knob, AND put a synchronous
// LAN DDS write on the controller strand's WAN-heartbeat path. MeshTick gives the LAN
// refresh its own period, the same way LinkStatsTick already has its own independent of
// PresenceTick's (see DrainThread.hpp). ActRouterMeshStatus is BEST_EFFORT (see
// mesh_writer_qos): with a steady periodic republish, an occasional dropped sample
// self-heals on the next tick, so RELIABLE's retry/ack machinery (and its blocking
// potential under backpressure) buys nothing here.
//
// D99: RouterMeshStatus.state_revision (mesh_revision_) is owned entirely by this class
// and is unrelated to RouterController's own D5 state_revision (echoed in RouterHealth,
// journaled via ControllerJournalRecord) -- it's this router's OWN generation counter for
// its aggregated view of its peer roster. Once MeshTick made build_mesh_locked() run
// unconditionally every tick, it could no longer be the one deciding "did the roster
// change" by simply incrementing on every call -- on_health_data()/on_health_reader_status()
// own the increment now (once per real change, under roster_mutex_, at their existing
// `changed` check), and build_mesh_locked() just reads the current value. This restores
// RouterAdminTypes.idl's documented contract ("bumps when the roster changes") which
// D98's unconditional per-tick build_mesh_locked() call had silently broken.
//
// D100: ActRouterMeshStatus dropped TRANSIENT_LOCAL too (D98 had kept it alongside
// BEST_EFFORT). Under BEST_EFFORT, TRANSIENT_LOCAL's late-joiner replay burst is itself
// just another best-effort sample -- no retry/repair if it's lost on the wire, and RTI's
// own docs are explicit that the replay is only GUARANTEED effective paired with
// RELIABLE. Keeping it bought a "repair mechanism" that wasn't actually repairing
// anything, while still paying the writer's retained-sample bookkeeping. VOLATILE is
// simpler and no less correct here: a late-joining reader just waits for the next
// periodic MeshTick (worst case ~0.5s) for its first sample, instead of an unreliable
// "instant" replay attempt. See mesh_writer_qos (PresenceMonitor.cxx).
//
// D101: mesh_writer_ also uses ASYNCHRONOUS publish mode, moving the actual network send
// off the controller strand entirely (onto Connext's own async-publish thread) — residual
// insurance against the strand ever blocking on this write, on top of (not instead of)
// D100's BEST_EFFORT. See mesh_writer_qos (PresenceMonitor.cxx).
//
// D102: build_mesh_locked() bounds and refreshes the transitive (second-hand) peers_seen
// it re-embeds from each peer's own cached RouterHealth (D97 stopped stripping this) —
// ALIVE-only (bounds the O(N*M) growth to the realistic alive-peer count, not the 100x100
// worst case) and with each kept edge's delta aged forward by how long our own view of
// that peer has sat since its last heartbeat (fixes mesh_graph.js's transitive edges
// visually "freshening" every MeshTick even though the underlying data never changed).
// See build_mesh_locked() (PresenceMonitor.cxx).
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
// MeshTick (D98) is a separate 0.5 s knob (kMeshPublishPeriodMs, also passed to
// DrainThread) — independent of the heartbeat/DEADLINE/lease numbers above.
//
// Threading: the ReadCondition/StatusCondition handlers run on AsyncWaitSet worker
// threads; publish_heartbeat() runs on the controller strand (PresenceTick) and
// publish_mesh_tick() also runs on the controller strand (MeshTick) — both ticks are
// posted by the same single-threaded DrainThread strand, just on independent periods.
// The roster is mutex-guarded; the DDS writers are internally thread-safe. shutdown()
// detaches the conditions (D32 blocking barriers) before destruction.

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
// D98: the LAN mesh-dashboard republish cadence — independent of the WAN heartbeat
// numbers above (see MeshTick in RouterEvents.hpp / DrainThread.hpp).
const int kMeshPublishPeriodMs = 500;

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
    // team_scoped_participant (D93, mesh-dashboard team-grouping follow-on; retargeted by
    // D103): the team-scoped WAN participant (platform_wan under D103 — team_wan is
    // retired), whose LIVE DomainParticipantQos.partition this router polls at each
    // heartbeat and copies into RouterHealth.team_partition (see publish_heartbeat).
    // Deliberately a live poll of the real entity, not a read of the controller's
    // config-mirrored ParticipantState — ground truth, no risk of drift between the two.
    // router_main filters filtered_participants by ParticipantState.team_scoped (D83)
    // rather than a fixed name (see design-decisions.md D93/D103 and the
    // is_wan-decomposition entry). dds::core::null when this process has no team_scoped
    // participant (a control node, or any config without one) — then team_partition is
    // always empty, same as "no team".
    PresenceMonitor(rti::core::cond::AsyncWaitSet &aws,
                    dds::domain::DomainParticipant wan_participant,
                    dds::domain::DomainParticipant lan_participant,
                    const std::string &node_name,
                    const std::string &router_name,
                    dds::domain::DomainParticipant team_scoped_participant = dds::core::null,
                    const std::string &health_topic = "RouterHealth",
                    const std::string &mesh_topic = "ActRouterMeshStatus");

    ~PresenceMonitor();

    // IPresencePublisher — called from the controller strand on each PresenceTick.
    void publish_heartbeat(const RouterHealth &hb) override;

    // IPresencePublisher (D98) — called from the controller strand on each MeshTick, its
    // own independent cadence (kMeshPublishPeriodMs). Just republishes the mesh; on-change
    // publishes from on_health_data/on_health_reader_status are unaffected.
    void publish_mesh_tick() override;

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
    // process has no team_scoped participant.
    dds::domain::DomainParticipant team_scoped_participant_;

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
    // BEFORE roster_mutex_; publish_heartbeat takes only roster_mutex_ and never touches
    // this one (D98: the mesh publish moved to its own MeshTick / publish_mesh_tick(),
    // no longer called from publish_heartbeat).
    std::mutex mesh_write_mutex_;

    std::vector<dds::core::cond::Condition> conditions_;
    std::atomic<bool> shut_down_;

    // Phase 9 (D81) link-stats delta baselines for the RouterHealth pair. Strand-only
    // (collect_wan_stats), so no lock — separate from roster_mutex_.
    std::map<std::string, WriterTotals> health_writer_prev_;
    std::map<std::string, ReaderTotals> health_reader_prev_;
};

} // namespace router
