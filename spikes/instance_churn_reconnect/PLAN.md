# Instance churn reconnect spike

Implement the native endpoint stage of the
[test plan](../../docs/instance-churn-reconnect-test-plan.md), then add actual
router legs without modifying the running mesh or production QoS.

Initial native and actual-adapter stages are now implemented and exercised.
Schema-3 main native and routed gates each report 3 passes and one Status
outage failure with 41 ghosts. See [measurements and remaining scope](README.md).
The subsequent native flow-controller/prioritization experiment is recorded
below; actual-router priority integration and radio-constrained qualification
remain separate next steps, not completed production changes.

The ContactReport/Event-only transient-local extension also passes both healthy
and outage cases natively and through actual routers. Native late readers
converge with the original writer alive and observe all measured disposed keys.
Only durability kinds change in experimental XML/private router snapshots;
production QoS, writer depth, and resource limits stay unchanged. Retained
history budgets, radio pacing, and writer/router restart remain unqualified.

The subsequent 120-second outage with unchanged production Event QoS fails
natively and through actual routers: both leave 160 disposed-contact ghosts.
Native readers become inactive around 30.5 seconds and volatile history is
removed despite the 300-second participant lease. A never-disposed stationary
contact remains ALIVE; actual application health transitions STALE/DEAD and
recovers independently. No production timer was changed. A longer reliable-reader
retry window and its queue/resource costs require a separate comparison.

The 300-second timeout-policy retest subsequently recovers contact state and
ACKs without reader inactivity, but leaves native latency gates failing.
The 2026-11-12 priority experiment adds XML-authored HPF pacing, topic-name
`WriteParams.priority` for writes/disposals/ACKs, and one channel per logical
topic inside the same Event writer. Four deterministic queued-send assertions
prove that channels, not sample priority alone, let a high-priority command
overtake 400 contacts while retaining their order. Eleven host tests and the
same-key dispose/revival control pass.

All four serial two-minute outage performance gates still fail. Per-topic
channels recover all command ACKs and reduce worst fresh-command RTT from
9.16 to 2.39 seconds, still above the 2-second target. The nominal 2 Mbps token
budget leaves contact queues undrained within the existing test window;
sample priority alone leaves fresh commands queued too. Retain these failures.
The next step is measured usable-capacity/recovery budgeting and broader
qualification, not deploying the experimental three-topic channel catalog or
declaring command/contact scheduling complete. See [results](README.md#asynchronous-topic-priority-experiment-2026-11-12).

The subsequent `priority_bands` mode uses generic `DDSPRIFLTR` channels
`@priority >= 100` and `@priority < 100`; topic names remain only in the
startup priority map/CLI, not channel XML. Twelve host tests and twelve native
ordering/lifecycle checks pass, including priorities 0/99/100 and same-band
FIFO behavior. A configuration-only inversion moves ContactReport into the
high band and passes the revival control. The full two-minute band test
acknowledges every command but still fails: fresh RTT 2.17 seconds, 7,296
pending samples and unconverged contact state. Status is outside this Event
controller and is not implicitly assigned a lower global priority. See
[band design/results](README.md#simplified-priority-bands-2026-11-12).

The subsequent recovery/packing measurement extends only the diagnostic drain
allowance, not acceptance thresholds. At equal nominal 2 Mbps, coarse/fine
controllers converge in 72.664/71.828 seconds, preserving all 14,561 valid
contact writes; both fail latency budgets. Native batching converges final
state in 64.627 seconds but has 15.846-second fresh-command RTT and 12,400
valid source writes not observed. Reject this candidate. The Event oracle now
checks every valid write, not just final state, and a separate 12-second outage
reproduces missing batched history while the unbatched control covers all writes.
Sixteen host tests and sixteen native scheduling/lifecycle controls pass.

Unbatched disposals are mostly individually packetized, with occasional paired
disposals; no logical coalescing is implemented. Both tested MultiChannel
filters send ordinary contact DATA to a local ACK-only reader that filters it
out. Qualify sender-side filtering/fan-out and real recovery capacity before
adoption. Burst allowance and repair tuning are not outage-qualified; a
latest-state/tombstone protocol would require an explicit semantics decision.
Keep raw bounded captures under `debug/pcap`, separate from frozen reports.
See [measurements and limitations](README.md#recovery-pacing-disposal-packing-and-batching-2026-11-12).

The subsequent self-ignore correction explains the extra local recipient:
publication ignore prevents local receive delivery, but subscription ignore is
also needed to remove local readers from writer destinations. Both are now
applied in the spike; production retains writer ignore and adds WAN reader
ignore, including deferred Status startup. Healthy Event DATA UDP bytes halve.
The corrected two-minute run has 13.645-ms worst fresh command RTT, complete
valid-write/ACK coverage and 60.342-second contact convergence. Contact recovery
still fails the original 10-second gate. Corrected batching still loses observed
history (720 missing writes in a 12-second outage) and remains rejected.
Nineteen native, sixteen host, nine C++ and twelve targeted production DDS
checks pass. No live deployment is performed. See
[corrected baseline](README.md#correct-native-self-ignore-on-both-sides-2026-11-12).

The 512 KiB repair-response experiment changes only that XML cap, retaining
the corrected self-ignore and the same priorities/history/pacing. It observes
all valid writes and ACKs but offers no recovery improvement (61.580 s versus
60.342 s baseline) and has 2.315 s worst fresh RTT. Both contact gates and the
candidate command gate fail. Seventeen host tests and 24 native controls pass.
Captured request bitmaps remain mostly full 256-bit windows in both runs;
measure request-window/cadence and pacing next. Do not infer causation of the
RTT difference from one comparison or deploy the larger cap. See
[repair-cap evidence](README.md#larger-repair-response-limit-2026-11-12).

The subsequent KEEP_LAST(1) writer-only experiment inherits the 512 KiB repair
profile. Final contact state converges in 12.929 s with receipt-triggered
measurement, fresh RTT is 13.726 ms, and the final reliable queue is empty.
However, 120/180 command ACKs and 10,072/14,561 intermediate valid contact writes
are missing. Original delivery and 10 s state-recovery gates remain failed.
A prior 180 s drain independently confirms command loss; its periodic
convergence probe sampled near producer ticks, so the repeat also checks after
receiving changed state. The refined repeat uses 10 s final observation only
after the long run established losses. Preserve both labelled methods/reports.
Eighteen host and 32 native controls pass, including seeded queued update,
dispose and revive replacement. Do not deploy KEEP_LAST on the shared command
writer or mistake state convergence for required-event delivery. See
[latest-state experiment](README.md#keep_last1-latest-state-experiment-2026-11-12).

1. Prove deterministic rates and instance-state comparison with unit tests.
2. Measure reliable Event and best-effort Status with existing production wire
   types, shared command/health endpoints, retained reader-instance keys, and
   an actual 2-second bidirectional packet blackout in a private namespace.
3. Keep healthy controls, failures, source truth, native observations, queue
   counters, command ACKs, health ages, effective QoS, and input hashes.
4. Add routed Event/Status receiver coverage and distinguish it from a complete
   fleet/radio qualification. Never fake many distinct normalized source keys
   behind one configured router.
5. Use measured backlog and ghost-instance results to evaluate XML-authored flow
   control, per-sample priority, ordering limits, and explicit deletion recovery.
   Do not assume prioritization repairs lost best-effort disposes.

The initial acceptance budgets are proposed experiment budgets, not production
guarantees. Native state-convergence failures remain failures in JUnit and the
runner exit status; expected protocol limitations are not silently skipped.
