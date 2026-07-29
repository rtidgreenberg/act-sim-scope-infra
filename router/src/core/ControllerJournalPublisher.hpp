// ControllerJournalPublisher.hpp — real IControllerJournal backed by a Connext DataWriter
// (Phase 6 slice 6b, D46/D49/D55).
//
// Writes one ControllerJournalRecord per processed controller event on
// ActRouterControllerJournal. QoS (D49): RELIABLE + KEEP_LAST(256) with the reliable send
// window set to LENGTH_UNLIMITED (BuiltinQosLib::Generic.KeepLastReliable pattern) so
// write() never blocks the controller strand — under sustained backpressure the oldest
// unacknowledged samples are overwritten instead. Backlog is observed (not fed back into
// route control) via a StatusCondition on RELIABLE_WRITER_CACHE_CHANGED_STATUS attached to
// the shared AsyncWaitSet, logged as journal_falling_behind on the rising edge.
//
// Debug/analysis stream: the writer is always created with the admin plumbing, but produces
// no data-sample traffic until a recorder reader matches ("debug mode"). Startup ordering
// mirrors CommandReader under the D52 disabled-startup dance — construct (attaching the
// status condition) before aws.start()/registry.enable_all().

#pragma once

#include "Interfaces.hpp"

#include "ActTypes.hpp"

#include <rti/core/cond/AsyncWaitSet.hpp>
#include <dds/dds.hpp>

#include <atomic>
#include <string>
#include <vector>

namespace router {

class ControllerJournalPublisher : public IControllerJournal {
public:
    // participant may be disabled at construction (D52): the writer is then created
    // disabled and enabled with the participant; record() before enable is a no-op (the
    // NotEnabledError branch). topic_name defaults to the command-status.md journal topic.
    ControllerJournalPublisher(
        dds::domain::DomainParticipant participant,
        rti::core::cond::AsyncWaitSet &aws,
        const std::string &topic_name = "ActRouterControllerJournal");

    void record(const ControllerJournalRecord &rec) override;

    // Detach the backlog status condition from the AsyncWaitSet. Call before aws.stop().
    void shutdown();

    ~ControllerJournalPublisher();

private:
    void on_backlog();

    rti::core::cond::AsyncWaitSet &aws_;
    dds::pub::Publisher publisher_;
    dds::topic::Topic<ControllerJournalRecord> topic_;
    dds::pub::DataWriter<ControllerJournalRecord> writer_;
    std::vector<dds::core::cond::Condition> conditions_;
    bool behind_;                  // last observed backlog state (edge-triggered logging)
    std::atomic<bool> shut_down_;
};

} // namespace router
