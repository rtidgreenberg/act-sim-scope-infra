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

#include "QosResolver.hpp"   // summaries + duration helpers (D45)
#include "WanStatsPoll.hpp"  // Phase 9 shared per-matched-endpoint poll (D81)

#include <dds/dds.hpp>
#include <dds/core/cond/StatusCondition.hpp>
#include <dds/sub/cond/ReadCondition.hpp>
#include <dds/topic/ContentFilteredTopic.hpp>

#include <atomic>
#include <cstdint>
#include <functional>
#include <map>
#include <string>
#include <vector>

namespace router {

// Type-erased handle the dispatcher stores and drives. It is also an IWanStatsSource
// (Phase 9, D81): a build with a WAN-side leg registers with the LinkStatsCollector so
// its per-matched-endpoint protocol statuses are polled where the payload type is known.
struct RouteTopicRuntimeBase : public IWanStatsSource {
    virtual ~RouteTopicRuntimeBase() {}

    // True if either leg lives on the WAN participant (D81) — the dispatcher registers
    // only these with the collector. Default false so a non-WAN build is never polled.
    virtual bool has_wan_leg() const { return false; }

    // IWanStatsSource: default no-op (a build with no WAN leg contributes nothing).
    void collect_wan_stats(LinkStatsSink &) override {}

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

    // In-place partition change on this build's Subscriber (input leg) and Publisher
    // (output leg) — 7b/D69. Pub/sub PARTITION is runtime-mutable via set_qos with
    // automatic rematching (D15), so no entity recreation; the rematch surfaces through
    // the matched-count StatusConditions (D66/D67). Empty string = the default
    // partition. Returns false on failure.
    virtual bool set_partitions(const std::string &subscriber_partition,
                                const std::string &publisher_partition) = 0;
};

template <typename T>
class RouteTopicRuntime : public RouteTopicRuntimeBase {
public:
    // on_qos_warning receives "reader:<POLICY>" / "writer:<POLICY>" strings; on_match
    // receives (input_side, current matched count) on every SUBSCRIPTION_MATCHED /
    // PUBLICATION_MATCHED transition (D64/D66 — DDS is the matching authority). Both are
    // invoked from AsyncWaitSet worker threads and must be thread-safe (they post to the
    // controller's MPSC queue). manual_liveliness enables upstream-liveliness
    // propagation (derived MANUAL kind, D42).
    // reader_is_wan/writer_is_wan (Phase 9, D81): set by the factory when the endpoint's
    // participant is the WAN participant — collect_wan_stats then polls that leg's
    // per-matched-endpoint protocol statuses. Both false = never registered (no WAN leg).
    RouteTopicRuntime(dds::sub::DataReader<T> reader, dds::pub::DataWriter<T> writer,
                      dds::pub::Publisher publisher, dds::sub::Subscriber subscriber,
                      dds::topic::ContentFilteredTopic<T> cft = dds::core::null,
                      std::function<void(const std::string &)> on_qos_warning
                              = std::function<void(const std::string &)>(),
                      bool manual_liveliness = false,
                      std::function<void(bool, std::int32_t)> on_match
                              = std::function<void(bool, std::int32_t)>(),
                      bool reader_is_wan = false, bool writer_is_wan = false)
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
          manual_liveliness_(manual_liveliness),
          on_match_(on_match),
          reader_is_wan_(reader_is_wan),
          writer_is_wan_(writer_is_wan) {
        using dds::core::status::StatusMask;
        StatusMask reader_mask = StatusMask::requested_incompatible_qos()
                | StatusMask::subscription_matched();
        if (manual_liveliness_) {
            reader_mask |= StatusMask::liveliness_changed();
        }
        reader_status_.enabled_statuses(reader_mask);
        reader_status_->handler([this]() { on_reader_status(); });
        writer_status_.enabled_statuses(StatusMask::offered_incompatible_qos()
                                        | StatusMask::publication_matched());
        writer_status_->handler([this]() { on_writer_status(); });
    }

    bool has_wan_leg() const override { return reader_is_wan_ || writer_is_wan_; }

    // Poll the WAN leg(s)' per-matched-endpoint reliable-protocol statuses, resolve each
    // peer via the middleware discovery DB (D81 item 1 — no roster), self-compute interval
    // deltas from cumulative totals (D14 — never trust native *_change), and fold into the
    // sink. Runs on the controller strand (the LinkStatsTick), single-threaded with the
    // dispatcher's attach/detach, so the baseline maps need no lock. The pump handler on
    // the AWS worker never touches them.
    void collect_wan_stats(LinkStatsSink &sink) override {
        if (writer_is_wan_) {
            poll_writer_wan_stats(writer_, writer_prev_, sink);
        }
        if (reader_is_wan_) {
            poll_reader_wan_stats(reader_, reader_prev_, sink);
        }
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

    bool set_partitions(const std::string &subscriber_partition,
                        const std::string &publisher_partition) override {
        try {
            dds::sub::qos::SubscriberQos sq = subscriber_.qos();
            sq << (subscriber_partition.empty()
                           ? dds::core::policy::Partition()
                           : dds::core::policy::Partition(subscriber_partition));
            subscriber_.qos(sq);
            dds::pub::qos::PublisherQos pq = publisher_.qos();
            pq << (publisher_partition.empty()
                           ? dds::core::policy::Partition()
                           : dds::core::policy::Partition(publisher_partition));
            publisher_.qos(pq);
            return true;
        } catch (const std::exception &) {
            return false;
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
        if ((changes & StatusMask::subscription_matched()).any()) {
            dds::core::status::SubscriptionMatchedStatus st =
                    reader_.subscription_matched_status(); // read clears the flag
            if (on_match_) {
                on_match_(/*input_side=*/true, st.current_count());
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
        using dds::core::status::StatusMask;
        StatusMask changes = writer_.status_changes();
        if ((changes & StatusMask::offered_incompatible_qos()).any()) {
            dds::core::status::OfferedIncompatibleQosStatus st =
                    writer_.offered_incompatible_qos_status(); // read clears the flag
            if (on_qos_warning_) {
                on_qos_warning_("writer:" + qos_policy_name(st.last_policy_id()));
            }
        }
        if ((changes & StatusMask::publication_matched()).any()) {
            dds::core::status::PublicationMatchedStatus st =
                    writer_.publication_matched_status(); // read clears the flag
            if (on_match_) {
                on_match_(/*input_side=*/false, st.current_count());
            }
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
    std::function<void(bool, std::int32_t)> on_match_;
    std::atomic<std::uint64_t> count_{0};

    // Phase 9 (D81): which leg is on the WAN participant, and the delta baselines. Touched
    // only by collect_wan_stats on the controller strand.
    bool reader_is_wan_ = false;
    bool writer_is_wan_ = false;
    std::map<std::string, WriterTotals> writer_prev_;
    std::map<std::string, ReaderTotals> reader_prev_;
};

} // namespace router
