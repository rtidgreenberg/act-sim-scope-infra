// TypeResolver.hpp — generated-type fast path (D31 step 2).
//
// Phase 3 forwards a *generated* type the router links against. Construction readiness
// is: the discovered topic's registered type_name matches a type this build has generated
// support for. This class is the registry of locally supported type names; the
// EntityFactory consults it before creating route entities. A discovered type_name with
// no local support is a RouteEntityError, not a silent skip.
//
// This is deliberately NOT DynamicType/TypeLookup schema-equivalence proof (the later,
// stronger path). It is the name-match fast path D31 pins for explicit-QoS routes.

#pragma once

#include <set>
#include <string>

namespace router {

class TypeResolver {
public:
    // Declare a generated type this build can construct DataReaders/DataWriters for.
    void register_type(const std::string &type_name) { known_.insert(type_name); }

    // Construction readiness: is this discovered type_name locally supported?
    bool is_constructible(const std::string &type_name) const {
        return known_.find(type_name) != known_.end();
    }

private:
    std::set<std::string> known_;
};

} // namespace router
