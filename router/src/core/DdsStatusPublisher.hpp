// DdsStatusPublisher.hpp — real IStatusPublisher backed by Connext DataWriters.
//
// Creates a RouterStatus DataWriter on the supplied participant with
// RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1) QoS (D26), and — as of Phase 6 slice 6a — a
// RouterCommandAck DataWriter with RELIABLE + VOLATILE + KEEP_LAST(16) QoS (D48). The
// controller drives both seams (publish on state change, publish_ack on each processed
// command); this class owns the wire.

#pragma once

#include "Interfaces.hpp"

#include "ActTypes.hpp"

#include <dds/dds.hpp>

#include <memory>
#include <string>

namespace router {

class DdsStatusPublisher : public IStatusPublisher {
public:
    // participant may be disabled at construction (D52 disabled startup): the writers are
    // then created disabled and enabled with the participant. publish()/publish_ack()
    // before enable is a no-op (see the NotEnabledError branch); router_main re-publishes
    // status after enable. status_topic is the RouterStatus topic (e.g. "ActRouterStatus");
    // ack_topic defaults to the command-status.md RouterCommandAck topic name.
    DdsStatusPublisher(dds::domain::DomainParticipant participant,
                       const std::string &status_topic,
                       const std::string &ack_topic = "ActRouterCommandAck");

    void publish(std::shared_ptr<const RouterStatus> snapshot) override;
    void publish_ack(const RouterCommandAck &ack) override;

private:
    dds::pub::Publisher publisher_;
    dds::topic::Topic<RouterStatus> topic_;
    dds::pub::DataWriter<RouterStatus> writer_;
    dds::topic::Topic<RouterCommandAck> ack_topic_;
    dds::pub::DataWriter<RouterCommandAck> ack_writer_;
};

} // namespace router
