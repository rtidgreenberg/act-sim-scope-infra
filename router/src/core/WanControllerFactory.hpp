#pragma once

#include "DynamicRouteFactory.hpp"
#include "WanInterests.hpp"

namespace router {

class WanControllerFactory : public IEntityFactory {
public:
    WanControllerFactory(ParticipantRegistry& registry, TypeResolver& types, QosResolver& qos,
                         AsyncWaitSetDispatcher& dispatcher, dds::core::QosProvider model,
                         const std::string& wan, const RouterIdentityInfo& identity);
    void set_controller(RouterController* controller);
    bool uses_wan_audiences() const override { return true; }
    bool validate_route_update(const RouterRouteSpec&, std::string&) const override;
    void create_topic_entities(const RouteView&, const std::string&, uint64_t) override;
    void teardown_topic_entities(const std::string&, const std::string&, uint64_t) override;
    void abort_topic_creation(const std::string&, const std::string&, uint64_t) override;
    bool update_route_partitions(const std::string&, const std::string&,
                                const std::vector<std::string>&,
                                const std::vector<std::string>&) override;
    bool apply_participant_partition(const std::string&, const std::vector<std::string>&) override;
    uint64_t forwarded_count(const std::string&, const std::string&) const override;
    void shutdown();
    WanAdapterFactory& adapters() { return *adapters_; }

private:
    using Key = std::pair<std::string, std::string>;
    struct Live {
        uint64_t generation;
        bool source;
        WanInterest interest;
        WanBindingStatus status;
    };
    bool on_wan(const RouterRouteSpec&) const;
    WanInterest interest(const RouterRouteSpec&, const std::string&) const;
    WanAdapterFactory::MetadataProvider provider(const WanInterest&, uint64_t) const;
    void refresh_interests();
    void remove(const Key&);
    void report_error(const Key&, uint64_t, const std::string&);
    void poll_status(const Key&) const;

    ParticipantRegistry& registry_;
    TypeResolver& types_;
    QosResolver& qos_;
    dds::core::QosProvider model_;
    RouterIdentityInfo identity_;
    std::string wan_;
    DynamicRouteFactory legacy_;
    std::unique_ptr<WanAdapterFactory> adapters_;
    RouterController* controller_ = nullptr;
    mutable std::map<Key, Live> live_;
    uint64_t membership_revision_ = 0;
};

} // namespace router
