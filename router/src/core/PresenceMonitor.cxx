// PresenceMonitor.cxx — Phase 8 presence & health (D75), name-keyed roster (D79).

#include "PresenceMonitor.hpp"
#include "Log.hpp"

#include <dds/sub/ddssub.hpp>
#include <rti/core/policy/CorePolicy.hpp> // D101: PublishMode::Asynchronous (mesh_writer_qos)

#include <iterator> // std::next (prune_dead_locked's handle sweep)
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
    // LAN state topic. BEST_EFFORT + VOLATILE (D100): ActRouterMeshStatus republishes on
    // its own periodic MeshTick (kMeshPublishPeriodMs, 0.5s) regardless of change, so an
    // occasionally-dropped sample self-heals on the next tick — RELIABLE's retry/ack
    // machinery (and its blocking potential on this writer under backpressure) buys
    // nothing here. TRANSIENT_LOCAL was dropped too (D98 kept it, D100 removes it): under
    // BEST_EFFORT, TRANSIENT_LOCAL's late-joiner replay burst is itself just another
    // best-effort sample with no retry/repair if lost -- RTI's own docs are explicit that
    // TRANSIENT_LOCAL's replay is only GUARANTEED effective paired with RELIABLE. Keeping
    // it would have offered a repair mechanism that doesn't actually repair anything,
    // while still paying its retained-sample bookkeeping cost. A late-joining reader now
    // just waits for the next periodic MeshTick (worst case ~0.5s) instead of an
    // unreliable "instant" replay attempt -- simpler, and no less correct in practice.
    // The WIS reader profile (wis_config.xml.template) and both e2e mesh_reader() test
    // helpers must match: BEST_EFFORT + VOLATILE (RxO: both policies must independently
    // satisfy offered >= requested).
    //
    // D101: ASYNCHRONOUS publish mode. mesh_writer_.write() (publish_mesh()) is called
    // from publish_mesh_tick(), which — like publish_heartbeat() — runs on the controller
    // strand (MeshTick, D98); that strand must never block behind a DDS send (see
    // PresenceMonitor.hpp's threading doc, and Interfaces.hpp's IPresencePublisher
    // contract). BEST_EFFORT already removes the ack-wait blocking a RELIABLE writer could
    // hit; ASYNCHRONOUS additionally moves the actual network send itself off the calling
    // thread onto Connext's own async-publish thread (confirmed via ask_connext_question,
    // 7.7.0: the send happens on an RTI Publisher-owned thread, shared by every
    // asynchronous writer on this Publisher — lan_publisher_ carries only this one writer,
    // so no contention with another topic). Cheap insurance against any remaining local
    // resource-limit stall, on top of (not instead of) BEST_EFFORT.
    dds::pub::qos::DataWriterQos qos = publisher.default_datawriter_qos();
    qos << dds::core::policy::Reliability::BestEffort();
    qos << dds::core::policy::Durability::Volatile();
    qos << dds::core::policy::History::KeepLast(1);
    qos << rti::core::policy::PublishMode::Asynchronous();
    return qos;
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
                                 dds::domain::DomainParticipant team_scoped_participant,
                                 const std::string &health_topic,
                                 const std::string &mesh_topic)
        : aws_(aws),
          node_name_(node_name),
          router_name_(router_name),
          identity_(node_name + "/" + router_name),
          team_scoped_participant_(team_scoped_participant),
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
               {"identity", identity_}});
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
    // D93: team_partition is a LIVE poll of the team-scoped participant's actual
    // DomainParticipantQos.partition, taken fresh on every heartbeat — never cached, never
    // read from the controller's config-mirrored state. No lock needed: DDS entities are
    // internally thread-safe for concurrent QoS reads, and team_scoped_participant_ is set
    // once at construction and never mutated after. null when there's no team_scoped
    // participant (team_wan is retired under D103; platform_wan is the team_scoped one now).
    if (team_scoped_participant_ != dds::core::null) {
        dds::core::StringSeq names =
                team_scoped_participant_.qos().policy<dds::core::policy::Partition>().name();
        for (const std::string &n : names) {
            if (out.team_partition.size() == out.team_partition.max_size()) {
                break; // same bounded-sequence cap as participant_partition itself
            }
            out.team_partition.push_back(n);
        }
    }
    {
        const auto now = std::chrono::steady_clock::now();
        std::lock_guard<std::mutex> lk(roster_mutex_);
        for (const auto &entry : roster_) {
            // peers_seen is a codegen bounded_sequence (unbounded IDL -> cap 100);
            // growing past the cap throws PreconditionNotMetError, which would escape
            // the controller strand's DrainThread and terminate the process. Truncate
            // instead (name-order-lowest kept — roster_ is name-ordered) and say so once.
            if (out.peers_seen.size() == out.peers_seen.max_size()) {
                if (!heartbeat_peers_truncated_) {
                    heartbeat_peers_truncated_ = true;
                    Log::warn("heartbeat_peers_seen_truncated",
                              {{"roster", std::to_string(roster_.size())},
                               {"cap", std::to_string(out.peers_seen.max_size())}});
                }
                break;
            }
            RouterPeerRef ref;
            ref.router = entry.first;
            ref.presence = entry.second.presence;
            // D97: same "now - last heartbeat receipt" math as
            // build_mesh_locked()'s last_seen_delta_ms, off this router's own
            // steady_clock — relative, so it stays meaningful once relayed onto
            // another router's mesh aggregate without needing clock sync.
            ref.last_seen_delta_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                    now - entry.second.last_seen).count();
            out.peers_seen.push_back(ref);
        }
        if (out.peers_seen.size() < out.peers_seen.max_size()) {
            heartbeat_peers_truncated_ = false; // roster shrank back under the cap
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

// D98: the full-rate mesh publish for GUI consumers (mesh dashboard) used to happen
// unconditionally at the end of publish_heartbeat() above, coupling the LAN dashboard's
// refresh rate to the WAN heartbeat cadence and putting a synchronous LAN DDS write on
// the controller strand's WAN-heartbeat path. It now runs on its own MeshTick
// (kMeshPublishPeriodMs), on top of the existing on-change publishes in
// on_health_data/on_health_reader_status (which still fire immediately on a transition
// rather than waiting on either tick's period).
void PresenceMonitor::publish_mesh_tick() {
    publish_mesh();
}

void PresenceMonitor::collect_wan_stats(LinkStatsSink &sink) {
    // The bellwether pair: writer -> every peer's RouterHealth reader, reader <- every
    // peer's RouterHealth writer. Same discovery-DB attribution + self-delta as a route
    // WAN leg (D81), reusing the shared poll so there is one implementation.
    poll_writer_wan_stats(health_writer_, health_writer_prev_, sink);
    poll_reader_wan_stats(health_reader_, health_reader_prev_, sink);
}

void PresenceMonitor::on_health_data() {
    // Two phases so no DDS call runs under roster_mutex_ — the controller strand's
    // publish_heartbeat() takes the same mutex and must never block route control
    // behind loan iteration. Phase 1 (no lock): drain the loan into plain events.
    // Phase 2 (lock): apply the events to the roster and build the mesh; the mesh
    // write happens after.
    struct HealthEvent {
        bool valid = false;
        bool duplicate_identity = false; // valid sample wearing OUR name, foreign writer
        std::string handle_key;
        RouterHealth hb; // valid only
    };
    std::vector<HealthEvent> events;
    {
        auto samples = health_reader_.take();
        for (auto it = samples.begin(); it != samples.end(); ++it) {
            HealthEvent ev;
            ev.handle_key = [&] {
                std::ostringstream os;
                os << it->info().instance_handle();
                return os.str();
            }();
            if (it->info().valid()) {
                ev.valid = true;
                ev.hb = it->data();
                if (ev.hb.router == identity_) {
                    // A sample wearing OUR key: normally our own heartbeat (the reader
                    // matches the local writer — publication_handle IS that writer's
                    // instance handle). A foreign writer on our exact name is a
                    // duplicate-identity misconfiguration: it can never enter the
                    // roster (same key = OUR instance), but staying silent would make
                    // the two routers mutually invisible with no clue why.
                    if (it->info().publication_handle()
                        == health_writer_.instance_handle()) {
                        continue; // own heartbeat
                    }
                    ev.duplicate_identity = true;
                }
            } else if (it->info().state().instance_state()
                       != dds::sub::status::InstanceState::not_alive_no_writers()) {
                continue; // only NO_WRITERS transitions matter (D75)
            }
            events.push_back(std::move(ev));
        }
    } // loan returned before the lock
    if (events.empty()) {
        return;
    }

    bool changed = false;
    {
        std::lock_guard<std::mutex> lk(roster_mutex_);
        for (const HealthEvent &ev : events) {
            if (ev.duplicate_identity) {
                if (!duplicate_identity_warned_) { // warn once (edge-trigger)
                    duplicate_identity_warned_ = true;
                    Log::warn("presence_duplicate_identity",
                              {{"identity", identity_},
                               {"reason", "another router is heartbeating under this "
                                          "exact \"<node>/<router>\" name"}});
                }
                continue;
            }
            if (ev.valid) {
                handle_to_name_[ev.handle_key] = ev.hb.router;
                PeerEntry &e = roster_[ev.hb.router];
                const bool was_alive =
                        e.presence == RouterPresenceState::PRESENCE_ALIVE
                        && e.last_seen != std::chrono::steady_clock::time_point();
                // Republish triggers (presence-and-health.md): peer appears, presence
                // transition, or the peer's state_revision advances.
                if (!was_alive || e.last_summary.state_revision != ev.hb.state_revision) {
                    changed = true;
                }
                if (!was_alive) { // first appearance AND STALE/DEAD -> ALIVE recovery
                    Log::info("presence_peer_alive", {{"router", ev.hb.router}});
                }
                e.presence = RouterPresenceState::PRESENCE_ALIVE;
                e.last_summary = ev.hb;
                // D97: last_summary.peers_seen is kept (not stripped) so
                // build_mesh_locked() can re-embed it into RouterMeshPeer.health —
                // the mesh-dashboard's transitive/second-hand edges (mesh_graph.js)
                // read exactly this field. D77 originally stripped it here to avoid
                // "redundant" nesting, but that rationale was about WAN frugality;
                // ActRouterMeshStatus is LAN-only and each entry stays bounded by
                // peers_seen's existing 100-cap, so keeping it costs nothing D77 was
                // guarding against.
                e.last_seen = std::chrono::steady_clock::now();
            } else {
                // Liveliness lost or participant purged -> DEAD (the roster passes
                // through STALE first on a real crash — deadline fires before the
                // lease; D75).
                auto found = handle_to_name_.find(ev.handle_key);
                if (found == handle_to_name_.end()) {
                    continue; // never seen a valid heartbeat for this instance
                }
                PeerEntry &e = roster_[found->second];
                if (e.presence != RouterPresenceState::PRESENCE_DEAD) {
                    e.presence = RouterPresenceState::PRESENCE_DEAD;
                    changed = true;
                    Log::info("presence_peer_dead", {{"router", found->second}});
                }
            }
        }
        // D99: bump the mesh generation counter here — once per batch, still under
        // roster_mutex_ — rather than in build_mesh_locked() (which now just reads it).
        // build_mesh_locked() runs unconditionally on every MeshTick (D98) regardless of
        // whether anything changed, so it can no longer be the one deciding "did the
        // roster change" — this `changed` flag is that decision, computed once per
        // batch (not per peer), matching RouterAdminTypes.idl's documented contract for
        // RouterMeshStatus.state_revision ("bumps when the roster changes").
        if (changed) {
            ++mesh_revision_;
        }
    }
    if (changed) {
        publish_mesh();
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
    bool changed = false;
    {
        std::lock_guard<std::mutex> lk(roster_mutex_);
        // STALE only from ALIVE: deadline misses keep firing for a DEAD instance, and a
        // policy flag must never mask definite death (presence-and-health.md).
        auto mark_stale = [&](const std::string &name, PeerEntry &e) {
            if (e.presence != RouterPresenceState::PRESENCE_ALIVE) {
                return;
            }
            e.presence = RouterPresenceState::PRESENCE_STALE;
            changed = true;
            Log::info("presence_peer_stale", {{"router", name}});
        };
        // The status carries only last_instance_handle, so coalesced misses (several
        // peers silent inside one dispatch window) would surface just one peer through
        // the handle. Handle path first for the triggering instance (whose last_seen
        // delta can still be marginally under the deadline), then a last_seen sweep
        // catches every other overdue ALIVE peer in the same pass.
        auto found = handle_to_name_.find(handle_key);
        if (found != handle_to_name_.end()) {
            auto rit = roster_.find(found->second);
            if (rit != roster_.end()) {
                mark_stale(rit->first, rit->second);
            }
        }
        const auto now = std::chrono::steady_clock::now();
        for (auto &entry : roster_) {
            if (now - entry.second.last_seen
                >= std::chrono::milliseconds(kHealthDeadlineMs)) {
                mark_stale(entry.first, entry.second);
            }
        }
        // D99: same once-per-batch bump as on_health_data() — see its comment.
        if (changed) {
            ++mesh_revision_;
        }
    }
    if (changed) {
        publish_mesh();
    }
}

bool PresenceMonitor::prune_dead_locked() {
    const auto now = std::chrono::steady_clock::now();
    const auto retention = std::chrono::milliseconds(kDeadPeerRetentionMs);
    bool pruned = false;
    for (auto it = roster_.begin(); it != roster_.end();) {
        if (it->second.presence != RouterPresenceState::PRESENCE_DEAD
            || now - it->second.last_seen < retention) {
            ++it;
            continue;
        }
        // Drop every handle that resolved to this name. A peer can have been seen under
        // more than one instance handle across a restart, so this is a scan by value, not
        // a single erase — the map is roster-sized, and this runs only when a peer is
        // actually being forgotten.
        for (auto h = handle_to_name_.begin(); h != handle_to_name_.end();) {
            h = (h->second == it->first) ? handle_to_name_.erase(h) : std::next(h);
        }
        Log::info("presence_peer_forgotten",
                  {{"router", it->first},
                   {"dead_for_ms", std::to_string(
                        std::chrono::duration_cast<std::chrono::milliseconds>(
                                now - it->second.last_seen).count())}});
        it = roster_.erase(it);
        pruned = true;
    }
    return pruned;
}

RouterMeshStatus PresenceMonitor::build_mesh_locked() {
    RouterMeshStatus mesh;
    mesh.observer_node = node_name_;
    mesh.observer_router = router_name_;
    // D99: read only — on_health_data()/on_health_reader_status() own the increment
    // (once per real change, under the same roster_mutex_ this function is always
    // called under). This function runs on every MeshTick regardless of whether the
    // roster changed, so it can no longer be the one bumping the counter.
    mesh.state_revision = mesh_revision_;
    const auto now = std::chrono::steady_clock::now();
    for (const auto &entry : roster_) {
        // Same bounded_sequence cap as peers_seen (unbounded IDL -> 100): overflowing
        // it throws on the AWS worker; truncate (name-order-lowest kept) and say so once.
        if (mesh.peers.size() == mesh.peers.max_size()) {
            if (!mesh_peers_truncated_) {
                mesh_peers_truncated_ = true;
                Log::warn("mesh_peers_truncated",
                          {{"roster", std::to_string(roster_.size())},
                           {"cap", std::to_string(mesh.peers.max_size())}});
            }
            break;
        }
        RouterMeshPeer peer;
        peer.health = entry.second.last_summary;
        peer.presence = entry.second.presence;
        peer.last_seen_delta_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                now - entry.second.last_seen).count();
        // D102: bound + refresh the transitive (second-hand) edges re-embedded from this
        // peer's own cached peers_seen (D97 stopped stripping it). Two problems fixed at
        // once, both from the same root cause -- these nested refs are frozen at whatever
        // the peer itself reported at ITS last heartbeat, never touched again until a
        // fresh one arrives:
        //   1. O(N*M) growth: peers_seen is independently capped at 100 same as
        //      mesh.peers, so an uncapped re-embed can put up to 100*100 RouterPeerRef
        //      entries in one sample. A STALE/DEAD nested edge is stale telemetry about a
        //      peer WE don't even watch directly -- low value for the mesh-dashboard's
        //      transitive view -- so keep ALIVE-only, which bounds the realistic case to
        //      however many peers are actually up, not the worst-case cap.
        //   2. Frozen freshness: mesh_graph.js recomputes asOfMs = Date.now() -
        //      last_seen_delta_ms on every ingest; since this nested delta was never
        //      updated relative to OUR "now", a transitive edge visually "freshened" every
        //      MeshTick even while the underlying data went stale. Fix: age each kept
        //      edge's delta forward by peer.last_seen_delta_ms (how long OUR OWN view of
        //      this peer has sat since we last heard from it) -- the peer stamped its
        //      delta relative to its own "now" at heartbeat time, so from our perspective
        //      as of right now that edge is (peer's reported delta + our own staleness on
        //      this peer) old.
        {
            const auto reported = peer.health.peers_seen;
            peer.health.peers_seen.clear();
            for (const auto &sub : reported) {
                if (sub.presence != RouterPresenceState::PRESENCE_ALIVE) {
                    continue;
                }
                if (peer.health.peers_seen.size() == peer.health.peers_seen.max_size()) {
                    break; // defensive only -- an ALIVE-only subset of an already-capped
                            // list can't actually exceed the same cap
                }
                RouterPeerRef aged = sub;
                aged.last_seen_delta_ms += peer.last_seen_delta_ms;
                peer.health.peers_seen.push_back(aged);
            }
        }
        mesh.peers.push_back(peer);
        Log::debug("presence_mesh_peer",
                   {{"router", entry.first},
                    {"presence", presence_name(entry.second.presence)}});
    }
    if (mesh.peers.size() < mesh.peers.max_size()) {
        mesh_peers_truncated_ = false; // roster shrank back under the cap
    }
    return mesh;
}

void PresenceMonitor::publish_mesh() {
    // The DDS write happens OFF roster_mutex_ (publish_heartbeat on the controller
    // strand contends on it and must never block route control), but build+write are
    // serialized under mesh_write_mutex_ so two concurrently-triggered handlers cannot
    // put mesh revisions on the wire out of order (KEEP_LAST(1): an older snapshot
    // written last would stick). Lock order: mesh_write_mutex_ -> roster_mutex_;
    // publish_heartbeat takes only roster_mutex_, so no cycle.
    std::lock_guard<std::mutex> wl(mesh_write_mutex_);
    RouterMeshStatus mesh;
    {
        std::lock_guard<std::mutex> lk(roster_mutex_);
        // H4: forget long-dead peers first, so this sample (and the next heartbeat's
        // peers_seen, which reads the same roster) no longer carries them. Forgetting a
        // peer is a roster change, so it bumps the revision here — build_mesh_locked()
        // stays a pure read (D99).
        if (prune_dead_locked()) {
            ++mesh_revision_;
        }
        mesh = build_mesh_locked();
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
