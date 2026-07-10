// TypeResolver.hpp — type construction readiness for both route lanes (D31/D35).
//
// Two lanes coexist (D35):
//   - Generated-type lane (Phase 3): a registry of locally supported compiled type names
//     (the router's own admin types, and any generated fast-path route type).
//   - DynamicData lane (Phase 4, the default for forwarded app payloads): types loaded
//     from a DDS-type XML at runtime via a QosProvider; the resolver hands out the
//     DynamicType by registered name so the DynamicData factory can build entities.
//
// The router is data-model-agnostic (D35): the forwarded data model is reference-only, so
// the XML this loads is a router-authored example, not any application's committed schema.

#pragma once

#include <dds/dds.hpp>

#include <memory>
#include <set>
#include <stdexcept>
#include <string>

namespace router {

class TypeResolver {
public:
    // --- Generated-type lane (Phase 3) ---
    void register_type(const std::string &type_name) { known_.insert(type_name); }
    bool is_constructible(const std::string &type_name) const {
        return known_.find(type_name) != known_.end();
    }

    // --- DynamicData lane (Phase 4) ---
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
};

} // namespace router
