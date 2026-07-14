# Spike: type discovery — learn a usable type purely from the wire

## Question

The C++ dynamic router is a generic relay. In the Phase 7 design discussion we adopted the
premise that **the router knows topic names (from config) but has no type objects** — no
local IDL/XML for the forwarded application types. To create its `DynamicData`
reader/writer for a route topic it must obtain the `DynamicType` from discovery.

For the "learn from the local LAN app endpoint, then build both legs with the same type
object" model to hold, two things must be true, and neither should depend on a
Connext-7.7-only mechanism (a LAN app may be an older/different Connext version, and
`request_types_filter` turned out to be unavailable in the Python binding and rejected by
this install's runtime XML parser):

1. **The type can be learned from a discovered *writer* AND a discovered *reader*.** The
   source-side router faces an app *writer* on its LAN; the destination-side router faces an
   app *reader* on its LAN (e.g. `control_command`: the platform node's app is a command
   *consumer*). So both directions must work.
2. **The learned type is COMPLETE** (carries member names), not MINIMAL (names stripped to
   hashes). The router accesses `DynamicData` fields by name (`data["msg.destination"]`) and
   builds a `ContentFilteredTopic` filtering on a member name (`msg.destination = %0`).
   MINIMAL cannot do either.

## Approach (rti_view / rtiddsspy model — not the 7.7 TypeLookup dance)

Read the type object **directly off the builtin discovery data** (`data.type` on the
participant's `publication_reader` / `subscription_reader`) — the same accessor `rti_view`
and `rtiddsspy` use to subscribe to unknown types. For a *small* type the COMPLETE
TypeObject rides **inline** in the SEDP endpoint announcement, so it is present in the
builtin sample with no TypeLookup round-trip and no `request_types_filter`.

One UDPv4 domain per part (fresh participants, so Part B is not contaminated by Part A):

- **App** participant owns `act_types.xml` and creates `control_command` endpoints — this is
  exactly how the real ACT sim publishes (`harness/act/node_sim/python/*` uses
  `rti.connextdds` + `QosProvider.type("control_command")`).
- **Router-like** participant has **no type library at all** and reads `data.type`.

- **Part A (learn from a WRITER):** app creates a `control_command` writer; router obtains
  the type from publication discovery, asserts COMPLETE (nested `msg.destination` reachable
  by name), builds a CFT from the wire-learned type, and proves end-to-end that the CFT
  forwards only the addressed sample.
- **Part B (learn from a READER):** app creates a `control_command` *reader*; router obtains
  the type from subscription discovery and asserts COMPLETE the same way (named access + CFT
  construction). This is the destination-side case.
- **Part C (prove it is INLINE, not TypeLookup):** app creates a writer; the router disables
  its TypeLookup channel entirely (`enabled_builtin_channels = NONE` — SPDP/SEDP live in the
  separate `builtin_discovery_plugins` field, so basic discovery still runs) and confirms the
  COMPLETE type STILL resolves. It can only do so if the TypeObject rode inline in the SEDP
  announcement. This is the decisive test that settles the spike-vs-MCP contradiction below.

## Pass / fail

PASS iff all three parts hold: obtain a COMPLETE `DynamicType` from the wire (from a writer
and from a reader), usable for named `DynamicData` access and content filtering, with no
`request_types_filter` and no local type library; and prove the mechanism is inline (Part C).
`spikes/type_discovery/type_discovery_spike.py` exits nonzero on any failed assertion; it
reports the mechanism (INLINE vs DEFERRED) and distinguishes "endpoint never discovered" from
"endpoint seen but type never resolved."

## Result — PASS (2026-07-14, Connext 7.7.0, x64Linux4gcc7.3.0, rti.connextdds py38)

- A no-type participant learns `control_command` from **both** a discovered writer (Part A)
  and a discovered reader (Part B).
- The learned type is **COMPLETE**: `msg.destination` is reachable by name; a CFT built from
  the wire-learned type forwards only the addressed sample (`addressed=1, other=0`).
- **Mechanism is INLINE, proven decisively (Part C).** With the router's TypeLookup channel
  **fully disabled** (`enabled_builtin_channels = NONE`), the COMPLETE type STILL resolves —
  so it rode inline in the SEDP announcement, not via TypeLookup. The instrument also shows
  the type present on the *first* endpoint sample (no endpoint-then-type gap). No
  `request_types_filter`, no matching local endpoint, no local XML. Reading `data.type` off
  the builtin readers is sufficient (the rti_view/rtiddsspy model).
- Stable **3/3** (and earlier 5/5) repeated runs once each part runs on its own domain with
  fresh participants. The one earlier Part B failure was test-harness cross-contamination
  (participants reused across parts), **not** a writer-vs-reader difference in Connext.

## MCP contradictions found here

Recorded canonically in [`docs/connext-ai-issues/connext-ai-issues.md`](../../docs/connext-ai-issues/connext-ai-issues.md)
(2026-07-14 entries): `validate_xml_code` accepted a `<request_types_filter>` element the
runtime parser rejects; `ask_connext_question` gave a nonexistent Python
`discovery_config.request_types_filter` accessor; and `ask_connext_question` claimed
TypeObject v2 is never inline / always TypeLookup, which Part C disproved. Build is the
arbiter.

## Residuals / out of scope

- **Large types / the size threshold.** The inline-vs-TypeLookup boundary is a **QoS size
  threshold** (only a TypeObject exceeding the limit is withheld from inline SEDP). The exact
  knob is **unconfirmed** — the MCP's claim that `type_object_max_serialized_length` is
  "v1-only" is untrusted given its other errors here. A type larger than the threshold would
  need TypeLookup, and for a learner with no matching local endpoint that means
  `request_types_filter` — which on this install is **C++-only** (not in the Python binding,
  rejected as an XML profile element). Testing a >threshold type would pin both the knob and
  the boundary. **Not on the Phase 7 path:** all ACT types are tiny (`base_type`: a few
  strings), well under the default threshold, so they propagate inline.
- **Cross-version.** Both sides here are the same installed 7.7 Python binding (the ACT sim
  uses it too). A genuinely older LAN app (e.g. 6.x) is **not** testable on this
  single-version rig. The mechanism used — inline TypeObject read directly from builtin
  discovery — is the version-robust one (it is what `rtiddsspy`/`rti_view` rely on across
  mixed-version systems), but confirming a specific old version would need a mixed-version
  rig or an actual ACT app build.

## Design implications

- The router acquires types by **reading `data.type` off builtin discovery** (rti_view
  model), gated per topic on "type resolved from discovery," then builds both legs with that
  one type object. This supersedes the D13 `request_types_filter`/TypeLookup framing for the
  ACT (small-type) case.
- Confirms the Phase 7 "learn from the LAN app endpoint (writer on source side, reader on
  destination side), then create the WAN leg with the same type" model is viable, and that a
  CFT on a wire-learned type filters correctly — which the `control_command` route needs.
