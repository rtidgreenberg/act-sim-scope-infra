// EntityFactory.hpp — real IEntityFactory for one generated type (Phase 3, D31 step 4).
//
// Creates the per-topic route entities after the controller reports discovery readiness,
// following the D31 ordering strictly:
//   1. create the OUTPUT DataWriter
//   2. dds::pub::ignore(output_participant, writer.instance_handle())  — before any
//      forwarding and before the input ReadCondition is attached; failure is a
//      RouteEntityError, not a warning (D31.4)
//   3. create the INPUT DataReader + its forwarding ReadCondition (via RouteTopicRuntime)
//   4. hand the runtime to the AsyncWaitSetDispatcher (attaches the condition)
//   5. post TopicEntitiesReady with the controller-issued generation stamp (D23)
// Any failure posts RouteEntityError(topic, gen) instead — the controller then makes the
// topic sticky-ERROR (D2/D11/D21).
//
// This factory is bound to ONE generated type T (the generated-type fast path). Multi-type
// support is a later generalization: a type-dispatching factory that selects the right
// typed sub-factory by the discovered type_name. TypeResolver is the seam that decision
// consults; here it gates that T is a locally supported type.
//
// All methods run on the controller strand (reconcile_topic / handle_disable call them), so
// the dispatcher's runtime map is touched single-threaded (D12/D32).

#pragma once

#include "AsyncWaitSetDispatcher.hpp"
#include "Interfaces.hpp"
#include "Log.hpp"
#include "ParticipantRegistry.hpp"
#include "QosResolver.hpp"
#include "RouteRuntime.hpp"
#include "RouteView.hpp"
#include "RouterController.hpp"
#include "TypeResolver.hpp"

#include <dds/dds.hpp>
#include <dds/pub/discovery.hpp> // dds::pub::ignore (D31.4)

#include <memory>
#include <stdexcept>
#include <string>

namespace router {

template <typename T>
class EntityFactory : public IEntityFactory {
public:
    EntityFactory(ParticipantRegistry &registry, TypeResolver &types, QosResolver &qos,
                  AsyncWaitSetDispatcher &dispatcher, const std::string &type_name)
        : registry_(registry), types_(types), qos_(qos), dispatcher_(dispatcher),
          type_name_(type_name), controller_(nullptr) {
        types_.register_type(type_name_);
    }

    // Wire the controller after construction (completion events are posted to it).
    void set_controller(RouterController *controller) { controller_ = controller; }

    void create_topic_entities(const RouteView &view, const std::string &topic_name,
                               std::uint64_t generation) override {
        const std::string &route = view.spec.route_name;
        try {
            if (!types_.is_constructible(type_name_)) {
                throw std::runtime_error("type not locally supported: " + type_name_);
            }
            const RouterRouteTopicSpec *spec = find_topic_spec(view, topic_name);
            if (spec == nullptr) {
                throw std::runtime_error("topic not in route spec: " + topic_name);
            }

            dds::domain::DomainParticipant in_dp =
                registry_.get(view.spec.input.participant);
            dds::domain::DomainParticipant out_dp =
                registry_.get(view.spec.output.participant);

            // (1) output writer first.
            dds::topic::Topic<T> out_topic = find_or_create_topic(out_dp, topic_name);
            dds::pub::DataWriter<T> writer(dds::pub::Publisher(out_dp), out_topic,
                                           qos_.writer_qos(spec->writer_qos));

            // (2) ignore our own output writer before forwarding / attaching input
            //     conditions (D31.4). Belt-and-suspenders: input and output legs live on
            //     different participants, so intra-participant self-match cannot occur
            //     anyway (D32). Remote readers on other participants are unaffected.
            try {
                dds::pub::ignore(out_dp, writer.instance_handle());
            } catch (const std::exception &e) {
                throw std::runtime_error(std::string("ignore output writer failed: ")
                                         + e.what());
            }

            // (3) input reader + forwarding condition.
            dds::topic::Topic<T> in_topic = find_or_create_topic(in_dp, topic_name);
            dds::sub::DataReader<T> reader(dds::sub::Subscriber(in_dp), in_topic,
                                           qos_.reader_qos(spec->reader_qos));

            std::unique_ptr<RouteTopicRuntimeBase> runtime(
                new RouteTopicRuntime<T>(reader, writer));

            // (4) attach the condition to the AsyncWaitSet.
            dispatcher_.attach(route, topic_name, std::move(runtime));

            Log::info("route_entities_created",
                      {{"route", route}, {"topic", topic_name}, {"type", type_name_},
                       {"in", view.spec.input.participant},
                       {"out", view.spec.output.participant}});

            // (5) report completion with the issued generation stamp (D21/D23).
            if (controller_) {
                controller_->post(ControllerEvent::topic_entities_ready(route, topic_name,
                                                                        generation));
            }
        } catch (const std::exception &e) {
            Log::warn("route_entities_error",
                      {{"route", route}, {"topic", topic_name}, {"error", e.what()}});
            if (controller_) {
                controller_->post(ControllerEvent::route_entity_error(route, topic_name,
                                                                      generation, e.what()));
            }
        }
    }

    void teardown_topic_entities(const std::string &route, const std::string &topic_name,
                                 std::uint64_t generation) override {
        dispatcher_.detach_and_close(route, topic_name); // D32 detach barrier + close
        if (controller_) {
            controller_->post(ControllerEvent::topic_teardown_complete(route, topic_name,
                                                                       generation));
        }
    }

    void abort_topic_creation(const std::string &route, const std::string &topic_name,
                              std::uint64_t /*generation*/) override {
        // Discovery regressed mid-create: discard the partial entities. The controller has
        // already zeroed the generation and moved the topic to IDLE, and will discard the
        // (now stale) TopicEntitiesReady — so abort posts no completion event (D8/D23).
        dispatcher_.detach_and_close(route, topic_name);
    }

private:
    static const RouterRouteTopicSpec *find_topic_spec(const RouteView &view,
                                                       const std::string &topic_name) {
        for (size_t i = 0; i < view.spec.topics.size(); ++i) {
            if (view.spec.topics.at(i).name == topic_name) {
                return &view.spec.topics.at(i);
            }
        }
        return nullptr;
    }

    // Topics are reused across rebuilds (only readers/writers are closed on teardown), so
    // find-or-create avoids a duplicate-topic error on the second create.
    static dds::topic::Topic<T> find_or_create_topic(dds::domain::DomainParticipant dp,
                                                      const std::string &name) {
        dds::topic::Topic<T> found = dds::topic::find<dds::topic::Topic<T>>(dp, name);
        if (found != dds::core::null) {
            return found;
        }
        return dds::topic::Topic<T>(dp, name);
    }

    ParticipantRegistry &registry_;
    TypeResolver &types_;
    QosResolver &qos_;
    AsyncWaitSetDispatcher &dispatcher_;
    std::string type_name_;
    RouterController *controller_;
};

} // namespace router
