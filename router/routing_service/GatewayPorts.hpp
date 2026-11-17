#pragma once

#include <dds/dds.hpp>
#include <map>
#include <mutex>

namespace act_rs {

// Only native DDS handles cross route boundaries; never retain LAN Route references.
struct GatewayPorts {
    using Data = dds::core::xtypes::DynamicData;
    std::recursive_mutex mutex;
    std::map<std::string, dds::sub::DataReader<Data>> inputs;
    std::map<std::string, dds::pub::DataWriter<Data>> outputs;
};

} // namespace act_rs
