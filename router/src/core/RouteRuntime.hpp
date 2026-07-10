// RouteRuntime.hpp — per-topic forwarding entity bundle (Phase 3, Phase 5 statuses).
//
// One RouteTopicRuntime owns the DDS entities for one route topic build: the input
// DataReader, the output DataWriter, the ReadCondition whose handler pumps samples from
// reader to writer, AND the per-build parents/description it was created with — the
// Publisher, the Subscriber, and (for a filtered input) the ContentFilteredTopic. Owning
// the whole build means close() reclaims everything, so a route that flaps does not
// accumulate orphaned entities and a rebuild can recreate the CFT under the same name
// (D41). It is type-erased behind RouteTopicRuntimeBase so the AsyncWaitSetDispatcher can
// hold runtimes of different payload types uniformly, while the forwarding itself stays
// strongly typed (generated-type fast path, D31).
//
// Phase 5 (D39/D42/D45) adds the entity StatusConditions, dispatched on the same
// AsyncWaitSet:
//   - input reader: REQUESTED_INCOMPATIBLE_QOS (residual mismatches warn, never adapt)
//     + LIVELINESS_CHANGED (upstream-liveliness propagation for MANUAL-kind writers)
//   - output writer: OFFERED_INCOMPATIBLE_QOS
// Warnings surface through a thread-safe callback (RouterController::post). Reading the
// status inside the handler clears its change flag, so the condition untriggers
// (validated 7.7). For MANUAL liveliness kinds each forwarded write() already asserts
// (middleware); the handler additionally asserts on upstream alive transitions. A quiet
// MANUAL-kind route past its lease still shows not-alive downstream until upstream
// writes or transitions again — documented residual (D45).
//
// Threading (D32): condition handlers run on AsyncWaitSet worker threads. The
// AsyncWaitSet serializes dispatch of a single condition, so pump never runs
// concurrently with itself. Teardown detaches ALL conditions on the control strand —
// blocking barriers that guarantee no handler is in flight — before close() is called.
// set_writer_deadline runs on the control strand and only touches the (thread-safe)
// writer QoS setter, never dispatcher state.

#pragma once

#include "QosResolver.hpp" // summaries + duration helpers (D45)

#include <dds/dds.hpp>
#include <dds/core/cond/StatusCondition.hpp>
#include <dds/sub/cond/ReadCondition.hpp>
#include <dds/topic/ContentFilteredTopic.hpp>

#include <atomic>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace router {

// Type-erased handle the dispatcher stores and drives.
struct RouteTopicRuntimeBase {
    virtual ~RouteTopicRuntimeBase() {}

    // Every condition attached to the AsyncWaitSet for this build (read condition +
    // entity status conditions). The dispatcher attaches all and detaches all (D32).
    virtual std::vector<dds::core::cond::Condition> conditions() const = 0;

    // Close DDS entities. MUST be called only after all conditions have been detached
    // from the AsyncWaitSet (D32 barrier). Order inside: close condition, then reader,
    // then writer, then the CFT (its reader must be gone first), then the
    // Subscriber/Publisher parents.
    virtual void close() = 0;

    virtual std::uint64_t forwarded() const = 0;

    // In-place mutable deadline update on the output writer (D39). Returns the writer's
    // new QoS summary, or "" on failure.
    virtual std::string set_writer_deadline(std::int64_t deadline_nanos) = 0;
};

template <typename T>
class RouteTopicRuntime : public RouteTopicRuntimeBase {
public:
    // on_qos_warning receives "reader:<POLICY>" / "writer:<POLICY>" strings; it is
    // invoked from AsyncWaitSet worker threads and must be thread-safe (it posts to the
    // controller's MPSC queue). manual_liveliness enables upstream-liveliness
    // propagation (derived MANUAL kind, D42).
    RouteTopicRuntime(dds::sub::DataReader<T> reader, dds::pub::DataWriter<T> writer,
                      dds::pub::Publisher publisher, dds::sub::Subscriber subscriber,
                      dds::topic::ContentFilteredTopic<T> cft = dds::core::null,
                      std::function<void(const std::string &)> on_qos_warning
                              = std::function<void(const std::string &)>(),
                      bool manual_liveliness = false)
        : reader_(reader),
          writer_(writer),
          publisher_(publisher),
          subscriber_(subscriber),
          cft_(cft),
          cond_(reader_, dds::sub::status::DataState::any(),
                [this]() { pump(); }),
          selector_(reader_.select().condition(cond_)),
          reader_status_(reader_),
          writer_status_(writer_),
          on_qos_warning_(on_qos_warning),
          manual_liveliness_(manual_liveliness) {
        using dds::core::status::StatusMask;
        StatusMask reader_mask = StatusMask::requested_incompatible_qos();
        if (manual_liveliness_) {
            reader_mask |= StatusMask::liveliness_changed();
        }
        reader_status_.enabled_statuses(reader_mask);
        reader_status_->handler([this]() { on_reader_status(); });
        writer_status_.enabled_statuses(StatusMask::offered_incompatible_qos());
        writer_status_->handler([this]() { on_writer_status(); });
    }

    std::vector<dds::core::cond::Condition> conditions() const override {
        std::vector<dds::core::cond::Condition> all;
        all.push_back(cond_);
        all.push_back(reader_status_);
        all.push_back(writer_status_);
        return all;
    }

    void close() override {
        // All conditions are already detached from the AsyncWaitSet by the dispatcher
        // (D32). The StatusConditions are entity-owned — closing the entities reclaims
        // them; only the ReadCondition is closed explicitly.
        cond_.close();
        reader_.close();
        writer_.close();
        if (cft_ != dds::core::null) {
            cft_.close(); // frees the fixed "<route>_<topic>_cft" name for the next build
        }
        subscriber_.close();
        publisher_.close();
    }

    std::uint64_t forwarded() const override {
        return count_.load(std::memory_order_relaxed);
    }

    std::string set_writer_deadline(std::int64_t deadline_nanos) override {
        try {
            dds::pub::qos::DataWriterQos q = writer_.qos();
            q << dds::core::policy::Deadline(duration_from_nanos(deadline_nanos));
            writer_.qos(q);
            return QosResolver::summarize(q);
        } catch (const std::exception &) {
            return std::string();
        }
    }

private:
    // Drain the input reader through our condition and forward each valid sample to the
    // output writer. Phase 3 forwards live data only; Phase 10 will mirror the
    // dispose/unregister meta-samples here. A single bad sample must never kill the loop.
    void pump() {
        auto samples = selector_.take();
        for (auto it = samples.begin(); it != samples.end(); ++it) {
            if (!it->info().valid()) {
                continue; // meta-sample (instance-state change) — Phase 10 mirrors these
            }
            try {
                writer_.write(it->data()); // write() itself asserts MANUAL liveliness
                count_.fetch_add(1, std::memory_order_relaxed);
            } catch (const std::exception &) {
                // isolate per-sample write faults; keep forwarding the rest
            }
        }
    }

    void on_reader_status() {
        using dds::core::status::StatusMask;
        StatusMask changes = reader_.status_changes();
        if ((changes & StatusMask::requested_incompatible_qos()).any()) {
            dds::core::status::RequestedIncompatibleQosStatus st =
                    reader_.requested_incompatible_qos_status(); // read clears the flag
            if (on_qos_warning_) {
                on_qos_warning_("reader:" + qos_policy_name(st.last_policy_id()));
            }
        }
        if (manual_liveliness_
            && (changes & StatusMask::liveliness_changed()).any()) {
            dds::core::status::LivelinessChangedStatus st =
                    reader_.liveliness_changed_status();
            if (st.alive_count() > 0) {
                try {
                    writer_.assert_liveliness(); // propagate upstream liveliness (D42)
                } catch (const std::exception &) {
                    // never let a liveliness assert kill the dispatch thread
                }
            }
        }
    }

    void on_writer_status() {
        dds::core::status::OfferedIncompatibleQosStatus st =
                writer_.offered_incompatible_qos_status(); // read clears the flag
        if (on_qos_warning_) {
            on_qos_warning_("writer:" + qos_policy_name(st.last_policy_id()));
        }
    }

    dds::sub::DataReader<T> reader_;
    dds::pub::DataWriter<T> writer_;
    dds::pub::Publisher publisher_;
    dds::sub::Subscriber subscriber_;
    dds::topic::ContentFilteredTopic<T> cft_;
    dds::sub::cond::ReadCondition cond_;
    // Query reused across pumps (hot path); valid because reader_/cond_ outlive it and
    // the D32 barrier stops dispatch before close().
    typename dds::sub::DataReader<T>::Selector selector_;
    dds::core::cond::StatusCondition reader_status_;
    dds::core::cond::StatusCondition writer_status_;
    std::function<void(const std::string &)> on_qos_warning_;
    bool manual_liveliness_;
    std::atomic<std::uint64_t> count_{0};
};

} // namespace router
