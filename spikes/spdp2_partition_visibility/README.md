# Spike: spdp2_partition_visibility — a monitor participant that sees every discovered
participant's partition, even mismatched ones

## What this proves

D73/D87 Finding 2 established that two participants with disjoint concrete
`participant_partition` values are **mutually invisible** — good for team isolation, but it
means nothing today can build a global roster ("what team is every mesh participant in
right now?") without joining every team itself. This spike checks whether a single
wildcard-partition monitor participant can get that roster visibility without breaking the
isolation between the participants it's watching, and whether it can read back each
participant's *actual* partition value once discovered.

## How to run

```
python3 spikes/spdp2_partition_visibility/spdp2_partition_visibility_spike.py [base_domain]
```

## Result — PASS, stable 3/3 (base domains 3000, 3100, 3200)

- **A (control):** two participants with disjoint concrete `participant_partition`
  (`TEAM_A`/`TEAM_B`) held mutually invisible (`discovered_participants()` empty both sides)
  across the whole hold window — reconfirms D73/D87 Finding 2 on this build.
- **B (wildcard monitor sees both):** a third participant M with `participant_partition =
  ["*"]` discovered both A and B in every run (0–39ms). Confirms the Connext MCP's claim
  that PARTITION QoS wildcard/`fnmatch` matching applies at the DomainParticipant level, not
  just Publisher/Subscriber.
- **C (no bridging):** with M present and matched to both, A and B still never discovered
  each other (held for the full 4s window, 3/3). Confirms the MCP's claim that participant
  partition matching is pairwise/independent — no transitive relay through M. This is the
  property that makes the design viable: a roster/monitor role can exist without leaking
  team topology between teams, which is exactly the kind of leak D85 flagged as the risk of
  merging WAN participants.
- **D (partition-field claim — REFUTED, logged to connext-ai-issues.md):** the MCP claimed
  `ParticipantBuiltinTopicData` exposes a `partition` field for the discovered remote. It
  doesn't, on this `rti.connextdds` 7.7 Python binding — `dir()` on the class, and on a live
  matched instance, show only `partial_configuration`/`participant_name` matching a `part*`
  filter. See `docs/connext-ai-issues/connext-ai-issues.md` (2026-07-20 entry).
- **E (user_data workaround — PASS):** with A/B each stuffing their own partition string
  into `DomainParticipantQos.user_data`, M reads both values back correctly
  (`discovered_participant_data(handle).user_data`) in every run, while A and B stayed
  mutually invisible to each other throughout. This is the mechanism that actually
  satisfies the "aware of what partitions all discovered DPs have, even mismatched" goal.
- **F (SPDP2 stretch, informative only):** repeated B/C/E with `builtin_discovery_plugins =
  SPDP2|SEDP` and immediate `autoenable` (not the disabled-then-delayed-enable sequence D87
  found broken) — same result, 3/3. Does not reopen D87's SPDP2 retraction; SPDP2 isn't
  applied in the router today.

## Why this matters for the roster/C2 use case

- **The mechanism works as a discovery-visibility tool.** A dedicated monitor participant
  with `participant_partition = ["*"]` is a safe way to get a live roster of every
  participant in the mesh regardless of team partition, with no risk of bridging teams to
  each other — confirmed empirically, not just from MCP doc citation.
- **But the "aware of what partitions" part needs an app-level convention, not a builtin
  field.** `ParticipantBuiltinTopicData` doesn't carry the remote's partition value in this
  binding. Any router/C2 design that wants "see everyone's team from one participant" has
  to publish that value itself — `user_data` is a proven, minimal-footprint channel for it
  (confirmed round-tripping correctly here), an alternative to inventing a dedicated topic
  for the same purpose.
- **Caveat:** a wildcard monitor's own endpoints (if it has any Publishers/Subscribers) would
  SEDP-match against every concrete-partition participant it discovers, per normal PARTITION
  QoS rules — this spike only exercises the participant-level (SPDP) visibility, not
  endpoint-level implications. A real monitor role should have no matching pub/sub-level
  partition on any topic it doesn't intend to expose mesh-wide.
