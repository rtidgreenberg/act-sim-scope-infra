# Spike plan: does SPDP2 actually break under disabled-then-delayed-enable? (D87 follow-up)

## Motivation

D87 (2026-07-20) retracted D78's SPDP2 proposal, citing a "minimal `rti.connextdds` repro"
built during live debugging that allegedly showed: two SPDP2 participants, one created
disabled then `enable()`d after a delay (mirroring `ParticipantRegistry`'s D52
create-disabled/attach-conditions/`aws.start()`/`enable_all()` sequence), produce
**asymmetric, non-retrying discovery failure 3/3** — the earlier-enabled participant sees
the later one, but not vice versa, and it never self-heals. That repro was never saved as a
spike deliverable. This task (`docs/cpp_router/spdp2-and-boot-init-tasks.md` Task 1) is to
rebuild it fresh, root-cause the mechanism, and reach a definitive go/no-go before Task 2
(the wildcard-boot-then-revert init pattern) assumes SPDP2's timing model.

## Claims under test

**A. Control** — two SPDP2 participants, both immediate `autoenable` (default), same
partition: discover each other symmetrically and quickly. Sanity check that the harness
itself works and reconfirms D87's own "immediate autoenable discovers symmetrically every
time" baseline.

**B. THE MONEY TEST — disabled-then-delayed-enable, one process.** Two SPDP2 participants
created disabled (factory `EntityFactory::ManuallyEnable`, immediately restored — same
sequence as `ParticipantRegistry::ParticipantRegistry`), same partition. Participant A is
`enable()`d immediately; participant B is `enable()`d after a delay. Swept across a range of
delays (20ms to 45s, spanning `initial_participant_announcements`' burst window and past one
full `participant_announcement_period`/`participant_liveliness_assert_period` default AUTO
cycle of 30s) to see whether the claimed asymmetric, non-healing failure appears at any
point in that range, 3 reps per delay value.

**C. Cross-process replay of B.** Same experiment as B, but A and B are genuinely separate
OS processes (not two participants in one Python process), removing any doubt that
same-process/same-domain-factory participants get some in-process shortcut real WAN routers
wouldn't have. This is the most faithful analog to two independent `router_main` processes
starting at different wall-clock times.

**D. Extended-hold self-heal check.** Repeat B/C with a long post-enable hold window
(comfortably past several `participant_announcement_period` cycles) to distinguish "never
self-heals" from "self-heals, just slower than the isolated short-hold tests suggest."

**E. Wire-level confirmation SPDP2 is genuinely active** (not silently falling back to plain
SPDP, which would trivially explain any observed symmetric self-healing — plain SPDP always
self-heals per D73). Capture with `dumpcap`/`tshark`, diff RTPS submessage writer entity IDs
between an `spdp2=True` and an `spdp2=False` run on this build to confirm SPDP2 participants
emit a genuinely distinct writer entity ID from plain SPDP's `SPDPbuiltinParticipantWriter`
(`0x000100c2`).

**F. Cross-check via Connext MCP** (hint only, not ground truth per repo protocol) — what
does SPDP2's discovery_config actually say should happen when a peer enables late relative
to another? Verify the claimed behavior (periodic bootstrap resend to unmatched peers via
`participant_announcement_period`) against the wire capture, not just the MCP's prose.

## Method

Reuse `spikes/partition_retarget/partition_retarget_spike.py`'s `make_participant`/`fmt`
helpers via `sys.path` insert (same pattern as `spikes/spdp2_partition_visibility/`). UDPv4
only, local domains offset per part. Part C spawns two `python3` subprocesses directly
(not via the shared spike-helper import, since it must be a standalone script per process).

Exit 0 = A holds (sanity) and no failure is observed in B/C/D across the swept delay/hold
ranges — i.e., D87's claimed bug does NOT reproduce. Exit 1 = the claimed asymmetric,
non-healing failure DID reproduce at least once, in which case the wire capture (E) is used
to root-cause the actual mechanism and candidate workarounds get tried next.
