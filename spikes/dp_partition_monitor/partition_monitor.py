"""PartitionMonitor -- a live table of every DomainParticipant's partition membership AND
identity (router name), combining THREE mechanisms that are each individually incomplete:

1. discovered_participants() on the monitor's own (default) partition -- gives real
   identity (participant_name) for free, but only for peers sharing that partition.
2. Log-parsing of PRESParticipant_hasMatchingPartition:UNMATCH -- gives the actual
   partition string for peers that DON'T match (1), but the message carries no identity
   field at all, only a GUID.
3. A second, WILDCARD-partition (["*"]) local participant -- matches literally everyone
   regardless of their partition (verified: PARTITION QoS wildcard/fnmatch matching applies
   at the DomainParticipant level, and is pairwise -- it doesn't bridge two otherwise
   -mismatched peers to each other, see spikes/spdp2_partition_visibility/). Once matched,
   a peer's real participant_name IS readable via the standard discovered_participant_data()
   API -- verified against the router's actual EntityName convention
   ("<node>/<router>" / role_name "act.router", router/src/core/ParticipantRegistry.cxx).
   But ParticipantBuiltinTopicData has no partition field at all (verified: dir() has none,
   even for a fully-matched peer) -- wildcard-matching tells you WHO, never WHAT TEAM.

So identity (3) and partition (1+2) are mutually exclusive on any single local
participant's own posture and have to be resolved by two separate co-located local
participants, joined by GUID. Neither one alone answers "what team is this named router
in" -- only the join does.

Handler lifecycle: registering dds.Logger.instance.output_handler(...) with verbosity raised
and never resetting it before process exit segfaults the Python interpreter (verified in
this spike, part C -- reproduces with zero DomainParticipants involved, so it's a
Logger-singleton/interpreter-teardown interaction). PartitionMonitor.close() resets both the
category verbosity and the output handler; callers MUST call it before the process exits.
"""

import os
import re
import threading
import time

os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

import rti.connextdds as dds  # noqa: E402

# The UNMATCH message is tagged under LogCategory.entities, NOT LogCategory.discovery --
# confirmed by hand-testing both (discovery: 0/14 UNMATCH lines captured; entities: 14/14,
# at ~1/6th the line volume of raising global verbosity). See PLAN.md part D.
_MONITOR_CATEGORY = dds.LogCategory.entities

_UNMATCH_RE = re.compile(
    r'Remote DP \(GUID: (0x[0-9A-Fa-f]+,0x[0-9A-Fa-f]+,0x[0-9A-Fa-f]+)\) '
    r'with partitions \("([^"]*)"\) does not match with local DP\('
    r'GUID: (0x[0-9A-Fa-f]+,0x[0-9A-Fa-f]+,0x[0-9A-Fa-f]+)\) '
    r'with partitions \("([^"]*)"\)'
)


def _split_partitions(raw):
    """Wire format is one quoted, comma-joined string ("TEAM_A,TEAM_C"), not separate
    quoted tokens -- confirmed against a real multi-valued-partition capture."""
    return [p for p in raw.split(",") if p]


def _guid_str_from_key(key):
    v = key.value
    return f"0x{v[0]:08X},0x{v[1]:08X},0x{v[2]:08X}"


def _own_guid_prefix(participant):
    """The log line's GUID format (0xXXXXXXXX,0xXXXXXXXX,0xXXXXXXXX, uppercase hex, first
    12 bytes = 3 uint32s of the GUID prefix) doesn't match str(instance_handle)'s plain
    32-hex-char form, so convert explicitly rather than comparing the two formats."""
    hex_str = str(participant.instance_handle).upper()
    prefix = hex_str[:24]
    return ",".join(f"0x{prefix[i:i + 8]}" for i in (0, 8, 16))


class PartitionMonitor:
    # Default SPDP re-announce period observed empirically at ~30s (a late-joining monitor
    # took up to that long to reach full coverage of already-existing mismatched peers).
    # A ttl_s equal to that period races it: one delayed announcement transiently evicts a
    # still-live peer (observed: table dropped from 18 to 13 entries then recovered a few
    # seconds later, at ttl_s=30.0). 3x the observed period gives real margin.
    def __init__(self, domain, own_partition=None, ttl_s=90.0, resolve_identity=True):
        q = dds.DomainParticipantQos()
        q.transport_builtin = dds.TransportBuiltin.udpv4
        if own_partition:
            q.partition = dds.Partition(list(own_partition))
        self._own_partition = list(own_partition) if own_partition else []
        self._participant = dds.DomainParticipant(domain, q)
        self._own_guid = _own_guid_prefix(self._participant)
        self._ttl_s = ttl_s
        self._lock = threading.Lock()
        self._unmatched = {}  # guid_str -> {"partitions": [...], "last_seen": monotonic}
        self._line_count = 0

        # Wildcard-partition sibling participant, purely for identity resolution -- matches
        # every peer regardless of its partition (verified pairwise, no bridging risk to the
        # peers themselves) so participant_name is readable via the standard API for peers
        # the main (self._participant) posture would never discover at all.
        self._identity_participant = None
        self._identity_own_guid = None
        if resolve_identity:
            iq = dds.DomainParticipantQos()
            iq.transport_builtin = dds.TransportBuiltin.udpv4
            iq.partition = dds.Partition(["*"])
            self._identity_participant = dds.DomainParticipant(domain, iq)
            self._identity_own_guid = _own_guid_prefix(self._identity_participant)
            # A wildcard-only partition also implicitly carries the default ("") partition
            # (per PARTITION QoS semantics), so this sibling and self._participant (default
            # partition) mutually match EACH OTHER as an artifact of being co-located in the
            # same process -- not a real external peer. Filtered out below in matched()/
            # identities() by comparing against each other's own GUID.

        dds.Logger.instance.output_handler(self._on_log)
        dds.Logger.instance.verbosity_by_category(_MONITOR_CATEGORY, dds.Verbosity.STATUS_REMOTE)

    def _on_log(self, msg):
        self._line_count += 1
        m = _UNMATCH_RE.search(msg)
        if not m:
            return
        remote_guid, printed_after_remote, local_guid, printed_after_local = m.groups()
        if local_guid.lower() != self._own_guid.lower():
            # The Logger is process-global: this UNMATCH is between two OTHER co-located
            # participants (e.g. the router's other WAN/LAN participants sharing the
            # process), not one involving this monitor. Discard it.
            return
        # Connext's own UNMATCH message swaps the partition strings relative to their GUID
        # labels: the value printed next to "Remote DP" is actually the LOCAL participant's
        # partition, and the value printed next to "local DP" is the REMOTE's. Verified with
        # two participants on distinct concrete partitions ("AAA"/"BBB") and their real
        # instance_handle GUIDs printed independently -- the printed pairing is backwards.
        remote_partitions = printed_after_local
        with self._lock:
            self._unmatched[remote_guid] = {
                "partitions": _split_partitions(remote_partitions),
                "last_seen": time.monotonic(),
            }

    @property
    def line_count(self):
        """Total log lines the handler has seen (matched + discarded) -- for the scale
        measurement in part D, not part of the table itself."""
        return self._line_count

    def matched(self):
        result = {}
        for h in self._participant.discovered_participants():
            try:
                data = self._participant.discovered_participant_data(h)
            except dds.Error:
                continue
            guid = _guid_str_from_key(data.key)
            if guid == self._identity_own_guid:
                continue  # our own wildcard sibling, not a real external peer
            # ParticipantBuiltinTopicData has no partition field at all (verified, see
            # spikes/spdp2_partition_visibility/ and docs/product-gaps.md LP-5) -- being
            # matched only proves the peer shares AT LEAST ONE partition name with
            # self._own_partition, not which one. Report what's provable, not a guess.
            result[guid] = {"partitions": None, "source": "matched"}
        return result

    def unmatched(self):
        now = time.monotonic()
        with self._lock:
            stale = [g for g, v in self._unmatched.items()
                     if now - v["last_seen"] > self._ttl_s]
            for g in stale:
                del self._unmatched[g]
            return {g: {"partitions": v["partitions"], "source": "log"}
                     for g, v in self._unmatched.items()}

    def identities(self):
        """GUID -> {"name": ..., "role_name": ...} for every peer the wildcard identity
        participant has matched, regardless of its real partition. Empty if this
        PartitionMonitor was constructed with resolve_identity=False."""
        if self._identity_participant is None:
            return {}
        result = {}
        for h in self._identity_participant.discovered_participants():
            try:
                data = self._identity_participant.discovered_participant_data(h)
            except dds.Error:
                continue
            guid = _guid_str_from_key(data.key)
            if guid == self._own_guid:
                continue  # the main (self._participant) sibling, not a real external peer
            pn = data.participant_name
            result[guid] = {
                "name": pn.name if pn else None,
                "role_name": pn.role_name if pn else None,
            }
        return result

    def table(self):
        """Merged view: partitions/source from matched()+unmatched(), joined by GUID with
        name/role_name from identities() where resolvable. A log-derived (unmatched) entry
        commonly HAS a name (via the wildcard sibling) even though its own posture never
        discovered it -- that's the whole point of running both."""
        t = self.matched()
        t.update(self.unmatched())
        idents = self.identities()
        for guid, entry in t.items():
            ident = idents.get(guid, {})
            entry["name"] = ident.get("name")
            entry["role_name"] = ident.get("role_name")
        return t

    def close(self):
        dds.Logger.instance.verbosity_by_category(_MONITOR_CATEGORY, dds.Verbosity.EXCEPTION)
        dds.Logger.instance.reset_output_handler()
        self._participant.close()
        if self._identity_participant is not None:
            self._identity_participant.close()
