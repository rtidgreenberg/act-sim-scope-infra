// DynamicRouteFactory.cxx — DynamicData lane hooks (D35/D41; per-topic wire types, D70).

#include "DynamicRouteFactory.hpp"

#include "TypeResolver.hpp"

#include <stdexcept>

namespace router {

using DynData = dds::core::xtypes::DynamicData;

DynamicRouteFactory::DynamicRouteFactory(ParticipantRegistry &registry, TypeResolver &types,
                                         QosResolver &qos, AsyncWaitSetDispatcher &dispatcher)
    : RouteEntityFactory<DynData>(registry, qos, dispatcher), types_(types) {}

void DynamicRouteFactory::ensure_type_available(const std::string &topic_name) const {
    // topic_type() throws its own descriptive exception when the topic's type has not
    // been wire-learned — unreachable via the controller's TypeResolved gate (D70), but
    // it propagates straight to the skeleton's catch as a RouteEntityError if it ever is.
    types_.topic_type(topic_name);
}

dds::topic::Topic<DynData> DynamicRouteFactory::make_topic(
        dds::domain::DomainParticipant dp, const std::string &name) const {
    return dds::topic::Topic<DynData>(dp, name, types_.topic_type(name));
}

} // namespace router
