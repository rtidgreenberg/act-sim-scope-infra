"""Lightweight, synchronous DynamicData pub/sub helpers for the router e2e suite.

Mirrors the pattern already proven in router/test/test_dynamic_forward.cxx (one writer,
one reader, poll-with-timeout) rather than reusing harness/act's asyncio interactive sim
scripts — deterministic, single-shot, easy to assert against.
"""

import time

import rti.connextdds as dds

ACT_TYPES_XML = "harness/act/node_sim/datamodel/act_types.xml"

_provider = None


def _types():
    global _provider
    if _provider is None:
        _provider = dds.QosProvider(ACT_TYPES_XML)
    return _provider


class Probe:
    """One UDPv4-only DomainParticipant on `domain`, for app-side pub/sub in a test."""

    def __init__(self, domain, user_data=None):
        qos = dds.DomainParticipant.default_participant_qos
        qos.transport_builtin = dds.TransportBuiltin.udpv4
        # user_data (e.g. an "act.router=<node>/<router>" tag, D15) lets a probe pose as a
        # same-node router so the D15 same-node-ignore path can be exercised.
        if user_data is not None:
            qos.user_data = dds.UserData(list(user_data.encode("utf-8")))
        self.participant = dds.DomainParticipant(domain, qos)
        self._publisher = dds.Publisher(self.participant)
        self._subscriber = dds.Subscriber(self.participant)
        self._topics = {}

    def _topic(self, topic_name, dtype):
        # One Topic per name per participant — a second Topic with the same name would
        # raise, so multiple readers/writers on one topic (the auto-QoS test) reuse it.
        topic = self._topics.get(topic_name)
        if topic is None:
            topic = dds.DynamicData.Topic(self.participant, topic_name, dtype)
            self._topics[topic_name] = topic
        return topic

    def writer(self, topic_name, type_name, qos=None, dtype=None):
        dtype = dtype if dtype is not None else _types().type(type_name)
        topic = self._topic(topic_name, dtype)
        if qos is None:
            return dds.DynamicData.DataWriter(self._publisher, topic)
        return dds.DynamicData.DataWriter(self._publisher, topic, qos)

    def reader(self, topic_name, type_name, qos=None, dtype=None):
        dtype = dtype if dtype is not None else _types().type(type_name)
        topic = self._topic(topic_name, dtype)
        if qos is None:
            return dds.DynamicData.DataReader(self._subscriber, topic)
        return dds.DynamicData.DataReader(self._subscriber, topic, qos)

    def sample(self, type_name, **fields):
        """A DynamicData instance of `type_name` with dotted-path fields set, e.g.
        sample("control_command", **{"msg.destination": "Platform_30"})."""
        dtype = _types().type(type_name)
        data = dds.DynamicData(dtype)
        for path, value in fields.items():
            data[path] = value
        return data

    def discovered_participant_tags(self):
        """Return {participant_key_str: user_data_text} for every participant this probe
        has discovered (builtin DCPSParticipant data). Used to observe a router's
        act.router=<node>/<router> user-data tag without any application type."""
        tags = {}
        for handle in self.participant.discovered_participants():
            data = self.participant.discovered_participant_data(handle)
            text = bytes(data.user_data.value).decode("utf-8", errors="replace")
            tags[str(data.key.value)] = text
        return tags

    def discovered_publications(self):
        """Return a list of {topic, type, participant_key} for every DataWriter this probe
        has discovered (builtin DCPSPublication data). Reads builtin discovery only — no
        application type needed to see a writer's topic_name/type_name (e.g. a router's
        RouterStatus writer, whose type isn't defined in Python)."""
        found = []
        for data, info in self.participant.publication_reader.read():
            if not info.valid:
                continue
            found.append({
                "topic": data.topic_name,
                "type": data.type_name,
                "participant_key": str(data.participant_key.value),
            })
        return found

    def close(self):
        self.participant.close()


_RELIABILITY = {"reliable": dds.ReliabilityKind.RELIABLE,
                "best_effort": dds.ReliabilityKind.BEST_EFFORT}
_DURABILITY = {"volatile": dds.DurabilityKind.VOLATILE,
               "transient_local": dds.DurabilityKind.TRANSIENT_LOCAL,
               "transient": dds.DurabilityKind.TRANSIENT}
_OWNERSHIP = {"shared": dds.OwnershipKind.SHARED,
              "exclusive": dds.OwnershipKind.EXCLUSIVE}


def _apply_common(qos, reliability, durability, ownership):
    if reliability is not None:
        qos.reliability = dds.Reliability(kind=_RELIABILITY[reliability])
    if durability is not None:
        qos.durability = dds.Durability(kind=_DURABILITY[durability])
    if ownership is not None:
        qos.ownership = dds.Ownership(kind=_OWNERSHIP[ownership])


def reader_qos(reliability=None, durability=None, deadline_ms=None,
               liveliness_automatic_lease_ms=None, ownership=None, keep_all=False):
    """Build a DataReaderQos from simple options (only the policies the auto-QoS test
    varies). Kinds are lowercase strings: 'reliable'/'best_effort',
    'volatile'/'transient_local'/'transient', 'shared'/'exclusive'. keep_all=True sets
    KEEP_ALL history — required for event-stream topics (e.g. the controller journal):
    the default KEEP_LAST(1) cache holds ONE sample per instance, and one router's
    journal is one instance (keyed by target_node/target_router), so a back-to-back
    burst (7m creates entities in the same event-drain as the command) overwrites
    earlier samples before a polling take()."""
    q = dds.DataReaderQos()
    _apply_common(q, reliability, durability, ownership)
    if keep_all:
        q.history = dds.History.keep_all
    if deadline_ms is not None:
        q.deadline = dds.Deadline(period=dds.Duration.from_milliseconds(deadline_ms))
    if liveliness_automatic_lease_ms is not None:
        q.liveliness = dds.Liveliness(
            dds.LivelinessKind.AUTOMATIC,
            dds.Duration.from_milliseconds(liveliness_automatic_lease_ms))
    return q


def writer_qos(reliability=None, durability=None, ownership=None):
    q = dds.DataWriterQos()
    _apply_common(q, reliability, durability, ownership)
    return q


# RouterRouteOperationalState / RouterRouteDiscoveryState enum ordinals (RouterAdminTypes.idl,
# declaration order; no explicit values so they are 0-based sequential).
ROUTE_STATE = {0: "ROUTE_DISABLED", 1: "ROUTE_WAITING_FOR_DISCOVERY", 2: "ROUTE_RESOLVING",
               3: "ROUTE_ENABLED", 4: "ROUTE_DEGRADED", 5: "ROUTE_ERROR"}
DISCOVERY_STATE = {0: "DISCOVERY_NONE", 1: "DISCOVERY_PARTIAL", 2: "DISCOVERY_READY"}
TOPIC_STATE = {0: "TOPIC_IDLE", 1: "TOPIC_CREATING", 2: "TOPIC_FORWARDING",
               3: "TOPIC_TEARING_DOWN", 4: "TOPIC_ERROR"}


def read_route_facts(status_reader, route_name):
    """Read the newest RouterStatus sample and return the named route's observable facts
    as a dict {state, discovery, reader_summary, writer_summary, qos_warning,
    input_matched, output_matched, match_reason}, or None if the route isn't present
    yet. read() (not take) — RouterStatus is TRANSIENT_LOCAL KEEP_LAST state; last
    matching entry across cached samples wins (most recent)."""
    facts = None
    for data, info in status_reader.read():
        if not info.valid:
            continue
        for i in range(len(data["routes"])):
            if data.get_string(f"routes[{i}].route_name") != route_name:
                continue
            f = {
                "state": ROUTE_STATE.get(int(data[f"routes[{i}].state"]), "?"),
                "discovery": DISCOVERY_STATE.get(
                    int(data[f"routes[{i}].discovery_state"]), "?"),
                "topic_count": len(data[f"routes[{i}].topic_status"]),
                "topic_state": None,
                "reader_summary": "", "writer_summary": "", "qos_warning": "",
                # D64/D66 create-and-observe: live matched counts + unmatched reason.
                "input_matched": 0, "output_matched": 0, "match_reason": "",
            }
            if len(data[f"routes[{i}].topic_status"]) > 0:
                base = f"routes[{i}].topic_status[0]"
                f["topic_state"] = TOPIC_STATE.get(
                    int(data[f"{base}.topic_state"]), "?")
                f["reader_summary"] = data.get_string(f"{base}.reader_qos_summary")
                f["writer_summary"] = data.get_string(f"{base}.writer_qos_summary")
                f["qos_warning"] = data.get_string(f"{base}.qos_warning")
                f["input_matched"] = int(data[f"{base}.input_matched"])
                f["output_matched"] = int(data[f"{base}.output_matched"])
                f["match_reason"] = data.get_string(f"{base}.match_reason")
            facts = f
    return facts


def read_status_revision(status_reader):
    """Return the newest RouterStatus sample's top-level state_revision (D5 global
    counter), or None if no valid sample yet. RouterStatus is KEEP_LAST(1) per
    (target_node, target_router) instance, so read() yields the current snapshot."""
    rev = None
    for data, info in status_reader.read():
        if not info.valid:
            continue
        rev = int(data["state_revision"])
    return rev


class AckCollector:
    """Buffered reader for RouterCommandAck. `RouterCommandAck` has no @key, so a single
    take() drains every cached ack regardless of command_id — a bare take()-and-match loop
    would silently discard acks for other in-flight commands. This collector take()s into a
    per-command_id buffer each poll and pops the requested one, so waiting on command A
    never loses command B's ack. Scope one per test (owns the buffer); no cross-test state.

    wait() returns {command_id, route_name, accepted, message} or None on timeout. Each ack
    is returned once (popped); a duplicate-command_id replay re-published by the router (D4)
    arrives as a fresh sample on the next send and is collected on the next wait()."""

    def __init__(self, ack_reader):
        self._reader = ack_reader
        self._by_id = {}

    def wait(self, command_id, timeout_s=10.0, poll_s=0.1, check_alive=None):
        if command_id in self._by_id:
            return self._by_id.pop(command_id)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if check_alive is not None and not check_alive():
                raise RuntimeError("AckCollector.wait: check_alive() returned False — "
                                   "router process likely exited early")
            # Drain the whole batch into the buffer FIRST (take() already removed it from
            # the reader cache), then check — never return mid-batch and drop the rest.
            for sample in self._reader.take():
                if not sample.info.valid:
                    continue
                d = sample.data
                self._by_id[d["command_id"]] = {
                    "command_id": d["command_id"],
                    "route_name": d["route_name"],
                    "accepted": bool(d["accepted"]),
                    "message": d["message"],
                }
            if command_id in self._by_id:
                return self._by_id.pop(command_id)
            time.sleep(poll_s)
        return None


# ControllerJournalEventKind ordinals (RouterAdminTypes.idl declaration order, D46).
JOURNAL_KIND = {0: "COMMAND_RECEIVED", 1: "PUBLICATION_DISCOVERED",
                2: "SUBSCRIPTION_DISCOVERED", 3: "ENDPOINT_LOST",
                4: "TOPIC_ENTITIES_READY", 5: "TOPIC_TEARDOWN_COMPLETE",
                6: "ROUTE_ENTITY_ERROR", 7: "TOPIC_QOS_WARNING"}


class JournalCollector:
    """Accumulating reader for ControllerJournalRecord (the debug journal, D55/D56).
    ControllerJournalRecord is keyed per router (target_node/target_router), and one
    test drives one router, so take() drains that instance's records at once — this
    buffers all records into `records` so successive wait_for() queries (e.g. first for a
    COMMAND_RECEIVED, then for discovery records) never lose each other's samples. Scope one
    per test. A matched Python reader IS "debug mode" (D56): the router's journal writer
    produces no data traffic until this reader discovers it."""

    def __init__(self, journal_reader):
        self._reader = journal_reader
        self.records = []

    def _drain(self):
        for sample in self._reader.take():
            if not sample.info.valid:
                continue
            d = sample.data
            self.records.append({
                "event_kind": JOURNAL_KIND.get(int(d["event_kind"]), "?"),
                "event_sequence": int(d["event_sequence"]),
                "pre": int(d["pre_state_revision"]),
                "post": int(d["post_state_revision"]),
                "state_changed": bool(d["state_changed"]),
                "route": d["route_name"],
                "topic": d["topic_name"],
                "command_id": d["command_id"],
                "decision": d["decision"],
                "reason": d["reason"],
            })

    def wait_for(self, predicate, timeout_s=15.0, poll_s=0.1, check_alive=None):
        """Poll until at least one accumulated record satisfies predicate, returning ALL
        matching records (or whatever matched by timeout). Records stay buffered for later
        queries."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if check_alive is not None and not check_alive():
                raise RuntimeError("JournalCollector.wait_for: check_alive() returned "
                                   "False — router process likely exited early")
            self._drain()
            matched = [r for r in self.records if predicate(r)]
            if matched:
                return matched
            time.sleep(poll_s)
        self._drain()
        return [r for r in self.records if predicate(r)]


def wait_for_route(status_reader, route_name, predicate, timeout_s=20.0, poll_s=0.25,
                   check_alive=None):
    """Poll read_route_facts until predicate(facts) is true, returning the facts (or the
    last-seen facts / None on timeout)."""
    facts = None
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if check_alive is not None and not check_alive():
            raise RuntimeError("wait_for_route: check_alive() returned False — router "
                               "process likely exited early")
        facts = read_route_facts(status_reader, route_name)
        if facts is not None and predicate(facts):
            return facts
        time.sleep(poll_s)
    return facts


def write_until_seen(write_fn, reader, classify, stop_key,
                     timeout_s=15.0, poll_s=0.15, grace_s=0.5, check_alive=None):
    """Repeatedly call `write_fn()` (publishes one or more samples) then poll `reader`
    via take(), bucketing every valid sample by `classify(data)` into a dict of lists,
    until `stop_key`'s bucket is non-empty or `timeout_s` elapses. Then drains for
    `grace_s` more (to catch a late/leaked sample landing after the stop condition —
    e.g. a wrongly-forwarded sample a content filter should have dropped) before
    returning the accumulated `{key: [DynamicData, ...]}` dict.

    `check_alive`, if given, is called every poll iteration; when it returns False this
    raises immediately instead of waiting out the full timeout — e.g. pass
    `lambda: control_proc.is_alive() and platform_proc.is_alive()` so a crashed router
    process fails fast with a clear message rather than a slow, confusing "sample never
    arrived" timeout.

    This is the router e2e suite's one poll-with-timeout primitive (mirrors the
    write-then-take loop already proven in test_dynamic_forward.cxx): tests that need a
    single-bucket "did X ever arrive" check can pass a `classify` that always returns
    the same key; tests that also need a negative-case ("and Y never arrived") read a
    second bucket out of the same result."""
    buckets = {}

    def drain():
        for sample in reader.take():
            if sample.info.valid:
                buckets.setdefault(classify(sample.data), []).append(sample.data)

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not buckets.get(stop_key):
        if check_alive is not None and not check_alive():
            raise RuntimeError(
                "write_until_seen: check_alive() returned False — a router process "
                "likely exited early; check its log instead of waiting out the timeout")
        write_fn()
        time.sleep(poll_s)
        drain()
    if grace_s:
        time.sleep(grace_s)
        drain()
    return buckets
