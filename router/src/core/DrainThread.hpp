// DrainThread.hpp — background strand that periodically drains the RouterController's
// event queue (D12). One thread per process; router_main and the route-forwarding test
// mains all need the exact same loop, so it lives here once instead of duplicated per
// binary.
//
// 7d (D63): the strand loop also carries the status-refresh tick — with a non-zero
// refresh_period it posts RefreshCounters at that cadence (config-fixed, not adaptive;
// D14 precedent), which the controller handles by pulling forwarded() counters and
// republishing RouterStatus without a state_revision bump. Default 0 = no tick (the
// C++ unit tests drive the controller manually and post the event themselves).

#pragma once

#include "RouterController.hpp"

#include <atomic>
#include <chrono>
#include <thread>

namespace router {

class DrainThread {
public:
    explicit DrainThread(RouterController &ctrl,
                         std::chrono::milliseconds refresh_period
                                 = std::chrono::milliseconds(0))
            : ctrl_(ctrl), running_(true) {
        thread_ = std::thread([this, refresh_period]() {
            auto next_refresh = std::chrono::steady_clock::now() + refresh_period;
            while (running_.load(std::memory_order_relaxed)) {
                if (refresh_period.count() > 0
                    && std::chrono::steady_clock::now() >= next_refresh) {
                    ctrl_.post(ControllerEvent::refresh_counters());
                    next_refresh += refresh_period;
                }
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
