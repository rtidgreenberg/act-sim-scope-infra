// DrainThread.hpp — background strand that periodically drains the RouterController's
// event queue (D12). One thread per process; router_main and the route-forwarding test
// mains all need the exact same loop, so it lives here once instead of duplicated per
// binary.

#pragma once

#include "RouterController.hpp"

#include <atomic>
#include <chrono>
#include <thread>

namespace router {

class DrainThread {
public:
    explicit DrainThread(RouterController &ctrl) : ctrl_(ctrl), running_(true) {
        thread_ = std::thread([this]() {
            while (running_.load(std::memory_order_relaxed)) {
                ctrl_.wait_and_drain(std::chrono::milliseconds(100));
            }
        });
    }
    void stop() { if (running_.exchange(false)) thread_.join(); }
    ~DrainThread() { stop(); }

private:
    RouterController &ctrl_;
    std::atomic<bool> running_;
    std::thread thread_;
};

} // namespace router
