// DiscoveryDispatcher.cxx — builtin reader → controller event translation (D30).

#include "DiscoveryDispatcher.hpp"
#include "RouterEvents.hpp"
#include "Log.hpp"

#include <dds/pub/discovery.hpp>  // dds::pub::ignore (D15)
#include <dds/sub/ddssub.hpp>

#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace router {

namespace {

template <typename T>
dds::sub::DataReader<T> find_builtin_reader(dds::sub::Subscriber builtin_sub,
                                             const std::string &topic_name) {
    std::vector<dds::sub::DataReader<T>> found;
    dds::sub::find<dds::sub::DataReader<T>>(
        builtin_sub, topic_name, std::back_inserter(found));
    if (found.empty()) {
        throw std::runtime_error("builtin reader not found: " + topic_name);
    }
    return found.front();
}

} // namespace

DiscoveryDispatcher::DiscoveryDispatcher(rti::core::cond::AsyncWaitSet &aws,
                                         RouterController &controller,
                                         ParticipantRegistry &registry,
                                         const std::string &own_router_tag)
    : aws_(aws), controller_(controller), own_router_tag_(own_router_tag) {
    for (const std::string &name : registry.names()) {
        attach_participant(registry.get(name));
    }
    // Phase 3: call registry.enable_all() here for disabled-startup (D12).
    // Phase 2.5: participants are already enabled; omit the call.
}

DiscoveryDispatcher::~DiscoveryDispatcher() {
    if (!shut_down_) {
        shutdown();
    }
}

void DiscoveryDispatcher::shutdown() {
    if (shut_down_) return;
    shut_down_ = true;
    for (const auto &cond : conditions_) {
        try {
            aws_.detach_condition(cond);
        } catch (const std::exception &e) {
            Log::warn("detach_condition_failed", {{"error", e.what()}});
        }
    }
    conditions_.clear();
}

void DiscoveryDispatcher::attach_participant(dds::domain::DomainParticipant participant) {
    dds::sub::Subscriber builtin_sub = dds::sub::builtin_subscriber(participant);

    auto part_reader =
        find_builtin_reader<dds::topic::ParticipantBuiltinTopicData>(
            builtin_sub, dds::topic::participant_topic_name());
    auto pub_reader =
        find_builtin_reader<dds::topic::PublicationBuiltinTopicData>(
            builtin_sub, dds::topic::publication_topic_name());
    auto sub_reader =
        find_builtin_reader<dds::topic::SubscriptionBuiltinTopicData>(
            builtin_sub, dds::topic::subscription_topic_name());

    // Participant builtin reader condition
    {
        dds::sub::cond::ReadCondition cond(
            part_reader,
            dds::sub::status::DataState::any(),
            [this, part_reader]() mutable {
                on_participant(part_reader);
            });
        aws_.attach_condition(cond);
        conditions_.push_back(cond);
    }

    // Publication builtin reader condition
    {
        dds::sub::cond::ReadCondition cond(
            pub_reader,
            dds::sub::status::DataState::any(),
            [this, pub_reader, participant]() mutable {
                on_publication(pub_reader, participant);
            });
        aws_.attach_condition(cond);
        conditions_.push_back(cond);
    }

    // Subscription builtin reader condition
    {
        dds::sub::cond::ReadCondition cond(
            sub_reader,
            dds::sub::status::DataState::any(),
            [this, sub_reader]() mutable {
                on_subscription(sub_reader);
            });
        aws_.attach_condition(cond);
        conditions_.push_back(cond);
    }
}

void DiscoveryDispatcher::on_participant(
    dds::sub::DataReader<dds::topic::ParticipantBuiltinTopicData> reader) {
    auto samples = reader.take();
    for (auto it = samples.begin(); it != samples.end(); ++it) {
        // Only process ALIVE samples with data; skip key-only NOT_ALIVE samples.
        // Phase 2.5: stale table entries for NOT_ALIVE participants are harmless;
        // cleanup is a Phase 3 refinement.
        if (!it->info().valid()) continue;
        std::string guid = format_key(it->data().key());
        std::string tag = extract_router_tag(it->data().user_data());
        std::lock_guard<std::mutex> lk(table_mutex_);
        participant_table_[guid] = tag;
        if (!tag.empty()) {
            Log::debug("participant_router_tagged", {{"guid", guid}, {"tag", tag}});
        }
    }
}

void DiscoveryDispatcher::on_publication(
    dds::sub::DataReader<dds::topic::PublicationBuiltinTopicData> reader,
    dds::domain::DomainParticipant participant) {
    auto samples = reader.take();
    for (auto it = samples.begin(); it != samples.end(); ++it) {
        if (!it->info().valid()) {
            // NOT_ALIVE: key fields are still accessible in the key-only sample.
            try {
                std::string guid = format_key(it->data().key());
                Log::debug("endpoint_lost_pub", {{"guid", guid}});
                controller_.post(ControllerEvent::endpoint_lost(guid));
            } catch (...) {}
            continue;
        }

        const auto &data = it->data();
        std::string endpoint_guid = format_key(data.key());
        std::string participant_guid = format_key(data.participant_key());

        std::string origin_router;
        {
            std::lock_guard<std::mutex> lk(table_mutex_);
            auto pit = participant_table_.find(participant_guid);
            if (pit != participant_table_.end()) {
                origin_router = pit->second;
            }
        }

        // Same-node router publication: ignore via DDS API and skip (D15).
        if (is_same_node(origin_router)) {
            Log::info("endpoint_ignored_same_node",
                      {{"guid", endpoint_guid},
                       {"topic", data.topic_name()},
                       {"origin", origin_router}});
            try {
                dds::pub::ignore(participant, it->info().instance_handle());
            } catch (const std::exception &e) {
                Log::warn("ignore_failed", {{"error", e.what()}});
            }
            continue;
        }

        EndpointRecord rec;
        rec.guid          = endpoint_guid;
        rec.is_publication = true;
        rec.topic_name    = data.topic_name();
        rec.type_name     = data.type_name();
        rec.has_type      = !rec.type_name.empty(); // generated-type fast path (D31)
        rec.origin_router = origin_router;

        Log::debug("publication_discovered",
                   {{"guid", endpoint_guid},
                    {"topic", rec.topic_name},
                    {"type", rec.type_name},
                    {"has_type", rec.has_type ? "true" : "false"},
                    {"origin", origin_router}});
        controller_.post(ControllerEvent::publication_discovered(rec));
    }
}

void DiscoveryDispatcher::on_subscription(
    dds::sub::DataReader<dds::topic::SubscriptionBuiltinTopicData> reader) {
    auto samples = reader.take();
    for (auto it = samples.begin(); it != samples.end(); ++it) {
        if (!it->info().valid()) {
            try {
                std::string guid = format_key(it->data().key());
                Log::debug("endpoint_lost_sub", {{"guid", guid}});
                controller_.post(ControllerEvent::endpoint_lost(guid));
            } catch (...) {}
            continue;
        }

        const auto &data = it->data();
        EndpointRecord rec;
        rec.guid          = format_key(data.key());
        rec.is_publication = false;
        rec.topic_name    = data.topic_name();
        rec.type_name     = data.type_name();
        rec.has_type      = !rec.type_name.empty();
        rec.origin_router = ""; // subscriptions not used for same-node ignore

        Log::debug("subscription_discovered",
                   {{"guid", rec.guid}, {"topic", rec.topic_name}});
        controller_.post(ControllerEvent::subscription_discovered(rec));
    }
}

std::string DiscoveryDispatcher::format_key(const dds::topic::BuiltinTopicKey &key) {
    const auto &v = key.value();
    std::ostringstream os;
    os << std::hex << std::setfill('0');
    for (size_t i = 0; i < v.size(); ++i) {
        if (i != 0) os << ':';
        os << std::setw(8) << v[i];
    }
    return os.str();
}

std::string DiscoveryDispatcher::extract_router_tag(const dds::core::policy::UserData &ud) {
    const auto &v = ud.value();
    std::string text(v.begin(), v.end());
    return (text.find("act.router=") == 0) ? text : std::string();
}

bool DiscoveryDispatcher::is_same_node(const std::string &origin_router) const {
    if (origin_router.empty() || own_router_tag_.empty()) return false;
    // Both tags: "act.router=<node>/<router>" — compare the node component.
    auto node_of = [](const std::string &tag) {
        auto eq = tag.find('=');
        auto sl = tag.find('/', eq);
        if (eq == std::string::npos || sl == std::string::npos) return std::string();
        return tag.substr(eq + 1, sl - eq - 1);
    };
    return !node_of(origin_router).empty() &&
           node_of(origin_router) == node_of(own_router_tag_);
}

} // namespace router
