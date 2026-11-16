#include "GatewayModel.hpp"
#include "GatewayMonitor.hpp"
#include "core/Log.hpp"
#include "core/WanStatsPoll.hpp"

#include <rti/routing/processor/ProcessorPlugin.hpp>
#include <rti/pub/WriteParams.hpp>

#include <algorithm>
#include <map>
#include <limits>
#include <stdexcept>

namespace act_rs {
namespace {

using Data = dds::core::xtypes::DynamicData;
using Route = rti::routing::processor::Route;
using Reader = dds::sub::DataReader<Data>;
using Writer = dds::pub::DataWriter<Data>;
using Properties = rti::routing::PropertySet;

void validate_lan_participant(const dds::domain::DomainParticipant& participant,
                              const GatewayModel& model, const std::string& port) {
    const auto found = std::find_if(model.config.participants.begin(), model.config.participants.end(),
        [&](const router::ParticipantState& candidate) {
            return candidate.name == model.config.admin_participant;
        });
    const auto identity = lan_participant_identity(model.config.node_name, model.config.router_name,
                                                   model.config.admin_participant);
    const auto name = participant.qos().policy<rti::core::policy::EntityName>().name();
    if (found == model.config.participants.end() || participant.domain_id() != found->domain ||
        !name.is_set() || name.get() != identity) {
        router::Log::error("rs.gateway.endpoint_rejected",
                          {{"error", "LAN participant/domain mismatch"}, {"port", port},
                           {"expected_identity", identity},
                           {"actual_identity", name.is_set() ? name.get() : ""},
                           {"actual_domain", std::to_string(participant.domain_id())}});
        throw std::runtime_error("LAN participant/domain mismatch: " + port);
    }
}

bool enabled(const std::vector<Binding>& bindings) {
    return std::any_of(bindings.begin(), bindings.end(),
                       [](const Binding& binding) { return binding.enabled; });
}

bool lifecycle(Writer writer, const Data& key, const dds::sub::SampleInfo& info,
               int priority = 0) {
    const auto handle = writer.lookup_instance(key);
    if (handle == dds::core::null) {
        router::Log::warn("rs.lifecycle.unmaterialized",
                         {{"type", key.type().name()}});
        return false;
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
    return true;
}

class GatewayProcessor : public rti::routing::processor::NoOpProcessor {
public:
    explicit GatewayProcessor(const Properties& properties)
        : model_(properties), types_(model_.config.types_xml_path), codec_(types_, model_.codec_mappings()) {}

    void on_start(Route& route) override {
        for (const auto& family : model_.families()) {
            auto writer = route.output<Data>("wan_out_" + family).dds_data_writer();
            auto reader = route.input<Data>("wan_in_" + family).dds_data_reader();
            auto participant = writer.publisher().participant();
            dds::pub::ignore(participant, writer.instance_handle());
            dds::sub::ignore(participant, reader.instance_handle());
        }
        for (const auto& destination : model_.destinations) {
            auto output = route.output<Data>(model_.output_port(destination.first));
            auto writer = output.dds_data_writer();
            validate_lan_participant(writer.publisher().participant(), model_, output.name());
            if (output.create_data().type() != types_->type(codec_.mapping(destination.first).type)) {
                throw std::runtime_error("LAN output type mismatch: " + destination.first);
            }
            auto participant = writer.publisher().participant();
            dds::pub::ignore(participant, writer.instance_handle());
        }
        for (const auto& source : model_.sources) {
            auto input = route.input<Data>(model_.input_port(source.first));
            validate_lan_participant(input.dds_data_reader().subscriber().participant(), model_, input.name());
            if (input.create_data().type() != types_->type(codec_.mapping(source.first).type)) {
                throw std::runtime_error("LAN input type mismatch: " + source.first);
            }
        }
        if (!model_.config.presence_participant.empty()) {
            monitor_.reset(new GatewayMonitor(route, model_, types_));
        }
        router::Log::info("rs.gateway.started", {{"node", model_.config.node_name},
                          {"mode", monitor_ ? "mesh" : "data_plane_only"}});
    }

    void on_data_available(Route& route) override { pump(route); }
    void on_periodic_action(Route& route) override { pump(route); }
    void on_stop(Route&) override { monitor_.reset(); }

private:
    void observe_mission(Route& route, const Data& data) {
        if (!model_.mission_policy()) return;
        try {
            if (data.value<std::string>("source") != model_.config.node_name ||
                (!data.value<std::string>("source_node").empty() &&
                 data.value<std::string>("source_node") != model_.config.node_name)) {
                throw std::runtime_error("local mission source does not match configured platform");
            }
            const auto sent = data.value<int64_t>("sent_at_ns");
            const auto micros = data.value<int64_t>("timestamp");
            if (sent <= 0 && (micros <= 0 || micros > std::numeric_limits<int64_t>::max() / 1000)) {
                throw std::runtime_error("local mission update has invalid source timestamp");
            }
            const auto timestamp = sent > 0 ? sent : micros * 1000;
            const auto sequence = data.value<uint64_t>("audit_sequence");
            if (timestamp <= 0) throw std::runtime_error("local mission update has no source timestamp");
            if (timestamp < mission_timestamp_ ||
                (timestamp == mission_timestamp_ && sequence <= mission_sequence_)) {
                throw std::runtime_error("stale local mission update");
            }
            mission_timestamp_ = timestamp;
            mission_sequence_ = sequence;
            auto next = model_;
            next.update_receive_state(data.value<std::string>("mission_phase"));
            const bool changed = next.receive_state != model_.receive_state ||
                                 !model_.receive_filter_error.empty();
            if (changed) GatewayMonitor::apply_model(route, model_, std::move(next));
            if (changed && monitor_) monitor_->policy_changed();
            if (changed && monitor_) monitor_->policy_applied();
            if (changed) router::Log::info("rs.receive_policy.applied",
                {{"profile", model_.receive_profile()}, {"state", model_.receive_state}});
        } catch (const std::exception& error) {
            model_.receive_filter_error = diagnostic_message(error.what());
            if (monitor_) monitor_->policy_changed();
            router::Log::warn("rs.receive_policy.rejected", {{"error", error.what()}});
        }
    }

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
        if (monitor_) {
            try {
                monitor_->tick();
            } catch (const std::exception& error) {
                router::Log::error("rs.monitor.error", {{"error", error.what()}});
            }
        }
        // Policy observation commits a replacement model. Keep traversal keys
        // independent of its containers across that commit and the input loan.
        std::vector<std::string> source_topics;
        for (const auto& source : model_.sources) source_topics.push_back(source.first);
        for (const auto& topic : source_topics) {
            consume(route, model_.input_port(topic),
                    [&](const Data& data, const dds::sub::SampleInfo& info, bool valid) {
                if (!enabled(model_.sources.at(topic))) return;
                if (valid && topic == "PlatformMissionStatus") observe_mission(route, data);
                router::WanMetadata metadata;
                metadata.source_id = model_.config.node_name;
                if (valid) metadata = model_.metadata(topic, data);
                const auto frame = valid ? codec_.pack(topic, data, metadata)
                                         : codec_.key_frame(topic, data, metadata);
                auto writer = route.output<Data>(
                        "wan_out_" + std::string(model_.transport_mapping(topic).family))
                        .dds_data_writer();
                const int priority = model_.publication_priority(topic);
                record_write(topic, true, valid, [&] {
                    if (valid) {
                        rti::pub::WriteParams params;
                        params.priority(priority);
                        writer->write(frame, params);
                        return true;
                    }
                    return lifecycle(writer, frame, info, priority);
                });
            });
        }
        for (const auto& family : model_.families()) {
            consume(route, "wan_in_" + family,
                    [&](const Data& frame, const dds::sub::SampleInfo& info, bool valid) {
                if (frame.value<std::string>("source_id") == model_.config.node_name) return;
                auto decoded = valid ? codec_.unpack(frame) : codec_.unpack_key(frame);
                if (family != model_.transport_mapping(decoded.topic).family) {
                    throw std::runtime_error("wrong WAN family for " + decoded.topic);
                }
                if (monitor_) monitor_->received(family, frame, info);
                if (decoded.topic == "RouterHealth" || decoded.topic == "RouterConfig") return;
                const auto found = model_.destinations.find(decoded.topic);
                if (found == model_.destinations.end() || !enabled(found->second)) return;
                auto writer = route.output<Data>(model_.output_port(decoded.topic)).dds_data_writer();
                record_write(decoded.topic, false, valid, [&] {
                    if (valid) {
                        writer.write(decoded.data);
                        return true;
                    }
                    return lifecycle(writer, decoded.data, info);
                });
            });
        }
    }

    template<typename Write>
    void record_write(const std::string& topic, bool source, bool valid, Write write) {
        try {
            if (write() && monitor_) monitor_->forwarded(topic, source, valid);
        } catch (const dds::core::TimeoutError& error) {
            if (monitor_) monitor_->failed(topic, source, error.what(), true);
            throw;
        } catch (const std::exception& error) {
            if (monitor_) monitor_->failed(topic, source, error.what(), false);
            throw;
        }
    }

    GatewayModel model_;
    int64_t mission_timestamp_ = 0;
    uint64_t mission_sequence_ = 0;
    dds::core::QosProvider types_;
    router::WanCodec codec_;
    std::unique_ptr<GatewayMonitor> monitor_;
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
