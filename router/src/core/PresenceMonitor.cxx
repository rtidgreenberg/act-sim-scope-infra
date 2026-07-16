// PresenceMonitor.cxx — Phase 8 presence & health (D75).

#include "PresenceMonitor.hpp"
#include "Log.hpp"

#include <dds/sub/ddssub.hpp>

#include <iomanip>
#include <sstream>

namespace router {

namespace {

// RouterHealth QoS (presence-and-health.md, D75): RELIABLE + TRANSIENT_LOCAL +
// KEEP_LAST(1), DEADLINE 2 s, AUTOMATIC liveliness lease 3 s. Same shape both sides
// (RxO-compatible by construction).
template <typename QosT>
QosT health_qos(QosT qos) {
    qos << dds::core::policy::Reliability::Reliable();
    qos << dds::core::policy::Durability::TransientLocal();
    qos << dds::core::policy::History::KeepLast(1);
    qos << dds::core::policy::Deadline(
            dds::core::Duration::from_millisecs(kHealthDeadlineMs));
    qos << dds::core::policy::Liveliness::Automatic().lease_duration(
            dds::core::Duration::from_millisecs(kHealthLivelinessLeaseMs));
    return qos;
}

dds::pub::qos::DataWriterQos mesh_writer_qos(const dds::pub::Publisher &publisher) {
    // LAN state topic, same shape as ActRouterStatus (D26).
    dds::pub::qos::DataWriterQos qos = publisher.default_datawriter_qos();
    qos << dds::core::policy::Reliability::Reliable();
    qos << dds::core::policy::Durability::TransientLocal();
    qos << dds::core::policy::History::KeepLast(1);
    return qos;
}

std::string format_guid(const dds::topic::BuiltinTopicKey &key) {
    const auto &v = key.value();
    std::ostringstream os;
    os << std::hex << std::setfill('0');
    for (size_t i = 0; i < v.size(); ++i) {
        if (i != 0) os << ':';
        os << std::setw(8) << v[i];
    }
    return os.str();
}

const char *presence_name(RouterPresenceState s) {
    switch (s) {
    case RouterPresenceState::PRESENCE_ALIVE: return "ALIVE";
    case RouterPresenceState::PRESENCE_STALE: return "STALE";
    case RouterPresenceState::PRESENCE_DEAD:  return "DEAD";
    }
    return "?";
}

} // namespace

PresenceMonitor::PresenceMonitor(rti::core::cond::AsyncWaitSet &aws,
                                 dds::domain::DomainParticipant wan_participant,
                                 dds::domain::DomainParticipant lan_participant,
                                 const std::string &node_name,
                                 const std::string &router_name,
                                 std::uint32_t router_id,
                                 const std::string &health_topic,
                                 const std::string &mesh_topic)
        : aws_(aws),
          node_name_(node_name),
          router_name_(router_name),
          router_id_(router_id),
          wan_publisher_(wan_participant),
          wan_subscriber_(wan_participant),
          health_topic_(wan_participant, health_topic),
          health_writer_(wan_publisher_, health_topic_,
                         health_qos(wan_publisher_.default_datawriter_qos())),
          health_reader_(wan_subscriber_, health_topic_,
                         health_qos(wan_subscriber_.default_datareader_qos())),
          lan_publisher_(lan_participant),
          mesh_topic_(lan_participant, mesh_topic),
          mesh_writer_(lan_publisher_, mesh_topic_, mesh_writer_qos(lan_publisher_)),
          shut_down_(false) {
    // Heartbeat data (valid + instance-state transitions -> ALIVE/DEAD).
    dds::sub::cond::ReadCondition data_cond(
            health_reader_, dds::sub::status::DataState::any(),
            [this]() { on_health_data(); });
    aws_.attach_condition(data_cond);
    conditions_.push_back(data_cond);
    // Withheld heartbeats -> STALE (reading the status inside the handler clears the
    // change flag, so the condition untriggers — same pattern as RouteRuntime, D45).
    dds::core::cond::StatusCondition reader_status(health_reader_);
    reader_status.enabled_statuses(
            dds::core::status::StatusMask::requested_deadline_missed());
    reader_status->handler([this]() { on_health_reader_status(); });
    aws_.attach_condition(reader_status);
    conditions_.push_back(reader_status);
    Log::info("presence_monitor_ready",
              {{"health_topic", health_topic},
               {"mesh_topic", mesh_topic},
               {"router_id", std::to_string(router_id_)}});
}

PresenceMonitor::~PresenceMonitor() {
    shutdown();
}

void PresenceMonitor::shutdown() {
    if (shut_down_.exchange(true)) return;
    for (const auto &cond : conditions_) {
        try {
            aws_.detach_condition(cond); // D32 blocking barrier per condition
        } catch (const std::exception &e) {
            Log::warn("presence_detach_condition_failed", {{"error", e.what()}});
        }
    }
    conditions_.clear();
}

void PresenceMonitor::publish_heartbeat(const RouterHealth &hb) {
    // D77: the heartbeat carries this router's roster as a compact edge list, so
    // who-sees-who is observable from any single point on the WAN (C2 node map). The
    // controller builds the summary without knowing the roster; the edges are filled
    // here, where the roster lives. DEAD entries are included deliberately — a DEAD
    // edge ("lost this peer") is information a missing edge ("never saw it") is not.
    RouterHealth out = hb;
    {
        std::lock_guard<std::mutex> lk(roster_mutex_);
        for (const auto &entry : roster_) {
            RouterPeerRef ref;
            ref.router_id = entry.first;
            ref.presence = entry.second.presence;
            out.peers_seen.push_back(ref);
        }
    }
    try {
        health_writer_.write(out);
    } catch (const dds::core::NotEnabledError &) {
        // Defensive symmetry with DdsStatusPublisher (D52): ticks only start after
        // enable_all(), so this should not happen in practice.
        Log::debug("heartbeat_skipped_not_enabled", {});
    } catch (const std::exception &e) {
        Log::warn("heartbeat_publish_failed", {{"error", e.what()}});
    }
}

std::string PresenceMonitor::participant_guid_of(std::uint32_t router_id) const {
    std::lock_guard<std::mutex> lk(roster_mutex_);
    auto it = roster_.find(router_id);
    return (it == roster_.end()) ? std::string() : it->second.participant_guid;
}

void PresenceMonitor::on_health_data() {
    auto samples = health_reader_.take();
    std::lock_guard<std::mutex> lk(roster_mutex_);
    bool changed = false;
    for (auto it = samples.begin(); it != samples.end(); ++it) {
        const std::string handle_key = [&] {
            std::ostringstream os;
            os << it->info().instance_handle();
            return os.str();
        }();
        if (it->info().valid()) {
            const RouterHealth &hb = it->data();
            if (hb.router_id == router_id_) {
                continue; // own heartbeat (the reader matches the local writer too)
            }
            handle_to_id_[handle_key] = hb.router_id;
            PeerEntry &e = roster_[hb.router_id];
            const bool was_alive =
                    e.presence == RouterPresenceState::PRESENCE_ALIVE
                    && e.last_seen != std::chrono::steady_clock::time_point();
            // Republish triggers (presence-and-health.md): peer appears, presence
            // transition, or the peer's state_revision advances.
            if (!was_alive || e.last_summary.state_revision != hb.state_revision) {
                changed = true;
            }
            if (e.presence != RouterPresenceState::PRESENCE_ALIVE) {
                Log::info("presence_peer_alive",
                          {{"router_id", std::to_string(hb.router_id)},
                           {"node", hb.node_name}});
            }
            e.presence = RouterPresenceState::PRESENCE_ALIVE;
            e.last_summary = hb;
            e.last_seen = std::chrono::steady_clock::now();
            if (e.participant_guid.empty()) {
                // Roster correlation router_id -> participant GUID (D74/D75): read the
                // heartbeat writer's participant key off its publication data.
                try {
                    dds::topic::PublicationBuiltinTopicData pub =
                            dds::sub::matched_publication_data(
                                    health_reader_, it->info().publication_handle());
                    e.participant_guid = format_guid(pub.participant_key());
                } catch (const std::exception &) {
                    // Writer already gone — leave empty; a later heartbeat refills it.
                }
            }
        } else if (it->info().state().instance_state()
                   == dds::sub::status::InstanceState::not_alive_no_writers()) {
            // Liveliness lost or participant purged -> DEAD (the roster passes through
            // STALE first on a real crash — deadline fires before the lease; D75).
            auto found = handle_to_id_.find(handle_key);
            if (found == handle_to_id_.end()) {
                continue; // never seen a valid heartbeat for this instance
            }
            PeerEntry &e = roster_[found->second];
            if (e.presence != RouterPresenceState::PRESENCE_DEAD) {
                e.presence = RouterPresenceState::PRESENCE_DEAD;
                e.participant_guid.clear(); // stale GUID: that participant is gone
                changed = true;
                Log::info("presence_peer_dead",
                          {{"router_id", std::to_string(found->second)}});
            }
        }
    }
    if (changed) {
        republish_mesh_locked();
    }
}

void PresenceMonitor::on_health_reader_status() {
    // Reading the status clears its change flag (condition untriggers).
    dds::core::status::RequestedDeadlineMissedStatus st =
            health_reader_.requested_deadline_missed_status();
    if (st.total_count() == deadline_missed_total_) {
        return;
    }
    deadline_missed_total_ = st.total_count();
    const std::string handle_key = [&] {
        std::ostringstream os;
        os << st.last_instance_handle();
        return os.str();
    }();
    std::lock_guard<std::mutex> lk(roster_mutex_);
    auto found = handle_to_id_.find(handle_key);
    if (found == handle_to_id_.end()) {
        return;
    }
    PeerEntry &e = roster_[found->second];
    // STALE only from ALIVE: deadline misses keep firing for a DEAD instance, and a
    // policy flag must never mask definite death (presence-and-health.md).
    if (e.presence == RouterPresenceState::PRESENCE_ALIVE) {
        e.presence = RouterPresenceState::PRESENCE_STALE;
        Log::info("presence_peer_stale",
                  {{"router_id", std::to_string(found->second)}});
        republish_mesh_locked();
    }
}

void PresenceMonitor::republish_mesh_locked() {
    RouterMeshStatus mesh;
    mesh.observer_node = node_name_;
    mesh.observer_router = router_name_;
    mesh.state_revision = ++mesh_revision_;
    const auto now = std::chrono::steady_clock::now();
    for (const auto &entry : roster_) {
        RouterMeshPeer peer;
        peer.health = entry.second.last_summary;
        peer.presence = entry.second.presence;
        peer.last_seen_delta_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                now - entry.second.last_seen).count();
        mesh.peers.push_back(peer);
        Log::debug("presence_mesh_peer",
                   {{"router_id", std::to_string(entry.first)},
                    {"presence", presence_name(entry.second.presence)}});
    }
    try {
        mesh_writer_.write(mesh);
    } catch (const dds::core::NotEnabledError &) {
        // Roster events before enable_all() would be discovery-order surprises, but a
        // disabled write must not crash the AWS worker (D52 defensive symmetry).
        Log::debug("mesh_publish_skipped_not_enabled", {});
    } catch (const std::exception &e) {
        Log::warn("mesh_publish_failed", {{"error", e.what()}});
    }
}

} // namespace router
