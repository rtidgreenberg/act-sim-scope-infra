// AsyncWaitSetDispatcher.cxx — route condition attach/detach on the shared AsyncWaitSet.

#include "AsyncWaitSetDispatcher.hpp"
#include "Log.hpp"

namespace router {

namespace {

// Detach every condition of a runtime from the AsyncWaitSet. Each detach is the D32
// blocking barrier: it returns only when any in-flight handler for that condition has
// completed, so the subsequent close() can never race an active handler.
void detach_all(rti::core::cond::AsyncWaitSet &aws, RouteTopicRuntimeBase &runtime,
                const std::string &route, const std::string &topic) {
    std::vector<dds::core::cond::Condition> conds = runtime.conditions();
    for (size_t i = 0; i < conds.size(); ++i) {
        try {
            aws.detach_condition(conds[i]);
        } catch (const std::exception &e) {
            Log::warn("route_detach_failed", {{"route", route}, {"topic", topic},
                                              {"error", e.what()}});
        }
    }
}

} // namespace

void AsyncWaitSetDispatcher::attach(const std::string &route, const std::string &topic,
                                    std::unique_ptr<RouteTopicRuntimeBase> runtime) {
    std::vector<dds::core::cond::Condition> conds = runtime->conditions();
    RouteTopicRuntimeBase *raw = runtime.get();
    runtimes_[key(route, topic)] = std::move(runtime);
    for (size_t i = 0; i < conds.size(); ++i) {
        aws_.attach_condition(conds[i]);
    }
    // Phase 9 (D81): a build with a WAN-side leg registers with the collector so its
    // per-matched-endpoint protocol statuses are polled on the tick. Strand-only.
    if (stats_registry_ != nullptr && raw->has_wan_leg()) {
        stats_registry_->register_source(raw);
    }
    Log::debug("route_conditions_attached",
               {{"route", route}, {"topic", topic},
                {"count", std::to_string(conds.size())}});
}

bool AsyncWaitSetDispatcher::detach_and_close(const std::string &route,
                                              const std::string &topic) {
    std::map<std::string, std::unique_ptr<RouteTopicRuntimeBase>>::iterator it =
        runtimes_.find(key(route, topic));
    if (it == runtimes_.end()) {
        return false;
    }
    // Unregister from the collector before close (D81 — baselines drop with the endpoint;
    // no poll can touch a closing runtime since both run on the strand).
    if (stats_registry_ != nullptr) {
        stats_registry_->unregister_source(it->second.get());
    }
    detach_all(aws_, *it->second, route, topic);
    it->second->close(); // close condition, then reader, then writer (D32)
    runtimes_.erase(it);
    Log::debug("route_conditions_detached", {{"route", route}, {"topic", topic}});
    return true;
}

void AsyncWaitSetDispatcher::shutdown() {
    while (!runtimes_.empty()) {
        std::map<std::string, std::unique_ptr<RouteTopicRuntimeBase>>::iterator it =
            runtimes_.begin();
        if (stats_registry_ != nullptr) {
            stats_registry_->unregister_source(it->second.get());
        }
        detach_all(aws_, *it->second, "", "");
        it->second->close();
        runtimes_.erase(it);
    }
}

std::uint64_t AsyncWaitSetDispatcher::forwarded(const std::string &route,
                                                const std::string &topic) const {
    std::map<std::string, std::unique_ptr<RouteTopicRuntimeBase>>::const_iterator it =
        runtimes_.find(key(route, topic));
    return it == runtimes_.end() ? 0 : it->second->forwarded();
}

std::string AsyncWaitSetDispatcher::set_writer_deadline(const std::string &route,
                                                        const std::string &topic,
                                                        std::int64_t deadline_nanos) {
    std::map<std::string, std::unique_ptr<RouteTopicRuntimeBase>>::iterator it =
        runtimes_.find(key(route, topic));
    if (it == runtimes_.end()) {
        return std::string();
    }
    return it->second->set_writer_deadline(deadline_nanos);
}

bool AsyncWaitSetDispatcher::set_partitions(const std::string &route,
                                            const std::string &topic,
                                            const std::string &subscriber_partition,
                                            const std::string &publisher_partition) {
    std::map<std::string, std::unique_ptr<RouteTopicRuntimeBase>>::iterator it =
        runtimes_.find(key(route, topic));
    if (it == runtimes_.end()) {
        return false;
    }
    return it->second->set_partitions(subscriber_partition, publisher_partition);
}

AsyncWaitSetDispatcher::~AsyncWaitSetDispatcher() {
    shutdown();
}

} // namespace router
