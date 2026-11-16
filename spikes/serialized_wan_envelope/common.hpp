#pragma once
#include <dds/dds.hpp>
#include <rti/topic/cdr.hpp>
#include <chrono>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <thread>
#include <vector>

namespace spike {
using Data = dds::core::xtypes::DynamicData;
using Type = dds::core::xtypes::DynamicType;
using Topic = dds::topic::Topic<Data>;
using Reader = dds::sub::DataReader<Data>;
using Writer = dds::pub::DataWriter<Data>;
using Participant = dds::domain::DomainParticipant;
using Clock = std::chrono::steady_clock;
const std::string lib = "SerializedSpike::";
inline void require(bool condition, const std::string& reason)
{
    if (!condition) throw std::runtime_error(reason);
}
inline void until(const std::string& reason, const std::function<bool()>& fn)
{
    const auto end = Clock::now() + std::chrono::seconds(35);
    do {
        if (fn()) return;
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    } while (Clock::now() < end);
    throw std::runtime_error("Timeout: " + reason);
}
inline bool exists(const std::string& name) { return bool(std::ifstream(name)); }
inline void mark(const std::string& name) { std::ofstream(name).put('1'); }
inline void wait(const std::string& name) { until(name, [&] { return exists(name); }); }
inline Participant participant(int domain, dds::core::QosProvider& qos)
{
    return Participant(domain, qos.participant_qos(lib + "udp"));
}
inline Reader reader(Participant p, Topic topic, dds::core::QosProvider& qos)
{
    return Reader(dds::sub::Subscriber(p), topic, qos.datareader_qos(lib + "sample"));
}
inline Writer writer(Participant p, Topic topic, dds::core::QosProvider& qos)
{
    return Writer(dds::pub::Publisher(p), topic, qos.datawriter_qos(lib + "sample"));
}
template<class T>
dds::sub::DataReader<T> builtin(Participant p, const std::string& name)
{
    using Builtin = dds::sub::DataReader<T>;
    std::vector<Builtin> found;
    dds::sub::find<Builtin>(dds::sub::builtin_subscriber(p), name, std::back_inserter(found));
    require(found.size() == 1, "Missing builtin " + name);
    return found.front();
}
template<class T>
void learn(dds::sub::DataReader<T>& discovery, std::map<std::string, Type>& types)
{
    for (const auto& sample : discovery.read()) {
        if (!sample.info().valid()) continue;
        const std::string name = sample.data().topic_name();
        if (name.compare(0, 4, "App_") != 0) continue;
        auto type = sample.data()->type();
        if (type && !types.count(name)) {
            types.emplace(name, type.get());
            const auto reliability = sample.data().reliability().kind();
            const auto durability = sample.data().durability().kind();
            require(reliability == dds::core::policy::ReliabilityKind::RELIABLE,
                    "Discovered LAN reliability differs from explicit spike contract");
            require(durability == dds::core::policy::DurabilityKind::VOLATILE,
                    "Discovered LAN durability differs from explicit spike contract");
            std::cout << "DISCOVER_QOS " << name
                      << " reliable/volatile; explicit profiles, NOT auto-configured" << std::endl;
        }
    }
}
inline std::string schema_id(const Type& type)
{
    // Same-release spike identity, not a standardized XTypes identifier or security hash.
    const auto text = rti::core::xtypes::to_string(type);
    uint64_t hash = 14695981039346656037ULL;
    for (unsigned char c : text) hash = (hash ^ c) * 1099511628211ULL;
    std::ostringstream result;
    result << "rti77-idl-fnv1a64:" << std::hex << hash;
    return result.str();
}
inline std::vector<uint8_t> octets(const std::vector<char>& buffer)
{
    return std::vector<uint8_t>(buffer.begin(), buffer.end());
}
inline std::vector<char> cdr(const Data& frame)
{
    std::vector<uint8_t> bytes;
    frame.get_values("payload", bytes);
    return std::vector<char>(bytes.begin(), bytes.end());
}
inline std::vector<char> serialize(const Data& value)
{
    std::vector<char> bytes;
    rti::topic::to_cdr_buffer(bytes, value, dds::core::policy::DataRepresentation::xcdr());
    return bytes;
}
inline std::string instance_id(const Data& value)
{
    const auto& type = static_cast<const dds::core::xtypes::StructType&>(value.type());
    std::string name;
    for (const auto& member : type.members()) {
        if (!member.is_key()) continue;
        require(name.empty()
                    && member.type().kind() == dds::core::xtypes::TypeKind::STRING_TYPE,
                "Only one top-level string key is qualified");
        name = member.name();
    }
    require(!name.empty(), "Unkeyed/compound payload keys are not qualified");
    return value.value<std::string>(name);
}
inline void validate(const Data& frame, const std::map<std::string, Type>& local)
{
    const auto found = local.find(frame.value<std::string>("original_topic"));
    require(found != local.end(), "unknown_local_type");
    require(frame.value<std::string>("schema_name") == found->second.name()
                && frame.value<std::string>("schema_identity") == schema_id(found->second),
            "schema_mismatch");
    require(frame.value<std::string>("encoding") == "connext-cdr-xcdr1",
            "unsupported_encoding");
    require(frame.value<std::string>("operation") == "WRITE", "unsupported_lifecycle");
    const auto bytes = cdr(frame);
    require(bytes.size() >= 4
                && uint8_t(bytes[0]) == 0
                && (uint8_t(bytes[1]) == 0 || uint8_t(bytes[1]) == 1
                    || uint8_t(bytes[1]) == 2 || uint8_t(bytes[1]) == 3),
            "invalid_cdr_encapsulation");
}
}
