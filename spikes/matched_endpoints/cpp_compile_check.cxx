// cpp_compile_check.cxx — D64 readiness: confirm the C++11 call surface the
// create-and-observe refactor depends on, by COMPILE (the MCP is not trusted — see
// docs/connext-ai-issues/). The Python spike (matched_endpoints_spike.py) proved the
// behavior; this file proves the Modern C++ API shape on this install (7.7.0,
// x64Linux4gcc7.3.0). Compile-only, never linked or run:
//
//   c++ -std=gnu++11 -DRTI_64BIT -DRTI_LINUX -DRTI_STATIC -DRTI_UNIX \
//       -isystem $NDDSHOME/include -isystem $NDDSHOME/include/ndds \
//       -isystem $NDDSHOME/include/ndds/hpp \
//       -fsyntax-only spikes/matched_endpoints/cpp_compile_check.cxx
//
// Surfaces checked (each tagged CHECK-n below):
//   1. dds::sub::matched_publications(reader)  -> dds::core::InstanceHandleSeq
//      dds::pub::matched_subscriptions(writer) -> dds::core::InstanceHandleSeq
//   2. reader.subscription_matched_status() / writer.publication_matched_status()
//      (.current_count()/.current_count_change()/.total_count()/last handle)
//   3. StatusCondition with StatusMask::subscription_matched()/publication_matched(),
//      attachable to rti::core::cond::AsyncWaitSet (the Phase 5 pattern reused)
//   4. dds::sub::matched_publication_data<T>(reader, handle) — builtin data for a
//      matched handle (near-miss diagnosis stays on the builtin readers, but the
//      matched-handle lookup is useful for status reasons)
//   5. PublicationBuiltinTopicData->type() -> optional<DynamicType> — the C++
//      equivalent of the Python spike's `data.type` (7c wire-learned type), and
//      building DynamicData entities from it.

#include <dds/dds.hpp>
#include <dds/sub/discovery.hpp>
#include <dds/pub/discovery.hpp>
#include <dds/core/cond/StatusCondition.hpp>
#include <dds/core/xtypes/DynamicType.hpp>
#include <dds/core/xtypes/StructType.hpp>
#include <rti/core/cond/AsyncWaitSet.hpp>

namespace check {

using dds::core::xtypes::DynamicData;

void surface(dds::sub::DataReader<DynamicData> reader,
             dds::pub::DataWriter<DynamicData> writer,
             dds::domain::DomainParticipant participant,
             rti::core::cond::AsyncWaitSet aws) {
    // CHECK-1: matched sets straight off the entities.
    dds::core::InstanceHandleSeq pubs = dds::sub::matched_publications(reader);
    dds::core::InstanceHandleSeq subs = dds::pub::matched_subscriptions(writer);
    const bool reader_unmatched = pubs.empty();
    const bool writer_unmatched = subs.empty();
    (void)reader_unmatched;
    (void)writer_unmatched;

    // CHECK-2: matched statuses (counts + last handle) for the status-reason surface.
    dds::core::status::SubscriptionMatchedStatus sm =
            reader.subscription_matched_status();
    dds::core::status::PublicationMatchedStatus pm =
            writer.publication_matched_status();
    (void)sm.current_count();
    (void)sm.current_count_change();
    (void)sm.total_count();
    (void)sm.last_publication_handle();
    (void)pm.current_count();
    (void)pm.current_count_change();
    (void)pm.last_subscription_handle();

    // CHECK-3: StatusCondition-driven observation on the AsyncWaitSet (Phase 5 pattern).
    dds::core::cond::StatusCondition reader_cond(reader);
    reader_cond.enabled_statuses(dds::core::status::StatusMask::subscription_matched());
    dds::core::cond::StatusCondition writer_cond(writer);
    writer_cond.enabled_statuses(dds::core::status::StatusMask::publication_matched());
    aws.attach_condition(reader_cond);
    aws.attach_condition(writer_cond);
    aws.detach_condition(reader_cond);
    aws.detach_condition(writer_cond);

    // CHECK-4: builtin data for a matched handle.
    if (!pubs.empty()) {
        dds::topic::PublicationBuiltinTopicData pub_data =
                dds::sub::matched_publication_data(reader, pubs[0]);
        (void)pub_data.topic_name();
        (void)pub_data.type_name();

        // CHECK-5: the wire-learned type object (Python spike's `data.type`) and
        // DynamicData entities built from it.
        const dds::core::optional<dds::core::xtypes::DynamicType> &wire_type =
                pub_data->type();
        if (wire_type.is_set()) {
            const dds::core::xtypes::StructType &st =
                    static_cast<const dds::core::xtypes::StructType &>(wire_type.get());
            dds::topic::Topic<DynamicData> topic(
                    participant, pub_data.topic_name(), st);
            dds::sub::DataReader<DynamicData> in(
                    dds::sub::Subscriber(participant), topic);
            dds::pub::DataWriter<DynamicData> out(
                    dds::pub::Publisher(participant), topic);
            (void)in;
            (void)out;
        }
    }
}

} // namespace check
