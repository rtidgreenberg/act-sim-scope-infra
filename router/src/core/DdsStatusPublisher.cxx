// DdsStatusPublisher.cxx — RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1) status writer.

#include "DdsStatusPublisher.hpp"
#include "Log.hpp"

#include "RouterAdminTypes.hpp"

namespace router {

namespace {

dds::pub::qos::DataWriterQos make_writer_qos(const dds::pub::Publisher &publisher) {
    dds::pub::qos::DataWriterQos qos = publisher.default_datawriter_qos();
    qos << dds::core::policy::Reliability::Reliable();
    qos << dds::core::policy::Durability::TransientLocal();
    qos << dds::core::policy::History::KeepLast(1);
    return qos;
}

} // namespace

DdsStatusPublisher::DdsStatusPublisher(dds::domain::DomainParticipant participant,
                                       const std::string &topic_name)
        : publisher_(participant),
            topic_(participant, topic_name),
            writer_(publisher_, topic_, make_writer_qos(publisher_)) {
    Log::info("status_publisher_ready", {{"topic", topic_name}});
}

void DdsStatusPublisher::publish(std::shared_ptr<const RouterStatus> snapshot) {
    try {
        writer_.write(*snapshot);
    } catch (const std::exception &e) {
        Log::warn("status_publish_failed", {{"error", e.what()}});
    }
}

void DdsStatusPublisher::publish_ack(const RouterCommandAck &) {
    // Phase 6: command-ack writer not yet wired in.
}

} // namespace router
