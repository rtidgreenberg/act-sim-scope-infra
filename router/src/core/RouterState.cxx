#include "RouterState.hpp"

#include <sstream>

namespace router {

bool topic_uses_auto_qos(const RouterRouteTopicSpec &spec) {
    return spec.reader_qos.empty() && spec.writer_qos.empty();
}

RouterRouteDiscoveryState derive_topic_discovery(const TopicRouteState &topic,
                                                 const RouterRouteTopicSpec &spec) {
    // input_writer_seen <=> matched-writer set non-empty (D20)
    if (topic.matched_writers.empty()) {
        return RouterRouteDiscoveryState::DISCOVERY_NONE;
    }
    // type_resolved: any currently matched writer carries a resolved type (strictly
    // derived from the current set — no memory, D1; late arrival via upsert, D13)
    bool type_resolved = false;
    for (std::map<std::string, MatchedEndpoint>::const_iterator it =
                 topic.matched_writers.begin();
         it != topic.matched_writers.end(); ++it) {
        if (it->second.has_type) {
            type_resolved = true;
            break;
        }
    }
    // qos_resolved: explicit alias => resolved by definition (D19: history/resource
    // limits are alias-supplied anyway); auto => needs a discovered output reader (D1)
    bool qos_resolved =
            topic_uses_auto_qos(spec) ? !topic.matched_readers.empty() : true;

    if (type_resolved && qos_resolved) {
        return RouterRouteDiscoveryState::DISCOVERY_READY;
    }
    return RouterRouteDiscoveryState::DISCOVERY_PARTIAL;
}

RouterRouteDiscoveryState derive_route_discovery(const RouteState &route) {
    RouterRouteDiscoveryState best = RouterRouteDiscoveryState::DISCOVERY_NONE;
    for (size_t i = 0; i < route.desired.topics.size(); ++i) {
        const RouterRouteTopicSpec &spec = route.desired.topics.at(i);
        std::map<std::string, TopicRouteState>::const_iterator t =
                route.topics.find(spec.name);
        if (t == route.topics.end()) {
            continue;
        }
        RouterRouteDiscoveryState d = derive_topic_discovery(t->second, spec);
        if (static_cast<int>(d) > static_cast<int>(best)) {
            best = d;
        }
    }
    return best;
}

RouterRouteOperationalState derive_operational(const RouteState &route) {
    // D11 derivation table, checked in precedence order.
    bool any_forwarding = false, any_creating = false, any_tearing = false;
    size_t error_count = 0, total = 0;
    for (std::map<std::string, TopicRouteState>::const_iterator it =
                 route.topics.begin();
         it != route.topics.end(); ++it) {
        ++total;
        switch (it->second.topic_state) {
        case RouterRouteTopicState::TOPIC_FORWARDING:   any_forwarding = true; break;
        case RouterRouteTopicState::TOPIC_CREATING:     any_creating = true;   break;
        case RouterRouteTopicState::TOPIC_TEARING_DOWN: any_tearing = true;    break;
        case RouterRouteTopicState::TOPIC_ERROR:        ++error_count;         break;
        case RouterRouteTopicState::TOPIC_IDLE:                                break;
        }
    }

    // Route-wide failure, or ALL topics errored (D11 error containment boundary).
    if (route.route_error || (total > 0 && error_count == total)) {
        return RouterRouteOperationalState::ROUTE_ERROR;
    }
    if (any_forwarding) {
        return RouterRouteOperationalState::ROUTE_ENABLED;
    }
    if (any_creating) {
        return RouterRouteOperationalState::ROUTE_RESOLVING;
    }
    if (any_tearing) {
        return RouterRouteOperationalState::ROUTE_DEGRADED;
    }
    // Quiescent: all IDLE, or IDLE/ERROR mix with no activity (D11).
    if (!route.desired.desired_enabled) {
        return RouterRouteOperationalState::ROUTE_DISABLED;
    }
    return RouterRouteOperationalState::ROUTE_WAITING_FOR_DISCOVERY;
}

std::string route_fingerprint(const RouteState &route) {
    std::ostringstream os;
    os << static_cast<int>(derive_operational(route)) << '|'
       << static_cast<int>(derive_route_discovery(route)) << '|'
       << (route.desired.desired_enabled ? 1 : 0) << '|'
       << (route.route_error ? 1 : 0) << '|' << route.last_error;
    for (size_t i = 0; i < route.desired.topics.size(); ++i) {
        const RouterRouteTopicSpec &spec = route.desired.topics.at(i);
        std::map<std::string, TopicRouteState>::const_iterator t =
                route.topics.find(spec.name);
        if (t == route.topics.end()) {
            continue;
        }
        os << '|' << spec.name << ':'
           << static_cast<int>(t->second.topic_state) << ':'
           << static_cast<int>(derive_topic_discovery(t->second, spec)) << ':'
           << t->second.last_error;
    }
    return os.str();
}

} // namespace router
