// RouteEntityFactory.hpp — the one route-entity factory skeleton, shared by both type
// lanes (D41; replaces the near-duplicate EntityFactory<T> / DynamicRouteFactory bodies).
//
// Creates the per-topic route entities after the controller reports discovery readiness,
// following the D31 ordering strictly:
//   1. create the OUTPUT DataWriter (in its own per-build Publisher)
//   2. dds::pub::ignore(output_participant, writer.instance_handle())  — before any
//      forwarding and before the input ReadCondition is attached; failure is a
//      RouteEntityError, not a warning (D31.4)
//   3. create the INPUT DataReader (+ ContentFilteredTopic when the input endpoint
//      carries a filter) + its forwarding ReadCondition (via RouteTopicRuntime)
//   4. hand the runtime to the AsyncWaitSetDispatcher (attaches the condition)
//   5. post TopicEntitiesReady with the controller-issued generation stamp (D23)
// Any failure posts RouteEntityError(topic, gen) instead — the controller then makes the
// topic sticky-ERROR (D2/D11/D21).
//
// Everything created per build — Publisher, Subscriber, CFT, reader, writer, condition —
// is owned by the RouteTopicRuntime, so teardown reclaims it all and a rebuild can
// recreate the CFT under the same fixed name (D41). Only the base Topic is deliberately
// find-or-created and never closed: topics are reused across rebuilds.
//
// QoS aliases are read from the ENDPOINT spec (view.spec.input.reader_qos /
// view.spec.output.writer_qos) — the one canonical location, matching the YAML config
// schema (D36/D41). The per-topic spec carries only the topic name.
//
// A lane subclass supplies only the payload-type binding:
//   - ensure_type_available(): throw if the bound type cannot be constructed locally
//   - make_topic(dp, name):    construct the typed Topic (generated T vs DynamicData+type)
//
// All methods run on the controller strand (reconcile_topic / handle_disable call them),
// so the dispatcher's runtime map is touched single-threaded (D12/D32).

#pragma once

#include "AsyncWaitSetDispatcher.hpp"
#include "Interfaces.hpp"
#include "Log.hpp"
#include "ParticipantRegistry.hpp"
#include "QosResolver.hpp"
#include "RouteRuntime.hpp"
#include "RouteView.hpp"
#include "RouterController.hpp"
#include "RouterState.hpp" // find_topic_spec (D44)

#include <dds/dds.hpp>
#include <dds/pub/discovery.hpp> // dds::pub::ignore (D31.4)
#include <dds/topic/ContentFilteredTopic.hpp>

#include <functional>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace router {

template <typename T>
class RouteEntityFactory : public IEntityFactory {
public:
    RouteEntityFactory(ParticipantRegistry &registry, QosResolver &qos,
                       AsyncWaitSetDispatcher &dispatcher, const std::string &type_name)
        : registry_(registry), qos_(qos), dispatcher_(dispatcher),
          type_name_(type_name), controller_(nullptr) {}

    // Wire the controller after construction (completion events are posted to it).
    void set_controller(RouterController *controller) { controller_ = controller; }

    void create_topic_entities(const RouteView &view, const std::string &topic_name,
                               std::uint64_t generation,
                               const DerivedWriterQos &derived) override {
        const std::string &route = view.spec.route_name;
        try {
            ensure_type_available();
            if (find_topic_spec(view.spec, topic_name) == nullptr) {
                throw std::runtime_error("topic not in route spec: " + topic_name);
            }

            dds::domain::DomainParticipant in_dp =
                    registry_.get(view.spec.input.participant);
            dds::domain::DomainParticipant out_dp =
                    registry_.get(view.spec.output.participant);

            // (1) output writer first — strong baseline plus the controller-derived
            //     deadline/liveliness when the output endpoint is auto (D39/D42).
            dds::topic::Topic<T> out_topic = find_or_create_topic(out_dp, topic_name);
            dds::pub::Publisher publisher(out_dp);
            dds::pub::qos::DataWriterQos wqos =
                    qos_.writer_qos(view.spec.output.writer_qos, derived);
            dds::pub::DataWriter<T> writer(publisher, out_topic, wqos);

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

            // (3) input reader; a filter on the input endpoint => ContentFilteredTopic.
            dds::topic::Topic<T> in_topic = find_or_create_topic(in_dp, topic_name);
            dds::sub::qos::DataReaderQos rqos =
                    qos_.reader_qos(view.spec.input.reader_qos);
            dds::sub::Subscriber subscriber(in_dp);
            dds::sub::DataReader<T> reader(dds::core::null);
            dds::topic::ContentFilteredTopic<T> cft = dds::core::null;
            if (!view.spec.input.filter_expression.empty()) {
                std::vector<std::string> params(view.spec.input.filter_parameters.begin(),
                                                view.spec.input.filter_parameters.end());
                // Qualified by route, not just topic (D44): two routes filtering the same
                // topic through the same input participant would otherwise collide on the
                // fixed CFT name and the second throws PRECONDITION_NOT_MET (validated 7.7).
                cft = dds::topic::ContentFilteredTopic<T>(
                        in_topic, route + "_" + topic_name + "_cft",
                        dds::topic::Filter(view.spec.input.filter_expression, params));
                reader = dds::sub::DataReader<T>(subscriber, cft, rqos);
                Log::info("route_input_filtered",
                          {{"route", route}, {"topic", topic_name},
                           {"filter", view.spec.input.filter_expression}});
            } else {
                reader = dds::sub::DataReader<T>(subscriber, in_topic, rqos);
            }

            // Incompatible-QoS warnings from the entity StatusConditions surface as
            // controller events carrying this build's stamp (D39/D45). The callback runs
            // on AsyncWaitSet worker threads; post() is the thread-safe MPSC producer.
            RouterController *controller = controller_;
            std::function<void(const std::string &)> on_warning;
            if (controller) {
                on_warning = [controller, route, topic_name,
                              generation](const std::string &warning) {
                    Log::warn("route_qos_incompatible",
                              {{"route", route}, {"topic", topic_name},
                               {"policy", warning}});
                    controller->post(ControllerEvent::topic_qos_warning(
                            route, topic_name, generation, warning));
                };
            }
            bool manual_liveliness = derived.derive
                    && derived.liveliness_kind != LivelinessKindPod::Automatic;

            std::unique_ptr<RouteTopicRuntimeBase> runtime(
                    new RouteTopicRuntime<T>(reader, writer, publisher, subscriber, cft,
                                             on_warning, manual_liveliness));

            // (4) attach the forwarding + status conditions to the AsyncWaitSet.
            dispatcher_.attach(route, topic_name, std::move(runtime));

            Log::info("route_entities_created",
                      {{"route", route}, {"topic", topic_name}, {"type", type_name_},
                       {"in", view.spec.input.participant},
                       {"out", view.spec.output.participant}});

            // (5) report completion with the issued generation stamp (D21/D23) and the
            //     resolved QoS summaries for status (D45).
            if (controller_) {
                controller_->post(ControllerEvent::topic_entities_ready(
                        route, topic_name, generation,
                        QosResolver::summarize(rqos), QosResolver::summarize(wqos)));
            }
        } catch (const std::exception &e) {
            Log::warn("route_entities_error",
                      {{"route", route}, {"topic", topic_name}, {"error", e.what()}});
            if (controller_) {
                controller_->post(ControllerEvent::route_entity_error(route, topic_name,
                                                                      generation,
                                                                      e.what()));
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

    std::string update_writer_deadline(const std::string &route,
                                       const std::string &topic_name,
                                       std::int64_t deadline_nanos) override {
        return dispatcher_.set_writer_deadline(route, topic_name, deadline_nanos);
    }

protected:
    // --- Type-lane hooks (D41) ---

    // Throw (with a message for the RouteEntityError) if the bound type is unavailable.
    virtual void ensure_type_available() const = 0;

    // Construct the typed Topic on this participant (called only when not found).
    virtual dds::topic::Topic<T> make_topic(dds::domain::DomainParticipant dp,
                                            const std::string &name) const = 0;

    const std::string &type_name() const { return type_name_; }

private:
    // Topics are reused across rebuilds (readers/writers/parents close on teardown, the
    // base topic does not), so find-or-create avoids a duplicate-topic error on rebuild.
    dds::topic::Topic<T> find_or_create_topic(dds::domain::DomainParticipant dp,
                                              const std::string &name) const {
        dds::topic::Topic<T> found = dds::topic::find<dds::topic::Topic<T>>(dp, name);
        if (found != dds::core::null) {
            return found;
        }
        return make_topic(dp, name);
    }

    ParticipantRegistry &registry_;
    QosResolver &qos_;
    AsyncWaitSetDispatcher &dispatcher_;
    std::string type_name_;
    RouterController *controller_;
};

} // namespace router
