// DynamicRouteFactory.hpp — IEntityFactory that forwards a DynamicData payload (D35).
//
// The Phase 4 default lane. Creates route entities for one DynamicData type (resolved
// from the TypeResolver's loaded DDS-type XML), following the same D31.4 create-order and
// D32 teardown barrier as the Phase 3 generated-type EntityFactory<T> — only the payload
// type differs (dds::core::xtypes::DynamicData instead of a compiled T), so the reader/
// writer creation needs the DynamicType and, when the route input carries a filter, a
// ContentFilteredTopic.
//
// Bound to ONE type name at construction (like Phase 3's EntityFactory<T>); multi-type
// dispatch by discovered type_name stays deferred (D34/D35). Methods run on the controller
// strand, so the dispatcher's runtime map stays single-threaded (D12/D32).

#pragma once

#include "Interfaces.hpp"

#include <cstdint>
#include <string>

namespace router {

class ParticipantRegistry;
class TypeResolver;
class QosResolver;
class AsyncWaitSetDispatcher;
class RouterController;

class DynamicRouteFactory : public IEntityFactory {
public:
    DynamicRouteFactory(ParticipantRegistry &registry, TypeResolver &types,
                        QosResolver &qos, AsyncWaitSetDispatcher &dispatcher,
                        const std::string &type_name);

    void set_controller(RouterController *controller) { controller_ = controller; }

    void create_topic_entities(const RouteView &view, const std::string &topic_name,
                               std::uint64_t generation) override;
    void teardown_topic_entities(const std::string &route_name,
                                 const std::string &topic_name,
                                 std::uint64_t generation) override;
    void abort_topic_creation(const std::string &route_name,
                              const std::string &topic_name,
                              std::uint64_t generation) override;

private:
    ParticipantRegistry &registry_;
    TypeResolver &types_;
    QosResolver &qos_;
    AsyncWaitSetDispatcher &dispatcher_;
    std::string type_name_;
    RouterController *controller_;
};

} // namespace router
