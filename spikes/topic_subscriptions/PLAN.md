# Topic subscription spike

## Objective

Test the mechanisms behind the
[graph subscription plan](../../docs/router-topic-subscriptions-plan.md)
without editing production types, processor code, QoS or running configurations.

## Fixture

- Two real Connext 7.7 Routing Services, each owning exactly three WAN pairs.
- Private network/IPC container; IPv4 domains 110/112 LAN and 111 WAN.
- No Routing Service administration configuration, participant or RPC client.
- Original typed LAN reports aggregated into a small typed union, not octets.
- State inventory derived from prepared bindings plus actual LAN reader matches.
- State applied-policy snapshots; reliable Event selection requests/results.
- Mission transitions through a local DDS input.
- A tiny isolated processor uses the production-style native CFT API.
- A LAN-only Python fixture controller drives policy/transactions; it is not a
  production GatewayModel implementation.
- The host graph communicates with the fixture through local files exposed to
  Playwright. This is test synchronization, not a production REST/WebSocket API.

## Acceptance

1. Discover three prepared available topics; keep inventory independently selected.
2. Verify Auto Init, active mission, user override, terminal mission transition,
   Auto restoration and empty optional selection.
3. Keep active navigation selected and enforce team audience restrictions.
4. Reject unsupported topics, stale sessions/revisions and conflicting duplicates.
5. Perform real multi-family native updates/readback; verify rollback after a
   fault injected after State mutation.
6. Deliberately refuse State rollback, expose actual native/model divergence,
   reject subsequent changes and show unknown state in the graph.
7. Observe producer disappearance/reappearance without clearing stored selection.
8. Demonstrate volatile late-LAN-reader recovery only after refresh.
9. Restart the real receiver Routing Service, explicitly reset the fixture's
   session/policy, and reject a request targeting the previous session.
10. Verify complete original DynamicData equality and exact selected sample
    identities in a bounded capture; receiver non-delivery alone is insufficient.
11. Browser-click User/Auto controls, show correlated native confirmation/errors,
    preserve drafts through producer churn and block editing on native divergence.
12. Confirm the sender configuration and writer prefix remain unchanged while
    receiver CFT updates change which original samples cross the WAN.
13. Stop all owned processes and close DDS entities exactly once.
14. With `--extended`, keep both original production and source relay running
    independently during native filter operations; require timestamped evidence
    of freshly offered samples crossing WAN during every operation.
15. Verify settled narrowing/widening/team/mission selection, record transition
    drains, and preserve reliable selected reports with exact payloads.
16. Block only the private WAN for two seconds; repair/reject a stale queued
    request while preserving newer local selection.
17. Exercise competing revisions and apply/rollback-refusal faults in each family,
    retaining degraded errors across snapshot refresh and rejecting stale snapshots.
18. Check supported native type bounds and typed WAN frame dispose/revive/
    unregister/re-register.
19. Restart the actual source and recreate the receiver WAN participant; observe
    early traffic separately, require fresh WAN readiness, and test volatile
    metadata refresh without mistaking LAN matching for end-to-end readiness.
20. Browser-regress disconnect disable/recovery, unconfirmed empty errors and
    explicit user choices separate from temporary protected interests.
21. With `--extended --transient-local`, change only WAN State durability.
    Verify no-refresh inventory replay, exactly the latest complete value per
    selected key on a new WAN reader, and continued team exclusion.
22. Observe in-place widening separately from new-reader historical replay.
    Restart the source and verify old history is absent on a subsequent new
    reader until source republishing. Preserve the volatile LAN controls.

## Exclusions

No production processor/bridge/UI subscription integration, six-topic manual
selection, mixed-schema deployment, command execution, authorization, production
multi-user concurrency, arbitrary discovered types, persistent override recovery,
original Report producer lifecycle, long outages, sustained fleet load/radio or
large inventories. The separate production-default six-topic baseline qualifies
existing ACT forwarding/compression, not this manual-selection implementation.
