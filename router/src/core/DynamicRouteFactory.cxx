// DynamicRouteFactory.cxx — DynamicData lane hooks (D35/D41).

#include "DynamicRouteFactory.hpp"

#include "TypeResolver.hpp"

#include <stdexcept>

namespace router {

using DynData = dds::core::xtypes::DynamicData;

DynamicRouteFactory::DynamicRouteFactory(ParticipantRegistry &registry, TypeResolver &types,
                                         QosResolver &qos, AsyncWaitSetDispatcher &dispatcher,
                                         const std::string &type_name)
    : RouteEntityFactory<DynData>(registry, qos, dispatcher, type_name), types_(types) {}

void DynamicRouteFactory::ensure_type_available() const {
    // Calls get_dynamic_type() directly (D44) rather than has_dynamic_type(), which
    // internally wraps the same QosProvider lookup in a try/catch just to return a bool —
    // that redundant internal catch-and-rethrow is avoided; its own descriptive exception
    // ("no DDS-type XML loaded..." / unknown type) propagates straight to the skeleton's
    // catch. make_topic() still does its own lookup, but only on a topic's first build
    // (find_or_create_topic skips it on rebuilds) — low-cost, low-frequency (verified).
    types_.get_dynamic_type(type_name());
}

dds::topic::Topic<DynData> DynamicRouteFactory::make_topic(
        dds::domain::DomainParticipant dp, const std::string &name) const {
    return dds::topic::Topic<DynData>(dp, name, types_.get_dynamic_type(type_name()));
}

} // namespace router
