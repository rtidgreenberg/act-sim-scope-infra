#pragma once

#include <chrono>
#include <climits>
#include <cstdlib>
#include <stdexcept>
#include <string>
#include <utility>

namespace router {

struct WanPolicy {
    int timeout_seconds;
    int heartbeat_seconds;
    int heartbeat_retries;

    static int positive_integer(const std::string& value, const std::string& name) {
        if (value.empty() || value.find_first_not_of("0123456789") != std::string::npos) {
            throw std::invalid_argument(name + " must be an integer from 1 to 2147483647");
        }
        std::size_t used = 0;
        const auto number = std::stoll(value, &used);
        if (used != value.size() || number <= 0 || number > INT_MAX) {
            throw std::invalid_argument(name + " must be an integer from 1 to 2147483647");
        }
        return static_cast<int>(number);
    }

    static WanPolicy from_environment() {
        const auto read = [](const char* name, const char* fallback) {
            const char* value = std::getenv(name);
            return positive_integer(value ? value : fallback, name);
        };
        WanPolicy policy{read("WAN_TIMEOUT_SEC", "300"), read("WAN_HB_PERIOD_SEC", "3"), 0};
        policy.heartbeat_retries = policy.timeout_seconds / policy.heartbeat_seconds
                + (policy.timeout_seconds % policy.heartbeat_seconds != 0);
        if (std::getenv("WAN_HB_RETRIES") &&
            read("WAN_HB_RETRIES", "100") != policy.heartbeat_retries) {
            throw std::invalid_argument(
                    "WAN_HB_RETRIES is derived: ceil(WAN_TIMEOUT_SEC / WAN_HB_PERIOD_SEC)");
        }
        return policy;
    }

    void export_environment() const {
        for (const auto& entry : {std::make_pair("WAN_TIMEOUT_SEC", timeout_seconds),
                                 std::make_pair("WAN_HB_PERIOD_SEC", heartbeat_seconds),
                                 std::make_pair("WAN_HB_RETRIES", heartbeat_retries)}) {
            if (setenv(entry.first, std::to_string(entry.second).c_str(), 1) != 0) {
                throw std::runtime_error(std::string("Unable to export ") + entry.first);
            }
        }
    }
};

inline bool status_timeout_expired(std::chrono::steady_clock::time_point last_received,
                                   std::chrono::steady_clock::time_point now,
                                   std::chrono::seconds timeout) {
    return now - last_received >= timeout;
}

} // namespace router
