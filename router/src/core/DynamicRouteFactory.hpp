// DynamicRouteFactory.hpp — DynamicData lane of the route-entity factory (D35/D41; D70).
//
// The default lane for forwarded app payloads. A thin binding of the shared
// RouteEntityFactory skeleton to per-topic WIRE-LEARNED DynamicTypes (7c, D64/D70): the
// TypeResolver holds each topic's type as registered from builtin discovery
// (first-learned-wins, D66); the skeleton supplies the D31.4 create-order, the
// content-filter branch, and the D32 teardown barrier — this lane contributes only the
// per-topic type gate and the DynamicType-bound topic. One instance serves ALL
// DynamicData topics regardless of type (`router.type_name` retired, D70).

#pragma once

#include "RouteEntityFactory.hpp"

#include <dds/core/xtypes/DynamicData.hpp>

#include <string>

namespace router {

class TypeResolver;

class DynamicRouteFactory : public RouteEntityFactory<dds::core::xtypes::DynamicData> {
public:
    DynamicRouteFactory(ParticipantRegistry &registry, TypeResolver &types,
                        QosResolver &qos, AsyncWaitSetDispatcher &dispatcher);

protected:
    void ensure_type_available(const std::string &topic_name) const override;

    dds::topic::Topic<dds::core::xtypes::DynamicData> make_topic(
            dds::domain::DomainParticipant dp, const std::string &name) const override;

private:
    TypeResolver &types_;
};

} // namespace router
