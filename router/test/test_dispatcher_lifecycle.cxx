#include "core/AsyncWaitSetDispatcher.hpp"

#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <future>
#include <mutex>
#include <set>
#include <stdexcept>

static int failures = 0;
#define CHECK(condition) do { \
    if (!(condition)) { \
        std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #condition); \
        ++failures; \
    } \
} while (0)

struct State {
    std::mutex mutex;
    std::condition_variable cv;
    bool started = false;
    bool release = true;
    int closed = 0;
};

struct Runtime : router::RouteTopicRuntimeBase {
    dds::core::cond::GuardCondition guard;
    std::shared_ptr<State> state;
    bool invalid;
    Runtime(std::shared_ptr<State> s, bool bad = false) : state(s), invalid(bad) {
        guard->handler([s] {
            std::unique_lock<std::mutex> lock(s->mutex);
            s->started = true;
            s->cv.notify_all();
            s->cv.wait(lock, [&] { return s->release; });
        });
    }
    std::vector<dds::core::cond::Condition> conditions() const override {
        auto result = std::vector<dds::core::cond::Condition>{guard};
        if (invalid) {
            result.push_back(dds::core::null);
        }
        return result;
    }
    void close() override { ++state->closed; }
    uint64_t forwarded() const override { return 7; }
    bool has_wan_leg() const override { return true; }
    bool set_partitions(const std::vector<std::string>&,
                        const std::vector<std::string>&) override { return false; }
};

struct Stats : router::IWanStatsRegistry {
    std::set<router::IWanStatsSource*> sources;
    ~Stats() override { CHECK(sources.empty()); }
    void register_source(router::IWanStatsSource* source) override {
        CHECK(sources.insert(source).second);
    }
    void unregister_source(router::IWanStatsSource* source) override {
        CHECK(sources.erase(source) == 1);
    }
};

static void test_startup_unwinding() {
    auto state = std::make_shared<State>();
    bool caught = false;
    try {
        rti::core::cond::AsyncWaitSet aws;
        Stats stats;
        router::AsyncWaitSetDispatcher dispatcher(aws);
        dispatcher.set_stats_registry(&stats);
        dispatcher.attach("pinned", "Status",
                          std::unique_ptr<router::RouteTopicRuntimeBase>(new Runtime(state)));
        CHECK(stats.sources.size() == 1);
        throw std::runtime_error("injected startup failure");
    } catch (const std::runtime_error& error) {
        caught = true;
        CHECK(std::string(error.what()) == "injected startup failure");
    }
    CHECK(caught);
    CHECK(state->closed == 1);
}

int main() {
    test_startup_unwinding();
    rti::core::cond::AsyncWaitSet aws;
    Stats stats;
    router::AsyncWaitSetDispatcher dispatcher(aws);
    dispatcher.set_stats_registry(&stats);
    aws.start();
    auto first = std::make_shared<State>();
    dispatcher.attach("route", "topic",
                      std::unique_ptr<router::RouteTopicRuntimeBase>(new Runtime(first)));
    CHECK(stats.sources.size() == 1);
    bool rejected = false;
    try {
        dispatcher.attach("route", "topic", std::unique_ptr<router::RouteTopicRuntimeBase>(
            new Runtime(std::make_shared<State>())));
    } catch (const std::exception&) {
        rejected = true;
    }
    CHECK(rejected);
    CHECK(first->closed == 0);
    CHECK(dispatcher.forwarded("route", "topic") == 7);
    CHECK(stats.sources.size() == 1);
    CHECK(dispatcher.detach_and_close("route", "topic"));
    CHECK(first->closed == 1);
    CHECK(stats.sources.empty());

    auto failed = std::make_shared<State>();
    rejected = false;
    try {
        dispatcher.attach("failed", "topic",
            std::unique_ptr<router::RouteTopicRuntimeBase>(new Runtime(failed, true)));
    } catch (const std::exception&) {
        rejected = true;
    }
    CHECK(rejected);
    CHECK(failed->closed == 1);
    CHECK(stats.sources.empty());
    CHECK(!dispatcher.detach_and_close("failed", "topic"));

    auto blocking = std::make_shared<State>();
    blocking->release = false;
    auto runtime = std::unique_ptr<Runtime>(new Runtime(blocking));
    auto guard = runtime->guard;
    dispatcher.attach("blocking", "topic", std::move(runtime));
    guard.trigger_value(true);
    {
        std::unique_lock<std::mutex> lock(blocking->mutex);
        CHECK(blocking->cv.wait_for(lock, std::chrono::seconds(3),
                                   [&] { return blocking->started; }));
    }
    auto detach = std::async(std::launch::async, [&] {
        return dispatcher.detach_and_close("blocking", "topic");
    });
    CHECK(detach.wait_for(std::chrono::milliseconds(100)) == std::future_status::timeout);
    {
        std::lock_guard<std::mutex> lock(blocking->mutex);
        blocking->release = true;
        blocking->cv.notify_all();
    }
    CHECK(detach.get());
    CHECK(blocking->closed == 1);
    CHECK(stats.sources.empty());
    dispatcher.shutdown();
    aws.stop();
    std::printf("Dispatcher ownership/barrier checks: %d failures\n", failures);
    return failures ? 1 : 0;
}
