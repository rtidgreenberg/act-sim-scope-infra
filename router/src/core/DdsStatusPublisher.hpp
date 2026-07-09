// DdsStatusPublisher.hpp — real IStatusPublisher backed by a Connext DataWriter.
//
// Creates a RouterStatus DataWriter on the supplied participant with
// RELIABLE + TRANSIENT_LOCAL + KEEP_LAST(1) QoS (D26).
//
// publish_ack() is a Phase 6 placeholder — command-ack plumbing arrives with the
// full admin channel. For now it is a no-op so the controller seam is satisfied.

#pragma once

#include "Interfaces.hpp"

#include <dds/dds.hpp>

#include <memory>
#include <string>

// RouterStatus and RouterCommandAck are generated types (no module namespace).
struct RouterStatus;
struct RouterCommandAck;

namespace router {

class DdsStatusPublisher : public IStatusPublisher {
public:
    // participant must be enabled before construction.
    // topic_name is the DDS topic to publish on (e.g. "ActRouterStatus").
    DdsStatusPublisher(dds::domain::DomainParticipant participant,
                       const std::string &topic_name);

    void publish(std::shared_ptr<const RouterStatus> snapshot) override;
    void publish_ack(const RouterCommandAck &ack) override; // Phase 6 no-op

private:
    dds::topic::Topic<RouterStatus> topic_;
    dds::pub::DataWriter<RouterStatus> writer_;
};

} // namespace router
