#include "core/HeartbeatRtt.hpp"

#include <cstdio>
#include <cstdlib>
#include <map>

#define CHECK(c) do { if (!(c)) { \
    std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); std::exit(1); \
} } while (0)

struct Sink : router::LinkStatsSink {
    std::map<std::string, router::RttLinkStats> records;
    void add_writer(const std::string &, const router::WriterLinkDeltas &, bool) override {}
    void add_reader(const std::string &, const router::ReaderLinkDeltas &, bool) override {}
    void add_rtt(const std::string &name, const router::RttLinkStats &s) override {
        records[name] = s;
    }
};

static router::HeartbeatRtt::Ack ack(std::uint64_t seq, std::uint64_t age,
                                     std::uint64_t session = 11) {
    router::HeartbeatRtt::Ack a;
    a.session = session;
    a.sequence = seq;
    a.age_us = age;
    return a;
}

int main() {
    router::HeartbeatRtt a(11), b(22);
    Sink sink;
    CHECK(a.observe("B", "writer-b", 22, 1, 725000, false));
    a.reserve(42, 0);
    a.finalize(42, true);
    CHECK(b.observe("A", "writer-a", 11, 42, 20000, false));
    const auto echo = b.acknowledgment("A", 700000);
    CHECK(echo.age_us == 680000);
    a.measure("B", 22, echo, 725000);
    a.collect(sink, 800000);
    CHECK(sink.records["B"].count == 1);
    CHECK(sink.records["B"].min_us == 45000);
    CHECK(sink.records["B"].sum_us == 45000);
    CHECK(sink.records["B"].valid);
    CHECK(sink.records["B"].last_sample_age_ms == 75);
    CHECK(sink.records["B"].last_ack_age_us == 680000);
    a.measure("B", 22, echo, 900000);
    a.collect(sink, 900000);
    CHECK(sink.records["B"].count == 0 && sink.records["B"].min_us == 0);
    CHECK(sink.records["B"].valid);
    CHECK(b.observe("A", "writer-a", 11, 42, 500000, true));
    CHECK(b.observe("A", "writer-a", 11, 41, 600000, true));
    CHECK(b.acknowledgment("A", 700000).age_us == 680000);
    CHECK(b.observe("A", "writer-a", 11, 43, 650000, true));
    CHECK(b.acknowledgment("A", 700000).age_us == 50000);
    b.reserve(9, 2000000);
    b.finalize(9, true);
    CHECK(a.observe("B", "writer-b", 22, 9, 2020000, true));
    b.measure("A", 11, a.acknowledgment("B", 2700000), 2725000);
    b.collect(sink, 2800000);
    CHECK(sink.records["A"].sum_us == 45000);

    // Receipt may beat write finalization; failed writes cannot yield RTT.
    a.reserve(43, 1000000);
    a.measure("B", 22, ack(43, 100000), 1145000);
    a.collect(sink, 1145000);
    CHECK(sink.records["B"].count == 0);
    a.finalize(43, true);
    a.collect(sink, 1145000);
    CHECK(sink.records["B"].count == 1 && sink.records["B"].sum_us == 45000);
    a.reserve(44, 2000000);
    a.measure("B", 22, ack(44, 100000), 2145000);
    a.finalize(44, false);
    a.collect(sink, 2145000);
    CHECK(sink.records["B"].count == 0);
    a.reserve(45, 3000000);
    a.finalize(45, true);
    a.measure("B", 22, ack(45, 200000), 3100000); // negative result
    a.measure("B", 22, ack(45, UINT64_MAX), 3100000);
    a.measure("B", 22, ack(45, 0, 99), 3100000);
    a.measure("B", 22, ack(45, 0, 0), 3100000);
    a.measure("B", 22, ack(0, 0), 3100000);
    a.measure("B", 22, ack(45, 0), 2999999); // invalid local boundary
    a.collect(sink, 3100000);
    CHECK(sink.records["B"].count == 0);

    CHECK(!a.observe("B", "foreign-b", 33, 1, 3200000, true));
    CHECK(a.observe("B", "new-b", 33, 1, 3200000, false));
    CHECK(!a.observe("B", "writer-b", 22, 2, 3300000, false));
    a.measure("B", 22, ack(45, 0), 3300000);
    a.collect(sink, 3300000);
    CHECK(!sink.records["B"].valid && sink.records["B"].count == 0);
    CHECK(a.observe("C", "writer-c", 44, 1, 3400000, false));
    a.measure("B", 33, ack(45, 100000), 3450000);
    a.measure("C", 44, ack(45, 200000), 3450000);
    a.collect(sink, 3500000);
    CHECK(sink.records["B"].sum_us == 350000);
    CHECK(sink.records["C"].sum_us == 250000);
    a.disconnect("B");
    CHECK(a.observe("B", "new-b", 33, 2, 3600000, true));
    a.collect(sink, 3600000);
    CHECK(!sink.records["B"].valid);
    a.forget("C");
    CHECK(a.observe("C", "writer-c", 44, 2, 3600000, false));
    a.collect(sink, 40000000);
    CHECK(!sink.records["B"].valid);
    CHECK(a.acknowledgment("B", 40000000).session == 0);
    a.measure("B", 33, ack(45, 0), 40000000);
    CHECK(a.take_rejections() > 0);

    router::HeartbeatRtt recovery(11);
    CHECK(recovery.observe("B", "old-b", 22, 7, 20000, false));
    recovery.reserve(1, 0);
    recovery.finalize(1, true);
    recovery.measure("B", 22, ack(1, 100000), 145000);
    recovery.disconnect("B");
    recovery.forget("B");
    CHECK(recovery.writer_for("B") == "old-b");
    CHECK(recovery.acknowledgment("B", 65000000).session == 0);
    CHECK(recovery.observe("B", "old-b", 22, 7, 65000000, true));
    CHECK(recovery.acknowledgment("B", 65000000).session == 0); // no rejuvenated duplicate
    CHECK(recovery.observe("B", "old-b", 22, 8, 65000000, true));
    CHECK(recovery.acknowledgment("B", 65010000).age_us == 10000);
    recovery.reserve(2, 65000000);
    recovery.finalize(2, true);
    recovery.measure("B", 22, ack(2, 100000), 65145000);
    recovery.collect(sink, 65145000);
    CHECK(sink.records["B"].valid && sink.records["B"].sum_us == 45000);
    recovery.disconnect("B");
    CHECK(!recovery.observe("B", "new-b", 33, 1, 66000000, true)); // live duplicate
    CHECK(recovery.observe("B", "new-b", 33, 1, 66000000, false)); // dead, still matched
    CHECK(!recovery.observe("B", "old-b", 22, 9, 66000001, false));
    recovery.forget("B");
    CHECK(!recovery.observe("B", "third-b", 44, 1, 67000000, true));
    CHECK(recovery.observe("B", "third-b", 44, 1, 67000000, false));
    CHECK(!recovery.observe("B", "new-b", 33, 2, 67000001, false));
    CHECK(!recovery.observe("B", "old-b", 22, 10, 67000002, false));

    router::HeartbeatRtt dedup(11);
    CHECK(dedup.observe("B", "b", 22, 7, 20000, false));
    dedup.reserve(1, 0);
    dedup.finalize(1, true);
    dedup.measure("B", 22, ack(1, 100000), 145000);
    dedup.forget("B");
    CHECK(dedup.observe("B", "b", 22, 7, 150000, true));
    CHECK(dedup.acknowledgment("B", 150000).age_us == 130000);
    dedup.measure("B", 22, ack(1, 100000), 150000);
    dedup.collect(sink, 150000);
    CHECK(!sink.records["B"].valid && sink.records["B"].count == 0);

    router::HeartbeatRtt dormant_bounded(11);
    for (std::size_t i = 0; i <= router::HeartbeatRtt::peer_cap; ++i) {
        const auto name = std::to_string(i);
        CHECK(dormant_bounded.observe(name, name, i + 30, 1, i, false));
        dormant_bounded.forget(name);
    }
    CHECK(dormant_bounded.writer_for("0").empty());
    CHECK(!dormant_bounded.writer_for(
            std::to_string(router::HeartbeatRtt::peer_cap)).empty());

    router::HeartbeatRtt bounded(11);
    CHECK(bounded.observe("B", "b", 22, 1, 0, false));
    for (unsigned int i = 1; i <= 65; ++i) {
        bounded.reserve(i, i);
        bounded.finalize(i, true);
    }
    bounded.measure("B", 22, ack(1, 0), 1000);
    bounded.collect(sink, 1000);
    CHECK(sink.records["B"].count == 0); // count cap evicted oldest send
    for (std::size_t i = 1; i < router::HeartbeatRtt::peer_cap; ++i) {
        CHECK(bounded.observe(std::to_string(i), std::to_string(i), i + 30, 1, 0, false));
    }
    CHECK(!bounded.observe("overflow", "overflow", 99, 1, 0, false));
    CHECK(!bounded.observe("B", "b", 0, 1, 0, true));
    bounded.measure("B", 22, ack(65, 0), 32000066);
    CHECK(bounded.take_rejections() >= 3);
    CHECK(!bounded.observe("B", "b", 11, 2, 0, true)); // incarnation collision

    router::HeartbeatRtt phases(11);
    CHECK(phases.observe("B", "b", 22, 1, 20000, false));
    phases.reserve(1, 0);
    phases.finalize(1, true);
    phases.measure("B", 22, ack(1, 25000000), 25045000);
    phases.collect(sink, 25045000);
    CHECK(sink.records["B"].sum_us == 45000);
    phases.reserve(2, 26000000);
    for (unsigned int i = 0; i < 129; ++i) {
        phases.measure("B", 22, ack(2, 100000), 26145000);
    }
    CHECK(phases.take_rejections() == 1); // bounded pending race queue
    phases.finalize(2, true);
    phases.collect(sink, 26145000);
    CHECK(sink.records["B"].count == 1 && sink.records["B"].sum_us == 45000);

    std::vector<std::string> names{"A", "B", "C", "DEAD"};
    std::size_t cursor = 0;
    router::HeartbeatRtt::rotate_live_coverage(names, 3, 2, cursor);
    CHECK(names[0] == "A" && names[1] == "B" && names[3] == "DEAD");
    names = {"A", "B", "C", "DEAD"};
    router::HeartbeatRtt::rotate_live_coverage(names, 3, 2, cursor);
    CHECK(names[0] == "C" && names[1] == "A" && names[3] == "DEAD");
    std::puts("heartbeat_rtt: OK");
}
