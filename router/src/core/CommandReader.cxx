// CommandReader.cxx — LAN admin command channel (Phase 6 slice 6a, D47/D48/D54).

#include "CommandReader.hpp"
#include "RouterEvents.hpp"
#include "Log.hpp"

#include <dds/sub/ddssub.hpp>

namespace router {

namespace {

// D47: the CFT parameters are this router's own identity strings, known here as plain
// std::string (not YAML scalars) — so they are wrapped in single quotes directly, with no
// D43 quote-vs-plain ambiguity to resolve.
std::string sql_quote(const std::string &s) { return "'" + s + "'"; }

dds::sub::qos::DataReaderQos make_reader_qos(const dds::sub::Subscriber &subscriber) {
    dds::sub::qos::DataReaderQos qos = subscriber.default_datareader_qos();
    qos << dds::core::policy::Reliability::Reliable();
    qos << dds::core::policy::Durability::Volatile();
    qos << dds::core::policy::History::KeepLast(16);
    return qos;
}

} // namespace

CommandReader::CommandReader(rti::core::cond::AsyncWaitSet &aws,
                             RouterController &controller,
                             dds::domain::DomainParticipant participant,
                             const std::string &target_node,
                             const std::string &target_router,
                             const std::string &command_topic)
        : aws_(aws),
          controller_(controller),
          subscriber_(participant),
          topic_(participant, command_topic),
          cft_(topic_, command_topic + "_target_cft",
               dds::topic::Filter("target_node = %0 AND target_router = %1",
                                  std::vector<std::string>{sql_quote(target_node),
                                                           sql_quote(target_router)})),
          reader_(subscriber_, cft_, make_reader_qos(subscriber_)),
          shut_down_(false) {
    dds::sub::DataReader<RouterCommand> reader = reader_;
    dds::sub::cond::ReadCondition cond(
        reader,
        dds::sub::status::DataState::any(),
        [this, reader]() mutable { on_command(reader); });
    aws_.attach_condition(cond);
    conditions_.push_back(cond);
    Log::info("command_reader_ready",
              {{"topic", command_topic},
               {"target_node", target_node},
               {"target_router", target_router}});
}

CommandReader::~CommandReader() {
    shutdown();
}

void CommandReader::shutdown() {
    if (shut_down_.exchange(true)) return;
    for (const auto &cond : conditions_) {
        try {
            aws_.detach_condition(cond);
        } catch (const std::exception &e) {
            Log::warn("command_detach_condition_failed", {{"error", e.what()}});
        }
    }
    conditions_.clear();
}

void CommandReader::on_command(dds::sub::DataReader<RouterCommand> reader) {
    auto samples = reader.take();
    for (auto it = samples.begin(); it != samples.end(); ++it) {
        if (!it->info().valid()) {
            continue; // NOT_ALIVE / dispose on a VOLATILE command topic — nothing to do
        }
        const RouterCommand &cmd = it->data();
        // The CFT already dropped commands for other routers, so every sample here is
        // addressed to us; hand it to the controller's state machine (D24).
        Log::info("command_received",
                  {{"command_id", cmd.command_id},
                   {"route", cmd.route_name}});
        controller_.post(ControllerEvent::command_received(cmd));
    }
}

} // namespace router
