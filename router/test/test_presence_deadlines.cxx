#include "core/StatusDeadlineQueue.hpp"
#include "core/WanPolicy.hpp"

#include <chrono>
#include <cstdio>
#include <thread>

static int failures = 0;
#define CHECK(condition) do { \
    if (!(condition)) { \
        std::fprintf(stderr, "FAIL %d: %s\n", __LINE__, #condition); \
        ++failures; \
    } \
} while (0)

int main() {
    unsetenv("WAN_TIMEOUT_SEC");
    unsetenv("WAN_HB_PERIOD_SEC");
    unsetenv("WAN_HB_RETRIES");
    auto policy = router::WanPolicy::from_environment();
    CHECK(policy.timeout_seconds == 300);
    CHECK(policy.heartbeat_seconds == 3);
    CHECK(policy.heartbeat_retries == 100);
    policy.export_environment();
    CHECK(std::string(std::getenv("WAN_HB_RETRIES")) == "100");
    unsetenv("WAN_HB_RETRIES");
    setenv("WAN_TIMEOUT_SEC", "301", 1);
    CHECK(router::WanPolicy::from_environment().heartbeat_retries == 101);
    setenv("WAN_HB_PERIOD_SEC", "7", 1);
    CHECK(router::WanPolicy::from_environment().heartbeat_retries == 43);
    setenv("WAN_HB_RETRIES", "10", 1);
    bool conflict_rejected = false;
    try { router::WanPolicy::from_environment(); }
    catch (const std::invalid_argument&) { conflict_rejected = true; }
    CHECK(conflict_rejected);
    for (const auto* invalid : {"", "0", "-1", "nan", "3.5", " 3", "2147483648"}) {
        bool rejected = false;
        try { router::WanPolicy::positive_integer(invalid, "test"); }
        catch (const std::exception&) { rejected = true; }
        CHECK(rejected);
    }

    const auto received = std::chrono::steady_clock::now();
    CHECK(!router::status_timeout_expired(received, received + std::chrono::seconds(299),
                                         std::chrono::seconds(300)));
    CHECK(router::status_timeout_expired(received, received + std::chrono::seconds(300),
                                        std::chrono::seconds(300)));
    const auto recovered_status = received + std::chrono::seconds(120);
    CHECK(!router::status_timeout_expired(recovered_status, received + std::chrono::seconds(300),
                                         std::chrono::seconds(300)));

    using Queue = router::StatusDeadlineQueue;
    Queue queue(2);
    CHECK(queue.missed("Boat30"));
    const auto first_received = queue.received("Boat30");

    std::this_thread::sleep_for(std::chrono::milliseconds(2));
    CHECK(queue.missed("Boat30"));
    const auto after_recovery = queue.take();
    CHECK(after_recovery.size() == 1);
    CHECK(after_recovery.at("Boat30") > first_received);

    CHECK(queue.missed("Boat30"));
    const auto before_repeat = Queue::Clock::now();
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
    CHECK(queue.missed("Boat30"));
    CHECK(queue.take().at("Boat30") <= before_repeat);

    // A tick can already own the old miss when recovery and a new loss arrive.
    CHECK(queue.missed("Boat30"));
    const auto old_tick = queue.take();
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
    const auto recovered = queue.received("Boat30");
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
    CHECK(queue.missed("Boat30"));
    const auto next_tick = queue.take();
    CHECK(old_tick.at("Boat30") < recovered);
    CHECK(next_tick.at("Boat30") > recovered);

    CHECK(queue.missed("Boat30"));
    CHECK(queue.missed("Boat31"));
    CHECK(!queue.missed("Boat32"));
    CHECK(queue.missed("Boat30"));
    queue.received("Boat30");
    CHECK(queue.missed("Boat32"));
    const auto bounded = queue.take();
    CHECK(bounded.size() == 2);
    CHECK(bounded.count("Boat31") == 1);
    CHECK(bounded.count("Boat32") == 1);
    CHECK(queue.take().empty());
    CHECK(queue.missed("Boat30"));
    queue.received("Boat30");
    CHECK(queue.take().empty());

    std::printf("Presence deadline ordering checks: %d failures\n", failures);
    return failures ? 1 : 0;
}
