#pragma once

#include <chrono>
#include <cstddef>
#include <map>
#include <mutex>
#include <string>

namespace router {

class StatusDeadlineQueue {
public:
    using Clock = std::chrono::steady_clock;
    using Events = std::map<std::string, Clock::time_point>;

    explicit StatusDeadlineQueue(std::size_t capacity) : capacity_(capacity) {}

    bool missed(const std::string& source) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (events_.size() >= capacity_ && !events_.count(source)) {
            return false;
        }
        events_.emplace(source, Clock::now());
        return true;
    }

    Clock::time_point received(const std::string& source) {
        std::lock_guard<std::mutex> lock(mutex_);
        // Recovery ends the old loss epoch atomically with its receipt timestamp.
        events_.erase(source);
        return Clock::now();
    }

    Events take() {
        std::lock_guard<std::mutex> lock(mutex_);
        Events events;
        events.swap(events_);
        return events;
    }

private:
    const std::size_t capacity_;
    std::mutex mutex_;
    Events events_;
};

} // namespace router
