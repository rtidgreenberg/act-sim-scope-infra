# Spike: spdp2_wan_lan_mix — SPDP2 on WAN, plain SPDP on LAN, same router process

## What this proves

The [partition_retarget spike](../partition_retarget/) found SPDP2 is a real win for the
WAN-only `participant_partition` team-scoping mechanism (D73), but it requires every WAN
participant in the mesh to run SPDP2 (not interoperable with plain SPDP) and has a
probabilistic post-match settle window. The LAN participant never does a partition
retarget (D20: LAN partition is fixed), so there's no reason to pay that settle-time risk
there. This spike checks whether a router can run **SPDP2 on its WAN participant only,
plain SPDP on its LAN participant**, in the same process, without interference — and
checks what happens if a mesh member forgets to enable SPDP2.

## How to run

```
python3 spikes/spdp2_wan_lan_mix/spdp2_wan_lan_mix_spike.py [base_domain]
```

## Result — PASS, stable 3/3 (base domains 2000, 2100, 2200)

- **A (mixed process, functional baseline):** one process holding four participants at
  once — `wan_local`/`wan_peer` on `SPDP2|SEDP`, `lan_local`/`lan_peer` on plain SPDP.
  WAN pair matched in 185–968ms (SPDP2, consistent with the partition_retarget baseline);
  LAN pair matched in 0–617ms (plain SPDP, unaffected). `discovered_participants()` showed
  exactly 1 peer on every participant in all 3 runs — no cross-domain leak between the two
  discovery-plugin configs sharing one process.
- **B (SPDP2 rematch speed unaffected by a concurrent plain-SPDP LAN participant):** with
  all four participants alive, the WAN pair's `participant_partition` retarget (2.0s
  settle, same dance as `partition_retarget_spike.py` Part F) rematched in **10–11ms** in
  all 3 runs — squarely in the standalone Part F range (11–20ms typical), not the slow
  86–684ms plain-SPDP range. The concurrent plain-SPDP LAN participant in the same process
  does not slow down or interfere with the WAN participant's SPDP2 behavior.
- **C (fail-closed boundary check):** a fresh WAN peer using plain SPDP (representing a
  mesh member that wasn't upgraded to SPDP2) joined the domain where an SPDP2-only
  participant runs. In all 3 runs: `matched_publications` held at 0 across the hold window,
  and `discovered_participants()` never went non-empty on **either** side — the legacy
  peer and the SPDP2 participant are mutually invisible, exactly like the participant-
  partition-mismatch case from the partition_retarget spike.

## Why this matters for the D73/Phase 10 decision

- **The selective-adoption plan is viable**: a router can put SPDP2 only where it's
  needed (WAN, for team-scope retargets) and leave LAN on plain SPDP, with no observed
  cross-talk or performance interference between the two configs in one process.
- **The failure mode for an un-upgraded mesh member is safe, not silent-partial**: it
  doesn't half-match, doesn't intermittently connect, doesn't error loudly either — it's
  cleanly invisible, the same operational signature as any other participant-partition
  mismatch a router already has to handle and monitor for. That means rolling out SPDP2
  incrementally across a mesh has a predictable, already-familiar failure mode (a router
  that should be visible isn't) rather than a novel one — but it does mean a
  partially-upgraded mesh will show real routers as "missing," not degraded, until every
  WAN participant is upgraded.
