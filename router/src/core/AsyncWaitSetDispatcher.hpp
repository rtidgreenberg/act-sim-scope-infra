// AsyncWaitSetDispatcher.hpp — sole owner of route ReadCondition attach/detach (D31.5/D32).
//
// The EntityFactory hands each newly created RouteTopicRuntime here to attach its
// ReadCondition to the shared AsyncWaitSet. Teardown/abort detach the condition — a
// blocking barrier (D32) that guarantees no forwarding handler is in flight — and then
// close the entities, in that order. All attach/detach/close calls are issued from the
// controller strand (the EntityFactory runs on it), so the runtime map is single-threaded;
// the AsyncWaitSet worker threads only ever invoke the runtime's pump handler.
//
// unlock_condition() is never called, so a route's forwarding handler is never dispatched
// concurrently with itself (D32).

#pragma once

#include "RouteRuntime.hpp"

#include <rti/core/cond/AsyncWaitSet.hpp>

#include <map>
#include <memory>
#include <string>

namespace router {

class AsyncWaitSetDispatcher {
public:
    explicit AsyncWaitSetDispatcher(rti::core::cond::AsyncWaitSet &aws) : aws_(aws) {}

    // Take ownership of a route runtime and attach its conditions (read condition +
    // entity status conditions, D45) to the AsyncWaitSet.
    void attach(const std::string &route, const std::string &topic,
                std::unique_ptr<RouteTopicRuntimeBase> runtime);

    // Blocking detach barrier + ordered close (D32). Returns false if no runtime exists
    // for this route/topic (already torn down / never created).
    bool detach_and_close(const std::string &route, const std::string &topic);

    // Detach and close every remaining route runtime (shutdown). Must run before the
    // AsyncWaitSet is stopped and before the participants are destroyed.
    void shutdown();

    // Cumulative samples forwarded by a live route topic (0 if not currently attached).
    std::uint64_t forwarded(const std::string &route, const std::string &topic) const;

    // In-place mutable deadline update on a live build's output writer (D39). Returns
    // the writer's new QoS summary, or "" if no runtime exists / the update failed.
    std::string set_writer_deadline(const std::string &route, const std::string &topic,
                                    std::int64_t deadline_nanos);

    // In-place partition change on a live build's Subscriber/Publisher (7b/D69).
    // Returns false if no runtime exists / the update failed.
    bool set_partitions(const std::string &route, const std::string &topic,
                        const std::string &subscriber_partition,
                        const std::string &publisher_partition);

    ~AsyncWaitSetDispatcher();

private:
    static std::string key(const std::string &route, const std::string &topic) {
        return route + "|" + topic;
    }

    rti::core::cond::AsyncWaitSet &aws_;
    std::map<std::string, std::unique_ptr<RouteTopicRuntimeBase>> runtimes_;
};

} // namespace router
