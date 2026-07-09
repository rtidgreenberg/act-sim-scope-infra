// RouteRuntime.hpp — per-topic forwarding entity bundle (Phase 3).
//
// One RouteTopicRuntime owns the DDS entities for one route topic: the input DataReader,
// the output DataWriter, and the ReadCondition whose handler pumps samples from reader to
// writer. It is type-erased behind RouteTopicRuntimeBase so the AsyncWaitSetDispatcher can
// hold runtimes of different generated types uniformly, while the forwarding itself stays
// strongly typed (generated-type fast path, D31).
//
// Threading (D32): the ReadCondition handler (pump) runs on an AsyncWaitSet worker thread.
// The AsyncWaitSet serializes dispatch of a single condition, so pump never runs
// concurrently with itself. Teardown detaches the condition on the control strand — a
// blocking barrier that guarantees no pump is in flight — before close() is called, so
// close() never races an active pump.

#pragma once

#include <dds/dds.hpp>
#include <dds/sub/cond/ReadCondition.hpp>

#include <atomic>
#include <cstdint>
#include <string>

namespace router {

// Type-erased handle the dispatcher stores and drives.
struct RouteTopicRuntimeBase {
    virtual ~RouteTopicRuntimeBase() {}

    // The route ReadCondition, for attach/detach on the AsyncWaitSet.
    virtual dds::core::cond::Condition condition() const = 0;

    // Close DDS entities. MUST be called only after the condition has been detached from
    // the AsyncWaitSet (D32 barrier). Order inside: close condition, then reader, then
    // writer.
    virtual void close() = 0;

    virtual std::uint64_t forwarded() const = 0;
};

template <typename T>
class RouteTopicRuntime : public RouteTopicRuntimeBase {
public:
    RouteTopicRuntime(dds::sub::DataReader<T> reader, dds::pub::DataWriter<T> writer)
        : reader_(reader),
          writer_(writer),
          cond_(reader_, dds::sub::status::DataState::any(),
                [this]() { pump(); }) {}

    dds::core::cond::Condition condition() const override { return cond_; }

    void close() override {
        // Condition is already detached from the AsyncWaitSet by the dispatcher (D32).
        cond_.close();
        reader_.close();
        writer_.close();
    }

    std::uint64_t forwarded() const override {
        return count_.load(std::memory_order_relaxed);
    }

private:
    // Drain the input reader through our condition and forward each valid sample to the
    // output writer. Phase 3 forwards live data only; Phase 10 will mirror the
    // dispose/unregister meta-samples here. A single bad sample must never kill the loop.
    void pump() {
        auto samples = reader_.select().condition(cond_).take();
        for (auto it = samples.begin(); it != samples.end(); ++it) {
            if (!it->info().valid()) {
                continue; // meta-sample (instance-state change) — Phase 10 mirrors these
            }
            try {
                writer_.write(it->data());
                count_.fetch_add(1, std::memory_order_relaxed);
            } catch (const std::exception &) {
                // isolate per-sample write faults; keep forwarding the rest
            }
        }
    }

    dds::sub::DataReader<T> reader_;
    dds::pub::DataWriter<T> writer_;
    dds::sub::cond::ReadCondition cond_;
    std::atomic<std::uint64_t> count_{0};
};

} // namespace router
