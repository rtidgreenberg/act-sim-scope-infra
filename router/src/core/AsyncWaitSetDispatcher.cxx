// AsyncWaitSetDispatcher.cxx — route condition attach/detach on the shared AsyncWaitSet.

#include "AsyncWaitSetDispatcher.hpp"
#include "Log.hpp"

#include <stdexcept>

namespace router {

namespace {

// Detach every condition of a runtime from the AsyncWaitSet. Each detach is the D32
// blocking barrier: it returns only when any in-flight handler for that condition has
// completed, so the subsequent close() can never race an active handler.
void detach_all(rti::core::cond::AsyncWaitSet &aws,
                std::vector<dds::core::cond::Condition> &attached,
                const std::string &route, const std::string &topic) {
    while (!attached.empty()) {
        try {
            aws.detach_condition(attached.back());
            attached.pop_back();
        } catch (const std::exception &e) {
            Log::error("route_detach_failed", {{"route", route}, {"topic", topic},
                                               {"error", e.what()}});
            throw;
        }
    }
}

} // namespace

void AsyncWaitSetDispatcher::attach(const std::string &route, const std::string &topic,
                                    std::unique_ptr<RouteTopicRuntimeBase> runtime) {
    std::vector<dds::core::cond::Condition> conds = runtime->conditions();
    const auto name = key(route, topic);
    if (runtimes_.count(name)) {
        throw std::runtime_error("runtime already attached: " + name);
    }
    RuntimeEntry entry;
    entry.runtime = std::move(runtime);
    entry.attached.reserve(conds.size());
    auto inserted = runtimes_.emplace(name, std::move(entry)).first;
    auto &live = inserted->second;
    try {
        for (const auto &condition : conds) {
            if (condition == dds::core::null) {
                throw std::invalid_argument("cannot attach a null condition");
            }
            aws_.attach_condition(condition);
            live.attached.push_back(condition);
        }
        if (stats_registry_ != nullptr && live.runtime->has_wan_leg()) {
            stats_registry_->register_source(live.runtime.get());
            live.registered = true;
        }
    } catch (const std::exception &e) {
        Log::error("route_attach_failed", {{"route", route}, {"topic", topic},
                                          {"error", e.what()}});
        detach_and_close(route, topic);
        throw;
    }
    Log::debug("route_conditions_attached",
               {{"route", route}, {"topic", topic},
                {"count", std::to_string(conds.size())}});
}

bool AsyncWaitSetDispatcher::detach_and_close(const std::string &route,
                                              const std::string &topic) {
    auto it = runtimes_.find(key(route, topic));
    if (it == runtimes_.end()) {
        return false;
    }
    // Unregister from the collector before close (D81 — baselines drop with the endpoint;
    // no poll can touch a closing runtime since both run on the strand).
    detach_all(aws_, it->second.attached, route, topic);
    if (it->second.registered) {
        stats_registry_->unregister_source(it->second.runtime.get());
        it->second.registered = false;
    }
    it->second.runtime->close();
    runtimes_.erase(it);
    Log::debug("route_conditions_detached", {{"route", route}, {"topic", topic}});
    return true;
}

void AsyncWaitSetDispatcher::shutdown() {
    while (!runtimes_.empty()) {
        auto it = runtimes_.begin();
        detach_all(aws_, it->second.attached, "", "");
        if (it->second.registered) {
            stats_registry_->unregister_source(it->second.runtime.get());
            it->second.registered = false;
        }
        it->second.runtime->close();
        runtimes_.erase(it);
    }
}

std::uint64_t AsyncWaitSetDispatcher::forwarded(const std::string &route,
                                                const std::string &topic) const {
    auto it = runtimes_.find(key(route, topic));
    return it == runtimes_.end() ? 0 : it->second.runtime->forwarded();
}

bool AsyncWaitSetDispatcher::set_partitions(const std::string &route,
                                            const std::string &topic,
                                            const std::vector<std::string> &subscriber_partition,
                                            const std::vector<std::string> &publisher_partition) {
    auto it = runtimes_.find(key(route, topic));
    if (it == runtimes_.end()) {
        return false;
    }
    return it->second.runtime->set_partitions(subscriber_partition, publisher_partition);
}

AsyncWaitSetDispatcher::~AsyncWaitSetDispatcher() {
    shutdown();
}

} // namespace router
