# Spike: partition_retarget — participant-partition retarget timing (D73)

## TL;DR

| mechanism | rematch latency | idle B/s | retarget event bytes |
|---|---|---|---|
| plain SPDP (default assert) | 86–684ms | 3,785–5,293 | 15,694–16,378 |
| SPDP fast-assert (200ms) | 170–195ms | 64,669–65,425 (~14x) | 15,694–16,162 |
| SPDP2 (post-settle) | **11–20ms** typical (n=8 median 12ms; occasional outlier to 164ms) | **1,612–1,876** (lower than default) | **10,532–11,216** (~30% less) |

**SPDP2 wins on every axis measured** — faster rematch, lower steady-state bandwidth than
even unmodified default SPDP, and fewer bytes per retarget event — but only *after* a
one-time, undocumented-duration post-match handshake settles (empirically ~2s margin on
this rig; not a portable constant, no QoS field sets it). SPDP fast-assert is a viable
fallback with no settle-time risk, but costs ~14x continuous bandwidth forever, mesh-wide.
Both require every WAN participant in the mesh to agree (fast-assert: value, roughly;
SPDP2: not interoperable with plain SPDP at all). Full detail below.

## What this proves

[D73](../../docs/cpp_router/design-decisions.md) claims a live `participant_partition`
retarget (`SET_PARTICIPANT_PARTITION`) is "announcement-paced" and slower to rematch than
a pub/sub-level (SEDP) partition change — sourced from the Connext MCP alone, and flagged
in the doc itself as unconfirmed. This spike measures the real unmatch/rematch timing on
loopback UDPv4 with `rti.connextdds` / Connext 7.7.0.

## How to run

```
python3 spikes/partition_retarget/partition_retarget_spike.py [base_domain]
python3 spikes/partition_retarget/bandwidth_compare.py [rep]     # wire-level bytes, see below
```

Structural parts (A, B) gate the exit code; the Part C/D/E/F timing table is reported, not
gated — see [PLAN.md](PLAN.md) for the claims under test.

## Result — 3/3 stable (base domains 411, 511, 611)

An earlier pass of this spike (base domains 111/211/311) measured `t_local_*` and
`t_remote_*` with **separate, sequential polling loops**, each starting its own clock only
after the previous wait returned. That understated `t_remote_rematch` to ~0ms every time
— a measurement artifact, not a real finding. The driver now polls all signals (match
state on both sides, plus live `discovered_participants()` SPDP visibility) **from one
shared t0**, at a 10ms resolution (`track_transitions`/`hold_never` in
`partition_retarget_spike.py`). The numbers below supersede the earlier pass.

- **A (baseline):** shared `participant_partition` matches normally, 409–823ms.
- **B (mismatch invisibility):** `matched_publications` held at 0 across a continuously
  (not just once) polled hold window in all 3 runs. `discovered_participants()` never
  went non-empty on **either side** in all 3 runs — mismatched participant partitions are
  mutually invisible at the participant (SPDP) level, not merely SEDP-suppressed while
  still SPDP-visible. Stronger than D73's wording, which conflates the two.
- **C/D/E timing (ms, across the 3 runs):**

| | C: participant-partition | D: SEDP (pub/sub) | E: participant-partition, 200ms assert |
|---|---|---|---|
| t_local_unmatch | 0–13 | 0–18 | 10–11 |
| t_remote_unmatch | 0–13 | 0–12 | 0–11 |
| t_local_spdp_lost | 0 | never | 0 |
| t_remote_spdp_lost | 0–13 | never | 0–10 |
| t_local_rematch | **86–684** | 0–12 | **170–195** |
| t_remote_rematch | **86–684** (== t_local_rematch, every run) | 0–12 | **170–195** (== t_local_rematch) |
| t_local_spdp_regained | **86–684** (== t_local_rematch, every run) | 0 | **170–195** (≈ t_local_rematch) |
| t_remote_spdp_regained | 0 | 0 | 0 |

## Verdict on the D73 claim

D73's framing — "unmatch immediate locally, rematch announcement-paced" — is correct that
there's a real slow leg, but wrong about which side carries it. Across all 3 runs:

- **Unmatch is fast on both sides, for both mechanisms** (0–18ms) — no evidence of a slow
  unmatch leg anywhere, participant- or SEDP-level.
- **Rematch is genuinely slower for the participant-partition path (86–684ms vs. SEDP's
  0–12ms) — but `t_local_rematch`, `t_remote_rematch`, and `t_local_spdp_regained` land at
  the *same* instant in every single run.** That means the peer (B) isn't lagging behind
  A at all: both sides' match state flips the moment A's own participant re-establishes
  SPDP sight of B (`t_local_spdp_regained`). `t_remote_spdp_regained` is 0ms throughout —
  B never loses sight of A's *presence* for long, it's specifically A's own bookkeeping
  that gates the whole rematch.
- **The delay tracks `participant_liveliness_assert_period`**: Part E (200ms configured
  assert period) consistently rematched in 170–195ms — close to that period, not spread
  uniformly across 0–200ms — while Part C's default (longer, unconfigured) period produced
  a wider 86–684ms spread. This is consistent with the delay being roughly one period of
  whatever cadence governs the participant's own re-announcement/re-discovery of an
  already-known peer, as D73 speculated — the mechanism just paces the *changer's* view,
  not the observer's.
- **SEDP-level (Publisher/Subscriber) partition retarget is uniformly fast** (0–18ms) on
  both legs, both sides, and never touches SPDP visibility (`spdp_lost` is `never` for
  Part D, confirming it's an orthogonal, endpoint-only mechanism) — this part of D73's
  claim holds cleanly.

Net: "participant-partition retarget is slower than SEDP" holds up quantitatively
(86–684ms vs. 0–18ms), but the practical implication for the Phase 10 restart-fallback
rationale (`connext-investigation-review.md:315-319`) is narrower than D73 implies — there
is no scenario observed where the remote peer lags behind the local participant; the two
converge together, gated by the *changing* router's own re-announcement cadence. Tuning
`participant_liveliness_assert_period` down (as Part E does) directly buys back rematch
latency for the whole mesh, not just for the local view.

## Part F: SPDP2 (`builtin_discovery_plugins=SPDP2|SEDP`)

Confirmed via direct introspection before testing: `SPDP2`/`SDP2` are real flags on
`DiscoveryConfigBuiltinPluginKindMask` in the installed Connext 7.7.0 Python binding, and
a participant built with `SPDP2 | SEDP` constructs successfully. Per the Connext MCP
(unverified beyond the enum's existence — treat the mechanism description as a hint, not
fact): SPDP2 splits discovery into periodic **bootstrap** (small, for new-peer discovery),
reliable **configuration** (sent once when discovery completes, then again on any
participant-config change — e.g. a partition change), and periodic **liveliness**
messages, instead of SPDP's single repeating full announcement.

**First 3 runs (no settle delay) were inconsistent** — 136–774ms, overlapping plain
SPDP's 368–663ms range in the same batch — no clean win. Suspecting we were retargeting
before SPDP2's one-time post-match configuration handshake ("sent once when discovery
completes") had settled, a 2s settle delay was added after baseline match, before
retargeting (`settle_s` param in `_retarget_timing`). The MCP was asked to clarify this
mechanism directly (`ask_connext_question`, unverified beyond what's checked below): it
describes a "first discovered" (bootstrap match, `DATA(Pb)`) vs. "fully discovered" (after
the reliable configuration message `DATA(Pc)` is received) distinction, and explicitly says
**there is no documented QoS field that sets the duration of this post-match window** —
the closest levers are the bootstrap-resend knobs (`initial_participant_announcements`,
`min/max_initial_participant_announcement_period`), which affect time-to-reach the
configuration phase but don't define a fixed handshake delay.

That "no fixed duration, it's a race" framing was then checked two ways, not just the
original 3-run pass:

- **A 7-value sweep of `settle_s` (0.0 → 1.5s, 3 reps each, ad hoc script, not part of the
  committed spike)** showed **no clean monotonic threshold** — rematch times were noisy at
  every settle value tried, including 1.5s (three reps: 456ms, 597ms, 825ms — all slow).
  This contradicts a simple "wait N seconds and it's fixed" model.
- **A larger, dedicated A/B at a fixed `settle_s=2.0s` (8 reps each side)** told a clearer
  story: plain SPDP (`C`) `t_remote_rematch` ranged 123–711ms (median 367ms); SPDP2 (`F`)
  ranged 11–164ms (median 12ms) — **7 of 8 SPDP2 reps landed at 11–20ms** (SEDP-class
  speed), with one 164ms outlier still faster than every C rep but not as clean as the
  rest.

**Reconciling the two:** the settle effect is real (median 12ms vs. 367ms at n=8 is not
noise), but it is **probabilistic, not a hard cutoff** — consistent with the MCP's "race
against an undocumented-duration handshake" description rather than a fixed timer. A short
settle (≤1.5s in our small samples) sometimes lands after the handshake completes and
sometimes doesn't; by 2.0s the handshake has very likely completed, but even then an
occasional slower rep (164ms) is possible. **2.0s is an empirically reasonable margin for
this test rig, not a derived or portable constant** — a real router should not hardcode a
specific wait, since the mechanism has no documented duration to hardcode against.

**Verdict:** SPDP2 is a real, verified improvement for the slow leg D73 identified — median
~30x faster once the (variable-duration, undocumented) post-match handshake has had time to
complete — but it is probabilistic, not deterministic, and a router can't assume it's "hot"
immediately after a peer is matched. `t_local_spdp_regained` still tracks
`t_local_rematch`/`t_remote_rematch` exactly in every SPDP2 rep, so the underlying
mechanism (both sides converging together, gated by re-establishing SPDP-level sight) is
unchanged — SPDP2 just makes that re-establishment reliable-and-on-change instead of
periodic-best-effort, once its own handshake is done. Also note: SPDP2 is **not
interoperable with plain SPDP** — it would need to be enabled on every router participant
in the mesh, not selectively.

## Wire-level bandwidth (`bandwidth_compare.py`)

The timing parts above answer "how fast," not "at what continuous cost." Since Part E's
fast-assert fix and Part F's SPDP2 fix both looked similarly good on speed, the actual wire
traffic was measured directly for three scenarios — plain SPDP (default assert period),
SPDP with the Part E 200ms fast-assert period, and SPDP2 (Part F, 2.0s settle) — via a real
packet capture (`dumpcap -i lo`, no sudo needed here: it has `cap_net_raw`/`cap_net_admin`
and this user is in the `wireshark` group) parsed with `tshark`'s RTPS dissector. Traffic is
attributed to the two test participants by RTPS **GuidPrefix** (a message-header field, one
value per packet — sums cannot double-count a packet across submessages). Two numbers per
scenario: **idle bytes/sec** (8s of matched-and-unchanged steady state) and **retarget
event bytes** (total bytes from the moment the partition is mismatched to full rematch).

**3/3 reps, stable:**

| scenario | idle B/s | retarget event bytes |
|---|---|---|
| plain SPDP (default) | 3,785–5,293 | 15,694–16,378 |
| SPDP fast-assert (200ms) | 64,669–65,425 | 15,694–16,162 |
| SPDP2 (2.0s settle) | 1,612–1,876 | 10,532–11,216 |

This confirms the tradeoff predicted from first principles: **SPDP fast-assert buys its
retarget speed (Part E) at ~14x the continuous steady-state bandwidth of default SPDP** —
every participant pair in the mesh pays that ~65KB/s tax forever, whether or not anything
ever changes, because the periodic message it's resending faster is the full participant
announcement. **SPDP2 wins on both axes at once**: its idle steady state (1.6–1.9KB/s) is
*lower* than even default (unmodified) SPDP's, not just lower than fast-assert SPDP's —
and its retarget event itself moves ~30% fewer bytes than either SPDP variant, consistent
with the reliable configuration message being smaller/more targeted than a resent full
announcement. The only cost SPDP2 doesn't show up in this table is the probabilistic
post-match settle window documented above — that's a latency risk, not a bandwidth one.
