#!/usr/bin/env python3
"""Type-discovery spike — can a router learn a usable type purely from the wire?

Question (from the Phase 7 design discussion). The router is a generic relay: it has
topic names from config but NO type objects (no local IDL/XML). To create its
DynamicData reader/writer it must obtain the DynamicType from discovery. Two things must
hold for the "learn from the LAN app endpoint, then build both legs" model to work:

  1. It can learn the type from a discovered *writer* AND from a discovered *reader*
     (the destination-side router faces an app reader on its LAN, not a writer).
  2. The learned type is COMPLETE (carries member names), not MINIMAL — because the
     router accesses DynamicData fields by name (data["msg.destination"]) and builds a
     ContentFilteredTopic filtering on a member name ("msg.destination = %0"). MINIMAL
     TypeObjects strip names and cannot do either.

The connext MCP claims request_types_filter forces COMPLETE into the builtin path, but
that is a "strong hint, not ground truth" — and it also depends on how the publishing
app propagates its type. So we prove it against the real 7.7 install here.

Method. One UDPv4 domain. An "app" participant owns act_types.xml (type control_command:
struct { base_type msg }, base_type has a string member `destination`) — this is exactly
how the real ACT sim publishes (harness/act/node_sim/python/*, rti.connextdds +
QosProvider.type(...)). A "router-like" participant has NO type at all and reads the type
object straight off the builtin discovery data (data.type), the rti_view / rtiddsspy
model — NOT the 7.7-only request_types_filter/TypeLookup path (which is unavailable in the
Python QoS binding and rejected by this install's runtime XML parser, and which we should
not depend on since a LAN app may not be a matching Connext version). For a small type the
COMPLETE TypeObject rides inline in the SEDP announcement, so it is present in the builtin
sample with no lookup round-trip. We then:
  Part A (learn from a WRITER): app creates a control_command writer; router reads its
    builtin publication data, obtains the DynamicType, asserts it is COMPLETE (nested
    member `msg.destination` reachable by name), builds a CFT on "msg.destination = %0",
    and proves end-to-end that the CFT (built from the wire-learned type) forwards only
    the addressed sample.
  Part B (learn from a READER): app creates a control_command *reader*; router reads its
    builtin subscription data, obtains the DynamicType, and asserts COMPLETE the same way
    (named DynamicData access + CFT construction). This is the destination-side case.
  Part C (prove it is INLINE, not TypeLookup): app creates a writer; the router disables its
    TypeLookup channel entirely (enabled_builtin_channels = NONE) and confirms the COMPLETE
    type STILL resolves — only possible if it rode inline in SEDP. This is the decisive
    mechanism proof; Parts A/B's "first sample" timing is only a hint (see learn_type_from).

Exit 0 = all three parts passed; nonzero = a failure (prints which assertion failed). Each
part runs on its own domain (DOMAIN + part index) with fresh participants.

Run:  python3 spikes/type_discovery/type_discovery_spike.py [base_domain_id]
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

import rti.connextdds as dds  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
ACT_TYPES_XML = str(REPO_ROOT / "harness/act/node_sim/datamodel/act_types.xml")

_types_provider = None


def _act_types():
    """The app-side type library, parsed once (mirrors dds_probe.py's cached _provider)."""
    global _types_provider
    if _types_provider is None:
        _types_provider = dds.QosProvider(ACT_TYPES_XML)
    return _types_provider

TOPIC = "ControlCommand"
TYPE = "control_command"
FIELD = "msg.destination"          # nested member — the real CFT / access path
ADDRESSED = "Platform_30"
OTHER = "Platform_31"

DOMAIN = int(sys.argv[1]) if len(sys.argv) > 1 else 91
RESOLVE_TIMEOUT_S = 15.0


class SpikeError(AssertionError):
    pass


def _poll(fn, timeout_s, poll_s=0.2):
    """Call fn() until it returns a non-None value or timeout; return it (or None). Uses
    `is not None`, not truthiness, so a valid-but-falsy result (e.g. a DynamicType a binding
    makes falsy via __len__, or an empty struct) is not mistaken for 'not ready'. Callers
    that mean 'not done yet' must return None, not False."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        v = fn()
        if v is not None:
            return v
        time.sleep(poll_s)
    return None


def learn_type_from(builtin_reader, topic_name):
    """Poll a builtin discovery reader (publication_reader or subscription_reader) for an
    endpoint on topic_name and return its discovered DynamicType, or None on timeout. Type
    resolution can be async, so the endpoint may appear before its type — we keep polling
    `.type`. On timeout, distinguishes 'endpoint never discovered' from 'endpoint seen but
    type never resolved' (the two have very different design consequences) via `seen`."""
    # Instrumented to distinguish the MECHANISM (spike vs MCP contradiction, 2026-07-14):
    #   - INLINE in SEDP  => data.type is populated on the very FIRST sample that carries
    #     the endpoint (no gap between "endpoint seen" and "type resolved").
    #   - via TypeLookup  => endpoint is seen first with NO type, then the type appears on a
    #     LATER sample/poll (a measurable gap), the async pattern.
    state = {"endpoint_seen_at": None, "polls_with_endpoint_no_type": 0}
    t0 = time.monotonic()

    def attempt():
        for data, info in builtin_reader.read():
            if not info.valid or data.topic_name != topic_name:
                continue
            if state["endpoint_seen_at"] is None:
                state["endpoint_seen_at"] = time.monotonic() - t0
            try:
                dt = data.type          # DynamicType, or None until resolved
            except Exception:
                dt = None
            if dt is not None:
                return dt
            state["polls_with_endpoint_no_type"] += 1
        return None
    dt = _poll(attempt, RESOLVE_TIMEOUT_S)
    if dt is None:
        why = ("endpoint discovered but its type never resolved"
               if state["endpoint_seen_at"] is not None else "endpoint never discovered")
        print(f"    (diagnostic: {why})")
        return None
    resolved_at = time.monotonic() - t0
    gap = resolved_at - (state["endpoint_seen_at"] or resolved_at)
    # NOTE: "present on first sample" is only a HINT of inline propagation — a TypeLookup
    # that completes between two polls would also land the type on the first sample we
    # observe. Part C (TypeLookup channel disabled) is the decisive inline proof, not this.
    mech = ("type present on first observed sample (hint of inline; Part C is decisive)"
            if state["polls_with_endpoint_no_type"] == 0
            else f"DEFERRED (type appeared {gap*1000:.0f}ms / "
                 f"{state['polls_with_endpoint_no_type']} polls after the endpoint)")
    print(f"    (mechanism: {mech})")
    return dt


def assert_complete(dtype, where):
    """A learned type is COMPLETE iff its member names survived: the top struct has a
    member literally named `msg`, and that member's own type has one named `destination`.
    MINIMAL TypeObjects replace names with hashes, so find_member_by_name('msg') fails.
    Belt-and-suspenders: also construct a DynamicData sample and set the nested field by
    dotted name — the exact access the router relies on."""
    names = [dtype.member(i).name for i in range(dtype.member_count)]
    if "msg" not in names:
        raise SpikeError(
            f"[{where}] learned type is MINIMAL (no member names): top members={names}")
    inner = dtype.member(names.index("msg")).type
    inner_names = [inner.member(i).name for i in range(inner.member_count)]
    if "destination" not in inner_names:
        raise SpikeError(
            f"[{where}] nested member names missing: msg has {inner_names}")
    # Named DynamicData access, the router's hot path.
    sample = dds.DynamicData(dtype)
    sample[FIELD] = ADDRESSED
    if sample[FIELD] != ADDRESSED:
        raise SpikeError(f"[{where}] named DynamicData access round-trip failed")
    print(f"  [{where}] COMPLETE: top={names}, msg={inner_names}, "
          f"named access {FIELD!r} OK")


def make_cft(router, dtype, name):
    """Build a Topic + ContentFilteredTopic on the wire-learned type. Constructing the CFT
    at all requires member-name filtering support — it throws on a MINIMAL type."""
    topic = dds.DynamicData.Topic(router, TOPIC, dtype)
    cft = dds.DynamicData.ContentFilteredTopic(
        topic, name, dds.Filter(f"{FIELD} = %0", [f"'{ADDRESSED}'"]))
    return topic, cft


def part_a_learn_from_writer(domain):
    print("Part A: router learns control_command from a discovered WRITER")
    app = dds.DomainParticipant(domain, _udpv4_qos())
    router = dds.DomainParticipant(domain, _udpv4_qos())
    try:
        _part_a_body(app, router)
    finally:
        router.close()
        app.close()


def _part_a_body(app, router):
    dtype_local = _act_types().type(TYPE)
    app_topic = dds.DynamicData.Topic(app, TOPIC, dtype_local)
    app_writer = dds.DynamicData.DataWriter(dds.Publisher(app), app_topic)

    learned = learn_type_from(router.publication_reader, TOPIC)
    if learned is None:
        raise SpikeError("[A] router never obtained the type from publication discovery")
    print(f"  [A] learned type from the wire: {learned.name}")
    assert_complete(learned, "A")

    # End-to-end: build the CFT reader from the wire-learned type and prove it forwards
    # only the addressed sample.
    _topic, cft = make_cft(router, learned, "A_cft")
    cft_reader = dds.DynamicData.DataReader(dds.Subscriber(router), cft)

    def make(dest):
        s = dds.DynamicData(dtype_local)
        s[FIELD] = dest
        return s

    got = {"addressed": 0, "other": 0}

    def drive():
        app_writer.write(make(ADDRESSED))
        app_writer.write(make(OTHER))
        time.sleep(0.15)
        for sample in cft_reader.take():
            if not sample.info.valid:
                continue
            d = sample.data[FIELD]
            got["addressed" if d == ADDRESSED else "other"] += 1
        return True if got["addressed"] > 0 else None  # None => keep polling (_poll)

    if not _poll(drive, RESOLVE_TIMEOUT_S):
        raise SpikeError("[A] CFT reader (wire-learned type) never received the "
                         f"addressed sample; counts={got}")
    time.sleep(0.5)                      # grace for a wrongly-passed sample to show up
    for sample in cft_reader.take():
        if sample.info.valid:
            d = sample.data[FIELD]
            got["addressed" if d == ADDRESSED else "other"] += 1
    if got["other"] != 0:
        raise SpikeError(f"[A] CFT leaked a non-addressed sample; counts={got}")
    print(f"  [A] CFT on wire-learned type forwards only the addressed sample "
          f"(addressed={got['addressed']}, other={got['other']})  PASS")


def part_b_learn_from_reader(domain):
    print("Part B: router learns control_command from a discovered READER "
          "(destination-side case)")
    app = dds.DomainParticipant(domain, _udpv4_qos())
    router = dds.DomainParticipant(domain, _udpv4_qos())
    try:
        _part_b_body(app, router)
    finally:
        router.close()
        app.close()


def _part_b_body(app, router):
    dtype_local = _act_types().type(TYPE)
    app_topic = dds.DynamicData.Topic(app, TOPIC, dtype_local)
    app_reader = dds.DynamicData.DataReader(dds.Subscriber(app), app_topic)  # noqa: F841

    learned = learn_type_from(router.subscription_reader, TOPIC)
    if learned is None:
        raise SpikeError("[B] router never obtained the type from subscription discovery")
    print(f"  [B] learned type from the wire (reader): {learned.name}")
    assert_complete(learned, "B")

    # Constructing the CFT from the reader-learned type must also succeed.
    make_cft(router, learned, "B_cft")
    print("  [B] CFT construction on reader-learned type succeeded  PASS")


def part_c_inline_proof(domain):
    """Decisive mechanism proof (resolves the spike-vs-MCP contradiction, 2026-07-14).
    ask_connext_question claimed TypeObject v2 is NEVER inline in SEDP and always needs
    TypeLookup. We disable the router's TypeLookup channel entirely
    (enabled_builtin_channels = NONE; SPDP/SEDP live in the separate builtin_discovery_plugins
    field, so basic discovery still runs) and confirm the COMPLETE type STILL resolves — which
    is only possible if it rode inline in the SEDP announcement. Build is the arbiter."""
    print("Part C: prove INLINE by disabling TypeLookup (service-request channel)")
    app = dds.DomainParticipant(domain, _udpv4_qos())
    router_qos = _udpv4_qos()
    router_qos.discovery_config.enabled_builtin_channels = \
        dds.DiscoveryConfigBuiltinChannelKindMask.NONE
    router = dds.DomainParticipant(domain, router_qos)
    try:
        app_topic = dds.DynamicData.Topic(app, TOPIC, _act_types().type(TYPE))
        _w = dds.DynamicData.DataWriter(dds.Publisher(app), app_topic)  # noqa: F841
        learned = learn_type_from(router.publication_reader, TOPIC)
        if learned is None:
            raise SpikeError("[C] type did not resolve with TypeLookup disabled — it was "
                             "NOT inline (would need TypeLookup after all)")
        assert_complete(learned, "C")
        print("  [C] COMPLETE type resolved with TypeLookup DISABLED -> inline in SEDP  PASS")
    finally:
        router.close()
        app.close()


def _udpv4_qos():
    q = dds.DomainParticipantQos()
    q.transport_builtin = dds.TransportBuiltin.udpv4
    return q


def main():
    print(f"type-discovery spike on domain {DOMAIN} (UDPv4-only)\n")
    # Both participants are plain UDPv4 — the router-like one sets NO request_types_filter.
    # CONFIRMED (2026-07-14): for a small type the COMPLETE TypeObject rides inline in the
    # SEDP announcement, so the learner reads it straight off the builtin discovery sample
    # (data.type), rti_view-style, with no TypeLookup round-trip. request_types_filter is
    # neither available (not in the Python QoS binding; runtime XML parser rejects the
    # element on this install) nor needed here; it would only matter for LARGE types whose
    # TypeObject does not fit inline — out of scope (all ACT types are small).
    # Each part runs on its OWN domain with fresh participants, so Part B's reader-learning
    # result cannot be contaminated by Part A's leftover entities.
    failures = []
    parts = (part_a_learn_from_writer, part_b_learn_from_reader, part_c_inline_proof)
    for i, part in enumerate(parts):
        try:
            part(DOMAIN + i)
        except SpikeError as e:
            failures.append(str(e))
            print(f"  FAIL: {e}")
        print()

    if failures:
        print(f"SPIKE FAILED ({len(failures)} failure(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SPIKE PASSED: a no-XML participant learns a COMPLETE type from both a "
          "discovered writer and reader, usable for named access + content filtering.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
