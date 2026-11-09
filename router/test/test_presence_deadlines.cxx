#include "core/StatusDeadlineQueue.hpp"

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
