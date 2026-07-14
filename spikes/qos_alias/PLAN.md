# Spike: QoS-alias resolution (Phase 7a) against the real production QoS libraries

## Question

Phase 7a (D60) resolves the named QoS aliases in `control-platform.yaml`
(`wan_event → WAN_QOS_LIB::event_qos`, participant `qos: control_wan_udpv4_qos`, …) from the
loaded `qos_libraries` XML, and applies them to route entities and participants. D60's API
specifics were **MCP-sourced and never build-verified**, and the connext MCP has been wrong
3× on this exact Python/XML QoS surface (see the `docs/connext-ai-issues` submodule). Does
the mechanism actually work against the *real* files the config names?

## Approach

Load the three `qos_libraries` `control-platform.yaml` declares
(`harness/act/config/qos/{lan,wan}_qos_lib.xml`, `relay/qos_isc.xml`), resolve every profile
its `qos_profiles:` map references, apply one to real endpoints and one to a participant, and
confirm they work — all against Connext 7.7.0 via `rti.connextdds` (the same binding the ACT
sim and the e2e suite use). Parts:

1. **Load** the three libs.
2. **Resolve** every alias→profile the config uses (endpoint + participant).
3. **Apply + forward**: build a writer/reader with the resolved `event_qos` and confirm they
   match and forward a `control_command` sample end-to-end (the QoS is usable, not just
   parseable).
4. **Participant-from-profile**: create a `DomainParticipant` from the resolved
   `control_participant_udpv4_qos` (participant-level `qos:` application).

## Pass / fail

PASS iff the mechanism works (parts 1–4). Config *defects* the spike detects (a missing
profile) are reported but do not fail the run — they are bugs in the config, and exactly what
7a's `validate_qos_aliases` (D44/D60) must catch at load time.
`spikes/qos_alias/qos_alias_spike.py` exits nonzero only on a mechanism failure.

## Result — PASS (2026-07-14, Connext 7.7.0, x64Linux4gcc7.3.0, rti.connextdds py38)

Mechanism works, stable 3/3: 6 libraries load from the 3 files; the real profiles resolve;
`event_qos`-profiled writer/reader match and forward; a participant is created from
`control_participant_udpv4_qos`. **D60's mechanism is sound.** But the spike surfaced three
concrete facts D60 did not capture — the reason 7a was *not* high-confidence on paper:

1. **The production QoS libs are templated with 14 environment variables** (`*_LAN_PEER*`,
   `*_WAN_PEER*`, `WAN_HB_PERIOD_SEC`, `WAN_TTL`, `WAN_TIMEOUT_SEC`, …). They do **not parse**
   unless those are defined — `RTIXMLHelper` fails with "Undefined environment variable". So
   `router_main`/7a cannot just point a `QosProvider` at these files; the deployment must
   supply the env (the ACT harness does before launch). The spike sets loopback/test values
   to be self-contained.
2. **The WAN participant profile has an env-var *constraint*.**
   `participant_liveliness_assert_period` is hardcoded 30 s and
   `participant_liveliness_lease_duration = $(WAN_TIMEOUT_SEC)`; Connext requires assert <
   lease, so **`WAN_TIMEOUT_SEC` must be > 30** (the XML documents DEFAULT 100). A value ≤ 30
   produces an *inconsistent participant QoS that fails to create* — discovered when Part 4
   first ran with `WAN_TIMEOUT_SEC=30` and `create_participant` threw "Inconsistent QoS".
3. **`control-platform.yaml` referenced a non-existent profile (found here, now fixed).**
   `lan_status_1hz` pointed at `LAN_QOS_LIB::status_1hz_qos` — the lib defines
   `status_1sec_qos` / `status_qos`, not `status_1hz_qos`. Fixed to `status_1sec_qos`. 7a's
   `validate_qos_aliases` must still add a profile-**existence** check (not just the
   `is_resolvable_qos_alias` string rule) so this class of error is caught at load time; the
   spike's resolve loop reports any unresolvable alias.

## API confirmed against the build (vs D60's MCP-sourced calls)

- Multi-file load: `dds.QosProvider(";".join(paths))` (Python) — a `;`-joined URL group.
- Resolve: `datareader_qos_from_profile("LIB::prof")` / `datawriter_qos_from_profile` /
  `participant_qos_from_profile` — **not** D60's `provider.datareader_qos("LIB::profile")`.
- **C++ residual:** the router is C++, so the C++ `QosProvider`/`datareader_qos_w_profile`
  surface still needs a compile-check at implementation time — the MCP is not trusted, and
  this spike only proves the Python binding + the underlying (language-agnostic) load/resolve
  behavior of the real files.

## Implications for Phase 7a

7a is now **high confidence on mechanism**, with three concrete deliverable additions D60
must absorb: (a) require/propagate the QoS-lib env vars (fail fast, naming the missing var);
(b) `validate_qos_aliases` must check profile *existence* in the loaded provider, not just the
`is_resolvable_qos_alias` string rule — and it would catch the `lan_status_1hz` bug; (c) fix
that alias in `control-platform.yaml` (→ `status_1sec_qos`). The C++ API surface is the only
remaining unproven bit and is a compile-check, not a design risk.
