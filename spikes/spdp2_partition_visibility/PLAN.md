# Spike plan: partition-visibility monitor (D73/D87 follow-up)

## Motivation

D73/D87 Finding 2 established that two participants with disjoint concrete
`participant_partition` values are **mutually invisible** — `discovered_participants()`
stays empty on both sides, not merely SEDP-blocked. That's the isolation property Phase 10
wants. But it creates a gap for anything that wants a **global roster**: a C2 or monitoring
role that needs to know what team/partition every mesh participant currently claims, without
itself joining every team's traffic plane (see `orchestrator-design-capture` — a per-node
mission orchestrator concept where C2 liveliness rides "the existing mesh roster").

Question: can a single participant get discovery visibility into every other participant's
existence *and* its actual partition value, regardless of partition mismatch, without
breaking the mutual-invisibility guarantee between the participants being observed?

## Claims under test

**A. Control/sanity** — reconfirm D73/D87 Finding 2 still holds on this build: two
participants with disjoint concrete `participant_partition` ("TEAM_A"/"TEAM_B") never
appear in each other's `discovered_participants()`.

**B. Wildcard monitor visibility** — a third participant M with `participant_partition =
["*"]` sees both A and B in `discovered_participants()`. Sourced from the Connext MCP
(2026-07-20): PARTITION QoS wildcard/`fnmatch` matching applies at the DomainParticipant
level too, and `["*"]`-only membership also implicitly carries the default `""` partition.
Unverified by prior spikes — verify here.

**C. No bridging** — with M present and matched to both A and B, A and B still do NOT see
each other. Matching is pairwise per the MCP; this is the property that makes the whole
design viable for a router that must not let a monitor role leak team topology between
teams.

**D. Can M read A's/B's *actual* partition value?** — the MCP claimed
`ParticipantBuiltinTopicData` "includes a `partition` field for the remote participant."
Check the live `rti.connextdds` 7.7 Python binding: does
`discovered_participant_data(handle)` expose anything partition-shaped? (Static check
already done: `dir(dds.ParticipantBuiltinTopicData)` has no `partition` attribute — only
`partial_configuration` and `participant_name` match a `part*` filter. This part re-checks
against a real matched instance, not just the class shape, in case it's a dynamic/property
path.)

**E. Practical workaround if D comes back negative** — have A/B stuff their own partition
string into `DomainParticipantQos.user_data` (a field every prior spike confirms travels
with the participant announcement once matched). Confirm M can read each one's actual
partition value back out via `discovered_participant_data(handle).user_data`, while A/B
remain mutually invisible (C still holds). This is the fallback mechanism the router would
actually ship if D is negative.

**F (stretch, informative only)** — repeat B/C/E with `builtin_discovery_plugins =
SPDP2|SEDP` instead of plain SPDP, using immediate `autoenable` (matching how
`partition_retarget_spike.py`/`spdp2_wan_lan_mix_spike.py` measured it, NOT the
disabled-then-delayed-enable sequence D87 found broken). Not gating — SPDP2 is currently
retracted from the router (D87) and this doesn't reopen that decision, it just checks
whether the wildcard-monitor mechanism would also work if SPDP2 is revisited later.

## Method

Reuse `spikes/partition_retarget/partition_retarget_spike.py`'s helpers
(`make_participant`, `wait_matched`, `hold_never`, `fmt`, `SpikeError`) via `sys.path`
insert, same pattern as `spikes/spdp2_wan_lan_mix/`. UDPv4-only, local domains, run at
0/1/2 domain offsets per part to avoid cross-part interference.

Exit 0 = A/B/C structurally hold (the safety-critical claims). D/E/F print findings; D and
F are informative regardless of outcome, E gates on whether the workaround actually works
(needed if D is negative, which the static check suggests it will be).
