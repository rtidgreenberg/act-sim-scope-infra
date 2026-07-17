# Spike: partition_retarget — participant-partition retarget timing (D73)

## Question

[D73](../../docs/cpp_router/design-decisions.md) makes `participant_partition` the sole
team-scope mechanism for the `platform-team` router instance: a `SET_PARTICIPANT_PARTITION`
retarget is supposed to make out-of-team participants mutually invisible (SEDP endpoint
discovery suppressed entirely), while an in-team retarget re-enables matching. D73 claims
this rematch is "announcement-paced" — slower than pub/sub partition changes, which ride
the reliable SEDP channels — but says so on the strength of an `ask_connext_question` MCP
answer alone, and flags it as unconfirmed: "the suppression claim is MCP+docs-sourced and
must be confirmed empirically." `SET_PARTICIPANT_PARTITION` isn't implemented yet
(`RouterController.cxx` rejects it as "unsupported in this build") — this spike runs ahead
of Phase 10 to pin real numbers before that work starts.

Confirmed by direct introspection before writing this spike: `DomainParticipantQos.partition`
is a real, live-settable field (`type: rti.connextdds.Partition`), so the mechanism
`participant_partition` config maps to is genuine, not a fabricated QoS name.

## Approach

Single-process Python driver (`partition_retarget_spike.py`), two `DomainParticipant`s (A,
B) per part — no subprocess peers needed since nothing gets killed. UDPv4-only. Types built
programmatically (`dds.StructType`), no XML dependency. Each part on its own domain
(`base + i`), default base domain 111 (fresh block, clear of `matched_endpoints` 61-63 and
`presence` 71+).

- **Part A — baseline:** A and B share one `participant_partition` value; A's reader
  matches B's writer (default/empty pub-sub partitions) within the usual short window.
  Sanity check only.
- **Part B — mismatch invisibility:** A and B on **different** `participant_partition`
  values. Assert `matched_publications`/`matched_subscriptions` stay at 0 over a hold
  window (b1), and separately report whether each side's `discovered_participants()`
  includes the other's GUID at all (b2) — D73's wording conflates "mutually invisible" with
  "SEDP suppressed"; this splits them.
- **Part C — THE MONEY CLAIM, retarget timing:** A and B start matched on a shared
  partition. At t0, retarget A to a mismatched partition via `p.qos` read-modify-write;
  poll both sides' `matched_*` counts (~15ms) to get `t_local_unmatch` (A's own view) and
  `t_remote_unmatch` (B's view). Then retarget A back to B's value; measure the mirror
  `t_local_rematch` / `t_remote_rematch`. D73 predicts unmatch is fast/local, rematch is
  announcement-paced and materially slower.
- **Part D — SEDP comparison:** same matched-pair retarget experiment, but mutating
  `PublisherQos.partition`/`SubscriberQos.partition` on an already-matched pair instead of
  the participant partition — a same-run number for the "SEDP is faster" side of the claim.
- **Part E (stretch) — fast assert period:** repeat C with a short
  `discovery_config.participant_liveliness_assert_period` (200ms) to see whether rematch
  time scales with that knob.
- **Part F — SPDP2 comparison:** repeat C with
  `discovery_config.builtin_discovery_plugins = SPDP2 | SEDP` on both participants. SPDP2
  (confirmed a real, buildable Connext 7.7.0 discovery plugin via direct introspection) is
  RTI's alternative discovery protocol that sends participant configuration changes to
  already-matched peers over a reliable channel instead of the periodic best-effort
  announcement SPDP uses — the MCP names partition changes specifically as a case that
  benefits. Includes a `settle_s` delay after baseline match, added after the first pass
  showed inconsistent numbers (suspected: retargeting before SPDP2's one-time post-match
  configuration handshake had settled).

This is a measurement spike, not a strict-gate spike like `presence`/`matched_endpoints`:
its job is to produce timing numbers. It still exits nonzero if a structural assumption
breaks (Part A never matches at baseline, or Part B never reaches invisibility at all).

## Pass / fail

Structural PASS iff: Part A reaches a match; Part B holds `matched_* == 0` for the full
window. Parts C/D always "pass" in the sense of printing their four-number timing table —
the D73 comparison itself is reported as an observed verdict, not an assertion.

## Result — PASS (structural), stable 3/3 (base domains 411, 511, 611)

An earlier pass (base 111/211/311) used separate sequential polling loops per signal,
which understated `t_remote_rematch` to ~0ms as a measurement artifact (each wait started
its own clock only after the prior one returned). The driver was rewritten to poll all
signals — both sides' match state plus live `discovered_participants()` SPDP visibility —
from one shared t0 at 10ms resolution; the numbers below supersede the earlier pass.

Part A baseline matched in 409–823ms. Part B held `matched_publications=0` continuously
in all 3 runs, and `discovered_participants()` never went non-empty on **either** side —
mismatched participant partitions are mutually invisible at SPDP, not just
SEDP-suppressed (stronger than D73's wording).

Parts C/D/E (informational, not gated): unmatch is fast (0–18ms) on both sides for both
mechanisms. Rematch is genuinely slower for participant-partition (86–684ms) vs. SEDP
(0–12ms) — but **`t_local_rematch`, `t_remote_rematch`, and `t_local_spdp_regained` land
at the exact same instant in every run**: the peer isn't lagging behind, both sides flip
together the moment the *changing* participant re-establishes its own SPDP sight of the
peer. `t_remote_spdp_regained` is 0ms throughout. The delay tracks
`participant_liveliness_assert_period` (Part E: 200ms configured period → 170–195ms
rematch, clustered near the period rather than spread 0–period; Part C's longer default
period → wider 86–684ms spread). Full numbers and verdict in [README.md](README.md).

**This corrects D73's framing**, not just confirms/refutes it: the "announcement-paced"
mechanism is real and roughly D73's claimed magnitude, but it paces the *changer's own*
re-discovery of an already-known peer — once that fires, the whole mesh (both sides)
converges together. There is no observed scenario where the remote peer specifically lags;
the Phase 10 restart-fallback rationale (`connext-investigation-review.md:315-319`) should
weigh that the slow leg is bounded by `participant_liveliness_assert_period` and shared by
the whole link, not confined to a remote straggler.

**Part F (SPDP2):** the first 3 trials (2s settle delay) beat plain SPDP in the same run
(122ms vs 388ms; 12ms vs 607ms; 20ms vs 126ms). Asked the MCP directly why a settle delay
was needed; it describes an undocumented-duration "first discovered vs. fully discovered"
handshake race (`DATA(Pb)` bootstrap match vs. `DATA(Pc)` reliable config exchange) with
**no QoS field that sets its duration**. Checked that two ways: a 7-value `settle_s` sweep
(0–1.5s, 3 reps each, ad hoc, not in the committed spike) showed **no clean monotonic
threshold** — even 1.5s gave 3 slow reps (456–825ms). But a larger dedicated A/B at a fixed
`settle_s=2.0s` (8 reps each side) told a clearer story: plain SPDP ranged 123–711ms (median
367ms) vs. SPDP2's 11–164ms (median 12ms), 7/8 SPDP2 reps at SEDP-class speed (11–20ms).

**Reconciled verdict:** the SPDP2 speedup is real (~30x at the median, n=8) but
**probabilistic, not deterministic** — consistent with the MCP's "undocumented-duration
race" description, not a fixed timer. 2.0s is an empirically reasonable margin for *this
test rig*, not a derived or portable constant; a real router can't safely hardcode a
specific wait since Connext documents no duration to hardcode against. Also requires SPDP2
on every participant in the mesh (not interoperable with plain SPDP). Full numbers and
caveats in [README.md](README.md).

**Wire-level bandwidth (`bandwidth_compare.py`), stable 3/3 reps:** plain SPDP idles at
3,785–5,293 B/s; SPDP fast-assert (Part E's 200ms knob) idles at 64,669–65,425 B/s — **~14x
more continuous bandwidth for its retarget-speed win**; SPDP2 idles at 1,612–1,876 B/s,
*lower* than default SPDP, while also moving ~30% fewer bytes per retarget event
(10,532–11,216 vs. 15,694–16,378 for either SPDP variant). SPDP2 wins on both bandwidth axes
at once; fast-assert SPDP trades continuous bandwidth for speed. Measured via `dumpcap`
capture on `lo` + `tshark`'s RTPS dissector, attributing packets to the two test
participants by RTPS GuidPrefix (message-header field — no per-submessage double count).
Full table in [README.md](README.md).
