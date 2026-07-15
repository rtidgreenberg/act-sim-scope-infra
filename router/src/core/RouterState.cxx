#include "RouterState.hpp"

#include <sstream>

namespace router {

bool output_uses_auto_qos(const RouterRouteSpec &spec) {
    return spec.output.writer_qos.empty();
}

DerivedWriterQos derive_writer_qos(const TopicRouteState &topic,
                                   const RouterRouteSpec &route_spec) {
    DerivedWriterQos d;
    d.derive = output_uses_auto_qos(route_spec);
    if (!d.derive) {
        return d;
    }
    for (std::map<std::string, MatchedEndpoint>::const_iterator it =
                 topic.matched_readers.begin();
         it != topic.matched_readers.end(); ++it) {
        const MatchedEndpoint &r = it->second;
        if (r.deadline_nanos < d.deadline_nanos) {
            d.deadline_nanos = r.deadline_nanos;
        }
        if (static_cast<int>(r.liveliness_kind)
            > static_cast<int>(d.liveliness_kind)) {
            d.liveliness_kind = r.liveliness_kind;
        }
        if (r.lease_nanos < d.lease_nanos) {
            d.lease_nanos = r.lease_nanos;
        }
    }
    return d;
}

const RouterRouteTopicSpec *find_topic_spec(const RouterRouteSpec &spec,
                                            const std::string &topic_name) {
    for (size_t i = 0; i < spec.topics.size(); ++i) {
        if (spec.topics.at(i).name == topic_name) {
            return &spec.topics.at(i);
        }
    }
    return nullptr;
}

RouterRouteDiscoveryState derive_topic_discovery(const TopicRouteState &topic,
                                                 const RouterRouteSpec &route_spec) {
    // D64/D66: DDS is the matching authority — the rollup reads the live build's own
    // matched counts, not the builtin-discovery record maps (demoted to derivation/
    // diagnosis input). A topic with no live entities has both counts 0 -> NONE.
    (void)route_spec; // counts are leg-symmetric; no alias-dependent gating remains
    bool input = topic.input_matched_count > 0;
    bool output = topic.output_matched_count > 0;
    if (input && output) {
        return RouterRouteDiscoveryState::DISCOVERY_READY;
    }
    if (input || output) {
        return RouterRouteDiscoveryState::DISCOVERY_PARTIAL;
    }
    return RouterRouteDiscoveryState::DISCOVERY_NONE;
}

std::string derive_match_reason(const TopicRouteState &topic) {
    if (topic.topic_state != RouterRouteTopicState::TOPIC_FORWARDING) {
        return std::string();
    }
    std::string reason;
    if (topic.input_matched_count == 0) {
        reason = "input_unmatched";
    }
    if (topic.output_matched_count == 0) {
        if (!reason.empty()) {
            reason += ',';
        }
        reason += "output_unmatched";
    }
    return reason;
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
        RouterRouteDiscoveryState d = derive_topic_discovery(t->second, route.desired);
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
       << (route.route_error ? 1 : 0) << '|' << route.last_error << '|'
       // Endpoint partitions ride the status desired spec and are runtime-mutable via
       // SET_ROUTE_PARTITION (7b/D69) — a change is externally visible D5 state.
       << route.desired.input.subscriber_partition << '|'
       << route.desired.output.publisher_partition;
    for (size_t i = 0; i < route.desired.topics.size(); ++i) {
        const RouterRouteTopicSpec &spec = route.desired.topics.at(i);
        std::map<std::string, TopicRouteState>::const_iterator t =
                route.topics.find(spec.name);
        if (t == route.topics.end()) {
            continue;
        }
        os << '|' << spec.name << ':'
           << static_cast<int>(t->second.topic_state) << ':'
           << static_cast<int>(derive_topic_discovery(t->second, route.desired)) << ':'
           << t->second.last_error << ':'
           << t->second.qos_warning << ':'
           << t->second.reader_qos_summary << ':'
           << t->second.writer_qos_summary << ':'
           // Matched counts are externally visible status fields (D66), so a count
           // change IS a D5 externally-visible change — unlike the sample counters,
           // which stay excluded.
           << t->second.input_matched_count << ':'
           << t->second.output_matched_count;
    }
    return os.str();
}

} // namespace router
