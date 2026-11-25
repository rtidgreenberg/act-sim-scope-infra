#include "GatewayMonitor.hpp"
#include "core/Log.hpp"
#include "core/WanWriterObservations.hpp"

#include <rti/pub/WriteParams.hpp>

#include <algorithm>
#include <stdexcept>

namespace act_rs {
namespace {

int64_t unix_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();
}

bool active(const std::vector<Binding>& bindings) {
    return std::any_of(bindings.begin(), bindings.end(),
                       [](const Binding& binding) { return binding.enabled; });
}

} // namespace

GatewayMonitor::GatewayMonitor(Route& route, GatewayModel& model, router::WanCodec& codec,
                               dds::core::QosProvider types,
                               GatewayPorts* ports)
    : route_(route), ports_(ports), model_(model), codec_(codec) {
    auto writer = status_writer();
    auto reader = status_reader();
    auto mesh = route_.output<Data>("monitor_mesh").dds_data_writer();
    auto diagnostics = route_.output<Data>("monitor_diagnostics").dds_data_writer();
    presence_.reset(new router::PresenceMonitor(
            waitset_, writer.publisher().participant(), mesh.publisher().participant(),
            model_.config.node_name, model_.config.router_name, reader.qos(), writer.qos(),
            mesh.qos(), diagnostics.qos(), dds::core::null, writer.topic().name(),
            "ActRouterMeshStatus", types, this, mesh, diagnostics, &codec_));
    links_.reset(new router::LinkStatsCollector(
            route_.output<Data>("monitor_links").dds_data_writer(),
            model_.config.node_name + "/" + model_.config.router_name, model_.wan.name,
            model_.config.link_stats_period_ms));
    links_->register_source(this);
    links_->register_source(presence_.get());
    policy_applied();
}

GatewayMonitor::~GatewayMonitor() {
    links_->shutdown();
    presence_->shutdown();
}

void GatewayMonitor::bind_status(std::shared_ptr<router::WanStatusCallbacks> callbacks) {
    status_callbacks_ = std::move(callbacks);
}

void GatewayMonitor::bind_config(std::shared_ptr<router::WanConfigCallbacks> callbacks) {
    config_callbacks_ = std::move(callbacks);
}

dds::pub::DataWriter<GatewayMonitor::Data> GatewayMonitor::status_writer() const {
    return route_.output<Data>("wan_out_" + model_.health_family())
            .dds_data_writer();
}

dds::sub::DataReader<GatewayMonitor::Data> GatewayMonitor::status_reader() const {
    return route_.input<Data>("wan_in_" + model_.health_family())
            .dds_data_reader();
}

void GatewayMonitor::write_status(const Data& frame) {
    auto writer = status_writer();
    rti::pub::WriteParams params;
    params.priority(model_.publication_priority("RouterHealth"));
    writer->write(frame, params);
}

void GatewayMonitor::write_config(const Data& frame) {
    auto writer = route_.output<Data>("wan_out_" + model_.config_family()).dds_data_writer();
    rti::pub::WriteParams params;
    params.priority(model_.publication_priority("RouterConfig"));
    writer->write(frame, params);
}

std::vector<RouterWriterFilterObservation> GatewayMonitor::writer_filter_observations() const {
    std::vector<RouterWriterFilterObservation> observations;
    for (const auto& family : model_.families()) {
        observations.push_back(router::observe_wan_writer(
                route_.output<Data>("wan_out_" + family).dds_data_writer()));
    }
    return observations;
}

void GatewayMonitor::collect_wan_stats(router::LinkStatsSink& sink) {
    for (const auto& family : model_.families()) {
        auto writer = route_.output<Data>("wan_out_" + family).dds_data_writer();
        auto reader = route_.input<Data>("wan_in_" + family).dds_data_reader();
        router::poll_writer_wan_stats(writer, writer_previous_[family], sink, "gateway", family);
        router::poll_reader_wan_stats(reader, reader_previous_[family], sink, "gateway", family);
    }
}

std::string GatewayMonitor::key(const std::string& topic, bool source) {
    return (source ? "tx:" : "rx:") + topic;
}

void GatewayMonitor::forwarded(const std::string& topic, bool source, bool valid) {
    auto& count = counters_[key(topic, source)];
    if (valid) ++count.samples;
    else ++count.lifecycle;
}

void GatewayMonitor::failed(const std::string& topic, bool source,
                            const std::string& error, bool timeout) {
    auto& count = counters_[key(topic, source)];
    if (!timeout) count.error = diagnostic_message(error);
    if (timeout) ++count.timeouts;
}

RouterStatus GatewayMonitor::snapshot() const {
    RouterStatus result;
    result.target_node = model_.config.node_name;
    result.target_router = model_.config.router_name;
    result.status_id = std::to_string(revision_);
    result.state_revision = revision_;
    result.caused_by_command_id = command_id_;
    for (const auto& participant : model_.config.participants) {
        RouterParticipantStatus status;
        status.name = participant.name;
        status.domain = participant.domain;
        status.qos_profile_alias = participant.qos_profile_alias;
        const auto& state = participant.name == model_.wan.name ? model_.wan : participant;
        for (const auto& name : state.participant_partition) status.participant_partition.push_back(name);
        for (const auto& name : state.team_partition) status.team_partition.push_back(name);
        result.participants.push_back(status);
    }
    for (const auto& spec : model_.config.routes) {
        RouterRouteStatus route;
        route.route_name = spec.route_name;
        route.desired = spec;
        route.state_revision = revision_;
        route.caused_by_command_id = command_id_;
        route.discovery_state = RouterRouteDiscoveryState::DISCOVERY_READY;
        route.state = RouterRouteOperationalState::ROUTE_ENABLED;
        bool any_enabled = false;
        const bool source = spec.output.participant == model_.wan.name;
        const auto& bindings = source ? model_.sources : model_.destinations;
        for (const auto& topic : spec.topics) {
            const auto found = bindings.find(topic.name);
            if (found == bindings.end()) continue;
            const bool enabled = active(found->second);
            any_enabled = any_enabled || enabled;
            RouterRouteTopicStatus status;
            status.name = topic.name;
            status.wan_family = model_.transport_mapping(topic.name).family;
            status.publication_priority = model_.publication_priority(topic.name);
            status.type_resolved = true;
            status.discovery_state = RouterRouteDiscoveryState::DISCOVERY_READY;
            status.topic_state = enabled ? RouterRouteTopicState::TOPIC_FORWARDING
                                         : RouterRouteTopicState::TOPIC_IDLE;
            const auto count = counters_.find(key(topic.name, source));
            if (count != counters_.end()) {
                status.samples_forwarded = count->second.samples;
                status.lifecycle_events_forwarded = count->second.lifecycle;
                status.output_write_timeouts = count->second.timeouts;
                status.last_error = count->second.error;
            }
            const auto input_name = source ? model_.input_port(topic.name) : "wan_in_" + status.wan_family;
            const auto output_name = source ? "wan_out_" + status.wan_family : model_.output_port(topic.name);
            const bool internal_diagnostics = source && ports_ &&
                    model_.input_port(topic.name) == "monitor_diagnostics";
            const bool registered = internal_diagnostics || !ports_ || (source ? ports_->inputs.count(topic.name)
                                                       : ports_->outputs.count(topic.name));
            if (!ports_ || !source)
                status.input_matched = route_.input<Data>(input_name).dds_data_reader()
                        .subscription_matched_status().current_count();
            else if (registered && !internal_diagnostics)
                status.input_matched = ports_->inputs.at(topic.name).subscription_matched_status().current_count();
            if (!ports_ || source)
                status.output_matched = route_.output<Data>(output_name).dds_data_writer()
                        .publication_matched_status().current_count();
            else if (registered)
                status.output_matched = ports_->outputs.at(topic.name).publication_matched_status().current_count();
            status.reader_qos_summary = source ? found->second.front().qos : status.wan_family;
            status.writer_qos_summary = source ? status.wan_family : found->second.front().qos;
            status.match_reason = source ? "WAN matches are shared-family connectivity"
                                         : "Native LAN output";
            if (internal_diagnostics) status.match_reason = "Internal diagnostics producer; shared WAN output";
            const bool available = registered && (internal_diagnostics || !ports_ ||
                    (source ? status.input_matched > 0 : status.output_matched > 0));
            if (!available) {
                status.topic_state = RouterRouteTopicState::TOPIC_IDLE;
                status.type_resolved = false;
                status.discovery_state = RouterRouteDiscoveryState::DISCOVERY_PARTIAL;
                status.match_reason = "Waiting for native LAN domain match; WAN history retained";
                route.discovery_state = status.discovery_state;
                route.state = RouterRouteOperationalState::ROUTE_DEGRADED;
            }
            if (!status.last_error.empty()) {
                status.topic_state = RouterRouteTopicState::TOPIC_ERROR;
                route.state = RouterRouteOperationalState::ROUTE_ERROR;
                route.last_error = status.last_error;
            }
            route.samples_forwarded += status.samples_forwarded;
            route.lifecycle_events_forwarded += status.lifecycle_events_forwarded;
            route.topic_status.push_back(status);
        }
        route.desired.desired_enabled = any_enabled;
        if (!any_enabled) route.state = RouterRouteOperationalState::ROUTE_DISABLED;
        result.routes.push_back(route);
    }
    return result;
}

void GatewayMonitor::publish_diagnostics(const RouterDiagnostics& diagnostics) {
    presence_->publish_diagnostics(diagnostics);
    const std::string topic = "ActRouterDiagnostics";
    const auto source = model_.sources.find(topic);
    if (!ports_ || source == model_.sources.end() || !active(source->second) ||
        model_.input_port(topic) != "monitor_diagnostics") return;
    try {
        const auto data = rti::core::xtypes::convert(diagnostics);
        const auto frame = codec_.pack(topic, data, model_.metadata(topic, data));
        auto writer = route_.output<Data>(
                "wan_out_" + std::string(model_.transport_mapping(topic).family)).dds_data_writer();
        rti::pub::WriteParams params;
        params.priority(model_.publication_priority(topic));
        writer->write(frame, params);
        forwarded(topic, true, true);
    } catch (const dds::core::TimeoutError& error) {
        failed(topic, true, error.what(), true);
        router::Log::error("rs.diagnostics.error",
                          {{"topic", topic}, {"error", error.what()}, {"timeout", "true"}});
    } catch (const std::exception& error) {
        failed(topic, true, error.what(), false);
        router::Log::error("rs.diagnostics.error",
                          {{"topic", topic}, {"error", error.what()}, {"timeout", "false"}});
    }
}

void GatewayMonitor::update_teams(const std::vector<std::string>& teams) {
    auto next = model_;
    next.update_teams(teams);
    apply_model(route_, model_, std::move(next));
    policy_changed();
    policy_applied();
}

void GatewayMonitor::apply_model(Route& route, GatewayModel& current, GatewayModel next) {
    current.apply_filters(std::move(next), [&](const std::string& family,
                                             const router::WanReceiveFilter& filter) {
        auto description = route.input<Data>("wan_in_" + family)
                .dds_data_reader().topic_description();
        auto cft = dds::core::polymorphic_cast<dds::topic::ContentFilteredTopic<Data>>(description);
        cft->filter(dds::topic::Filter(filter.expression, filter.parameters));
    });
}

void GatewayMonitor::policy_changed() {
    ++revision_;
    last_status_ = {};
    last_heartbeat_ = {};
}

void GatewayMonitor::policy_applied() {
    if (!model_.mission_policy()) return;
    auto teams = model_.wan.team_partition;
    teams.erase(std::remove(teams.begin(), teams.end(), model_.config.node_name), teams.end());
    if (policy_audited_ && audited_profile_ == model_.receive_profile() && audited_teams_ == teams) return;
    presence_->log_receive_policy_audit(model_.receive_profile(), teams);
    policy_audited_ = true;
    audited_profile_ = model_.receive_profile();
    audited_teams_ = std::move(teams);
}

void GatewayMonitor::command(const RouterCommand& command) {
    if ((!command.target_node.empty() && command.target_node != model_.config.node_name)
        || (!command.target_router.empty() && command.target_router != model_.config.router_name)) return;
    RouterCommandAck ack;
    ack.target_node = model_.config.node_name;
    ack.target_router = model_.config.router_name;
    ack.command_id = command.command_id;
    ack.route_name = command.route_name;
    try {
        if (command.participant_name != model_.wan.name || !model_.wan.team_scoped) {
            throw std::runtime_error("only local WAN team membership commands are supported");
        }
        auto teams = model_.wan.team_partition;
        auto found = std::find(teams.begin(), teams.end(), command.partition_name);
        if (command.kind == RouterCommandKind::ADD_PARTICIPANT_PARTITION) {
            if (found == teams.end()) teams.push_back(command.partition_name);
        } else if (command.kind == RouterCommandKind::REMOVE_PARTICIPANT_PARTITION) {
            if (command.partition_name == model_.config.node_name || found == teams.end()) {
                throw std::runtime_error("cannot remove protected or absent membership");
            }
            teams.erase(found);
        } else {
            throw std::runtime_error("static processor routes do not support route-management commands");
        }
        update_teams(teams);
        command_id_ = command.command_id;
        last_status_ = {};
        last_heartbeat_ = {};
        ack.accepted = true;
        ack.message = "team membership updated";
    } catch (const std::exception& error) {
        ack.message = diagnostic_message(error.what());
        model_.receive_filter_error = ack.message;
        policy_changed();
        router::Log::warn("rs.command.rejected",
                         {{"command_id", command.command_id}, {"error", error.what()}});
    }
    route_.output<Data>("monitor_ack").dds_data_writer().write(rti::core::xtypes::convert(ack));
}

void GatewayMonitor::received(const std::string& family, const Data& frame,
                              const dds::sub::SampleInfo& info) {
    if (frame.value<std::string>("source_id") == model_.config.node_name) return;
    if ((family == model_.health_family() || family == "Periodic" || family == "Status") && status_callbacks_) {
        std::lock_guard<std::mutex> lock(status_callbacks_->mutex);
        if (info.valid() && status_callbacks_->data) status_callbacks_->data(frame, info);
        if (!info.valid() && frame.value<std::string>("original_topic") == "RouterHealth"
                && status_callbacks_->health_missing) {
            status_callbacks_->health_missing(frame);
        }
    } else if (family == model_.config_family() && frame.value<std::string>("original_topic") == "RouterConfig"
               && config_callbacks_) {
        std::lock_guard<std::mutex> lock(config_callbacks_->mutex);
        if (config_callbacks_->data) config_callbacks_->data(frame, info);
    }
}

void GatewayMonitor::check_deadline() {
    auto reader = status_reader();
    const auto status = reader.requested_deadline_missed_status();
    if (status.total_count() == deadline_total_) return;
    deadline_total_ = status.total_count();
    Data key = route_.input<Data>("wan_in_" + model_.health_family())
            .create_data();
    reader.key_value(key, status.last_instance_handle());
    if (key.value<std::string>("original_topic") != "RouterHealth" || !status_callbacks_) return;
    std::lock_guard<std::mutex> lock(status_callbacks_->mutex);
    if (status_callbacks_->health_missing) status_callbacks_->health_missing(key);
}

void GatewayMonitor::tick() {
    const auto commands = route_.input<Data>("monitor_command").select().max_samples(32).take();
    for (const auto& sample : commands) {
        if (sample.info().valid()) command(rti::core::xtypes::convert<RouterCommand>(sample.data()));
    }
    check_deadline();
    const auto now = std::chrono::steady_clock::now();
    if (now - last_status_ >= std::chrono::milliseconds(router::kMeshPublishPeriodMs)) {
        const auto state = snapshot();
        route_.output<Data>("monitor_status").dds_data_writer().write(rti::core::xtypes::convert(state));
        presence_->publish_mesh_tick();
        last_status_ = now;
    }
    if (now - last_heartbeat_ >= std::chrono::milliseconds(router::kHeartbeatPeriodMs)) {
        const auto state = snapshot();
        RouterHealth health;
        health.router = model_.config.node_name + "/" + model_.config.router_name;
        health.role = model_.config.node_role;
        health.router_backend = "routing_service";
        health.reporting_policy = "always_on";
        health.receive_profile = model_.receive_profile();
        health.receive_state = model_.mission_policy() ? model_.receive_state : "";
        health.receive_filter_error = diagnostic_message(model_.receive_filter_error);
        health.config_hash = model_.config.config_hash;
        health.state_revision = revision_;
        health.heartbeat_seq = ++heartbeat_;
        health.send_timestamp = unix_ns();
        health.n_routes = state.routes.size();
        for (const auto& team : model_.wan.team_partition) health.team_partition.push_back(team);
        for (const auto& status : state.routes) {
            RouterCompactRouteHealth route;
            route.route_name = status.route_name;
            route.state = status.state;
            route.discovery_state = status.discovery_state;
            route.samples_forwarded = status.samples_forwarded;
            health.routes.push_back(route);
            if (status.state == RouterRouteOperationalState::ROUTE_ERROR) ++health.n_error;
            if (status.state == RouterRouteOperationalState::ROUTE_DEGRADED) ++health.n_degraded;
        }
        health.overall_state = health.n_error ? RouterOverallState::ROUTER_ERROR
                : health.n_degraded ? RouterOverallState::ROUTER_DEGRADED
                                    : RouterOverallState::ROUTER_OK;
        presence_->publish_heartbeat(health);
        RouterDiagnostics diagnostics;
        diagnostics.router = health.router;
        diagnostics.config_hash = health.config_hash;
        diagnostics.snapshot_seq = ++diagnostics_;
        diagnostics.send_timestamp = health.send_timestamp;
        diagnostics.state_revision = revision_;
        diagnostics.routes = state.routes;
        publish_diagnostics(diagnostics);
        last_heartbeat_ = now;
    }
    if (now - last_links_ >= std::chrono::milliseconds(model_.config.link_stats_period_ms)) {
        links_->on_link_stats_tick();
        last_links_ = now;
    }
}

} // namespace act_rs
