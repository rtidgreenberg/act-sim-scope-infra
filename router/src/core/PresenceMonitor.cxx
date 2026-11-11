// PresenceMonitor.cxx — Phase 8 presence & health (D75), name-keyed roster (D79).

#include "PresenceMonitor.hpp"
#include "Log.hpp"

#include <dds/sub/ddssub.hpp>
#include <rti/sub/discoveryImpl.hpp>
#include <cstdlib>
#include <fstream>
#include <iterator> // std::next (prune_dead_locked's handle sweep)
#include <random>
#include <set>
#include <sstream>

namespace router {

namespace {

std::int64_t monotonic_us(std::chrono::steady_clock::time_point time) {
    return std::chrono::duration_cast<std::chrono::microseconds>(
            time.time_since_epoch()).count();
}

std::uint64_t new_health_session() {
    std::random_device entropy;
    for (int attempt = 0; attempt < 8; ++attempt) {
        const auto token = (static_cast<std::uint64_t>(entropy()) << 32) | entropy();
        if (token) return token;
    }
    throw std::runtime_error("Unable to generate a nonzero RouterHealth session token");
}

std::string handle_string(const dds::core::InstanceHandle &handle) {
    std::ostringstream os;
    os << handle;
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

std::int64_t now_unix_ns() {
    return static_cast<std::int64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
}

std::string trim_newline(std::string value) {
    while (!value.empty() && (value.back() == '\n' || value.back() == '\r')) {
        value.pop_back();
    }
    return value;
}

std::string read_run_id(const std::string &debug_dir) {
    const char *env = std::getenv("ACT_RUN_ID");
    if (env != nullptr && *env != '\0') {
        return env;
    }
    if (debug_dir.empty()) {
        return "";
    }
    std::ifstream input(debug_dir + "/run_id");
    std::string value;
    std::getline(input, value);
    return trim_newline(value);
}

std::string json_escape(const std::string &value) {
    std::string out;
    out.reserve(value.size() + 8);
    for (char c : value) {
        switch (c) {
        case '\\': out += "\\\\"; break;
        case '"': out += "\\\""; break;
        case '\b': out += "\\b"; break;
        case '\f': out += "\\f"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default: out.push_back(c); break;
        }
    }
    return out;
}

std::string node_from_router(const std::string &router) {
    const std::string::size_type slash = router.find('/');
    return slash == std::string::npos ? router : router.substr(0, slash);
}

} // namespace

PresenceMonitor::PresenceMonitor(rti::core::cond::AsyncWaitSet &aws,
                                 dds::domain::DomainParticipant wan_participant,
                                 dds::domain::DomainParticipant lan_participant,
                                 const std::string &node_name,
                                 const std::string &router_name,
                                 const dds::sub::qos::DataReaderQos &health_reader_qos,
                                 const dds::pub::qos::DataWriterQos &health_writer_qos,
                                 const dds::pub::qos::DataWriterQos &mesh_writer_qos,
                                 const dds::pub::qos::DataWriterQos &diagnostics_writer_qos,
                                 dds::domain::DomainParticipant team_scoped_participant,
                                 const std::string &health_topic,
                                 const std::string &mesh_topic,
                                 dds::core::QosProvider wan_types,
                                 WanAdapterFactory* shared_status)
        : aws_(aws),
          node_name_(node_name),
          router_name_(router_name),
          identity_(node_name + "/" + router_name),
          health_session_(new_health_session()),
          rtt_(health_session_),
          team_scoped_participant_(team_scoped_participant),
          shared_status_(shared_status),
          wan_publisher_(shared_status ? dds::core::null : dds::pub::Publisher(wan_participant)),
          wan_subscriber_(shared_status ? dds::core::null : dds::sub::Subscriber(wan_participant)),
          status_codec_(wan_types == dds::core::null ? nullptr : new WanCodec(wan_types)),
          health_topic_(shared_status ? dds::core::null
                  : dds::topic::Topic<dds::core::xtypes::DynamicData>(
                          wan_participant, health_topic,
                          rti::topic::dynamic_type<RouterHealth>::get())),
          health_writer_(shared_status ? dds::core::null
                  : dds::pub::DataWriter<dds::core::xtypes::DynamicData>(
                          wan_publisher_, health_topic_, health_writer_qos)),
          health_reader_(shared_status ? dds::core::null
                  : dds::sub::DataReader<dds::core::xtypes::DynamicData>(
                          wan_subscriber_, health_topic_, health_reader_qos)),
          lan_publisher_(lan_participant),
          mesh_topic_(lan_participant, mesh_topic),
          mesh_writer_(lan_publisher_, mesh_topic_, mesh_writer_qos),
          diagnostics_topic_(lan_participant, "ActRouterDiagnostics",
                             rti::topic::dynamic_type<RouterDiagnostics>::get()),
          diagnostics_writer_(lan_publisher_, diagnostics_topic_, diagnostics_writer_qos),
          shut_down_(false) {
    const char *debug_dir = std::getenv("NODE_DEBUG_DIR");
    if (debug_dir != nullptr && *debug_dir != '\0') {
        audit_run_id_ = read_run_id(debug_dir);
        if (!audit_run_id_.empty()) {
            audit_path_ = std::string(debug_dir) + "/events.jsonl";
        }
    }
    if (shared_status_) {
        if (!status_codec_) throw std::runtime_error("shared Status requires the WAN type catalog");
        const auto text = health_reader_qos.policy<rti::core::policy::Property>().get(
                "act.presence.dead_after_stale_ms");
        size_t used = 0;
        const auto milliseconds = std::stoll(text, &used);
        if (used != text.size() || milliseconds <= 0) {
            throw std::runtime_error("act.presence.dead_after_stale_ms must be a positive integer");
        }
        dead_after_stale_ = std::chrono::milliseconds(milliseconds);
        status_callbacks_ = std::make_shared<WanStatusCallbacks>();
        status_callbacks_->data = [this](const dds::core::xtypes::DynamicData& frame,
                                         const dds::sub::SampleInfo& info) {
            on_status_data(frame, info);
        };
        const std::weak_ptr<StatusDeadlineQueue> pending = status_deadlines_;
        status_callbacks_->health_missing = [pending](const dds::core::xtypes::DynamicData& key) {
            auto queue = pending.lock();
            if (!queue) return;
            const auto source = key.value<std::string>("source_id");
            if (!queue->missed(source)) {
                Log::warn("presence_deadline_queue_full", {{"source_id", source}});
            }
        };
        shared_status_->bind_status(status_callbacks_);
        health_writer_ = shared_status_->status_writer();
        health_reader_ = shared_status_->status_reader();
        Log::info("presence_monitor_ready", {{"health_topic", "Status"},
                  {"identity", identity_}, {"dead_after_stale_ms", text}});
        return;
    }
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
              {{"health_topic", health_topic_.name()},
               {"mesh_topic", mesh_topic},
               {"identity", identity_}});
}

PresenceMonitor::~PresenceMonitor() {
    shutdown();
}

void PresenceMonitor::shutdown() {
    if (shut_down_.exchange(true)) return;
    if (status_callbacks_) {
        std::lock_guard<std::mutex> lock(status_callbacks_->mutex);
        status_callbacks_->data = {};
    }
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
        std::lock_guard<std::mutex> lk(roster_mutex_);
        std::vector<std::string> eligible;
        for (const auto &entry : roster_) {
            if (entry.second.presence != RouterPresenceState::PRESENCE_DEAD)
                eligible.push_back(entry.first);
        }
        for (const auto &entry : roster_) {
            if (entry.second.presence == RouterPresenceState::PRESENCE_DEAD)
                eligible.push_back(entry.first);
        }
        // Rotate only live coverage; retained DEAD topology must not starve live RTT.
        const auto live_end = std::find_if(eligible.begin(), eligible.end(),
                [&](const std::string &name) {
                    return roster_.at(name).presence == RouterPresenceState::PRESENCE_DEAD;
                });
        const auto live_count = static_cast<std::size_t>(live_end - eligible.begin());
        HeartbeatRtt::rotate_live_coverage(eligible, live_count,
                                          out.peers_seen.max_size(), heartbeat_peer_cursor_);
        const auto now = std::chrono::steady_clock::now();
        out.session_id = health_session_;
        out.peers_seen.clear();
        for (const auto &name : eligible) {
            const auto &entry = *roster_.find(name);
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
            // D97: same "now - last Status receipt" math as
            // build_mesh_locked()'s direct last_seen_delta_ms, off this router's own
            // steady_clock — relative, so it stays meaningful once relayed onto
            // another router's mesh aggregate without needing clock sync.
            ref.last_seen_delta_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                    now - entry.second.last_status_received).count();
            out.peers_seen.push_back(ref);
        }
        if (out.peers_seen.size() < out.peers_seen.max_size()) {
            heartbeat_peers_truncated_ = false; // roster shrank back under the cap
        }
    }
    bool success = false;
    try {
        out.wan_writer_filters.clear();
        if (shared_status_) {
            for (auto& observation : shared_status_->writer_filter_observations()) {
                out.wan_writer_filters.push_back(std::move(observation));
            }
        }
        const auto local = rti::core::xtypes::convert(out);
        WanMetadata metadata;
        if (status_codec_) {
            metadata.source_id = node_name_;
            metadata.source_role = out.role;
            metadata.run_id = audit_run_id_;
            metadata.session_id = out.session_id;
            metadata.audit_sequence = out.heartbeat_seq;
            metadata.sent_at_ns = out.send_timestamp;
            metadata.membership_revision = out.state_revision;
            metadata.global = true;
        }
        auto sample = status_codec_
                ? status_codec_->pack("RouterHealth", local, metadata) : local;
        // Stamp after envelope conversion so RTT subtracts the encoding wait too.
        const auto stamp = [&](dds::core::xtypes::DynamicData &health) {
            auto peers = health.loan_value("peers_seen");
            std::lock_guard<std::mutex> lk(roster_mutex_);
            const auto boundary = monotonic_us(std::chrono::steady_clock::now());
            for (size_t index = 0; index < out.peers_seen.size(); ++index) {
                auto &peer = out.peers_seen[index];
                const auto ack = rtt_.acknowledgment(peer.router, boundary);
                peer.ack_session_id = ack.session;
                peer.ack_heartbeat_seq = ack.sequence;
                peer.ack_age_at_send_us = ack.age_us;
                auto ref = peers.get().loan_value(static_cast<uint32_t>(index + 1));
                ref.get().value<uint64_t>("ack_session_id", peer.ack_session_id);
                ref.get().value<uint64_t>("ack_heartbeat_seq", peer.ack_heartbeat_seq);
                ref.get().value<uint64_t>("ack_age_at_send_us", peer.ack_age_at_send_us);
            }
            rtt_.reserve(out.heartbeat_seq, boundary);
        };
        if (status_codec_) {
            auto payload = sample.loan_value("payload");
            auto health = payload.get().loan_value("health");
            stamp(health.get());
        } else {
            stamp(sample);
        }
        health_writer_.write(sample);
        success = true;
        log_router_health_audit("sent", out);
    } catch (const dds::core::NotEnabledError &) {
        // Defensive symmetry with DdsStatusPublisher (D52): ticks only start after
        // enable_all(), so this should not happen in practice.
        Log::debug("heartbeat_skipped_not_enabled", {});
    } catch (const std::exception &e) {
        Log::warn("heartbeat_publish_failed", {{"error", e.what()}});
    }
    {
        std::lock_guard<std::mutex> lk(roster_mutex_);
        rtt_.finalize(out.heartbeat_seq, success);
        if (success) {
            last_published_health_ = out;
            last_health_published_at_ = std::chrono::steady_clock::now();
        }
    }
}

// D98: the full-rate mesh publish for GUI consumers (mesh dashboard) used to happen
// unconditionally at the end of publish_heartbeat() above, coupling the LAN dashboard's
// refresh rate to the WAN heartbeat cadence and putting a synchronous LAN DDS write on
// the controller strand's WAN-heartbeat path. It now runs on its own MeshTick
// (kMeshPublishPeriodMs), on top of the existing on-change publishes in
// on_health_data/on_health_reader_status (which still fire immediately on a transition
// rather than waiting on either tick's period).
void PresenceMonitor::publish_diagnostics(const RouterDiagnostics &snapshot) {
    try {
        diagnostics_writer_.write(rti::core::xtypes::convert(snapshot));
    } catch (const dds::core::NotEnabledError &) {
        Log::debug("diagnostics_skipped_not_enabled", {});
    } catch (const std::exception &e) {
        Log::warn("diagnostics_publish_failed", {{"error", e.what()}});
    }
}

void PresenceMonitor::publish_mesh_tick() {
    if (shared_status_) apply_status_deadlines();
    publish_mesh();
}

void PresenceMonitor::collect_wan_stats(LinkStatsSink &sink) {
    // The bellwether pair: writer -> every peer's RouterHealth reader, reader <- every
    // peer's RouterHealth writer. Same discovery-DB attribution + self-delta as a route
    // WAN leg (D81), reusing the shared poll so there is one implementation.
    if (!shared_status_) {
        poll_writer_wan_stats(health_writer_, health_writer_prev_, sink,
                              "presence", health_topic_.name());
        poll_reader_wan_stats(health_reader_, health_reader_prev_, sink,
                              "presence", health_topic_.name());
    }
    std::lock_guard<std::mutex> lk(roster_mutex_);
    rtt_.collect(sink, monotonic_us(std::chrono::steady_clock::now()));
    const auto rejected = rtt_.take_rejections();
    if (rejected) {
        Log::warn("heartbeat_rtt_rejected", {{"count", std::to_string(rejected)}});
    }
}

RouterHealth PresenceMonitor::decode_health(
        const dds::core::xtypes::DynamicData &sample) const {
    const auto local = status_codec_ ? status_codec_->unpack(sample).data : sample;
    if (!rti::core::xtypes::can_convert<RouterHealth>(local)) {
        throw std::runtime_error("presence sample is not RouterHealth");
    }
    const auto health = rti::core::xtypes::convert<RouterHealth>(local);
    if (status_codec_ &&
        (sample.value<std::string>("source_id") != node_from_router(health.router) ||
         sample.value<uint64_t>("session_id") != health.session_id ||
         sample.value<uint64_t>("audit_sequence") != health.heartbeat_seq ||
         sample.value<int64_t>("sent_at_ns") != health.send_timestamp)) {
        throw std::runtime_error("presence envelope does not match RouterHealth identity/audit");
    }
    return health;
}

void PresenceMonitor::on_health_data() {
    on_health_samples(nullptr, nullptr);
}

void PresenceMonitor::on_status_data(const dds::core::xtypes::DynamicData& frame,
                                     const dds::sub::SampleInfo& info) {
    if (frame.value<std::string>("original_topic") == "RouterHealth") {
        on_health_samples(&frame, &info);
        return;
    }
    const auto source = frame.value<std::string>("source_id");
    std::string identity;
    {
        std::lock_guard<std::mutex> lock(roster_mutex_);
        for (const auto& peer : roster_) {
            if (node_from_router(peer.first) == source) {
                identity = peer.first;
                break;
            }
        }
    }
    if (identity.empty()) {
        try {
            const auto participant = rti::sub::matched_publication_participant_data(
                    health_reader_, info.publication_handle());
            const auto name = participant->participant_name().name();
            if (name.is_set()) identity = name.get();
            if (identity.empty() || node_from_router(identity) != source) {
                Log::warn("presence_status_source_mismatch", {{"source_id", source}});
                return;
            }
        } catch (const std::exception& error) {
            Log::warn("presence_status_source_unresolved", {{"source_id", source},
                      {"error", error.what()}});
            return;
        }
    }
    bool changed = false;
    {
        std::lock_guard<std::mutex> lock(roster_mutex_);
        auto found = roster_.find(identity);
        if (found == roster_.end()) {
            if (roster_.size() >= HeartbeatRtt::peer_cap) {
                Log::warn("presence_roster_full", {{"router", identity}});
                return;
            }
            found = roster_.emplace(identity, PeerEntry{}).first;
            found->second.last_summary.router = identity;
            found->second.last_summary.role = frame.value<std::string>("source_role");
            changed = true;
        }
        auto& peer = found->second;
        changed = changed || peer.presence != RouterPresenceState::PRESENCE_ALIVE;
        peer.presence = RouterPresenceState::PRESENCE_ALIVE;
        peer.last_status_received = status_deadlines_->received(source);
        peer.stale_since = {};
        if (changed) ++mesh_revision_;
    }
    if (changed) publish_mesh();
}

void PresenceMonitor::apply_status_deadlines() {
    const auto events = status_deadlines_->take();
    std::lock_guard<std::mutex> lock(roster_mutex_);
    bool changed = false;
    for (auto& item : roster_) {
        auto& peer = item.second;
        const auto event = events.find(node_from_router(item.first));
        if (event != events.end() && event->second >= peer.last_status_received &&
            peer.presence == RouterPresenceState::PRESENCE_ALIVE) {
            peer.presence = RouterPresenceState::PRESENCE_STALE;
            peer.stale_since = event->second;
            changed = true;
            Log::info("presence_peer_stale", {{"router", item.first}});
        }
        if (peer.presence == RouterPresenceState::PRESENCE_STALE &&
            std::chrono::steady_clock::now() - peer.stale_since >= dead_after_stale_) {
            peer.presence = RouterPresenceState::PRESENCE_DEAD;
            rtt_.disconnect(item.first);
            changed = true;
            Log::info("presence_peer_dead", {{"router", item.first},
                      {"reason", "no Status after STALE countdown"}});
        }
    }
    if (changed) ++mesh_revision_;
}

void PresenceMonitor::on_health_samples(const dds::core::xtypes::DynamicData* frame,
                                       const dds::sub::SampleInfo* info) {
    // Two phases so no DDS call runs under roster_mutex_ — the controller strand's
    // publish_heartbeat() takes the same mutex and must never block route control
    // behind loan iteration. Phase 1 (no lock): drain the loan into plain events.
    // Phase 2 (lock): apply the events to the roster and build the mesh; the mesh
    // write happens after.
    struct HealthEvent {
        bool valid = false;
        bool duplicate_identity = false; // valid sample wearing OUR name, foreign writer
        std::string handle_key;
        std::string writer_key;
        std::chrono::steady_clock::time_point received;
        RouterHealth hb; // valid only
    };
    std::vector<HealthEvent> events;
    std::set<std::string> alive_writers;
    for (const auto &handle : dds::sub::matched_publications(health_reader_)) {
        try {
            if (rti::sub::is_matched_publication_alive(health_reader_, handle)) {
                alive_writers.insert(handle_string(handle));
            }
        } catch (const dds::core::InvalidArgumentError &ex) {
            Log::warn("presence_publication_unmatched",
                      {{"writer", handle_string(handle)}, {"error", ex.what()}});
        }
    }
    const auto append = [&](const dds::core::xtypes::DynamicData* data,
                            const dds::sub::SampleInfo& sample_info) {
            HealthEvent ev;
            ev.received = std::chrono::steady_clock::now();
            ev.writer_key = handle_string(sample_info.publication_handle());
            ev.handle_key = handle_string(sample_info.instance_handle());
            if (sample_info.valid()) {
                ev.valid = true;
                try {
                    ev.hb = decode_health(*data);
                } catch (const std::exception &error) {
                    Log::warn("presence_invalid_sample", {{"error", error.what()}});
                    return;
                }
                if (ev.hb.router == identity_) {
                    // A sample wearing OUR key: normally our own heartbeat (the reader
                    // matches the local writer — publication_handle IS that writer's
                    // instance handle). A foreign writer on our exact name is a
                    // duplicate-identity misconfiguration: it can never enter the
                    // roster (same key = OUR instance), but staying silent would make
                    // the two routers mutually invisible with no clue why.
                    if (sample_info.publication_handle()
                        == health_writer_.instance_handle()) {
                        return;
                    }
                    ev.duplicate_identity = true;
                }
            } else if (sample_info.state().instance_state()
                       != dds::sub::status::InstanceState::not_alive_no_writers()) {
                return;
            }
            events.push_back(std::move(ev));
    };
    if (frame) {
        append(frame, *info);
    } else {
        auto samples = health_reader_.take();
        for (const auto& sample : samples) {
            append(sample.info().valid() ? &sample.data() : nullptr, sample.info());
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
                if (ev.hb.router.empty()) {
                    Log::warn("presence_invalid_identity", {});
                    continue;
                }
                if (roster_.find(ev.hb.router) == roster_.end()
                    && roster_.size() >= HeartbeatRtt::peer_cap) {
                    Log::warn("presence_roster_full", {{"router", ev.hb.router}});
                    continue;
                }
                const auto previous = rtt_.writer_for(ev.hb.router);
                const bool direct = alive_writers.count(ev.writer_key)
                    && rtt_.observe(ev.hb.router, ev.writer_key, ev.hb.session_id,
                                    ev.hb.heartbeat_seq, monotonic_us(ev.received),
                                    alive_writers.count(previous) != 0);
                if (direct) {
                    const auto count = std::count_if(ev.hb.peers_seen.begin(),
                            ev.hb.peers_seen.end(), [&](const RouterPeerRef &ref) {
                                return ref.router == identity_;
                            });
                    if (count == 1) {
                        for (const auto &ref : ev.hb.peers_seen) {
                            if (ref.router != identity_) continue;
                            if (!ref.ack_session_id && !ref.ack_heartbeat_seq
                                && !ref.ack_age_at_send_us) continue;
                            HeartbeatRtt::Ack ack;
                            ack.session = ref.ack_session_id;
                            ack.sequence = ref.ack_heartbeat_seq;
                            ack.age_us = ref.ack_age_at_send_us;
                            rtt_.measure(ev.hb.router, ev.hb.session_id, ack,
                                         monotonic_us(ev.received));
                        }
                    } else if (count > 1) {
                        rtt_.note_rejection();
                    }
                }
                handle_to_name_[ev.handle_key] = ev.hb.router;
                PeerEntry &e = roster_[ev.hb.router];
                const bool was_alive =
                        e.presence == RouterPresenceState::PRESENCE_ALIVE
                        && e.last_status_received != std::chrono::steady_clock::time_point();
                // Republish triggers (presence-and-health.md): peer appears, presence
                // transition, or the peer's state_revision advances.
                if (!was_alive || e.last_summary.state_revision != ev.hb.state_revision) {
                    changed = true;
                }
                if (!was_alive) { // first appearance AND STALE/DEAD -> ALIVE recovery
                    Log::info("presence_peer_alive", {{"router", ev.hb.router}});
                }
                e.presence = RouterPresenceState::PRESENCE_ALIVE;
                e.stale_since = {};
                e.last_summary = ev.hb;
                // D97: last_summary.peers_seen is kept (not stripped) so
                // build_mesh_locked() can re-embed it into RouterMeshPeer.health —
                // the mesh-dashboard's transitive/second-hand edges (mesh_graph.js)
                // read exactly this field. D77 originally stripped it here to avoid
                // "redundant" nesting, but that rationale was about WAN frugality;
                // ActRouterMeshStatus is LAN-only and each entry stays bounded by
                // peers_seen's existing 100-cap, so keeping it costs nothing D77 was
                // guarding against.
                e.last_status_received = shared_status_
                        ? status_deadlines_->received(node_from_router(ev.hb.router))
                        : std::chrono::steady_clock::now();
                e.last_health_received = e.last_status_received;
                log_router_health_audit("received", ev.hb);
            } else {
                // Liveliness lost or participant purged -> DEAD (the roster passes
                // through STALE first on a real crash — deadline fires before the
                // lease; D75).
                auto found = handle_to_name_.find(ev.handle_key);
                if (found == handle_to_name_.end()) {
                    continue; // never seen a valid heartbeat for this instance
                }
                PeerEntry &e = roster_[found->second];
                rtt_.disconnect(found->second);
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

void PresenceMonitor::log_router_health_audit(const std::string &event,
                                              const RouterHealth &hb) {
    if (audit_path_.empty() || audit_run_id_.empty()) {
        return;
    }
    const std::string source_node = node_from_router(hb.router);
    if (source_node.empty() || hb.heartbeat_seq == 0 || hb.send_timestamp == 0) {
        return;
    }
    std::lock_guard<std::mutex> lk(audit_mutex_);
    std::ofstream output(audit_path_, std::ios::app);
    if (!output) {
        return;
    }
    output << "{\"event\":\"" << json_escape(event)
           << "\",\"node\":\"" << json_escape(node_name_)
           << "\",\"recorded_at_ns\":" << now_unix_ns()
           << ",\"run_id\":\"" << json_escape(audit_run_id_)
           << "\",\"sent_at_ns\":" << hb.send_timestamp
           << ",\"sequence\":" << hb.heartbeat_seq
           << ",\"source\":\"" << json_escape(hb.router)
           << "\",\"source_node\":\"" << json_escape(source_node)
           << "\",\"topic\":\"RouterHealth\"}\n";
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
        // the handle. Handle path first for the triggering instance (whose health age
        // can still be marginally under the deadline), then a health-receipt sweep
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
            if (now - entry.second.last_health_received
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
            || now - it->second.last_status_received < retention) {
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
                                now - it->second.last_status_received).count())}});
        rtt_.forget(it->first);
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
    mesh.observer_health = last_published_health_;
    mesh.observer_health_age_ms = last_health_published_at_ == std::chrono::steady_clock::time_point()
            ? -1 : std::chrono::duration_cast<std::chrono::milliseconds>(
                    now - last_health_published_at_).count();
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
                now - entry.second.last_status_received).count();
        peer.health_age_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                now - entry.second.last_health_received).count();
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
        //      edge's delta forward by the age of its cached RouterHealth, not by
        //      activity from other Status branches -- the peer stamped its
        //      delta relative to its own "now" at heartbeat time, so from our perspective
        //      as of right now that edge is (peer's reported delta + our own staleness on
        //      this peer) old.
        {
            const auto reported = peer.health.peers_seen;
            const auto health_age_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                    now - entry.second.last_health_received).count();
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
                aged.last_seen_delta_ms += health_age_ms;
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
