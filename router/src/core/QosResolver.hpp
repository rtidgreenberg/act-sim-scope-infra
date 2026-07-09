// QosResolver.hpp — explicit-QoS minimum path (Phase 3, D31 step; D19).
//
// Maps a route topic's QoS alias to concrete reader/writer QoS. Phase 3 is the
// *explicit-QoS* path: history and resource limits come from the alias/defaults and are
// never derived from discovery (D19). LAN `auto` derivation from discovered endpoints is
// Phase 5 and is intentionally absent here.
//
// For the POC the only alias is the built-in default: RELIABLE + TRANSIENT_LOCAL +
// KEEP_LAST so a route reader created after the source writer still receives its recent
// history, and a downstream reader that joins after the route writer does too. Real
// XML-alias resolution (QosProvider profiles) lands with the config parser in Phase 4/5.

#pragma once

#include <dds/dds.hpp>
#include <dds/core/policy/CorePolicy.hpp>

#include <string>

namespace router {

class QosResolver {
public:
    dds::sub::qos::DataReaderQos reader_qos(const std::string &alias) const {
        (void)alias; // Phase 4/5: look the alias up in the loaded QoS libraries.
        dds::sub::qos::DataReaderQos qos;
        qos << dds::core::policy::Reliability::Reliable();
        qos << dds::core::policy::Durability::TransientLocal();
        qos << dds::core::policy::History::KeepLast(16);
        return qos;
    }

    dds::pub::qos::DataWriterQos writer_qos(const std::string &alias) const {
        (void)alias;
        dds::pub::qos::DataWriterQos qos;
        qos << dds::core::policy::Reliability::Reliable();
        qos << dds::core::policy::Durability::TransientLocal();
        qos << dds::core::policy::History::KeepLast(16);
        return qos;
    }
};

} // namespace router
