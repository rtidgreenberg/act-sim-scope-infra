// TypeResolver.hpp — type construction readiness for both route lanes (D31/D35; D64/D70).
//
// Lanes:
//   - Generated-type lane (Phase 3): a registry of locally supported compiled type names
//     (the router's own admin types, and any generated fast-path route type).
//   - DynamicData lane (the default for forwarded app payloads). Since 7c (D64/D70) the
//     route topic's DynamicType is learned FROM THE WIRE: DiscoveryDispatcher reads the
//     COMPLETE type object inline off builtin discovery (`data->type()`, the rti_view
//     model — spike-proven, spikes/type_discovery/) and registers it here per topic,
//     first-learned-wins per topic per process (D66). The router carries NO local type
//     objects for forwarded payloads; the XML loader below remains only as a legacy/
//     debug path and is not on the route-build path.
//
// Thread-safety: register_discovered_type is called from AsyncWaitSet worker threads
// (discovery dispatch); topic_type/has_topic_type from the controller strand (entity
// builds) — the topic-type map is mutex-guarded.

#pragma once

#include <dds/dds.hpp>

#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string>

namespace router {

class TypeResolver {
public:
    // --- Wire-learned per-topic types (7c, D64/D70) ---

    // Register a topic's discovery-learned DynamicType. First-learned-wins (D66):
    // returns true iff this call established the topic's type; a later (possibly
    // different) type object for the same topic is ignored and returns false.
    bool register_discovered_type(const std::string &topic_name,
                                  const dds::core::xtypes::DynamicType &type) {
        std::lock_guard<std::mutex> lk(mutex_);
        if (topic_types_.find(topic_name) != topic_types_.end()) {
            return false;
        }
        topic_types_.insert(std::make_pair(topic_name, type));
        return true;
    }

    bool has_topic_type(const std::string &topic_name) const {
        std::lock_guard<std::mutex> lk(mutex_);
        return topic_types_.find(topic_name) != topic_types_.end();
    }

    // The wire-learned DynamicType for this topic (a cheap ref-counted wrapper copy).
    // Throws if the topic's type has not been learned — the controller's TypeResolved
    // gate should make that unreachable on the build path (defensive).
    dds::core::xtypes::DynamicType topic_type(const std::string &topic_name) const {
        std::lock_guard<std::mutex> lk(mutex_);
        std::map<std::string, dds::core::xtypes::DynamicType>::const_iterator it =
                topic_types_.find(topic_name);
        if (it == topic_types_.end()) {
            throw std::runtime_error("no wire-learned type for topic '" + topic_name
                                     + "' (D70: types come from discovery)");
        }
        return it->second;
    }
    // --- Generated-type lane (Phase 3) ---
    void register_type(const std::string &type_name) { known_.insert(type_name); }
    bool is_constructible(const std::string &type_name) const {
        return known_.find(type_name) != known_.end();
    }

    // --- Legacy XML lane (pre-7c; not on the route-build path) ---
    // Load a DDS-type XML (rooted <dds><types>). Throws on parse/open failure.
    void load_types(const std::string &xml_path) {
        provider_ = std::make_shared<dds::core::QosProvider>(xml_path);
    }

    bool has_dynamic_type(const std::string &type_name) const {
        try {
            get_dynamic_type(type_name);
            return true;
        } catch (const std::exception &) {
            return false;
        }
    }

    // Resolve a DynamicType by registered name. Throws if no XML is loaded or the name
    // is unknown.
    const dds::core::xtypes::DynamicType &get_dynamic_type(
            const std::string &type_name) const {
        if (!provider_) {
            throw std::runtime_error("no DDS-type XML loaded (wanted type: "
                                     + type_name + ")");
        }
        return provider_->extensions().type(type_name);
    }

private:
    std::set<std::string> known_;
    std::shared_ptr<dds::core::QosProvider> provider_;
    mutable std::mutex mutex_;
    std::map<std::string, dds::core::xtypes::DynamicType> topic_types_;
};

} // namespace router
