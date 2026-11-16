#include <dds/dds.hpp>
#include <rti/pub/WriteParams.hpp>
#include <rti/routing/processor/ProcessorPlugin.hpp>

#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <string>

namespace {
using Data = dds::core::xtypes::DynamicData;
using Reader = dds::sub::DataReader<Data>;
using Writer = dds::pub::DataWriter<Data>;
using Topic = dds::topic::Topic<Data>;
using Route = rti::routing::processor::Route;
using Properties = rti::routing::PropertySet;

struct Family {
    Topic topic;
    Reader reader;
    Writer writer;
    Family(dds::domain::DomainParticipant participant, const std::string& name,
           dds::core::QosProvider& types, dds::core::QosProvider& qos)
        : topic(participant, name, types->type("Uaq" + name)),
          reader(dds::sub::Subscriber(participant),
                 dds::topic::ContentFilteredTopic<Data>(
                     topic, "owner_" + name, dds::topic::Filter("source <> 'gateway'")),
                 qos.datareader_qos("UAQ::" + name)),
          writer(dds::pub::Publisher(participant), topic, qos.datawriter_qos("UAQ::" + name))
    {
        const auto mode = writer.qos().policy<rti::core::policy::PublishMode>();
        if (mode.kind() != rti::core::policy::PublishModeKind::ASYNCHRONOUS)
            throw std::runtime_error("WAN is not asynchronous");
        const auto channels = writer.qos().policy<rti::core::policy::MultiChannel>();
        if (channels.channels().size() != 2)
            throw std::runtime_error("WAN does not have exactly two channels");
        if (mode.flow_controller_name() != "dds.flow_controller.token_bucket.uaq")
            throw std::runtime_error("WAN does not use the shared flow controller");
        std::cout << "WAN_OWNER " << name
                  << " writer=1 reader=1 async=true channels=2 shared_flow=uaq" << std::endl;
    }
};

struct Owner {
    dds::core::QosProvider types;
    dds::core::QosProvider qos;
    dds::domain::DomainParticipant participant;
    std::map<std::string, std::unique_ptr<Family>> families;
    std::map<std::string, std::vector<Data>> pending;
    std::mutex mutex;
    explicit Owner(const Properties& p)
        : types(p.at("types")), qos(p.at("qos")),
          participant(std::stoi(p.at("wan_domain")), qos.participant_qos("UAQ::participant"))
    {
        for (const std::string name : {"Event", "State", "Periodic"})
            families.emplace(name, std::unique_ptr<Family>(new Family(participant, name, types, qos)));
    }
};

class Processor : public rti::routing::processor::NoOpProcessor {
public:
    Processor(std::shared_ptr<Owner> owner, const Properties& p)
        : owner_(std::move(owner)), direction_(p.at("direction")), topic_(p.at("topic")),
          family_(p.at("family")), branch_(p.at("branch")), priority_(std::stoi(p.at("priority"))) {
        if (direction_ == "rx") owner_->pending.emplace(topic_, std::vector<Data>());
    }

    void on_start(Route&) override {
        std::cout << "START " << direction_ << " " << topic_ << std::endl;
    }

    void on_data_available(Route& route) override { pump(route); }
    void on_periodic_action(Route& route) override { pump(route); }

private:
    void pump(Route& route) {
        std::lock_guard<std::mutex> lock(owner_->mutex);
        auto& family = *owner_->families.at(family_);
        if (direction_ == "tx") {
            for (const auto& sample : route.input<Data>("lan").take()) {
                if (!sample.info().valid()) continue;
                Data frame(owner_->types->type("Uaq" + family_));
                frame.value<std::string>("source", "gateway");
                frame.value<std::string>("original_topic", topic_);
                frame.value<std::string>("instance_id", sample.data().value<std::string>("id"));
                frame.value<int32_t>("priority", priority_);
                {
                    auto payload = frame.loan_value("payload");
                    payload.get().value<Data>(branch_, sample.data());
                }
                family.writer->write(frame, rti::pub::WriteParams().priority(priority_));
            }
        } else {
            for (const auto& sample : family.reader.take()) {
                if (!sample.info().valid()) continue;
                const auto topic = sample.data().value<std::string>("original_topic");
                const auto found = owner_->pending.find(topic);
                if (found == owner_->pending.end()) continue;
                auto& queue = found->second;
                if (queue.size() >= 128) throw std::runtime_error("Spike receive queue limit");
                queue.push_back(sample.data());
            }
            auto& queue = owner_->pending[topic_];
            for (auto& frame : queue) {
                auto payload = frame.loan_value("payload");
                route.output<Data>("lan").write(payload.get().value<Data>(branch_));
            }
            queue.clear();
        }
    }

    std::shared_ptr<Owner> owner_;
    std::string direction_, topic_, family_, branch_;
    int priority_;
};
}

class UnionAutoPlugin : public rti::routing::processor::ProcessorPlugin {
public:
    explicit UnionAutoPlugin(const rti::routing::PropertySet&) {}
    rti::routing::processor::Processor* create_processor(
            Route&, const Properties& properties) override {
        if (!owner_) owner_.reset(new Owner(properties));
        return new Processor(owner_, properties);
    }
    void delete_processor(Route&, rti::routing::processor::Processor* processor) override {
        delete processor;
    }
private:
    std::shared_ptr<Owner> owner_;
};

using rti::routing::PropertySet;
RTI_PROCESSOR_PLUGIN_CREATE_FUNCTION_DECL(UnionAutoPlugin)
RTI_PROCESSOR_PLUGIN_CREATE_FUNCTION_DEF(UnionAutoPlugin)
