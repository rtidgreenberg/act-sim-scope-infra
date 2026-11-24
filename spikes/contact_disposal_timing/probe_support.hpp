#pragma once

#include <dds/dds.hpp>
#include <chrono>
#include <functional>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>

using Data = dds::core::xtypes::DynamicData;
using Reader = dds::sub::DataReader<Data>;
using Writer = dds::pub::DataWriter<Data>;
using Handle = dds::core::InstanceHandle;
using State = dds::sub::status::InstanceState;
using Clock = std::chrono::steady_clock;

inline std::string quote(const std::string& value)
{
    std::ostringstream out;
    out << '"';
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') out << '\\' << c;
        else if (c < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c);
        else out << c;
    }
    return out.str() + '"';
}

inline void require(bool condition, const std::string& error)
{
    if (!condition) throw std::runtime_error(error);
}

inline double ms(Clock::time_point start)
{
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}

inline void wait_for(const std::function<bool()>& ready, int seconds, const std::string& error)
{
    auto deadline = Clock::now() + std::chrono::seconds(seconds);
    do {
        if (ready()) return;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    } while (Clock::now() < deadline);
    throw std::runtime_error("timeout: " + error);
}

inline Data contact(const dds::core::xtypes::DynamicType& type, int trial)
{
    Data sample(type);
    sample.value<std::string>("source", "deliberately-wrong-producer");
    sample.value<std::string>("run_id", "contact-disposal-20261119");
    sample.value<std::string>("source_node", "original-sensor-node");
    sample.value<uint64_t>("audit_sequence", 1234567890123ULL + trial);
    sample.value<int64_t>("sent_at_ns", 1795117700123456789LL + trial);
    sample.value<std::string>("contact_id", "long-contact-key-" + std::to_string(trial));
    sample.value<std::string>("classification", "SURFACE");
    sample.value<float>("bearing_deg", 123.25f);
    sample.value<float>("range_m", 1456.5f);
    sample.value<float>("course_deg", 217.75f);
    sample.value<float>("speed_knots", 12.5f);
    sample.value<float>("depth_m", 4.25f);
    sample.value<float>("confidence_pct", 93.75f);
    sample.value<std::string>("sensor_type", "isolated-spike");
    sample.value<float>("latitude", 32.125f);
    sample.value<float>("longitude", -117.25f);
    sample.value<bool>("lost", false);
    sample.value<int64_t>("timestamp", 1795117700123LL + trial);
    return sample;
}
