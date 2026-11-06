// ControllerJournalPublisher.cxx — RELIABLE + KEEP_LAST(256) + unlimited send window
// journal writer with a backlog StatusCondition (Phase 6 slice 6b, D46/D49/D55).

#include "ControllerJournalPublisher.hpp"
#include "Log.hpp"

#include <dds/core/cond/StatusCondition.hpp>
namespace router {

namespace {

const std::int32_t kBacklogThreshold = 128;

} // namespace

ControllerJournalPublisher::ControllerJournalPublisher(
        dds::domain::DomainParticipant participant,
        rti::core::cond::AsyncWaitSet &aws,
                const dds::pub::qos::DataWriterQos &writer_qos,
        const std::string &topic_name)
        : aws_(aws),
          publisher_(participant),
          topic_(participant, topic_name),
                    writer_(publisher_, topic_, writer_qos),
          behind_(false),
          shut_down_(false) {
    dds::pub::DataWriter<ControllerJournalRecord> writer = writer_;
    dds::core::cond::StatusCondition sc(writer);
    sc.enabled_statuses(dds::core::status::StatusMask::reliable_writer_cache_changed());
    sc->handler([this]() { on_backlog(); });  // arrow: handler is on the condition delegate
    aws_.attach_condition(sc);
    conditions_.push_back(sc);
    Log::info("controller_journal_ready", {{"topic", topic_name}});
}

ControllerJournalPublisher::~ControllerJournalPublisher() {
    shutdown();
}

void ControllerJournalPublisher::shutdown() {
    if (shut_down_.exchange(true)) return;
    for (const auto &cond : conditions_) {
        try {
            aws_.detach_condition(cond);
        } catch (const std::exception &e) {
            Log::warn("journal_detach_condition_failed", {{"error", e.what()}});
        }
    }
    conditions_.clear();
}

void ControllerJournalPublisher::record(const ControllerJournalRecord &rec) {
    try {
        writer_.write(rec);
    } catch (const dds::core::NotEnabledError &) {
        // Records only originate from processed events, which the drain thread starts after
        // enable_all() (D52) — defensive symmetry with DdsStatusPublisher.
        Log::debug("journal_record_skipped_not_enabled",
                   {{"seq", std::to_string(rec.event_sequence)}});
    } catch (const std::exception &e) {
        Log::warn("journal_record_failed", {{"error", e.what()}});
    }
}

void ControllerJournalPublisher::on_backlog() {
    rti::core::status::ReliableWriterCacheChangedStatus st =
        writer_.extensions().reliable_writer_cache_changed_status();
    std::int32_t unacked = st.unacknowledged_sample_count();
    bool behind = unacked >= kBacklogThreshold;
    // Edge-triggered so a sustained backlog logs once, not every watermark crossing.
    if (behind && !behind_) {
        Log::warn("journal_falling_behind",
                  {{"unacknowledged", std::to_string(unacked)},
                   {"depth", "256"}});
    } else if (!behind && behind_) {
        Log::info("journal_caught_up", {{"unacknowledged", std::to_string(unacked)}});
    }
    behind_ = behind;
}

} // namespace router
