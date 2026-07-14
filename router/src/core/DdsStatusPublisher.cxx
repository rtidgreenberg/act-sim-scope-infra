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

// Command-ack writer QoS (D48): RELIABLE + VOLATILE + KEEP_LAST(16). No durability — a
// resent command_id gets its cached ack republished by the controller (D4), so DDS history
// replay is not needed.
dds::pub::qos::DataWriterQos make_ack_writer_qos(const dds::pub::Publisher &publisher) {
    dds::pub::qos::DataWriterQos qos = publisher.default_datawriter_qos();
    qos << dds::core::policy::Reliability::Reliable();
    qos << dds::core::policy::Durability::Volatile();
    qos << dds::core::policy::History::KeepLast(16);
    return qos;
}

} // namespace

DdsStatusPublisher::DdsStatusPublisher(dds::domain::DomainParticipant participant,
                                       const std::string &status_topic,
                                       const std::string &ack_topic)
        : publisher_(participant),
            topic_(participant, status_topic),
            writer_(publisher_, topic_, make_writer_qos(publisher_)),
            ack_topic_(participant, ack_topic),
            ack_writer_(publisher_, ack_topic_, make_ack_writer_qos(publisher_)) {
    Log::info("status_publisher_ready",
              {{"status_topic", status_topic}, {"ack_topic", ack_topic}});
}

void DdsStatusPublisher::publish(std::shared_ptr<const RouterStatus> snapshot) {
    try {
        writer_.write(*snapshot);
    } catch (const dds::core::NotEnabledError &) {
        // Expected during disabled startup (D52): the controller's constructor-time
        // snapshot is written before the participant/writer is enabled. router_main
        // re-publishes via RouterController::republish_status() after enable_all().
        Log::debug("status_publish_skipped_not_enabled", {});
    } catch (const std::exception &e) {
        Log::warn("status_publish_failed", {{"error", e.what()}});
    }
}

void DdsStatusPublisher::publish_ack(const RouterCommandAck &ack) {
    try {
        ack_writer_.write(ack);
    } catch (const dds::core::NotEnabledError &) {
        // Only reachable if an ack is produced before enable_all() (D52); commands can
        // only arrive after enable, so this is defensive symmetry with publish().
        Log::debug("ack_publish_skipped_not_enabled", {{"command_id", ack.command_id}});
    } catch (const std::exception &e) {
        Log::warn("ack_publish_failed",
                  {{"command_id", ack.command_id}, {"error", e.what()}});
    }
}

} // namespace router
