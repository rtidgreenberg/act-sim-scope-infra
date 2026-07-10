// EntityFactory.hpp — generated-type lane of the route-entity factory (Phase 3, D31/D41).
//
// A thin binding of the shared RouteEntityFactory skeleton to ONE compiled type T (the
// generated-type fast path). All create/teardown/abort/report logic lives in the skeleton;
// this lane contributes the local-type gate (TypeResolver registry) and plain Topic<T>
// construction. Multi-type support is a later generalization: a type-dispatching factory
// that selects the right typed sub-factory by the discovered type_name (D34).

#pragma once

#include "RouteEntityFactory.hpp"
#include "TypeResolver.hpp"

#include <stdexcept>
#include <string>

namespace router {

template <typename T>
class EntityFactory : public RouteEntityFactory<T> {
public:
    EntityFactory(ParticipantRegistry &registry, TypeResolver &types, QosResolver &qos,
                  AsyncWaitSetDispatcher &dispatcher, const std::string &type_name)
        : RouteEntityFactory<T>(registry, qos, dispatcher, type_name), types_(types) {
        types_.register_type(type_name);
    }

protected:
    void ensure_type_available() const override {
        if (!types_.is_constructible(this->type_name())) {
            throw std::runtime_error("type not locally supported: " + this->type_name());
        }
    }

    dds::topic::Topic<T> make_topic(dds::domain::DomainParticipant dp,
                                    const std::string &name) const override {
        return dds::topic::Topic<T>(dp, name);
    }

private:
    TypeResolver &types_;
};

} // namespace router
