#pragma once

#include <dds/dds.hpp>

#include <chrono>
#include <cstdint>
#include <functional>
#include <iostream>
#include <iterator>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
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
constexpr int source_domain = 91;
constexpr int wan_domain = 92;
constexpr int destination_domain = 93;
const std::string library = "ACT_QOS_LIB::";

inline void require(bool valid, const std::string& message)
{
    if (!valid) throw std::runtime_error(message);
}

inline void until(const std::string& description, const std::function<bool()>& fn,
                  double seconds = 20)
{
    const auto end = Clock::now() + std::chrono::milliseconds(int(seconds * 1000));
    do {
        if (fn()) return;
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    } while (Clock::now() < end);
    throw std::runtime_error("Timeout: " + description);
}

inline Participant participant(int domain, dds::core::QosProvider& qos)
{
    return Participant(domain, qos.participant_qos(library + "lan_router_participant"));
}

inline Reader reader(Participant p, const Topic& topic, dds::core::QosProvider& qos,
                     const std::string& profile = "lan_event",
                     const std::string& name = "")
{
    auto endpoint_qos = qos.datareader_qos(library + profile);
    if (!name.empty()) endpoint_qos << rti::core::policy::EntityName(name);
    return Reader(dds::sub::Subscriber(p), topic, endpoint_qos);
}

inline Writer writer(Participant p, const Topic& topic, dds::core::QosProvider& qos,
                     const std::string& profile = "lan_event")
{
    return Writer(dds::pub::Publisher(p), topic, qos.datawriter_qos(library + profile));
}

inline dds::sub::DataReader<dds::topic::PublicationBuiltinTopicData>
publications(Participant p)
{
    using Builtin = dds::sub::DataReader<dds::topic::PublicationBuiltinTopicData>;
    std::vector<Builtin> found;
    dds::sub::find<Builtin>(dds::sub::builtin_subscriber(p), "DCPSPublication",
                           std::back_inserter(found));
    require(found.size() == 1, "Missing publication discovery reader");
    return found.front();
}

using Subscriptions = dds::sub::DataReader<dds::topic::SubscriptionBuiltinTopicData>;

inline Subscriptions subscriptions(Participant p)
{
    std::vector<Subscriptions> found;
    dds::sub::find<Subscriptions>(dds::sub::builtin_subscriber(p), "DCPSSubscription",
                                 std::back_inserter(found));
    require(found.size() == 1, "Missing subscription discovery reader");
    return found.front();
}

inline void require_no_state(Participant p)
{
    for (const auto& sample : publications(p).read()) {
        require(!sample.info().valid() || sample.data().topic_name() != "State",
                "Unexpected State publication in discovery-only mode");
    }
    for (const auto& sample : subscriptions(p).read()) {
        require(!sample.info().valid() || sample.data().topic_name() != "State",
                "Unexpected State subscription in discovery-only mode");
    }
}

inline std::string phase_reader_name(uint32_t phase)
{
    return "fixture_phase_" + std::to_string(phase);
}

inline Type discover(Participant p, const std::string& name)
{
    auto builtin = publications(p);
    std::unique_ptr<Type> result;
    until("complete discovered type for " + name, [&] {
        for (const auto& sample : builtin.read()) {
            if (!sample.info().valid() || sample.data().topic_name() != name) continue;
            auto type = sample.data()->type();
            if (type) result.reset(new Type(type.get()));
        }
        return bool(result);
    });
    return *result;
}

class Progress {
public:
    Progress(Participant p, dds::core::QosProvider& bootstrap, dds::core::QosProvider& qos)
        : type_(bootstrap->type("SpikeProgress")),
          topic_(p, "SpikeProgress", type_),
          writer_(writer(p, topic_, qos, "router_admin_status")),
          reader_(reader(p, topic_, qos, "router_admin_status"))
    {
    }

    void put(const std::string& actor, uint32_t phase, const std::string& status,
             const std::string& selection = "", uint32_t count = 0,
             uint32_t expected = 0, int64_t pushed = 0, uint32_t local_readers = 0)
    {
        Data value(type_);
        value.value<std::string>("actor", actor);
        value.value<uint32_t>("phase", phase);
        value.value<std::string>("status", status);
        value.value<std::string>("selection", selection);
        value.value<uint32_t>("count", count);
        value.value<uint32_t>("expected", expected);
        value.value<int64_t>("pushed", pushed);
        value.value<uint32_t>("local_readers", local_readers);
        writer_.write(value);
    }

    std::unique_ptr<Data> get(const std::string& actor, uint32_t phase,
                              const std::string& status)
    {
        for (const auto& sample : reader_.read()) {
            if (sample.info().valid()
                && sample.data().value<std::string>("actor") == actor
                && sample.data().value<uint32_t>("phase") == phase
                && sample.data().value<std::string>("status") == status) {
                return std::unique_ptr<Data>(new Data(sample.data()));
            }
        }
        return nullptr;
    }

    Data wait(const std::string& actor, uint32_t phase, const std::string& status)
    {
        std::unique_ptr<Data> value;
        until(actor + " phase " + std::to_string(phase) + " " + status, [&] {
            value = get(actor, phase, status);
            return bool(value);
        });
        return *value;
    }

private:
    Type type_;
    Topic topic_;
    Writer writer_;
    Reader reader_;
};

struct Entry {
    std::string topic, type_name, branch;
    int32_t label;
};

inline std::vector<Entry> entries(const Data& catalog)
{
    std::vector<Entry> result;
    auto values = catalog.value<Data>("entries");
    for (uint32_t i = 1; i <= values.member_count(); ++i) {
        auto value = values.value<Data>(i);
        result.push_back({value.value<std::string>("original_topic"),
                          value.value<std::string>("type_name"),
                          value.value<std::string>("branch"),
                          value.value<int32_t>("label")});
    }
    require(!result.empty(), "Empty catalog");
    return result;
}

inline const Entry& select(const std::vector<Entry>& catalog, const std::string& topic)
{
    for (const auto& entry : catalog) if (entry.topic == topic) return entry;
    throw std::invalid_argument("Unknown catalog topic: " + topic);
}

inline Type member_type(const Type& type, const std::string& name)
{
    require(type.kind() == dds::core::xtypes::TypeKind::STRUCTURE_TYPE,
            "Expected structure: " + type.name());
    const auto& structure = static_cast<const dds::core::xtypes::StructType&>(type);
    for (const auto& member : structure.members()) {
        if (member.name() == name) return member.type();
    }
    throw std::runtime_error("Missing named member: " + name);
}

inline Type branch_type(const Type& type, const Entry& entry)
{
    const auto& payload = member_type(type, "payload");
    require(payload.kind() == dds::core::xtypes::TypeKind::UNION_TYPE,
            "Envelope payload is not a union");
    const auto& union_type = static_cast<const dds::core::xtypes::UnionType&>(payload);
    for (const auto& member : union_type.members()) {
        if (member.name() == entry.branch) {
            require(member.type().name() == entry.type_name,
                    "Catalog type name disagrees with discovered branch");
            require(union_type.member(union_type.find_member_by_label(entry.label)).name()
                        == entry.branch, "Catalog label disagrees with discovered union");
            return member.type();
        }
    }
    throw std::runtime_error("Catalog branch absent from discovery");
}

inline std::vector<Entry> union_entries(const Type& envelope)
{
    auto payload = member_type(envelope, "payload");
    require(payload.kind() == dds::core::xtypes::TypeKind::UNION_TYPE,
            "Discovered payload is not a union");
    const auto& union_type = static_cast<const dds::core::xtypes::UnionType&>(payload);
    std::vector<Entry> result;
    std::set<int32_t> used_labels;
    for (const auto& member : union_type.members()) {
        const auto labels = member.labels();
        require(labels.size() == 1 && labels.front() > 0,
                "Spike requires one positive explicit label per branch");
        const std::string name = member.name();
        require(!name.empty() && used_labels.insert(labels.front()).second,
                "Missing branch name or duplicate label");
        result.push_back({name, member.type().name(), name, labels.front()});
        std::cout << "DISCOVERED binding " << labels.front() << " -> " << name
                  << " -> " << member.type().name() << std::endl;
    }
    require(!result.empty(), "No discovered union branches");
    return result;
}

inline std::map<std::string, Type> local_types(Subscriptions builtin, uint32_t phase,
                                             uint32_t expected_readers)
{
    std::map<std::string, Type> result;
    until("current-phase local subscription discovery", [&] {
        result.clear();
        uint32_t count = 0;
        bool pending = false;
        for (const auto& sample : builtin.read()) {
            if (!sample.info().valid()
                || sample.info().state().instance_state()
                    != dds::sub::status::InstanceState::alive()) continue;
            const std::string topic = sample.data().topic_name();
            if (topic.compare(0, 4, "Cmd_") != 0) continue;
            ++count;
            auto name = sample.data()->subscription_name().name();
            auto type = sample.data()->type();
            if (!name || name.get() != phase_reader_name(phase) || !type) {
                pending = true;
                continue;
            }
            require(result.find(topic) == result.end(), "Duplicate local fixture topic");
            result.emplace(topic, type.get());
        }
        return !pending && count == expected_readers;
    });
    return result;
}
} // namespace spike
