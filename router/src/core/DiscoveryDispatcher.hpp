// DiscoveryDispatcher.hpp — translates Connext builtin discovery events into
// RouterController events (D30: participant table only, no endpoint cache).
//
// Attaches ReadConditions on each participant's builtin publication, subscription,
// and participant readers to the injected AsyncWaitSet. Translates ALIVE/NOT_ALIVE
// samples into PublicationDiscovered / SubscriptionDiscovered / EndpointLost events
// posted to the RouterController.
//
// Participant table (GUID → act.router tag) is maintained from the participant
// builtin reader and is used for:
//   - D15 same-node ignore: publications from same-node routers are ignored via
//     dds::pub::ignore() and not forwarded to the controller.
//   - EndpointRecord.origin_router: the tag (or empty) is stamped on every event.
//
// has_type is set to !type_name.empty() — the generated-type fast path (D31):
// a non-empty type_name from the builtin data is sufficient for construction
// readiness in Phase 2.5 without a full TypeLookup round-trip.

#pragma once

#include "RouterController.hpp"
#include "ParticipantRegistry.hpp"

#include <rti/core/cond/AsyncWaitSet.hpp>
#include <dds/dds.hpp>

#include <atomic>
#include <map>
#include <mutex>
#include <string>
#include <vector>

namespace router {

class DiscoveryDispatcher {
public:
    // Attaches builtin reader conditions for every participant in registry, then
    // enables all participants (so no discovery events are missed before start).
    DiscoveryDispatcher(rti::core::cond::AsyncWaitSet &aws,
                        RouterController &controller,
                        ParticipantRegistry &registry,
                        const std::string &own_router_tag); // "act.router=<n>/<r>"

    // Detach all conditions from the AsyncWaitSet. Call before aws.stop() to
    // ensure the AWS drains safely before conditions and readers are torn down.
    void shutdown();

    ~DiscoveryDispatcher();

private:
    void attach_participant(dds::domain::DomainParticipant participant);

    struct PendingPublication;

    void on_participant(
        dds::sub::DataReader<dds::topic::ParticipantBuiltinTopicData> reader);
    void on_publication(
        dds::sub::DataReader<dds::topic::PublicationBuiltinTopicData> reader,
        dds::domain::DomainParticipant participant);
    void on_subscription(
        dds::sub::DataReader<dds::topic::SubscriptionBuiltinTopicData> reader);

    void handle_publication_sample(
        const dds::topic::PublicationBuiltinTopicData &data,
        const dds::core::InstanceHandle &handle,
        dds::domain::DomainParticipant participant,
        const std::string &origin_router,
        const std::string &participant_guid);

    // Identity captured from a valid endpoint discovery sample: the endpoint GUID the
    // controller keys on, plus the owning participant GUID so a participant loss can
    // purge and synthesize losses for its endpoints (D41).
    struct EndpointIdentity {
        std::string guid;
        std::string participant_guid;
    };

    static std::string format_key(const dds::topic::BuiltinTopicKey &key);
    static std::string handle_str(const dds::core::InstanceHandle &handle);
    // One shared pop-helper (D44): part_handle_guid_ uses EndpointIdentity too (its
    // participant_guid field is simply unused), so this is the only overload needed —
    // it previously had a near-identical std::map<string,string> twin purely because
    // the participant map was typed differently.
    std::string take_lost_guid(std::map<std::string, EndpointIdentity> &map,
                               const dds::core::InstanceHandle &handle);
    void drop_pending_publication(const dds::core::InstanceHandle &handle);
    std::vector<std::string> purge_participant_endpoints_locked(
            const std::string &participant_guid);
    static std::string extract_router_tag(const dds::core::policy::UserData &ud);
    bool is_same_node(const std::string &origin_router) const;

    rti::core::cond::AsyncWaitSet &aws_;
    RouterController &controller_;
    std::string own_router_tag_;

    // Participant GUID → act.router tag (D30; empty string = not a router participant).
    std::mutex table_mutex_;
    std::map<std::string, std::string> participant_table_;
    std::map<std::string, std::vector<PendingPublication>> pending_publications_;

    // Instance-handle → identity, captured from valid discovery samples so a native
    // NOT_ALIVE (which carries only the instance handle) can be translated back to the
    // GUID the controller keys on — for participants (D41) exactly as for endpoints
    // (D33). This is identity translation, not the endpoint-record cache D30 deleted —
    // no type/QoS/topic is stored. key_value() is unreliable once an instance is no
    // longer alive (validated against 7.7), so these maps are required. All three share
    // one map type (D44) — a participant's own entry simply leaves participant_guid unset.
    std::map<std::string, EndpointIdentity> part_handle_guid_;
    std::map<std::string, EndpointIdentity> pub_handle_guid_;
    std::map<std::string, EndpointIdentity> sub_handle_guid_;

    // Held ReadConditions (type-erased) — keep alive while attached to the AWS.
    std::vector<dds::core::cond::Condition> conditions_;
    std::atomic<bool> shut_down_;
};

} // namespace router
