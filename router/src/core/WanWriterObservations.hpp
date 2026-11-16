#pragma once

#include "ActTypes.hpp"
#include "Log.hpp"
#include "WanStatsPoll.hpp"

#include <algorithm>
#include <chrono>
#include <stdexcept>

namespace router {

inline RouterWriterFilterObservation observe_wan_writer(
        const dds::pub::DataWriter<dds::core::xtypes::DynamicData>& writer) {
    RouterWriterFilterObservation observation;
    observation.topic_name = writer.topic().name();
    observation.observed_at_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();
    observation.poll_ok = true;
    try {
        auto readers = dds::pub::matched_subscriptions(writer);
        observation.matched_readers = static_cast<uint32_t>(readers.size());
        std::sort(readers.begin(), readers.end(), [](const dds::core::InstanceHandle& a,
                                                    const dds::core::InstanceHandle& b) {
            return wan_handle_key(a) < wan_handle_key(b);
        });
        for (const auto& handle : readers) {
            if (observation.reader_filters.size() == observation.reader_filters.max_size()) {
                observation.truncated = true;
                observation.poll_ok = false;
                observation.last_error = "matched reader filter observations exceed capacity";
                Log::warn("wan_filter_observations_truncated",
                          {{"topic", observation.topic_name},
                           {"matched_readers", std::to_string(readers.size())}});
                break;
            }
            RouterReaderFilterObservation reader;
            reader.reader_id = wan_handle_key(handle);
            try {
                const auto subscription = dds::pub::matched_subscription_data(writer, handle);
                const auto& filter = subscription->content_filter_property();
                const auto expression = filter.filter_expression();
                reader.has_filter = bool(expression);
                if (expression) {
                    if (expression->size() > 16384) {
                        throw std::runtime_error("reader filter expression exceeds observation capacity");
                    }
                    reader.expression = *expression;
                }
                for (const auto& parameter : filter.expression_parameters()) {
                    if (parameter.size() > 257) {
                        throw std::runtime_error("reader filter parameter exceeds observation capacity");
                    }
                    reader.parameters.push_back(parameter);
                }
                auto participant = writer.publisher().participant();
                const auto name = participant.contains_entity(handle)
                    ? participant.qos().policy<rti::core::policy::EntityName>().name()
                    : rti::pub::matched_subscription_participant_data(writer, handle)
                          ->participant_name().name();
                if (name.is_set()) reader.peer_router = name.get();
            } catch (const std::exception& error) {
                reader.last_error = "Reader discovery query failed; see router log.";
                observation.poll_ok = false;
                Log::warn("wan_reader_filter_poll_failed",
                          {{"topic", observation.topic_name}, {"reader", reader.reader_id},
                           {"error", error.what()}});
            }
            observation.reader_filters.push_back(std::move(reader));
        }
    } catch (const std::exception& error) {
        observation.poll_ok = false;
        observation.last_error = "Writer discovery query failed; see router log.";
        Log::warn("wan_writer_filter_poll_failed",
                  {{"topic", observation.topic_name}, {"error", error.what()}});
    }
    return observation;
}

} // namespace router
