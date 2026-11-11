# Instance churn reconnect spike

Implement the native endpoint stage of the
[test plan](../../docs/instance-churn-reconnect-test-plan.md), then add actual
router legs without modifying the running mesh or production QoS.

Initial native and actual-adapter stages are now implemented and exercised.
Schema-3 main native and routed gates each report 3 passes and one Status
outage failure with 41 ghosts. See [measurements and remaining scope](README.md).
Flow-controller/prioritization experiments and radio-constrained qualification
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
