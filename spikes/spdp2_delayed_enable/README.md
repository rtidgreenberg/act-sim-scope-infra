# Spike: spdp2_delayed_enable — root-causing D87's claimed SPDP2 disabled-then-delayed-enable
discovery failure

> **2026-07-22 CORRECTION (read this first).** A controlled re-run overturns the "Part C"
> cross-process conclusion below. With a fixed-domain-per-rep, subprocess-launched runner
> (`xproc_index_probe.py`) and the correct **ever-seen** success metric, **66/66 reps
> discovered bidirectionally** (SPDP2 + plain SPDP, load 0.24–4.76) — the ~56% failure did
> **not** reproduce. The original number was a rig artifact: (1) a fragile final-read metric
> (a first instrumented attempt read 10/10 "FAIL" only because participant A closed ~5 s
> before B's final read — both had actually discovered); (2) discovery run over `lo`, which
> is **not multicast-capable** here, with no `initial_peers`, so it exercised the default
> localhost candidate-port fallback — neither production path; (3) "no multicast observed"
> was a `lo`-only capture artifact (multicast egresses `enp0s3`). **A clean wire capture also
> REFUTES the "no periodic resend" claim below:** a lonely SPDP2 participant
> (`announce_period=2s`) emits participant announcements (writer `0x00010082`) every 2 s for
> the full 20 s hold — SPDP2 *does* periodically re-announce to unmatched peers, exactly as
> the Connext MCP said. See design-decisions.md "D92 CORRECTION (2026-07-22)" for the full
> writeup. The sections below are the ORIGINAL (superseded) findings, kept for history.

## What this was supposed to prove, and what it actually found

D87 retracted D78's SPDP2 proposal on the strength of an unsaved "minimal repro" that
allegedly showed a **deterministic, always-one-direction, never-self-healing** discovery
failure when one of two SPDP2 participants is created disabled then `enable()`d after a
delay. This spike (`spdp2-and-boot-init-tasks.md` Task 1) set out to rebuild that repro,
root-cause the mechanism, and reach a go/no-go. It did — but the answer is more nuanced
than "confirmed" or "refuted," and surfaced a broader risk than the original SPDP2-specific
framing.

**Headline result: D87's claim, as stated, does NOT reproduce.** But a real, related failure
mode *does* exist, and it is not specific to SPDP2.

## Part A/B/D — one process, both participants: 0/27 failures, plus a 90s extended hold

`spdp2_delayed_enable_spike.py` mirrors `ParticipantRegistry::ParticipantRegistry`'s exact
sequence (factory `EntityFactory::ManuallyEnable`, create, restore factory qos, later
`enable()`) inside one Python process, with participant A enabled immediately and B enabled
after a swept delay (20ms to 45s, 3 reps each) plus an extended 90s-hold trial at a 10s
delay. **Every single trial (27/27) discovered symmetrically**, typically within tens to a
few hundred ms of whichever side enabled last. Run:

```
python3 spikes/spdp2_delayed_enable/spdp2_delayed_enable_spike.py [base_domain]
```

This directly contradicts D87's "asymmetric, non-retrying, 3/3" characterization — at least
for two participants sharing one OS process.

## Part C — cross-process replay: the real story is a process-boundary race, not an SPDP2 defect

Two participants in one Python process understates real deployment: actual routers are
**separate OS processes**. `cross_process_participant.py` reruns the same experiment as two
independent `python3` subprocesses (A enables immediately, B enables 5s later):

```
python3 spikes/spdp2_delayed_enable/cross_process_participant.py A <domain> 0  <hold_s> [announce_period_s] [spdp2:0|1]
python3 spikes/spdp2_delayed_enable/cross_process_participant.py B <domain> 5  <hold_s> [announce_period_s] [spdp2:0|1]
```

Result: **intermittent, asymmetric, non-self-healing failures — at a statistically
indistinguishable rate under SPDP2 and under plain SPDP, across two separate measurement
batteries taken at very different host-load levels (see confound discussion below).**

| Configuration | Reps | Failures (one side never discovers the other, held 40-90s) |
|---|---|---|
| SPDP2, default `participant_announcement_period` (AUTO/30s), batch 1 (load ~18-22) | 10 | 5/10 |
| SPDP2, `participant_announcement_period=2s` (candidate workaround), batch 1 | 6 | 3/6 (no improvement) |
| SPDP2, default config, batch 2 (re-run, load ~9-13) | 8 | 5/8 |
| **SPDP2 combined** | **18** | **10/18 (~56%)** |
| Plain SPDP, default config, batch 1 (load ~18-22) | 10 | 6/10 |
| Plain SPDP, default config, batch 2 (re-run, load ~2-10, dropping across the batch) | 8 | 4/8 |
| **Plain SPDP combined** | **18** | **10/18 (~56%)** |

Identical combined rate for both protocols. Batch 2 was a direct re-run requested after
batch 1, specifically to test whether the failure rate held up once the host quieted down.

### The disabled-then-enable mechanism is NOT the cause (enable-mode isolation)

D87's specific causal claim was that the failure is caused by the D52 disabled-then-
delayed-`enable()` sequence, and that immediate autoenable "discovers symmetrically every
time." To isolate that variable, `cross_process_participant.py` gained an `enable_mode`
arg: `immediate` sleeps `delay_s` first and then creates the participant already
autoenabled (same t0/t0+5 liveness stagger, but never touching `ManuallyEnable`/`enable()`).

| Configuration | Immediate autoenable (no delayed-enable dance) | Delayed-enable (D52 sequence) |
|---|---|---|
| SPDP2 | 6/8 fail | 10/18 fail |
| Plain SPDP | 4/8 fail | 10/18 fail |

**The cross-process failure happens at a comparable rate with immediate autoenable — the
disabled-then-enable dance is exonerated.** D87's causal claim is therefore wrong: the
variable it missed is the **process boundary**, not the enable mode. Two participants in
ONE process discover symmetrically every time (Part A, 27/27) regardless of enable timing;
two participants in SEPARATE processes with a staggered start race whether or not they go
through the `ManuallyEnable`/`enable()` sequence. D87's "immediate autoenable is fine"
observation was almost certainly an in-process measurement, which is exactly the case that
never fails.

**Plain SPDP failing at a comparable rate is the key finding.** D73 established that plain
SPDP self-heals mismatched/mutually-invisible participants within ~30s via its indefinite
periodic re-announce — but that measurement was of an **already-discovered peer's ongoing
announcement resuming** after a partition retarget (`spikes/partition_retarget/`). This
spike's scenario is different: **cold-start peer discovery**, where neither side has ever
seen the other and must find each other via the initial bootstrap/candidate-peer-list
mechanism. That mechanism — confirmed by wire capture (see below) — is the same underlying
machinery for both plain SPDP and SPDP2 on this build, and it is where the race lives.

## Wire-level findings

- **SPDP2 is genuinely active, not silently falling back to plain SPDP.** Diffing writer
  entity IDs: plain SPDP uses the classic `SPDPbuiltinParticipantWriter` (`0x000100c2`);
  SPDP2 uses a distinct writer (`0x00010082`) plus a separate reliable "configuration
  channel" pair (`0x00010182`). Both were confirmed present/absent exactly as expected in
  their respective captures.
- **Discovery here is unicast-only, to a small fixed set of "candidate participant-index"
  ports** (e.g. 50978/50980/50982/50984/50986 for one domain, 51228-51236 for another) —
  **no multicast traffic was observed at all** (`ip.dst` never showed a `239.x.x.x` group
  in any capture). Both plain SPDP and SPDP2 bootstrap by blasting to this same small
  candidate-port set, hoping a peer is bound to one of them — this is the actual mechanism
  being raced, independent of SPDP vs SPDP2.
- **SPDP2's own self-initiated bootstrap burst is bounded** (`initial_participant_
  announcements=5`, spaced up to `max_initial_participant_announcement_period=1s` —
  observed as 5 sends over ~1.7s), **and does not resume on its own once that burst
  completes** without an external stimulus (a received packet from a newly-appearing peer
  triggers a reactive re-burst, but nothing periodic happens absent that). This **contradicts
  the Connext MCP's claim** (asked during this spike) that a participant "will resend
  bootstrap messages periodically at `participant_announcement_period` to unmatched
  potential peers" — on this build, no such spontaneous periodic resend was observed over a
  90s hold with zero external stimulus. **Logged as a discrepancy candidate** — see below.

## The host-load confound, and what the re-run showed

Batch 1 ran while this VM had 6 concurrent `claude` processes plus an unrelated `pytest
router/test_e2e/` run active, load average 18-22 on a 10-core host (`%sy` ~50%) — a real,
plausible confound for any test whose outcome depends on OS process-scheduling latency
around a ~1-2s bootstrap burst window. Batch 2 was run specifically to test that theory:
this VM has no truly idle state available (it persistently runs multiple concurrent Claude
Code sessions as steady-state, not just during batch 1), but load had already dropped to
~9-13 by the start of batch 2 and continued falling to ~2-6 by its final reps as other
sessions' work finished — logged per-rep in the run output above.

**Failures kept happening at every load level observed, including the two lowest-load reps
in the entire spike (load 2.12 and 4.20, both plain SPDP, both failed).** The failure rate
did not measurably drop as load fell from ~20 to ~2. This weakens, without fully
eliminating, the pure-host-load explanation: if scheduling contention alone explained the
failures, the ~10x drop in load average should have shown some effect, and it didn't
(clearly) in this sample size. **Best current read: this is a real cold-start
discovery race intrinsic to the default unicast-candidate-port bootstrap mechanism (shared
by plain SPDP and SPDP2 on this build), not primarily a shared-VM artifact** — though the
sample (18 reps/protocol) isn't large enough to fully rule out a residual load effect, and
a true single-tenant host was never available to test against.

## Go/no-go for Task 1

**No clean "go."** The specific, deterministic, SPDP2-only bug D87 described could not be
reproduced. But the investigation surfaced something Task 1 didn't anticipate: **cold-start
peer discovery (two participants that have never seen each other, one coming into existence
after the other) is racier than the already-matched-peer-retargets-partition case D73
measured, for both plain SPDP and SPDP2** — and the failure, when it happens, does not
self-heal within the hold windows tested (up to 90s). Candidate workaround tested
(`participant_announcement_period=2s` instead of AUTO/30s) did not measurably help, which is
consistent with the failure being about the *initial* bootstrap window rather than the
ongoing retry cadence.

**Recommendation:**
1. **Do not reverse D87's SPDP2 retraction on this evidence.** Nothing here shows SPDP2 is
   safe; if anything, it shows the underlying cold-start discovery race is bigger than the
   SPDP2-specific framing suggested, and roughly load-independent in the range tested
   (~56% failure at both high and low load).
2. **This VM never offers a truly idle baseline** (multiple Claude Code sessions run here as
   steady state) — the batch-2 re-run is the closest available control, and it did not
   change the conclusion. Further confidence would require testing on a genuinely dedicated,
   single-tenant host, which isn't available in this environment.
3. **This is relevant to Task 2 (`spdp2-and-boot-init-tasks.md`) beyond SPDP2.** D91 already
   made the right call by gating route enablement on positive verification rather than
   elapsed time — this finding is a concrete argument *for* that caution, since it shows
   cold-start discovery convergence cannot be assumed to complete within any fixed timing
   margin, even under plain SPDP. Task 2's own unresolved boot-storm-shape measurement should
   fold in this process-scheduling-sensitivity angle, not just wire bandwidth.
4. **Connext MCP discrepancy to log**: the claim that SPDP2 periodically resends bootstrap
   announcements to unmatched peers at `participant_announcement_period` did not hold in this
   build's captures (see wire-level findings above) — pending one more clean confirmation
   run (to rule out the same host-load confound suppressing an expected resend), this should
   go into `docs/connext-ai-issues/connext-ai-issues.md`.

## Files

- `PLAN.md` — claims under test and method.
- `spdp2_delayed_enable_spike.py` — Parts A/B/D, one process, run directly (27/27 pass, ~4-5
  min runtime).
- `cross_process_participant.py` — Part C helper, invoked twice (role A/B) per rep from the
  shell; see "How to run" above. Not a self-contained pass/fail script — orchestration and
  interpretation is manual (or scriptable via the shell loop shown above) because it must
  launch genuinely separate processes.
