# Spike: spdp2_wan_lan_mix — SPDP2 on WAN, plain SPDP on LAN, same router process

## Question

D18 already gives each router process a separate WAN participant (per physical network)
and LAN participant. The [partition_retarget spike](../partition_retarget/) found SPDP2 is
a real win for the WAN-only `participant_partition` team-scoping mechanism (D73) — faster
rematch, lower steady-state bandwidth, fewer bytes per retarget event — but it requires
*every* WAN participant in the mesh to run SPDP2 (not interoperable with plain SPDP), and
has a probabilistic post-match settle window. The LAN participant has no team-partition
retarget scenario at all (D20: LAN partition is fixed), so there's no reason to pay SPDP2's
settle-time risk there.

This spike checks whether a router can selectively run **SPDP2 on its WAN participant(s)
only, plain SPDP on its LAN participant**, in the same process, without cross-interference
— and checks the operational failure mode if a mesh member forgets to enable SPDP2 on its
WAN participant (does it fail loudly, partially, or cleanly invisible?).

## Approach

Single-process Python driver (`spdp2_wan_lan_mix_spike.py`), reusing helpers from
`spikes/partition_retarget/partition_retarget_spike.py` (`make_participant` already
supports a `spdp2=` flag). UDPv4-only. Each part on its own domain block.

- **Part A — mixed process, functional baseline:** one process holds four participants at
  once: `wan_local`/`wan_peer` (both `SPDP2|SEDP`) on the WAN domain, `lan_local`/`lan_peer`
  (both plain/default SPDP) on a separate LAN domain. Assert: WAN pair matches (SPDP2 still
  works), LAN pair matches (plain SPDP still works), and neither pair's participants show up
  in the other's `discovered_participants()` (domain isolation holds, no bleed between the
  two discovery configs sharing a process).
- **Part B — SPDP2 WAN rematch speed unaffected by a concurrent plain-SPDP LAN
  participant:** with all four participants from Part A still alive, run the same
  `participant_partition` retarget-timing dance from `partition_retarget_spike.py`'s Part F
  (settle 2.0s) on the WAN pair. Compare `t_remote_rematch` against the standalone Part F
  result (11–20ms typical) — confirms the LAN participant's presence in-process doesn't
  slow down or interfere with the WAN participant's SPDP2 behavior.
- **Part C — fail-closed boundary check:** a fresh "legacy" WAN peer using plain SPDP (not
  SPDP2) joins the WAN domain where a `SPDP2`-only participant runs. Hold and assert
  `matched_publications == 0` and `discovered_participants()` empty on both sides — the
  operational risk this surfaces: if one router in the mesh isn't upgraded to SPDP2, does
  it silently vanish from the mesh (safe, loud-in-monitoring-terms failure) rather than
  half-working? This confirms it's the former.

## Pass / fail

PASS iff: Part A's WAN and LAN pairs both match, with no cross-domain visibility; Part B's
`t_remote_rematch` lands in the same fast range as standalone Part F; Part C shows the
legacy-SPDP peer never matches and never becomes SPDP-visible to the SPDP2 participant.

## Result — PASS, stable 3/3 (base domains 2000, 2100, 2200)

All three parts held on every run. Part A: WAN pair (SPDP2) matched 185–968ms, LAN pair
(plain SPDP) matched 0–617ms, `discovered_participants()` showed exactly 1 peer per
participant every time (no cross-domain leak). Part B: WAN retarget `t_remote_rematch` was
10–11ms in all 3 runs — in the standalone Part F fast range, unaffected by the concurrent
plain-SPDP LAN participant. Part C: a plain-SPDP "legacy" peer stayed mutually invisible to
the SPDP2 participant in all 3 runs (`matched_publications=0`, `discovered_participants()`
empty both sides) — same fail-closed signature as a participant-partition mismatch.

Full writeup and implications for the D73/Phase 10 SPDP2 adoption decision in
[README.md](README.md). Note: domain IDs above ~5900-6000 hit Connext's default port-
mapping ceiling on this install (`RTIOsapiSocket_bindWithIP: Permission denied`, tries to
bind a privileged port) — this spike's domains (2000s) are safely inside the working
range; keep new spikes' base domains under that ceiling too.
