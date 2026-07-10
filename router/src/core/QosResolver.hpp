// QosResolver.hpp — asymmetric route-entity QoS (Phase 5, D39/D42/D45; D19).
//
// The auto ("" alias) contract is asymmetric and static:
//   - INPUT reader: one fixed weakest-request profile — BEST_EFFORT + VOLATILE +
//     default deadline/latency_budget/liveliness/destination_order/presentation,
//     DataRepresentation = union {XCDR, XCDR2}. By RxO construction it matches EVERY
//     discovered writer, so reader-side QoS immutability stops mattering (D39).
//   - OUTPUT writer: fixed strong baseline — RELIABLE + TRANSIENT_LOCAL (the TL offer
//     is already the durability auto-match, D42) — plus two policies derived from the
//     matched local readers at creation: deadline (min requested period; mutable, so
//     later tightening happens in place) and liveliness kind+lease (max kind / min
//     lease; immutable, derived once — D42).
// History and resource limits are always alias/default-supplied, never derived — they
// are not propagated in discovery (D19). The router's default history is KEEP_LAST(16)
// on both auto profiles (the same depth the "default" alias uses; on the writer it is
// also the TRANSIENT_LOCAL late-joiner cache depth).
//
// The only named alias resolvable today is "default" (RELIABLE + TRANSIENT_LOCAL +
// KEEP_LAST(16)). Real XML-alias resolution (QosProvider profiles for wan_event etc.)
// lands with Phase 7, whose routes are its first consumers (D45); until then any other
// alias throws — a RouteEntityError the operator can see (D41). The resolvability rule
// lives in QosAliasPolicy.hpp (D44), shared with RouteConfigParser::validate_qos_aliases.

#pragma once

#include "QosAliasPolicy.hpp"
#include "RouterEvents.hpp" // DerivedWriterQos, kInfiniteNanos, LivelinessKindPod

#include <dds/dds.hpp>
#include <dds/core/policy/CorePolicy.hpp>

#include <cstdint>
#include <sstream>
#include <stdexcept>
#include <string>

namespace router {

// --- nanoseconds <-> dds::core::Duration (kInfiniteNanos <-> infinite) ---

inline dds::core::Duration duration_from_nanos(std::int64_t nanos) {
    if (nanos == kInfiniteNanos) {
        return dds::core::Duration::infinite();
    }
    return dds::core::Duration(static_cast<int32_t>(nanos / 1000000000LL),
                               static_cast<uint32_t>(nanos % 1000000000LL));
}

inline std::int64_t nanos_from_duration(const dds::core::Duration &d) {
    if (d == dds::core::Duration::infinite()) {
        return kInfiniteNanos;
    }
    return static_cast<std::int64_t>(d.sec()) * 1000000000LL + d.nanosec();
}

inline std::string nanos_str(std::int64_t nanos) {
    if (nanos == kInfiniteNanos) {
        return "inf";
    }
    std::ostringstream os;
    os << (nanos / 1000000LL) << "ms";
    return os.str();
}

// Runtime QosPolicyId -> name for incompatible-QoS warnings. 7.7 only ships the
// compile-time policy_id/policy_name traits, so the reverse map is spelled out for the
// policies that can actually mismatch against the fixed profiles (D39 residual set +
// the derived pair); anything else falls back to the numeric id.
inline std::string qos_policy_name(std::uint32_t id) {
    using namespace dds::core::policy;
    if (id == policy_id<Reliability>::value)        return "RELIABILITY";
    if (id == policy_id<Durability>::value)         return "DURABILITY";
    if (id == policy_id<Deadline>::value)           return "DEADLINE";
    if (id == policy_id<LatencyBudget>::value)      return "LATENCY_BUDGET";
    if (id == policy_id<Liveliness>::value)         return "LIVELINESS";
    if (id == policy_id<Ownership>::value)          return "OWNERSHIP";
    if (id == policy_id<Presentation>::value)       return "PRESENTATION";
    if (id == policy_id<DestinationOrder>::value)   return "DESTINATION_ORDER";
    if (id == policy_id<DataRepresentation>::value) return "DATA_REPRESENTATION";
    std::ostringstream os;
    os << "POLICY_" << id;
    return os.str();
}

inline const char *liveliness_kind_name(LivelinessKindPod kind) {
    switch (kind) {
    case LivelinessKindPod::Automatic:           return "AUTOMATIC";
    case LivelinessKindPod::ManualByParticipant: return "MANUAL_BY_PARTICIPANT";
    case LivelinessKindPod::ManualByTopic:       return "MANUAL_BY_TOPIC";
    }
    return "?";
}

class QosResolver {
public:
    dds::sub::qos::DataReaderQos reader_qos(const std::string &alias) const {
        ensure_resolvable(alias);
        dds::sub::qos::DataReaderQos qos;
        if (alias.empty()) {
            // Weakest-request input profile (D39).
            qos << dds::core::policy::Reliability::BestEffort();
            qos << dds::core::policy::Durability::Volatile();
            qos << dds::core::policy::DataRepresentation(
                    {dds::core::policy::DataRepresentation::xcdr(),
                     dds::core::policy::DataRepresentation::xcdr2()});
            qos << dds::core::policy::History::KeepLast(16); // router default (D19)
        } else {
            apply_default_profile(qos);
        }
        return qos;
    }

    dds::pub::qos::DataWriterQos writer_qos(const std::string &alias,
                                            const DerivedWriterQos &derived
                                                    = DerivedWriterQos()) const {
        ensure_resolvable(alias);
        dds::pub::qos::DataWriterQos qos;
        apply_default_profile(qos); // strong baseline == "default" alias (D39/D42)
        if (alias.empty() && derived.derive) {
            qos << dds::core::policy::Deadline(
                    duration_from_nanos(derived.deadline_nanos));
            dds::core::policy::Liveliness liveliness;
            switch (derived.liveliness_kind) {
            case LivelinessKindPod::Automatic:
                liveliness = dds::core::policy::Liveliness::Automatic();
                break;
            case LivelinessKindPod::ManualByParticipant:
                liveliness = dds::core::policy::Liveliness::ManualByParticipant();
                break;
            case LivelinessKindPod::ManualByTopic:
                liveliness = dds::core::policy::Liveliness::ManualByTopic();
                break;
            }
            liveliness.lease_duration(duration_from_nanos(derived.lease_nanos));
            qos << liveliness;
        }
        return qos;
    }

    // --- Resolved-QoS summaries for status (D45). Stable, human-readable, compact. ---

    static std::string summarize(const dds::sub::qos::DataReaderQos &qos) {
        std::ostringstream os;
        os << reliability_str(qos.policy<dds::core::policy::Reliability>())
           << "," << durability_str(qos.policy<dds::core::policy::Durability>());
        return os.str();
    }

    static std::string summarize(const dds::pub::qos::DataWriterQos &qos) {
        std::ostringstream os;
        os << reliability_str(qos.policy<dds::core::policy::Reliability>())
           << "," << durability_str(qos.policy<dds::core::policy::Durability>());
        const dds::core::policy::Deadline &deadline =
                qos.policy<dds::core::policy::Deadline>();
        os << ",deadline=" << nanos_str(nanos_from_duration(deadline.period()));
        const dds::core::policy::Liveliness &liveliness =
                qos.policy<dds::core::policy::Liveliness>();
        os << ",liveliness=" << liveliness_str(liveliness.kind()) << ":"
           << nanos_str(nanos_from_duration(liveliness.lease_duration()));
        return os.str();
    }

private:
    static void ensure_resolvable(const std::string &alias) {
        if (!is_resolvable_qos_alias(alias)) {
            throw std::runtime_error(
                    "unresolvable QoS alias '" + alias
                    + "' (XML alias lookup in loaded QoS libraries is Phase 7)");
        }
    }

    template <typename QosT>
    static void apply_default_profile(QosT &qos) {
        qos << dds::core::policy::Reliability::Reliable();
        qos << dds::core::policy::Durability::TransientLocal();
        qos << dds::core::policy::History::KeepLast(16);
    }

    static const char *reliability_str(const dds::core::policy::Reliability &r) {
        return r.kind() == dds::core::policy::ReliabilityKind::RELIABLE
                       ? "RELIABLE" : "BEST_EFFORT";
    }
    static const char *durability_str(const dds::core::policy::Durability &d) {
        switch (d.kind().underlying()) {
        case dds::core::policy::DurabilityKind::VOLATILE:        return "VOLATILE";
        case dds::core::policy::DurabilityKind::TRANSIENT_LOCAL: return "TRANSIENT_LOCAL";
        case dds::core::policy::DurabilityKind::TRANSIENT:       return "TRANSIENT";
        case dds::core::policy::DurabilityKind::PERSISTENT:      return "PERSISTENT";
        }
        return "?";
    }
    static const char *liveliness_str(const dds::core::policy::LivelinessKind &k) {
        switch (k.underlying()) {
        case dds::core::policy::LivelinessKind::AUTOMATIC:
            return "AUTOMATIC";
        case dds::core::policy::LivelinessKind::MANUAL_BY_PARTICIPANT:
            return "MANUAL_BY_PARTICIPANT";
        case dds::core::policy::LivelinessKind::MANUAL_BY_TOPIC:
            return "MANUAL_BY_TOPIC";
        }
        return "?";
    }
};

} // namespace router
