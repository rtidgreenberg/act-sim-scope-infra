// test_discovery_smoke.cxx - Phase 2 evidence: real Connext builtin discovery readers
// can observe generated admin endpoints and surface the facts the controller will consume.

#include "RouterAdminTypes.hpp"

#include <dds/dds.hpp>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iterator>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#include <unistd.h>

namespace {

static int g_failures = 0;

#define CHECK(cond)                                                                      \
    do {                                                                                 \
        if (!(cond)) {                                                                   \
            std::fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);         \
            ++g_failures;                                                                \
        }                                                                                \
    } while (0)

std::string key_to_string(const dds::topic::BuiltinTopicKey &key) {
    const std::vector<uint32_t> &value = key.value();
    std::ostringstream out;
    for (size_t i = 0; i < value.size(); ++i) {
        if (i != 0) {
            out << ".";
        }
        out << value.at(i);
    }
    return out.str();
}

std::vector<uint8_t> bytes(const std::string &text) {
    return std::vector<uint8_t>(text.begin(), text.end());
}

std::string text_from_bytes(const dds::core::policy::UserData &user_data) {
    const std::vector<uint8_t> &value = user_data.value();
    return std::string(value.begin(), value.end());
}

dds::domain::qos::DomainParticipantQos participant_qos(const std::string &tag) {
    dds::domain::qos::DomainParticipantQos qos =
            dds::domain::DomainParticipant::default_participant_qos();
    qos << rti::core::policy::TransportBuiltin::UDPv4();
    dds::core::ByteSeq user_data = bytes(tag);
    qos << dds::core::policy::UserData(user_data);
    return qos;
}

template <typename T>
dds::sub::DataReader<T> find_builtin_reader(dds::sub::Subscriber &subscriber,
                                            const std::string &topic_name) {
    std::vector<dds::sub::DataReader<T> > readers;
    dds::sub::find<dds::sub::DataReader<T> >(subscriber, topic_name,
                                             std::back_inserter(readers));
    if (readers.empty()) {
        throw std::runtime_error("builtin reader not found: " + topic_name);
    }
    return readers.at(0);
}

struct SeenFacts {
    bool tagged_participant = false;
    std::string tagged_participant_key;
    std::set<std::string> status_publication_participant_keys;
    std::set<std::string> status_subscription_participant_keys;
};

bool discovered_own_status_endpoints(const SeenFacts &seen) {
    return seen.tagged_participant
            && !seen.tagged_participant_key.empty()
            && seen.status_publication_participant_keys.count(seen.tagged_participant_key) != 0
            && seen.status_subscription_participant_keys.count(seen.tagged_participant_key) != 0;
}

void pump_discovery(
    dds::sub::DataReader<dds::topic::ParticipantBuiltinTopicData> &participant_reader,
    dds::sub::DataReader<dds::topic::PublicationBuiltinTopicData> &publication_reader,
    dds::sub::DataReader<dds::topic::SubscriptionBuiltinTopicData> &subscription_reader,
        SeenFacts &seen) {
    const std::string expected_tag = "act.router=Platform_30/control-platform";

    dds::sub::LoanedSamples<dds::topic::ParticipantBuiltinTopicData> participants =
            participant_reader.take();
    for (dds::sub::LoanedSamples<dds::topic::ParticipantBuiltinTopicData>::const_iterator it =
                 participants.begin();
         it != participants.end(); ++it) {
        if (!it->info().valid()) {
            continue;
        }
        const dds::topic::ParticipantBuiltinTopicData &data = it->data();
        if (text_from_bytes(data.user_data()) == expected_tag) {
            seen.tagged_participant = true;
            seen.tagged_participant_key = key_to_string(data.key());
        }
    }

    dds::sub::LoanedSamples<dds::topic::PublicationBuiltinTopicData> publications =
            publication_reader.take();
    for (dds::sub::LoanedSamples<dds::topic::PublicationBuiltinTopicData>::const_iterator it =
                 publications.begin();
         it != publications.end(); ++it) {
        if (!it->info().valid()) {
            continue;
        }
        const dds::topic::PublicationBuiltinTopicData &data = it->data();
        if (data.topic_name() == "ActRouterStatus" && data.type_name() == "RouterStatus") {
            seen.status_publication_participant_keys.insert(key_to_string(data.participant_key()));
        }
    }

    dds::sub::LoanedSamples<dds::topic::SubscriptionBuiltinTopicData> subscriptions =
            subscription_reader.take();
    for (dds::sub::LoanedSamples<dds::topic::SubscriptionBuiltinTopicData>::const_iterator it =
                 subscriptions.begin();
         it != subscriptions.end(); ++it) {
        if (!it->info().valid()) {
            continue;
        }
        const dds::topic::SubscriptionBuiltinTopicData &data = it->data();
        if (data.topic_name() == "ActRouterStatus" && data.type_name() == "RouterStatus") {
            seen.status_subscription_participant_keys.insert(key_to_string(data.participant_key()));
        }
    }
}

int domain_from_args(int argc, char **argv) {
    int domain = 200 + static_cast<int>(getpid() % 30);
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--domain" && i + 1 < argc) {
            domain = std::atoi(argv[++i]);
        }
    }
    return domain;
}

} // namespace

int main(int argc, char **argv) {
    try {
        const int domain = domain_from_args(argc, argv);

        dds::domain::DomainParticipant observer(
                domain, participant_qos("act.router=Observer/discovery-smoke"));
        dds::sub::Subscriber builtin_subscriber = dds::sub::builtin_subscriber(observer);

        dds::sub::DataReader<dds::topic::ParticipantBuiltinTopicData> participant_reader =
                find_builtin_reader<dds::topic::ParticipantBuiltinTopicData>(
                        builtin_subscriber, dds::topic::participant_topic_name());
        dds::sub::DataReader<dds::topic::PublicationBuiltinTopicData> publication_reader =
                find_builtin_reader<dds::topic::PublicationBuiltinTopicData>(
                        builtin_subscriber, dds::topic::publication_topic_name());
        dds::sub::DataReader<dds::topic::SubscriptionBuiltinTopicData> subscription_reader =
                find_builtin_reader<dds::topic::SubscriptionBuiltinTopicData>(
                        builtin_subscriber, dds::topic::subscription_topic_name());

        dds::sub::cond::ReadCondition participant_condition(
                participant_reader, dds::sub::status::DataState::any());
        dds::sub::cond::ReadCondition publication_condition(
                publication_reader, dds::sub::status::DataState::any());
        dds::sub::cond::ReadCondition subscription_condition(
                subscription_reader, dds::sub::status::DataState::any());
        dds::core::cond::WaitSet waitset;
        waitset += participant_condition;
        waitset += publication_condition;
        waitset += subscription_condition;

        dds::domain::DomainParticipant tagged(
                domain, participant_qos("act.router=Platform_30/control-platform"));
        dds::topic::Topic<RouterStatus> status_topic(tagged, "ActRouterStatus");
        dds::pub::DataWriter<RouterStatus> status_writer(
                dds::pub::Publisher(tagged), status_topic);
        dds::sub::DataReader<RouterStatus> status_reader(
                dds::sub::Subscriber(tagged), status_topic);
        (void) status_writer;
        (void) status_reader;

        SeenFacts seen;
        for (int i = 0; i < 20; ++i) {
            try {
                waitset.wait(dds::core::Duration::from_millisecs(250));
            } catch (const dds::core::TimeoutError &) {
            }
            pump_discovery(participant_reader, publication_reader, subscription_reader, seen);
            if (discovered_own_status_endpoints(seen)) {
                break;
            }
        }

        CHECK(seen.tagged_participant);
        CHECK(!seen.tagged_participant_key.empty());
        CHECK(seen.status_publication_participant_keys.count(seen.tagged_participant_key) != 0);
        CHECK(seen.status_subscription_participant_keys.count(seen.tagged_participant_key) != 0);

        if (g_failures == 0) {
            std::printf("test_discovery_smoke: OK domain=%d participant=%s\n",
                        domain, seen.tagged_participant_key.c_str());
            return 0;
        }
        std::fprintf(stderr, "test_discovery_smoke: %d failure(s)\n", g_failures);
        return 1;
    } catch (const std::exception &e) {
        std::fprintf(stderr, "test_discovery_smoke: exception: %s\n", e.what());
        return 1;
    }
}