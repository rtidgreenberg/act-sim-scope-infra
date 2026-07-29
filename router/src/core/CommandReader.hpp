// CommandReader.hpp — LAN admin command channel (Phase 6 slice 6a, D54).
//
// Subscribes to RouterCommand on the router's LAN admin participant through a
// ContentFilteredTopic on (target_node, target_router) = this router's identity (D47),
// so a command addressed to another router never reaches the callback at all. Each
// received command is posted to the RouterController as a CommandReceived event (D24) —
// the controller runs the already-tested command state machine and publishes its ack via
// IStatusPublisher::publish_ack (DdsStatusPublisher). command_id idempotency stays in the
// controller (D4/D8), not here.
//
// QoS: RELIABLE + VOLATILE + KEEP_LAST(16) (D48). No durability — a resent command_id is
// replayed from the controller's ack cache, not DDS history.
//
// Startup ordering mirrors DiscoveryDispatcher under the D52 disabled-startup dance:
// construct this (attaching its ReadCondition to the AsyncWaitSet) BEFORE aws.start() and
// registry.enable_all(), so a command arriving right after enable is dispatched as a
// genuine post-start condition transition and never stranded on the edge-triggered AWS.

#pragma once

#include "RouterController.hpp"

#include "ActTypes.hpp"

#include <rti/core/cond/AsyncWaitSet.hpp>
#include <dds/dds.hpp>
#include <dds/topic/ContentFilteredTopic.hpp>

#include <atomic>
#include <string>
#include <vector>

namespace router {

class CommandReader {
public:
    // participant may be disabled at construction (D52); the CFT/reader/condition are then
    // created disabled and enabled by registry.enable_all(). command_topic defaults to the
    // command-status.md name.
    CommandReader(rti::core::cond::AsyncWaitSet &aws,
                  RouterController &controller,
                  dds::domain::DomainParticipant participant,
                  const std::string &target_node,
                  const std::string &target_router,
                  const std::string &command_topic = "ActRouterCommand");

    // Detach the ReadCondition from the AsyncWaitSet. Call before aws.stop().
    void shutdown();

    ~CommandReader();

private:
    void on_command(dds::sub::DataReader<RouterCommand> reader);

    rti::core::cond::AsyncWaitSet &aws_;
    RouterController &controller_;

    // Held so the entities outlive the attached condition (order matters on teardown:
    // detach the condition first via shutdown(), then these destruct).
    dds::sub::Subscriber subscriber_;
    dds::topic::Topic<RouterCommand> topic_;
    dds::topic::ContentFilteredTopic<RouterCommand> cft_;
    dds::sub::DataReader<RouterCommand> reader_;
    std::vector<dds::core::cond::Condition> conditions_;
    std::atomic<bool> shut_down_;
};

} // namespace router
