// DynamicRouteFactory.cxx — DynamicData forwarding route entities (D35).

#include "DynamicRouteFactory.hpp"

#include "AsyncWaitSetDispatcher.hpp"
#include "Log.hpp"
#include "ParticipantRegistry.hpp"
#include "QosResolver.hpp"
#include "RouteRuntime.hpp"
#include "RouterController.hpp"
#include "TypeResolver.hpp"

#include <dds/dds.hpp>
#include <dds/core/xtypes/DynamicData.hpp>
#include <dds/pub/discovery.hpp> // dds::pub::ignore (D31.4)

#include <memory>
#include <stdexcept>
#include <vector>

namespace router {

using DynData = dds::core::xtypes::DynamicData;

namespace {

const RouterRouteTopicSpec *find_topic_spec(const RouteView &view,
                                            const std::string &topic_name) {
    for (size_t i = 0; i < view.spec.topics.size(); ++i) {
        if (view.spec.topics.at(i).name == topic_name) {
            return &view.spec.topics.at(i);
        }
    }
    return nullptr;
}

// Topics are reused across rebuilds (only readers/writers close on teardown), so
// find-or-create avoids a duplicate-topic error on the second create.
dds::topic::Topic<DynData> find_or_create_topic(dds::domain::DomainParticipant dp,
                                                const std::string &name,
                                                const dds::core::xtypes::DynamicType &dt) {
    dds::topic::Topic<DynData> found =
        dds::topic::find<dds::topic::Topic<DynData>>(dp, name);
    if (found != dds::core::null) {
        return found;
    }
    return dds::topic::Topic<DynData>(dp, name, dt);
}

} // namespace

DynamicRouteFactory::DynamicRouteFactory(ParticipantRegistry &registry, TypeResolver &types,
                                         QosResolver &qos, AsyncWaitSetDispatcher &dispatcher,
                                         const std::string &type_name)
    : registry_(registry), types_(types), qos_(qos), dispatcher_(dispatcher),
      type_name_(type_name), controller_(nullptr) {}

void DynamicRouteFactory::create_topic_entities(const RouteView &view,
                                                const std::string &topic_name,
                                                std::uint64_t generation) {
    const std::string &route = view.spec.route_name;
    try {
        if (!types_.has_dynamic_type(type_name_)) {
            throw std::runtime_error("dynamic type not loaded: " + type_name_);
        }
        const dds::core::xtypes::DynamicType &dt = types_.get_dynamic_type(type_name_);
        const RouterRouteTopicSpec *ts = find_topic_spec(view, topic_name);
        if (ts == nullptr) {
            throw std::runtime_error("topic not in route spec: " + topic_name);
        }

        dds::domain::DomainParticipant in_dp = registry_.get(view.spec.input.participant);
        dds::domain::DomainParticipant out_dp = registry_.get(view.spec.output.participant);

        // (1) output writer first; QoS from the output endpoint alias (D19/Phase 5 refine).
        dds::topic::Topic<DynData> out_topic = find_or_create_topic(out_dp, topic_name, dt);
        dds::pub::DataWriter<DynData> writer(dds::pub::Publisher(out_dp), out_topic,
                                             qos_.writer_qos(view.spec.output.writer_qos));

        // (2) ignore our own output writer before forwarding / attaching input (D31.4).
        try {
            dds::pub::ignore(out_dp, writer.instance_handle());
        } catch (const std::exception &e) {
            throw std::runtime_error(std::string("ignore output writer failed: ") + e.what());
        }

        // (3) input reader; a filter on the input endpoint => ContentFilteredTopic.
        dds::topic::Topic<DynData> in_topic = find_or_create_topic(in_dp, topic_name, dt);
        dds::sub::qos::DataReaderQos rqos = qos_.reader_qos(view.spec.input.reader_qos);
        dds::sub::DataReader<DynData> reader(dds::core::null);
        if (!view.spec.input.filter_expression.empty()) {
            std::vector<std::string> params(view.spec.input.filter_parameters.begin(),
                                            view.spec.input.filter_parameters.end());
            dds::topic::ContentFilteredTopic<DynData> cft(
                in_topic, topic_name + "_cft",
                dds::topic::Filter(view.spec.input.filter_expression, params));
            reader = dds::sub::DataReader<DynData>(dds::sub::Subscriber(in_dp), cft, rqos);
            Log::info("route_input_filtered",
                      {{"route", route}, {"topic", topic_name},
                       {"filter", view.spec.input.filter_expression}});
        } else {
            reader = dds::sub::DataReader<DynData>(dds::sub::Subscriber(in_dp), in_topic, rqos);
        }

        std::unique_ptr<RouteTopicRuntimeBase> runtime(
            new RouteTopicRuntime<DynData>(reader, writer));

        // (4) attach the forwarding condition to the AsyncWaitSet.
        dispatcher_.attach(route, topic_name, std::move(runtime));

        Log::info("route_entities_created_dynamic",
                  {{"route", route}, {"topic", topic_name}, {"type", type_name_},
                   {"in", view.spec.input.participant},
                   {"out", view.spec.output.participant}});

        // (5) report completion with the issued generation stamp (D21/D23).
        if (controller_) {
            controller_->post(ControllerEvent::topic_entities_ready(route, topic_name,
                                                                    generation));
        }
    } catch (const std::exception &e) {
        Log::warn("route_entities_error_dynamic",
                  {{"route", route}, {"topic", topic_name}, {"error", e.what()}});
        if (controller_) {
            controller_->post(ControllerEvent::route_entity_error(route, topic_name,
                                                                  generation, e.what()));
        }
    }
}

void DynamicRouteFactory::teardown_topic_entities(const std::string &route_name,
                                                  const std::string &topic_name,
                                                  std::uint64_t generation) {
    dispatcher_.detach_and_close(route_name, topic_name); // D32 detach barrier + close
    if (controller_) {
        controller_->post(ControllerEvent::topic_teardown_complete(route_name, topic_name,
                                                                    generation));
    }
}

void DynamicRouteFactory::abort_topic_creation(const std::string &route_name,
                                               const std::string &topic_name,
                                               std::uint64_t /*generation*/) {
    // Discovery regressed mid-create: discard partial entities; the controller has already
    // zeroed the generation and will discard the stale TopicEntitiesReady (D8/D23).
    dispatcher_.detach_and_close(route_name, topic_name);
}

} // namespace router
