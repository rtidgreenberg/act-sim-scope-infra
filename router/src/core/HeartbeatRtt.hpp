#pragma once

#include "LinkStatsSink.hpp"

#include <algorithm>
#include <cstdint>
#include <deque>
#include <limits>
#include <map>
#include <string>
#include <vector>

namespace router {

// DDS-free, externally synchronized. All times are local monotonic microseconds.
class HeartbeatRtt {
public:
    static const std::int64_t horizon_us = 32000000;
    static const std::size_t peer_cap = 256;
    struct Ack {
        std::uint64_t session = 0;
        std::uint64_t sequence = 0;
        std::uint64_t age_us = 0;
    };

    explicit HeartbeatRtt(std::uint64_t session) : session_(session) {}

    static void rotate_live_coverage(std::vector<std::string> &names,
                                    std::size_t live_count, std::size_t wire_cap,
                                    std::size_t &cursor) {
        if (live_count <= wire_cap) return;
        const auto offset = cursor % live_count;
        std::rotate(names.begin(), names.begin() + offset, names.begin() + live_count);
        cursor = (offset + wire_cap) % live_count;
    }

    std::string writer_for(const std::string &name) const {
        auto it = peers_.find(name);
        if (it != peers_.end()) return it->second.writer;
        auto dormant = dormant_.find(name);
        return dormant == dormant_.end() ? "" : dormant->second.writer;
    }

    bool observe(const std::string &name, const std::string &writer,
                 std::uint64_t session, std::uint64_t sequence,
                 std::int64_t now, bool previous_writer_alive) {
        if (!session || session == session_ || !sequence || writer.empty()) return reject();
        if (std::find(retired_.begin(), retired_.end(),
                      std::make_pair(writer, session)) != retired_.end()) return reject();
        auto it = peers_.find(name);
        if (it == peers_.end()) {
            if (peers_.size() >= peer_cap) return reject();
            auto dormant = dormant_.find(name);
            if (dormant != dormant_.end()) {
                const auto &p = dormant->second;
                if ((p.session != session || p.writer != writer)
                    && previous_writer_alive) return reject();
                it = peers_.emplace(name, std::move(dormant->second)).first;
                dormant_.erase(dormant);
            } else {
                it = peers_.emplace(name, PeerTiming()).first;
            }
        }
        PeerTiming &p = it->second;
        if (p.session != session || p.writer != writer) {
            if (!p.writer.empty() && previous_writer_alive) {
                return reject();
            }
            retire(p);
            p = PeerTiming();
            p.writer = writer;
            p.session = session;
        }
        if (sequence > p.sequence) {
            p.sequence = sequence;
            p.received_us = now;
        }
        return true;
    }

    Ack acknowledgment(const std::string &name, std::int64_t now) const {
        Ack ack;
        auto it = peers_.find(name);
        if (it == peers_.end()) return ack;
        const PeerTiming &p = it->second;
        const auto age = now - p.received_us;
        if (!p.sequence || age < 0 || age > horizon_us) return ack;
        ack.session = p.session;
        ack.sequence = p.sequence;
        ack.age_us = static_cast<std::uint64_t>(age);
        return ack;
    }

    void reserve(std::uint64_t sequence, std::int64_t now) {
        expire(now);
        sends_[sequence] = Send{now, false};
        while (sends_.size() > 64) sends_.erase(sends_.begin());
    }

    void finalize(std::uint64_t sequence, bool success) {
        auto it = sends_.find(sequence);
        if (it != sends_.end()) {
            if (success) it->second.committed = true;
            else sends_.erase(it);
        }
        const auto pending = std::move(pending_);
        pending_.clear();
        for (const auto &m : pending) {
            if (m.ack.sequence == sequence) {
                if (success) measure(m.name, m.peer_session, m.ack, m.received_us);
                else reject();
            } else {
                pending_.push_back(m);
            }
        }
    }

    void measure(const std::string &name, std::uint64_t peer_session,
                 const Ack &ack, std::int64_t received_us) {
        auto pit = peers_.find(name);
        auto sit = sends_.find(ack.sequence);
        if (pit == peers_.end() || pit->second.session != peer_session
            || ack.session != session_ || !ack.sequence
            || ack.age_us > static_cast<std::uint64_t>(horizon_us)
            || sit == sends_.end()) {
            reject();
            return;
        }
        PeerTiming &p = pit->second;
        if (ack.sequence <= p.last_ack_sequence) return;
        if (!sit->second.committed) {
            if (pending_.size() >= 128) { reject(); return; }
            Pending m;
            m.name = name;
            m.peer_session = peer_session;
            m.ack = ack;
            m.received_us = received_us;
            pending_.push_back(m);
            return;
        }
        const auto elapsed = received_us - sit->second.sent_us;
        if (elapsed < 0 || elapsed > horizon_us
            || ack.age_us > static_cast<std::uint64_t>(elapsed)) {
            reject();
            return;
        }
        const auto rtt = static_cast<std::uint64_t>(elapsed) - ack.age_us;
        if (rtt > std::numeric_limits<std::uint32_t>::max()) { reject(); return; }
        p.last_ack_sequence = ack.sequence;
        p.last_sample_us = received_us;
        p.last_ack_age_us = ack.age_us;
        p.have_sample = true;
        if (p.interval.count == std::numeric_limits<std::uint32_t>::max()) {
            reject();
            return;
        }
        ++p.interval.count;
        const auto us = static_cast<std::uint32_t>(rtt);
        p.interval.min_us = std::min(p.interval.min_us, us);
        p.interval.max_us = std::max(p.interval.max_us, us);
        p.interval.sum_us += us;
    }

    void forget(const std::string &name) {
        disconnect(name);
        auto it = peers_.find(name);
        if (it != peers_.end()) {
            // Pruning topology is not supersession: retain the binding/high-water
            // marks so the same writer can recover without replaying old RTT.
            dormant_[name] = std::move(it->second);
            peers_.erase(it);
            if (dormant_.size() > peer_cap) {
                auto oldest = std::min_element(dormant_.begin(), dormant_.end(),
                        [](const std::pair<const std::string, PeerTiming> &a,
                           const std::pair<const std::string, PeerTiming> &b) {
                            return a.second.received_us < b.second.received_us;
                        });
                dormant_.erase(oldest);
            }
        }
    }

    void disconnect(const std::string &name) {
        auto it = peers_.find(name);
        if (it != peers_.end()) {
            it->second.have_sample = false;
            it->second.interval = RttLinkStats();
        }
        pending_.erase(std::remove_if(pending_.begin(), pending_.end(),
                       [&](const Pending &m) { return m.name == name; }), pending_.end());
    }

    void collect(LinkStatsSink &sink, std::int64_t now) {
        expire(now);
        for (auto &entry : peers_) {
            PeerTiming &p = entry.second;
            RttLinkStats stats = p.interval;
            if (!stats.count) stats.min_us = 0;
            stats.valid = p.have_sample && now >= p.last_sample_us
                          && now - p.last_sample_us <= horizon_us;
            if (p.have_sample && now >= p.last_sample_us) {
                stats.last_sample_age_ms = static_cast<std::uint32_t>(
                        std::min<std::int64_t>((now - p.last_sample_us) / 1000,
                                              std::numeric_limits<std::uint32_t>::max()));
                stats.last_ack_age_us = p.last_ack_age_us;
            }
            sink.add_rtt(entry.first, stats);
            p.interval = RttLinkStats();
        }
    }

    std::uint64_t take_rejections() {
        const auto count = rejections_;
        rejections_ = 0;
        return count;
    }

    void note_rejection() { reject(); }

private:
    struct Send {
        std::int64_t sent_us;
        bool committed;
    };
    struct PeerTiming {
        std::string writer;
        std::uint64_t session = 0, sequence = 0, last_ack_sequence = 0;
        std::int64_t received_us = 0, last_sample_us = 0;
        std::uint64_t last_ack_age_us = 0;
        bool have_sample = false;
        RttLinkStats interval;
    };
    struct Pending {
        std::string name;
        std::uint64_t peer_session;
        Ack ack;
        std::int64_t received_us;
    };
    bool reject() { ++rejections_; return false; }
    void retire(const PeerTiming &p) {
        if (!p.session) return;
        retired_.emplace_back(p.writer, p.session);
        while (retired_.size() > peer_cap) retired_.pop_front();
    }
    void expire(std::int64_t now) {
        for (auto it = sends_.begin(); it != sends_.end();) {
            if (now - it->second.sent_us > horizon_us) it = sends_.erase(it);
            else ++it;
        }
    }
    std::uint64_t session_;
    std::uint64_t rejections_ = 0;
    std::map<std::uint64_t, Send> sends_;
    std::map<std::string, PeerTiming> peers_;
    std::map<std::string, PeerTiming> dormant_;
    std::deque<std::pair<std::string, std::uint64_t>> retired_;
    std::vector<Pending> pending_;
};

} // namespace router
