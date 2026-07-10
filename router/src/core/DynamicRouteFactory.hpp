// DynamicRouteFactory.hpp — DynamicData lane of the route-entity factory (D35/D41).
//
// The Phase 4 default lane. A thin binding of the shared RouteEntityFactory skeleton to
// one DynamicData type resolved from the TypeResolver's loaded DDS-type XML; the skeleton
// supplies the D31.4 create-order, the content-filter branch, and the D32 teardown
// barrier — this lane contributes only the type gate and the DynamicType-bound topic.
//
// Bound to ONE type name at construction (like the generated lane); multi-type dispatch
// by discovered type_name stays deferred (D34/D35).

#pragma once

#include "RouteEntityFactory.hpp"

#include <dds/core/xtypes/DynamicData.hpp>

#include <string>

namespace router {

class TypeResolver;

class DynamicRouteFactory : public RouteEntityFactory<dds::core::xtypes::DynamicData> {
public:
    DynamicRouteFactory(ParticipantRegistry &registry, TypeResolver &types,
                        QosResolver &qos, AsyncWaitSetDispatcher &dispatcher,
                        const std::string &type_name);

protected:
    void ensure_type_available() const override;

    dds::topic::Topic<dds::core::xtypes::DynamicData> make_topic(
            dds::domain::DomainParticipant dp, const std::string &name) const override;

private:
    TypeResolver &types_;
};

} // namespace router
