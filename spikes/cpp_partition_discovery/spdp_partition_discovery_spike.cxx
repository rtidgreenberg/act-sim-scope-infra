// spdp_partition_discovery_spike.cxx -- Modern C++ (C++11) re-run of the Python
// partition_retarget_spike.py Parts A/B: does dds::domain::discovered_participants()
// stay empty on both sides when two DomainParticipants have disjoint concrete
// participant_partition values? See PLAN.md for the full claim list and the
// MCP-hallucination this spike works around (participant_partition is a direct
// DomainParticipantQos::partition member, not a Discovery-policy method).
//
// Build:
//   cmake -B build -DCONNEXTDDS_ARCH=x64Linux4gcc7.3.0
//   cmake --build build
// Run (plain SPDP, UDPv4-only, single process, one domain):
//   ./build/spdp_partition_discovery_spike [domain_id]

#include <dds/dds.hpp>
#include <dds/domain/discovery.hpp>

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>

namespace {

const int DEFAULT_DOMAIN = 5750;
const std::chrono::milliseconds POLL_INTERVAL(50);

dds::domain::DomainParticipant make_participant(int32_t domain_id,
                                                 const std::string& participant_partition)
{
    dds::domain::qos::DomainParticipantQos qos;
    qos << rti::core::policy::TransportBuiltin::UDPv4();
    qos << dds::core::policy::Partition(participant_partition);
    return dds::domain::DomainParticipant(domain_id, qos);
}

std::size_t discovered_count(const dds::domain::DomainParticipant& participant)
{
    return dds::domain::discovered_participants(participant).size();
}

// Poll pred() until true or timeout; return elapsed ms, or -1 on timeout.
long wait_until(const std::function<bool()>& pred, long timeout_ms)
{
    auto t0 = std::chrono::steady_clock::now();
    auto deadline = t0 + std::chrono::milliseconds(timeout_ms);
    while (std::chrono::steady_clock::now() < deadline) {
        if (pred()) {
            return std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - t0).count();
        }
        std::this_thread::sleep_for(POLL_INTERVAL);
    }
    return pred() ? std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - t0).count() : -1;
}

// Poll pred() for the WHOLE window (no early exit); true iff it was NEVER true.
bool held_never(const std::function<bool()>& pred, long hold_ms)
{
    auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(hold_ms);
    bool ever_true = false;
    while (std::chrono::steady_clock::now() < deadline) {
        if (pred()) {
            ever_true = true;
        }
        std::this_thread::sleep_for(POLL_INTERVAL);
    }
    return !ever_true;
}

std::string fmt(long ms)
{
    return ms < 0 ? "never" : (std::to_string(ms) + "ms");
}

bool part_a_baseline(int32_t domain_id)
{
    std::cout << "Part A: baseline -- shared participant_partition (\"SHARED\") "
                 "matches at SPDP level\n";
    dds::domain::DomainParticipant pa = make_participant(domain_id, "SHARED");
    dds::domain::DomainParticipant pb = make_participant(domain_id, "SHARED");

    long a_sees_b = wait_until([&]() { return discovered_count(pa) > 0; }, 15000);
    long b_sees_a = wait_until([&]() { return discovered_count(pb) > 0; }, 15000);

    std::cout << "  a_sees_b=" << fmt(a_sees_b) << " b_sees_a=" << fmt(b_sees_a) << "\n";

    if (a_sees_b < 0 || b_sees_a < 0) {
        std::cout << "  FAIL: shared participant_partition never mutually discovered\n";
        return false;
    }
    std::cout << "  PASS\n";
    return true;
}

bool part_b_mismatch_invisible(int32_t domain_id)
{
    std::cout << "Part B: disjoint participant_partition (\"TEAM_A\"/\"TEAM_B\") -- "
                 "discovered_participants() held empty on BOTH sides\n";
    dds::domain::DomainParticipant pa = make_participant(domain_id, "TEAM_A");
    dds::domain::DomainParticipant pb = make_participant(domain_id, "TEAM_B");

    const long HOLD_MS = 4000;
    bool a_never_saw_b = held_never([&]() { return discovered_count(pa) > 0; }, HOLD_MS);
    bool b_never_saw_a = held_never([&]() { return discovered_count(pb) > 0; }, HOLD_MS);

    std::cout << "  a_never_saw_b=" << (a_never_saw_b ? "true" : "false")
              << " b_never_saw_a=" << (b_never_saw_a ? "true" : "false")
              << " (held " << HOLD_MS << "ms, polled continuously)\n";

    if (!a_never_saw_b || !b_never_saw_a) {
        std::cout << "  FAIL: mismatched participant_partition still became "
                     "mutually visible in discovered_participants()\n";
        return false;
    }
    std::cout << "  PASS\n";
    return true;
}

} // namespace

int main(int argc, char** argv)
{
    int32_t base_domain = argc > 1 ? std::atoi(argv[1]) : DEFAULT_DOMAIN;
    std::cout << "spdp_partition_discovery_spike on domain " << base_domain
              << " (plain SPDP, UDPv4-only)\n\n";

    bool a_ok = part_a_baseline(base_domain);
    std::cout << "\n";
    bool b_ok = part_b_mismatch_invisible(base_domain);
    std::cout << "\n";

    if (a_ok && b_ok) {
        std::cout << "SPIKE PASSED: baseline shared-partition match holds (A); "
                     "disjoint participant_partition holds discovered_participants()=empty "
                     "on both sides (B), confirmed via the Modern C++ API.\n";
        return 0;
    }
    std::cout << "SPIKE FAILED\n";
    return 1;
}
