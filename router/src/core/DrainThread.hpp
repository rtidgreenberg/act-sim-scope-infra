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
    // heartbeat_period (Phase 8, D75): same tick pattern, posting PresenceTick — a
    // SEPARATE knob from refresh_period so retuning the counter refresh can never
    // silently stretch the heartbeat past its 2s DEADLINE offer. Default 0 = no tick.
    explicit DrainThread(RouterController &ctrl,
                         std::chrono::milliseconds refresh_period
                                 = std::chrono::milliseconds(0),
                         std::chrono::milliseconds heartbeat_period
                                 = std::chrono::milliseconds(0))
            : ctrl_(ctrl), running_(true) {
        thread_ = std::thread([this, refresh_period, heartbeat_period]() {
            auto next_refresh = std::chrono::steady_clock::now() + refresh_period;
            auto next_heartbeat = std::chrono::steady_clock::now(); // first beat now:
            // the roster side (PresenceMonitor) marks a peer ALIVE only on a heartbeat,
            // so the first one should not wait a full period after startup.
            while (running_.load(std::memory_order_relaxed)) {
                if (refresh_period.count() > 0
                    && std::chrono::steady_clock::now() >= next_refresh) {
                    ctrl_.post(ControllerEvent::refresh_counters());
                    // Resync to real time in one step rather than drifting forward by a
                    // single period per iteration — a stall longer than one period (a slow
                    // event batch, scheduling delay) would otherwise leave next_refresh
                    // trailing now() by multiple periods, firing a tight burst of posts on
                    // successive loop iterations until it caught back up.
                    do {
                        next_refresh += refresh_period;
                    } while (next_refresh <= std::chrono::steady_clock::now());
                }
                if (heartbeat_period.count() > 0
                    && std::chrono::steady_clock::now() >= next_heartbeat) {
                    ctrl_.post(ControllerEvent::presence_tick());
                    do { // same catch-up resync as the refresh tick above
                        next_heartbeat += heartbeat_period;
                    } while (next_heartbeat <= std::chrono::steady_clock::now());
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
