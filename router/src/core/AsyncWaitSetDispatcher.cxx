// AsyncWaitSetDispatcher.cxx — route condition attach/detach on the shared AsyncWaitSet.

#include "AsyncWaitSetDispatcher.hpp"
#include "Log.hpp"

namespace router {

void AsyncWaitSetDispatcher::attach(const std::string &route, const std::string &topic,
                                    std::unique_ptr<RouteTopicRuntimeBase> runtime) {
    dds::core::cond::Condition cond = runtime->condition();
    runtimes_[key(route, topic)] = std::move(runtime);
    aws_.attach_condition(cond);
    Log::debug("route_condition_attached", {{"route", route}, {"topic", topic}});
}

bool AsyncWaitSetDispatcher::detach_and_close(const std::string &route,
                                              const std::string &topic) {
    std::map<std::string, std::unique_ptr<RouteTopicRuntimeBase>>::iterator it =
        runtimes_.find(key(route, topic));
    if (it == runtimes_.end()) {
        return false;
    }
    // D32 barrier: detach_condition blocks until any in-flight handler for this condition
    // has returned, so the subsequent close() can never race an active pump.
    dds::core::cond::Condition cond = it->second->condition();
    try {
        aws_.detach_condition(cond);
    } catch (const std::exception &e) {
        Log::warn("route_detach_failed", {{"route", route}, {"topic", topic},
                                          {"error", e.what()}});
    }
    it->second->close(); // close condition, then reader, then writer (D32)
    runtimes_.erase(it);
    Log::debug("route_condition_detached", {{"route", route}, {"topic", topic}});
    return true;
}

void AsyncWaitSetDispatcher::shutdown() {
    while (!runtimes_.empty()) {
        std::map<std::string, std::unique_ptr<RouteTopicRuntimeBase>>::iterator it =
            runtimes_.begin();
        dds::core::cond::Condition cond = it->second->condition();
        try {
            aws_.detach_condition(cond);
        } catch (const std::exception &e) {
            Log::warn("route_detach_failed", {{"error", e.what()}});
        }
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

AsyncWaitSetDispatcher::~AsyncWaitSetDispatcher() {
    shutdown();
}

} // namespace router
