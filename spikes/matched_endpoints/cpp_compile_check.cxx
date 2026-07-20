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
#include <rti/pub/AcknowledgmentInfo.hpp> // Phase 9: app-ack payload (D81)
// The rti::pub/sub::matched_*_participant_data() extensions ride in through the
// dds::pub/sub::discovery.hpp already included above (they include discoveryImpl).

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

// ---------------------------------------------------------------------------
// Phase 9 (Link-Metrics Capture, D14/D18/D81) call surface. The Python spike
// spikes/link_probe/ proved the app-ack behavior; this proves the Modern C++
// API shape on this install (7.7.0). Compile-only. Tagged CHECK-P9-n:
//   P9-1 per-matched-endpoint reliable-protocol statuses, keyed by handle
//        (writer: matched_subscription_datawriter_protocol_status,
//         reader: matched_publication_datareader_protocol_status) — the D14
//        counter sources; EventCount64.total() feeds self-computed deltas.
//   P9-2 discovery-DB peer attribution (D81 item 1): a matched-endpoint handle
//        -> ParticipantBuiltinTopicData -> participant_name().name() (the
//        D74/D79 identity). rti::pub::matched_subscription_participant_data /
//        rti::sub::matched_publication_participant_data.
//   P9-3 app-ack RTT (D81 item 3): a DataWriterListener overriding ONLY
//        on_application_acknowledgment; AcknowledgmentInfo carries the
//        subscription_handle (attribution) + sample_identity().sequence_number()
//        (the 1-based RTPS seq the collector joins send-times by).
//   P9-4 probe QoS knobs: Reliability.acknowledgment_kind(APPLICATION_AUTO),
//        rtps_reliable_writer heartbeats_per_max_samples + fixed send window,
//        rtps_reliable_reader zero heartbeat_response_delay.
// ---------------------------------------------------------------------------

// P9-3: the containment-minimal probe listener — the codebase's first listener,
// on the probe writer alone, overriding exactly one callback.
template <typename T>
class ProbeAckListener : public dds::pub::NoOpDataWriterListener<T> {
public:
    void on_application_acknowledgment(
            dds::pub::DataWriter<T> &,
            const rti::pub::AcknowledgmentInfo &info) override {
        const dds::core::InstanceHandle sub = info.subscription_handle();
        const rti::core::SampleIdentity id = info.sample_identity();
        const long long rtps_seq = id.sequence_number().value(); // 1-based (spike)
        (void)sub;
        (void)rtps_seq;
    }
};

void phase9_surface(dds::sub::DataReader<DynamicData> wan_reader,
                    dds::pub::DataWriter<DynamicData> wan_writer,
                    dds::pub::DataWriter<DynamicData> probe_writer) {
    // P9-1 + P9-2: writer-side rollup (this router's WAN writer -> peers' readers).
    dds::core::InstanceHandleSeq subs = dds::pub::matched_subscriptions(wan_writer);
    for (auto it = subs.begin(); it != subs.end(); ++it) {
        rti::core::status::DataWriterProtocolStatus wps =
                wan_writer->matched_subscription_datawriter_protocol_status(*it);
        (void)wps.pushed_sample_count().total();
        (void)wps.pulled_sample_count().total();
        (void)wps.sent_heartbeat_count().total();
        (void)wps.received_nack_count().total();
        (void)wps.received_nack_fragment_count();
        (void)wps.pushed_fragment_bytes();
        (void)wps.pulled_fragment_bytes();
        (void)wps.rejected_sample_count().total();
        // NOTE (verified here): the per-matched-endpoint DataWriterProtocolStatus does
        // NOT expose unacknowledged_sample_count — that gauge lives on the writer-global
        // ReliableWriterCacheChangedStatus, which cannot be attributed per peer. The
        // per-peer unacked/window gauges in the IDL sketch stay 0 in Phase 9 capture
        // (they feed the future correlation experiment, not this phase's evidence).

        dds::topic::ParticipantBuiltinTopicData pd =
                rti::pub::matched_subscription_participant_data(wan_writer, *it);
        rti::core::optional_value<std::string> name = pd->participant_name().name();
        (void)name;
    }

    // P9-1 + P9-2: reader-side rollup (peers' writers -> this router's WAN reader).
    dds::core::InstanceHandleSeq pubs = dds::sub::matched_publications(wan_reader);
    for (auto it = pubs.begin(); it != pubs.end(); ++it) {
        rti::core::status::DataReaderProtocolStatus rps =
                wan_reader->matched_publication_datareader_protocol_status(*it);
        (void)rps.received_sample_count().total();
        (void)rps.duplicate_sample_count().total();
        (void)rps.received_heartbeat_count().total();
        (void)rps.sent_nack_count().total();
        (void)rps.rejected_sample_count().total();
        (void)rps.out_of_range_rejected_sample_count();
        (void)rps.uncommitted_sample_count(); // gauge

        dds::topic::ParticipantBuiltinTopicData pd =
                rti::sub::matched_publication_participant_data(wan_reader, *it);
        rti::core::optional_value<std::string> name = pd->participant_name().name();
        (void)name;
    }

    // P9-3: install/clear the app-ack listener on the probe writer alone. The Modern
    // C++ set_listener takes a shared_ptr (the writer shares ownership) + a mask; clear
    // with a null shared_ptr before closing the writer (D31/D32 teardown discipline).
    std::shared_ptr<ProbeAckListener<DynamicData>> listener(
            new ProbeAckListener<DynamicData>());
    probe_writer.set_listener(
            listener,
            dds::core::status::StatusMask::datawriter_application_acknowledgment());
    probe_writer.set_listener(nullptr);

    // P9-4: the probe QoS shape (link-health.md; RELIABLE + APPLICATION_AUTO +
    // fixed send window with per-sample piggyback HB; reader zero ack delay). The
    // acknowledgment_kind extension is reached through the policy delegate (operator->,
    // same idiom as data->participant_name()).
    dds::pub::qos::DataWriterQos wqos;
    dds::core::policy::Reliability rel = dds::core::policy::Reliability::Reliable();
    rel->acknowledgment_kind(rti::core::policy::AcknowledgmentKind::APPLICATION_AUTO);
    wqos << rel;
    rti::core::policy::DataWriterProtocol dwp =
            wqos.policy<rti::core::policy::DataWriterProtocol>();
    dwp.rtps_reliable_writer().min_send_window_size(1);
    dwp.rtps_reliable_writer().max_send_window_size(1);
    dwp.rtps_reliable_writer().heartbeats_per_max_samples(1);
    wqos.policy(dwp);

    dds::sub::qos::DataReaderQos rqos;
    rti::core::policy::DataReaderProtocol drp =
            rqos.policy<rti::core::policy::DataReaderProtocol>();
    drp.rtps_reliable_reader().min_heartbeat_response_delay(
            dds::core::Duration::zero());
    drp.rtps_reliable_reader().max_heartbeat_response_delay(
            dds::core::Duration::zero());
    rqos.policy(drp);
}

} // namespace check
