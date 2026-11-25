#include "GatewayModel.hpp"
#include "GatewayMonitor.hpp"
#include "core/Log.hpp"
#include "core/WanStatsPoll.hpp"

#include <rti/routing/processor/ProcessorPlugin.hpp>
#include <rti/pub/WriteParams.hpp>

#include <algorithm>
#include <chrono>
#include <map>
#include <set>
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
        : model_(properties), types_(model_.config.types_xml_path),
          codec_(types_, model_.codec_mappings(), model_.six_topic()),
          independent_(properties.count("act.runtime_mode") != 0) {}

    GatewayPorts ports;

    void validate_registration(const std::string& role, const std::string& topic) const {
        if (role == "hub" && topic.empty()) return;
        if ((role == "tx" && model_.sources.count(topic)) ||
            (role == "rx" && model_.destinations.count(topic))) return;
        throw std::runtime_error("undeclared or invalid gateway route role/topic: " + role + ":" + topic);
    }

    void attach(Route& route, const std::string& role, const std::string& topic) {
        if (role == "tx") {
            if (!model_.sources.count(topic)) throw std::runtime_error("undeclared LAN input: " + topic);
            auto input = route.input<Data>(model_.input_port(topic));
            auto reader = input.dds_data_reader();
            validate_lan_participant(reader.subscriber().participant(), model_, input.name());
            if (input.create_data().type() != types_->type(codec_.mapping(topic).type))
                throw std::runtime_error("LAN input type mismatch: " + topic);
            if (!ports.inputs.emplace(topic, reader).second)
                throw std::runtime_error("duplicate active LAN input: " + topic);
        } else {
            if (!model_.destinations.count(topic)) throw std::runtime_error("undeclared LAN output: " + topic);
            auto output = route.output<Data>(model_.output_port(topic));
            auto writer = output.dds_data_writer();
            validate_lan_participant(writer.publisher().participant(), model_, output.name());
            if (output.create_data().type() != types_->type(codec_.mapping(topic).type))
                throw std::runtime_error("LAN output type mismatch: " + topic);
            auto participant = writer.publisher().participant();
            dds::pub::ignore(participant, writer.instance_handle());
            if (!ports.outputs.emplace(topic, writer).second)
                throw std::runtime_error("duplicate active LAN output: " + topic);
        }
        if (monitor_) monitor_->policy_changed();
        router::Log::info("rs.gateway.lan_active", {{"direction", role}, {"topic", topic}});
    }

    void detach(const std::string& role, const std::string& topic) {
        if (role == "tx") ports.inputs.erase(topic);
        else ports.outputs.erase(topic);
        if (monitor_) monitor_->policy_changed();
    }

    void on_start(Route& route) override {
        for (const auto& family : model_.families()) {
            auto writer = route.output<Data>("wan_out_" + family).dds_data_writer();
            auto reader = route.input<Data>("wan_in_" + family).dds_data_reader();
            auto participant = writer.publisher().participant();
            dds::pub::ignore(participant, writer.instance_handle());
            dds::sub::ignore(participant, reader.instance_handle());
        }
        if (!independent_) for (const auto& destination : model_.destinations) {
            auto output = route.output<Data>(model_.output_port(destination.first));
            auto writer = output.dds_data_writer();
            validate_lan_participant(writer.publisher().participant(), model_, output.name());
            if (output.create_data().type() != types_->type(codec_.mapping(destination.first).type)) {
                throw std::runtime_error("LAN output type mismatch: " + destination.first);
            }
            auto participant = writer.publisher().participant();
            dds::pub::ignore(participant, writer.instance_handle());
        }
        if (!independent_) for (const auto& source : model_.sources) {
            auto input = route.input<Data>(model_.input_port(source.first));
            validate_lan_participant(input.dds_data_reader().subscriber().participant(), model_, input.name());
            if (input.create_data().type() != types_->type(codec_.mapping(source.first).type)) {
                throw std::runtime_error("LAN input type mismatch: " + source.first);
            }
        }
        if (!model_.config.presence_participant.empty()) {
            monitor_.reset(new GatewayMonitor(route, model_, codec_, types_, independent_ ? &ports : nullptr));
        }
        router::Log::info("rs.gateway.started", {{"node", model_.config.node_name},
                          {"mode", monitor_ ? "mesh" : "data_plane_only"}});
    }

    void on_data_available(Route& route) override { pump(route); }
    void on_periodic_action(Route& route) override { pump(route); }
    void stop_hub() { monitor_.reset(); }
    void on_stop(Route&) override { stop_hub(); }

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

    template<typename Samples, typename Forward>
    void process_native(Reader reader, const std::string& topic, const Samples& samples, Forward forward) {
        std::map<std::string, dds::sub::SampleInfo> instances;
        for (const auto& sample : samples) {
            try {
                instances[router::wan_handle_key(sample.info().instance_handle())] = sample.info();
                if (sample.info().valid()) forward(sample.data(), sample.info(), true);
            } catch (const std::exception& error) {
                router::Log::error("rs.sample.error", {{"topic", topic}, {"error", error.what()}});
            }
        }
        for (const auto& instance : instances) {
            const auto& info = instance.second;
            if (info.state().instance_state() == dds::sub::status::InstanceState::alive()) continue;
            try {
                Data key(types_->type(reader.topic_description().type_name()));
                reader.key_value(key, info.instance_handle());
                forward(key, info, false);
            } catch (const std::exception& error) {
                router::Log::error("rs.lifecycle.error", {{"topic", topic}, {"error", error.what()}});
            }
        }
    }

    router::WanDecoded decode(const std::string& family, const Data& frame, bool valid) {
        auto decoded = valid ? codec_.unpack(frame) : codec_.unpack_key(frame);
        if (family != model_.transport_mapping(decoded.topic).family) {
            throw std::runtime_error("wrong WAN family for " + decoded.topic);
        }
        return decoded;
    }

    template<typename Forward>
    void consume_native(Reader reader, const std::string& topic, Forward forward,
                        bool observed_only = false) {
        if (topic.empty()) {
            const auto samples = reader.select().max_samples(32).take();
            process_native(reader, topic, samples, forward);
        } else {
            dds::sub::Query query(reader, "original_topic = %0", {"'" + topic + "'"});
            const auto state = observed_only
                    ? dds::sub::status::DataState(dds::sub::status::SampleState::read())
                    : dds::sub::status::DataState::any();
            dds::core::InstanceHandle previous = dds::core::null;
            int remaining = 32;
            while (remaining > 0) {
                const auto matching = reader.select().state(state).content(query).next_instance(previous)
                        .max_samples(1).read();
                auto handle = dds::core::InstanceHandle(dds::core::null);
                for (const auto& sample : matching) {
                    handle = sample.info().instance_handle();
                    break;
                }
                if (handle == dds::core::null) break;
                previous = handle;
                Data key(types_->type(reader.topic_description().type_name()));
                reader.key_value(key, handle);
                // Native Query selection also admits unrelated invalid-data
                // notifications. Check their authoritative key before taking.
                if (key.value<std::string>("original_topic") != topic) continue;
                const auto samples = reader.select().state(state).instance(handle)
                        .max_samples(remaining).take();
                remaining -= static_cast<int>(samples.length());
                process_native(reader, topic, samples, forward);
            }
        }
    }

    void observe_admission(Reader reader, const std::string& family) {
        const auto rejected = reader.sample_rejected_status().total_count();
        const auto lost_status = reader.sample_lost_status();
        const auto lost = lost_status.total_count();
        auto& previous = admission_[family];
        if (rejected == previous.first && lost == previous.second) return;
        const bool rejected_changed = rejected != previous.first;
        previous = {rejected, lost};
        const bool best_effort = reader.qos().policy<dds::core::policy::Reliability>().kind() ==
                dds::core::policy::ReliabilityKind::BEST_EFFORT;
        const bool latest_state = model_.is_state_family(family);
        if (!rejected_changed && (best_effort || latest_state) &&
            lost_status->last_reason() == rti::core::status::SampleLostState::lost_by_writer()) {
            const auto now = std::chrono::steady_clock::now();
            auto& reported = replaceable_loss_reported_[family];
            if (now - reported >= std::chrono::seconds(1)) {
                router::Log::warn(best_effort ? "rs.gateway.best_effort_loss" : "rs.gateway.latest_state_loss",
                                 {{"family", family}, {"lost", std::to_string(lost)}});
                reported = now;
            }
            return;
        }
        const auto message = "native WAN history loss/rejection: rejected=" +
                std::to_string(rejected) + " lost=" + std::to_string(lost) +
                " last_loss_reason=" + std::to_string(lost_status->last_reason().to_ulong());
        router::Log::error("rs.gateway.history_error", {{"family", family}, {"error", message}});
        if (monitor_) for (const auto& destination : model_.destinations) {
            if (family == model_.transport_mapping(destination.first).family)
                monitor_->failed(destination.first, false, message, false);
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
            if (independent_ && !ports.inputs.count(topic)) continue;
            const auto forward = [&](const Data& data, const dds::sub::SampleInfo& info, bool valid) {
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
            };
            if (independent_) consume_native(ports.inputs.at(topic), "", forward);
            else consume(route, model_.input_port(topic), forward);
        }
        for (const auto& family : model_.families()) {
            const auto forward = [&](const Data& frame, const dds::sub::SampleInfo& info, bool valid) {
                if (frame.value<std::string>("source_id") == model_.config.node_name) return;
                auto decoded = decode(family, frame, valid);
                if (monitor_ && (!independent_ || model_.is_event_family(family) || model_.is_state_family(family) || !valid))
                    monitor_->received(family, frame, info);
                if (decoded.topic == "RouterHealth" || decoded.topic == "RouterConfig") return;
                const auto found = model_.destinations.find(decoded.topic);
                if (found == model_.destinations.end() || !enabled(found->second)) return;
                auto writer = independent_ ? ports.outputs.at(decoded.topic) :
                        route.output<Data>(model_.output_port(decoded.topic)).dds_data_writer();
                record_write(decoded.topic, false, valid, [&] {
                    if (valid) {
                        writer.write(decoded.data);
                        return true;
                    }
                    return lifecycle(writer, decoded.data, info);
                });
            };
            if (!independent_) consume(route, "wan_in_" + family, forward);
            else {
                auto reader = route.input<Data>("wan_in_" + family).dds_data_reader();
                observe_admission(reader, family);
                const bool observe_status = monitor_ && family == model_.health_family();
                if (observe_status) {
                    // Observe any received status for presence even if its LAN
                    // destination is pending. Only this pass marks Status READ;
                    // forwarding selects READ samples, including lifecycle data.
                    const auto samples = reader.select().state(dds::sub::status::SampleState::not_read())
                            .max_samples(32).read();
                    for (const auto& sample : samples) {
                        try {
                            if (sample.info().valid() &&
                                sample.data().value<std::string>("source_id") != model_.config.node_name) {
                                decode(family, sample.data(), true);
                                monitor_->received(family, sample.data(), sample.info());
                            }
                        } catch (const std::exception& error) {
                            router::Log::error("rs.sample.error", {{"input", "wan_in_" + family},
                                                                 {"error", error.what()}});
                        }
                    }
                }
                for (const auto& destination : model_.destinations) {
                    const auto output = ports.outputs.find(destination.first);
                    if (output != ports.outputs.end() &&
                        output->second.publication_matched_status().current_count() > 0 &&
                        family == model_.transport_mapping(destination.first).family)
                        consume_native(reader, destination.first, forward, observe_status);
                }
                if (family == model_.config_family()) consume_native(reader, "RouterConfig", forward);
                if (family == model_.health_family())
                    consume_native(reader, "RouterHealth", forward, observe_status);
            }
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
    bool independent_;
    std::map<std::string, std::pair<int, int>> admission_;
    std::map<std::string, std::chrono::steady_clock::time_point> replaceable_loss_reported_;
};

class IndependentProcessor : public rti::routing::processor::NoOpProcessor {
public:
    IndependentProcessor(std::shared_ptr<GatewayProcessor> owner, const Properties& properties)
        : owner_(std::move(owner)), role_(properties.at("act.route_role")),
          topic_(role_ == "hub" ? "" : properties.at("act.route_topic")) {}
    ~IndependentProcessor() override {
        std::lock_guard<std::recursive_mutex> lock(owner_->ports.mutex);
        if (role_ == "hub") owner_->stop_hub();
        else if (active_) owner_->detach(role_, topic_);
    }
    std::string registration() const { return role_ + ":" + topic_; }
    void on_start(Route& route) override {
        std::lock_guard<std::recursive_mutex> lock(owner_->ports.mutex);
        if (role_ == "hub") owner_->on_start(route);
        else owner_->attach(route, role_, topic_);
        active_ = true;
    }
    void on_stop(Route& route) override {
        std::lock_guard<std::recursive_mutex> lock(owner_->ports.mutex);
        if (!active_) return;
        if (role_ == "hub") owner_->on_stop(route);
        else owner_->detach(role_, topic_);
        active_ = false;
    }
    void on_data_available(Route& route) override { pump(route); }
    void on_periodic_action(Route& route) override { pump(route); }
private:
    void pump(Route& route) {
        std::lock_guard<std::recursive_mutex> lock(owner_->ports.mutex);
        if (active_ && role_ == "hub") owner_->on_periodic_action(route);
    }
    std::shared_ptr<GatewayProcessor> owner_;
    std::string role_, topic_;
    bool active_ = false;
};

} // namespace
} // namespace act_rs

class ActGatewayPlugin : public rti::routing::processor::ProcessorPlugin {
public:
    explicit ActGatewayPlugin(const rti::routing::PropertySet&) {}
    rti::routing::processor::Processor* create_processor(
            rti::routing::processor::Route&, const rti::routing::PropertySet& properties) override {
        if (!properties.count("act.runtime_mode")) return new act_rs::GatewayProcessor(properties);
        auto common = properties;
        common.erase("act.route_role");
        common.erase("act.route_topic");
        std::lock_guard<std::mutex> lock(mutex_);
        if (!owner_) {
            owner_.reset(new act_rs::GatewayProcessor(properties));
            common_ = common;
        } else if (common != common_) {
            throw std::runtime_error("independent routes must declare the identical gateway model");
        }
        const auto role = properties.at("act.route_role");
        const auto topic = role == "hub" ? "" : properties.at("act.route_topic");
        owner_->validate_registration(role, topic);
        if (!registrations_.insert(role + ":" + topic).second)
            throw std::runtime_error("duplicate gateway route role/topic");
        return new act_rs::IndependentProcessor(owner_, properties);
    }
    void delete_processor(rti::routing::processor::Route&,
                          rti::routing::processor::Processor* processor) override {
        std::lock_guard<std::mutex> lock(mutex_);
        auto independent = dynamic_cast<act_rs::IndependentProcessor*>(processor);
        if (independent) registrations_.erase(independent->registration());
        delete processor;
        if (registrations_.empty()) {
            owner_.reset();
            common_.clear();
        }
    }
private:
    std::mutex mutex_;
    std::shared_ptr<act_rs::GatewayProcessor> owner_;
    rti::routing::PropertySet common_;
    std::set<std::string> registrations_;
};

using rti::routing::PropertySet;
RTI_PROCESSOR_PLUGIN_CREATE_FUNCTION_DECL(ActGatewayPlugin)
RTI_PROCESSOR_PLUGIN_CREATE_FUNCTION_DEF(ActGatewayPlugin)
