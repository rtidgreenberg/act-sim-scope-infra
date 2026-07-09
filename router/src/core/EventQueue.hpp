// EventQueue.hpp — the MPSC ControllerEvent queue (D12).
//
// Multiple producer threads post; only the controller strand drains. Phase 1's producers
// are test threads, but the producer side is thread-safe from the start so Phase 2 can
// point AsyncWaitSet dispatch threads at it without touching this class.

#pragma once

#include "RouterEvents.hpp"

#include <condition_variable>
#include <deque>
#include <mutex>
#include <vector>

namespace router {

class EventQueue {
public:
    void post(const ControllerEvent &event) {
        {
            std::lock_guard<std::mutex> lk(mutex_);
            queue_.push_back(event);
        }
        cv_.notify_one();
    }

    // Move all pending events out (consumer strand only).
    std::vector<ControllerEvent> drain() {
        std::lock_guard<std::mutex> lk(mutex_);
        std::vector<ControllerEvent> out(queue_.begin(), queue_.end());
        queue_.clear();
        return out;
    }

    bool empty() const {
        std::lock_guard<std::mutex> lk(mutex_);
        return queue_.empty();
    }

private:
    mutable std::mutex mutex_;
    std::condition_variable cv_;
    std::deque<ControllerEvent> queue_;
};

} // namespace router
