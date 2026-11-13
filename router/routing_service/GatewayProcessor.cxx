#include "GatewayModel.hpp"
#include "core/Log.hpp"
#include "core/WanStatsPoll.hpp"

#include <rti/routing/processor/ProcessorPlugin.hpp>
#include <rti/pub/WriteParams.hpp>

#include <algorithm>
#include <map>
#include <stdexcept>
#include <yaml-cpp/yaml.h>

namespace act_rs {
namespace {

using Data = dds::core::xtypes::DynamicData;
using Route = rti::routing::processor::Route;
using Reader = dds::sub::DataReader<Data>;
using Writer = dds::pub::DataWriter<Data>;
using Properties = rti::routing::PropertySet;

const std::string& required(const Properties& properties, const std::string& name) {
    const auto found = properties.find(name);
    if (found == properties.end() || found->second.empty()) {
        throw std::runtime_error("ACT processor missing property: " + name);
    }
    return found->second;
}

bool enabled(const std::vector<Binding>& bindings) {
    return std::any_of(bindings.begin(), bindings.end(),
                       [](const Binding& binding) { return binding.enabled; });
}

void lifecycle(Writer writer, const Data& key, const dds::sub::SampleInfo& info,
               int priority = 0) {
    const auto handle = writer.lookup_instance(key);
    if (handle == dds::core::null) {
        router::Log::warn("rs.lifecycle.unmaterialized",
                         {{"type", key.type().name()}});
        return;
    }
    rti::pub::WriteParams params;
    params.handle(handle).priority(priority);
    using State = dds::sub::status::InstanceState;
    if (info.state().instance_state() == State::not_alive_disposed()) {
        writer->dispose_instance(params);
    } else if (info.state().instance_state() == State::not_alive_no_writers()) {
        writer->unregister_instance(params);
    } else {
        throw std::runtime_error("unsupported invalid-data lifecycle state");
    }
}

class GatewayProcessor : public rti::routing::processor::NoOpProcessor {
public:
    explicit GatewayProcessor(const Properties& properties)
        : model_(required(properties, "config"), required(properties, "role"),
                 required(properties, "node"), required(properties, "router"),
                 YAML::Load(required(properties, "teams")).as<std::vector<std::string>>()),
          types_(model_.config.types_xml_path), codec_(types_) {
        if (!model_.config.presence_participant.empty() &&
            required(properties, "data_plane_only") != "true") {
            throw std::runtime_error("health/control parity is not implemented; "
                                     "data-plane-only execution requires explicit opt-in");
        }
    }

    void on_start(Route& route) override {
        for (const std::string family : {"Event", "Status"}) {
            auto writer = route.output<Data>("wan_out_" + family).dds_data_writer();
            auto reader = route.input<Data>("wan_in_" + family).dds_data_reader();
            auto participant = writer.publisher().participant();
            dds::pub::ignore(participant, writer.instance_handle());
            dds::sub::ignore(participant, reader.instance_handle());
        }
        for (const auto& destination : model_.destinations) {
            auto writer = route.output<Data>("lan_out_" + destination.first).dds_data_writer();
            auto participant = writer.publisher().participant();
            dds::pub::ignore(participant, writer.instance_handle());
        }
        for (const auto& source : model_.sources) {
            auto input = route.input<Data>("lan_in_" + source.first);
            if (input.dds_data_reader().topic_description().type_name() !=
                router::wan_mapping(source.first).type) {
                throw std::runtime_error("LAN input type mismatch: " + source.first);
            }
        }
        router::Log::info("rs.gateway.started", {{"node", model_.config.node_name},
                          {"mode", "data_plane_only"}});
    }

    void on_data_available(Route& route) override { pump(route); }
    void on_periodic_action(Route& route) override { pump(route); }

private:
    template<typename Forward>
    void consume(Route& route, const std::string& name, Forward forward) {
        auto input = route.input<Data>(name);
        auto reader = input.dds_data_reader();
        const auto samples = input.select().max_samples(32).take();
        std::map<std::string, dds::sub::SampleInfo> instances;
        for (const auto& sample : samples) {
            try {
                instances[router::wan_handle_key(sample.info().instance_handle())] = sample.info();
                if (sample.info().valid()) forward(sample.data(), sample.info(), true);
            } catch (const std::exception& error) {
                router::Log::error("rs.sample.error", {{"input", name}, {"error", error.what()}});
            }
        }
        // Keep the loan alive until native key recovery and final-state forwarding finish.
        for (const auto& instance : instances) {
            const auto& info = instance.second;
            if (info.state().instance_state() == dds::sub::status::InstanceState::alive()) continue;
            try {
                Data key = input.create_data();
                reader.key_value(key, info.instance_handle());
                forward(key, info, false);
            } catch (const std::exception& error) {
                router::Log::error("rs.lifecycle.error", {{"input", name}, {"error", error.what()}});
            }
        }
    }

    void pump(Route& route) {
        for (const auto& source : model_.sources) {
            consume(route, "lan_in_" + source.first,
                    [&](const Data& data, const dds::sub::SampleInfo& info, bool valid) {
                if (!enabled(source.second)) return;
                router::WanMetadata metadata;
                metadata.source_id = model_.config.node_name;
                if (valid) metadata = model_.metadata(source.first, data);
                const auto frame = valid ? codec_.pack(source.first, data, metadata)
                                         : codec_.key_frame(source.first, data, metadata);
                auto writer = route.output<Data>(
                        "wan_out_" + std::string(router::wan_mapping(source.first).family))
                        .dds_data_writer();
                const int priority = model_.publication_priority(source.first);
                if (valid) {
                    rti::pub::WriteParams params;
                    params.priority(priority);
                    writer->write(frame, params);
                } else {
                    lifecycle(writer, frame, info, priority);
                }
            });
        }
        for (const std::string family : {"Event", "Status"}) {
            consume(route, "wan_in_" + family,
                    [&](const Data& frame, const dds::sub::SampleInfo& info, bool valid) {
                if (frame.value<std::string>("source_id") == model_.config.node_name) return;
                auto decoded = valid ? codec_.unpack(frame) : codec_.unpack_key(frame);
                const auto found = model_.destinations.find(decoded.topic);
                if (found == model_.destinations.end() || !enabled(found->second)) return;
                auto writer = route.output<Data>("lan_out_" + decoded.topic).dds_data_writer();
                if (valid) writer.write(decoded.data);
                else lifecycle(writer, decoded.data, info);
            });
        }
    }

    GatewayModel model_;
    dds::core::QosProvider types_;
    router::WanCodec codec_;
};

} // namespace
} // namespace act_rs

class ActGatewayPlugin : public rti::routing::processor::ProcessorPlugin {
public:
    explicit ActGatewayPlugin(const rti::routing::PropertySet&) {}
    rti::routing::processor::Processor* create_processor(
            rti::routing::processor::Route&, const rti::routing::PropertySet& properties) override {
        return new act_rs::GatewayProcessor(properties);
    }
    void delete_processor(rti::routing::processor::Route&,
                          rti::routing::processor::Processor* processor) override {
        delete processor;
    }
};

using rti::routing::PropertySet;
RTI_PROCESSOR_PLUGIN_CREATE_FUNCTION_DECL(ActGatewayPlugin)
RTI_PROCESSOR_PLUGIN_CREATE_FUNCTION_DEF(ActGatewayPlugin)
