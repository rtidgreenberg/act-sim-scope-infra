#include "WanControllerFactory.hpp"

#include "Log.hpp"
#include "TypeResolver.hpp"

namespace router {

WanControllerFactory::WanControllerFactory(ParticipantRegistry& registry, TypeResolver& types,
        QosResolver& qos, AsyncWaitSetDispatcher& dispatcher, dds::core::QosProvider model,
        const std::string& wan,
        const RouterIdentityInfo& identity)
    : registry_(registry), types_(types), qos_(qos), model_(model),
      identity_(identity), wan_(wan), legacy_(registry, types, qos, dispatcher) {
    WanAdapterCallbacks callbacks;
    callbacks.error = [this](const std::string& route, const std::string& topic,
                            uint64_t generation, const std::string& message) {
        // Physical failures are already logged; one bad shared input must not poison
        // unrelated logical routes. Logical sample/build failures keep their stamp.
        if (controller_ && route.compare(0, 5, "@wan/") != 0) {
            controller_->post(ControllerEvent::route_entity_error(route, topic, generation, message));
        }
    };
    callbacks.warning = [](const std::string&, const std::string&, uint64_t, const std::string&) {};
    adapters_.reset(new WanAdapterFactory(registry, qos, dispatcher, model, wan,
                                          identity.node_name, identity.router_name, callbacks));
}

void WanControllerFactory::set_controller(RouterController* controller) {
    controller_ = controller;
    legacy_.set_controller(controller);
}

bool WanControllerFactory::on_wan(const RouterRouteSpec& route) const {
    return registry_.on_wan(route.input.participant) || registry_.on_wan(route.output.participant);
}

bool WanControllerFactory::validate_route_update(const RouterRouteSpec& route,
                                                std::string& error) const {
    if (!on_wan(route)) {
        return true;
    }
    try {
        for (const auto& topic : route.topics) {
            interest(route, topic.name);
        }
        return true;
    } catch (const std::exception& exception) {
        error = exception.what();
        Log::warn("wan_route_update_rejected", {{"route", route.route_name}, {"error", error}});
        return false;
    }
}

WanInterest WanControllerFactory::interest(const RouterRouteSpec& route,
                                          const std::string& topic) const {
    const bool source = registry_.on_wan(route.output.participant);
    const auto& lan = source ? route.input : route.output;
    if (!lan.subscriber_partition.empty() || !lan.publisher_partition.empty()) {
        throw std::runtime_error("two-topic LAN partition updates are unsupported");
    }
    return wan_interest(route, controller_->state().participants.at(wan_),
                        identity_.node_name, source, topic);
}

WanAdapterFactory::MetadataProvider WanControllerFactory::provider(
        const WanInterest& selected, uint64_t revision) const {
    const auto node = identity_.node_name;
    const auto router = identity_.router_name;
    const auto role = identity_.node_role;
    return [selected, revision, node, router, role](const dds::core::xtypes::DynamicData& data) {
        return wan_source_metadata(selected, data, node, router, role, revision);
    };
}

void WanControllerFactory::refresh_interests() {
    std::map<Key, WanInterest> selected;
    std::map<std::string, std::vector<WanInterest>> families;
    std::vector<WanInterest> sources;
    if (adapters_->status_bound()) {
        families["Status"];
    }
    for (const auto& entry : live_) {
        const auto& route = controller_->state().routes.at(entry.first.first).desired;
        auto current = interest(route, entry.first.second);
        selected.emplace(entry.first, current);
        if (entry.second.source) {
            sources.push_back(current);
        } else {
            families[wan_mapping(entry.first.second).family].push_back(current);
        }
        validate_wan_source_interests(sources);
    }
    std::map<std::string, WanReceiveFilter> filters;
    for (const auto& family : families) {
        filters.emplace(family.first,
                        wan_receive_filter(family.first, identity_.node_name, family.second));
    }
    std::vector<WanSourceMetadataUpdate> metadata;
    const auto revision = membership_revision_ + 1;
    for (const auto& entry : live_) {
        if (entry.second.source) {
            metadata.push_back({entry.first.first, entry.first.second, entry.second.generation,
                                provider(selected.at(entry.first), revision)});
        }
    }
    std::vector<std::pair<std::string, WanReceiveFilter>> changed;
    try {
        for (const auto& family : filters) {
            const auto& interests = families.at(family.first);
            const std::string topic = interests.empty() ? "RouterHealth" : interests.front().topic;
            auto previous = adapters_->receive_filter(topic);
            if (previous.expression == family.second.expression &&
                previous.parameters == family.second.parameters) {
                continue;
            }
            changed.emplace_back(topic, std::move(previous));
            adapters_->update_receive_filter(topic, family.second);
        }
        adapters_->update_source_metadata(std::move(metadata));
    } catch (const std::exception&) {
        for (auto it = changed.rbegin(); it != changed.rend(); ++it) {
            try {
                adapters_->update_receive_filter(it->first, it->second);
            } catch (const std::exception& exception) {
                Log::error("wan_filter_rollback_failed", {{"topic", it->first},
                           {"error", exception.what()}});
                // Stop rather than keep forwarding under an unreported partial policy.
                adapters_->shutdown();
                for (const auto& entry : live_) {
                    report_error(entry.first, entry.second.generation, "shared filter rollback failed");
                }
                throw;
            }
        }
        throw;
    }
    ++membership_revision_;
    for (auto& entry : live_) {
        entry.second.interest = std::move(selected.at(entry.first));
        std::string teams;
        for (const auto& destination : entry.second.interest.destination_ids) {
            teams += (teams.empty() ? "" : ",") + destination;
        }
        Log::info("wan_interest_applied", {{"node", identity_.node_name},
                  {"route", entry.first.first}, {"topic", entry.first.second},
                  {"source", entry.second.source ? "true" : "false"},
                  {"control", entry.second.interest.control ? "true" : "false"},
                  {"global", entry.second.interest.global ? "true" : "false"},
                  {"destination_ids", teams}, {"revision", std::to_string(membership_revision_)}});
    }
}

void WanControllerFactory::report_error(const Key& key, uint64_t generation,
                                        const std::string& message) {
    Log::error("wan_route_error", {{"route", key.first}, {"topic", key.second}, {"error", message}});
    controller_->post(ControllerEvent::route_entity_error(key.first, key.second, generation, message));
}

void WanControllerFactory::create_topic_entities(const RouteView& view,
                                               const std::string& topic, uint64_t generation) {
    if (!on_wan(view.spec)) {
        legacy_.create_topic_entities(view, topic, generation);
        return;
    }
    const Key key{view.spec.route_name, topic};
    try {
        if (live_.count(key)) {
            remove(key);
        }
        const auto& mapping = wan_mapping(topic);
        if (types_.topic_type(topic) != model_->type(mapping.type)) {
            throw std::runtime_error("wire-learned original type differs from the WAN catalog: " + topic);
        }
        const bool source = registry_.on_wan(view.spec.output.participant);
        auto selected = interest(view.spec, topic);
        if (source) {
            std::vector<WanInterest> sources{selected};
            for (const auto& entry : live_) {
                if (entry.second.source) {
                    sources.push_back(entry.second.interest);
                }
            }
            validate_wan_source_interests(sources);
            adapters_->bind_source(key.first, topic, generation, view.spec.input.participant,
                                   view.spec.input.reader_qos, provider(selected, membership_revision_));
        } else {
            std::vector<WanInterest> interests{selected};
            bool existing = false;
            for (const auto& entry : live_) {
                if (!entry.second.source && std::string(wan_mapping(entry.first.second).family) == mapping.family) {
                    existing = true;
                    interests.push_back(entry.second.interest);
                }
            }
            const auto filter = existing ? adapters_->receive_filter(topic) :
                wan_receive_filter(mapping.family, identity_.node_name, interests);
            adapters_->bind_destination(key.first, topic, generation, view.spec.output.participant,
                                        view.spec.output.writer_qos, filter);
        }
        live_.emplace(key, Live{generation, source, selected, {}});
        try {
            refresh_interests();
        } catch (const std::exception&) {
            adapters_->unbind(key.first, key.second, generation);
            live_.erase(key);
            throw;
        }
        const auto reader_alias = source ? view.spec.input.reader_qos : mapping.qos_profile;
        const auto writer_alias = source ? mapping.qos_profile : view.spec.output.writer_qos;
        controller_->post(ControllerEvent::topic_entities_ready(key.first, topic, generation,
            QosResolver::summarize(qos_.reader_qos(reader_alias)),
            QosResolver::summarize(qos_.writer_qos(writer_alias))));
        poll_status(key);
    } catch (const std::exception& exception) {
        report_error(key, generation, exception.what());
    }
}

void WanControllerFactory::remove(const Key& key) {
    const auto found = live_.find(key);
    if (found == live_.end()) {
        return;
    }
    adapters_->unbind(key.first, key.second, found->second.generation);
    live_.erase(found);
    refresh_interests();
}

void WanControllerFactory::teardown_topic_entities(const std::string& route,
                                                 const std::string& topic, uint64_t generation) {
    const auto& spec = controller_->state().routes.at(route).desired;
    if (!on_wan(spec)) {
        legacy_.teardown_topic_entities(route, topic, generation);
        return;
    }
    try {
        remove({route, topic});
        controller_->post(ControllerEvent::topic_teardown_complete(route, topic, generation));
    } catch (const std::exception& exception) {
        report_error({route, topic}, generation, exception.what());
    }
}

void WanControllerFactory::abort_topic_creation(const std::string& route,
                                              const std::string& topic, uint64_t generation) {
    if (!on_wan(controller_->state().routes.at(route).desired)) {
        legacy_.abort_topic_creation(route, topic, generation);
        return;
    }
    try {
        remove({route, topic});
    } catch (const std::exception& exception) {
        report_error({route, topic}, generation, exception.what());
    }
}

bool WanControllerFactory::update_route_partitions(const std::string& route,
        const std::string& topic, const std::vector<std::string>& subscriber,
        const std::vector<std::string>& publisher) {
    if (!on_wan(controller_->state().routes.at(route).desired)) {
        return legacy_.update_route_partitions(route, topic, subscriber, publisher);
    }
    try {
        for (const auto& entry : controller_->state().routes) {
            if (on_wan(entry.second.desired)) {
                for (const auto& topic : entry.second.desired.topics) {
                    interest(entry.second.desired, topic.name);
                }
            }
        }
        refresh_interests();
        return true;
    } catch (const std::exception& exception) {
        Log::error("wan_interest_update_failed", {{"route", route}, {"error", exception.what()}});
        return false;
    }
}

bool WanControllerFactory::apply_participant_partition(const std::string& participant,
                                                      const std::vector<std::string>& names) {
    if (!registry_.on_wan(participant)) {
        return legacy_.apply_participant_partition(participant, names);
    }
    try {
        for (const auto& entry : controller_->state().routes) {
            if (on_wan(entry.second.desired)) {
                for (const auto& topic : entry.second.desired.topics) {
                    interest(entry.second.desired, topic.name);
                }
            }
        }
        refresh_interests();
        return true;
    } catch (const std::exception& exception) {
        Log::error("wan_membership_update_failed", {{"participant", participant},
                   {"error", exception.what()}});
        return false;
    }
}

void WanControllerFactory::poll_status(const Key& key) const {
    auto& entry = live_.at(key);
    const auto status = adapters_->binding_status(key.first, key.second);
    if (entry.status.input_matched != status.input_matched) {
        controller_->post(ControllerEvent::topic_match_changed(key.first, key.second,
            entry.generation, true, status.input_matched));
    }
    if (entry.status.output_matched != status.output_matched) {
        controller_->post(ControllerEvent::topic_match_changed(key.first, key.second,
            entry.generation, false, status.output_matched));
    }
    if (entry.status.warning != status.warning && !status.warning.empty()) {
        controller_->post(ControllerEvent::topic_qos_warning(key.first, key.second,
            entry.generation, status.warning));
    }
    entry.status = status;
}

uint64_t WanControllerFactory::forwarded_count(const std::string& route,
                                             const std::string& topic) const {
    if (!on_wan(controller_->state().routes.at(route).desired)) {
        return legacy_.forwarded_count(route, topic);
    }
    const Key key{route, topic};
    if (!live_.count(key)) {
        return 0;
    }
    try {
        poll_status(key);
        return adapters_->forwarded(route, topic);
    } catch (const std::exception& exception) {
        Log::error("wan_status_poll_failed", {{"route", route}, {"topic", topic},
                   {"error", exception.what()}});
        controller_->post(ControllerEvent::route_entity_error(route, topic,
            live_.at(key).generation, exception.what()));
        return 0;
    }
}

void WanControllerFactory::shutdown() {
    adapters_->shutdown();
    live_.clear();
}

} // namespace router
