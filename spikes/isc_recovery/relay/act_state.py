#!/usr/bin/env python3
"""
ACT keyed state type + Instance-State-Consistency (ISC) QoS builders.

Phase 1 PoC (roadmap.md): a *concrete keyed* @idl.struct is used first (DynamicData
for the real keyed state topics comes in Phase 8). The type models a keyed state
instance — e.g. team membership / track lifecycle / disposition — where the DDS
`instance_state` (ALIVE / NOT_ALIVE_DISPOSED / NOT_ALIVE_NO_WRITERS) is the thing we
must preserve end-to-end across a disconnection.

The QoS builders here are the single source of truth for the ISC settings used by
every path in the PoC (direct baseline, relay legs). An equivalent XML profile lives
in qos_isc.xml (the roadmap's "QoS API or XML QoS profile fallback").
"""

import rti.connextdds as dds
import rti.types as idl


@idl.struct(
    member_annotations={
        "key_id": [idl.key],
    }
)
class ActState:
    """A keyed state instance.

    key_id  — the instance key (team id, track id, ...); its instance_state is the
              lifecycle signal ISC must recover.
    seq     — monotonic per-key sequence, so replayed transitions are distinguishable.
    payload — opaque app state; not interpreted by the relay.
    """

    key_id: str = ""
    seq: idl.int64 = 0
    send_ns: idl.int64 = 0   # publisher wall-clock ns (perf.py one-way latency); 0 otherwise
    payload: str = ""


# ISC enable value in this Connext build. NOTE: the Python 7.6 binding spells the
# enum RECOVER_STATE / NONE — NOT the C++/XML token RECOVER_INSTANCE_STATE_CONSISTENCY.
_ISC_RECOVER = dds.InstanceStateConsistencyKind.RECOVER_STATE
_ISC_OFF = dds.InstanceStateConsistencyKind.NONE


def participant_qos() -> dds.DomainParticipantQos:
    """UDPv4-only participant QoS (localhost PoC: avoids shared-memory transport)."""
    qos = dds.DomainParticipantQos()
    qos.transport_builtin.mask = dds.TransportBuiltinMask.UDPv4
    return qos


def writer_qos(isc: bool = True) -> dds.DataWriterQos:
    """RELIABLE + TRANSIENT_LOCAL DataWriter QoS; ISC toggled by `isc`.

    autodispose_unregistered_instances=False is essential: with the default (True),
    unregister_instance() would DISPOSE the instance, so NOT_ALIVE_NO_WRITERS could
    never be observed — the exact transition the PoC must distinguish from DISPOSED.

    `isc=False` yields an otherwise-identical profile with instance_state_consistency
    OFF — the control arm that isolates ISC's specific contribution.
    """
    qos = dds.DataWriterQos()
    qos.reliability.kind = dds.ReliabilityKind.RELIABLE
    qos.reliability.instance_state_consistency_kind = _ISC_RECOVER if isc else _ISC_OFF
    qos.durability.kind = dds.DurabilityKind.TRANSIENT_LOCAL
    qos.history.kind = dds.HistoryKind.KEEP_LAST
    qos.history.depth = 1  # last value per instance (state topic)
    qos.writer_data_lifecycle.autodispose_unregistered_instances = False
    # Ship the key bytes with dispose notifications so a reader that never saw a
    # valid sample for the instance can still recover its key (reader.key_value()).
    qos.data_writer_protocol.serialize_key_with_dispose = True
    return qos


def resolve_key_id(reader, data, info, cache: dict):
    """Return the key_id for a sample, or None if unrecoverable.

    Valid samples carry the key in `data`; we cache handle->key_id so that a later
    state-change-only sample (data is None) for the same instance can still be mapped.
    Falls back to reader.key_value() (works for DISPOSE thanks to serialize_key_with_dispose),
    and gives up (None) for the case DDS genuinely can't recover — a NOT_ALIVE_NO_WRITERS
    for an instance this reader never saw alive.
    """
    if data is not None:
        cache[info.instance_handle] = data.key_id
        return data.key_id
    cached = cache.get(info.instance_handle)
    if cached is not None:
        return cached
    try:
        return reader.key_value(info.instance_handle).key_id
    except Exception:
        return None


def reader_qos(isc: bool = True) -> dds.DataReaderQos:
    """RELIABLE + TRANSIENT_LOCAL DataReader QoS; ISC toggled by `isc`.

    keep_minimum_state_for_instances + propagate_dispose_of_unregistered_instances
    are the RTI-recommended companions for highest instance-state recovery fidelity.
    """
    qos = dds.DataReaderQos()
    qos.reliability.kind = dds.ReliabilityKind.RELIABLE
    qos.reliability.instance_state_consistency_kind = _ISC_RECOVER if isc else _ISC_OFF
    qos.durability.kind = dds.DurabilityKind.TRANSIENT_LOCAL
    qos.history.kind = dds.HistoryKind.KEEP_LAST
    qos.history.depth = 1
    qos.data_reader_resource_limits.keep_minimum_state_for_instances = True
    qos.data_reader_protocol.propagate_dispose_of_unregistered_instances = True
    return qos
