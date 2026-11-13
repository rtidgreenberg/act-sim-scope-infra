# Three-topic WAN retention spike

## Question

Can separate Contacts and Event writers preserve every required command/ACK while
recovering latest contact state quickly after a two-minute link outage?

## Design and controls

- Status: existing best-effort KEEP_LAST(1) telemetry/health.
- Contacts: reliable KEEP_LAST(1), keyed by origin + original topic + contact ID.
- Event: reliable KEEP_ALL commands and ACKs, including repeated use of one key.
- Event and Contacts share one XML-configured highest-priority-first controller
  per participant, nominal 2 Mbps. Sample priorities remain 100 and 10; no
  MultiChannel. Status is outside this controller, not guaranteed lower priority.
- Retain the previous KEEP_LAST experiment's 512 KiB repair-response cap.
- Reuse schema-3 WanEvent on Contacts and Event, WanStatus on Status. No IDL,
  production routing, live mesh or wire-version change.
- Both nodes have one writer/reader per physical topic. Ignore own publications
  and subscriptions; require exactly one remote match for every writer.
- Compare against the same deterministic churn and receipt-timed state oracle.
  Intermediate Contacts may coalesce; every command/ACK must arrive exactly once.

## Gates

1. Host mapping/XML tests and existing churn tests.
2. Native effective-QoS/endpoint inventory, self-ignore, serial disposal/revival,
   queued final update/dispose/revive and cross-writer priority reversal.
3. Healthy control, then 120-second outage with continued churn and commands.
4. Preserve thresholds: state recovery <=10 s, fresh command RTT <=2 s,
   producer lag <=50 ms, correct final state and drained reliable queues.
5. Retain manifests, results and JUnit even for failed gates. Captures are closed
   before retention and explicitly cover combined Event/Contacts source DATA.

## Not qualified here

Router adapters, all application branches, team transitions, permanent
tombstones, late joining, process restart, multi-producer ownership, bounded
fleet admission, real RF budgets and health reservations remain separate work.

## Execution checkpoint (2026-11-12)

Host tests 20/20, new native controls 9/9 and existing native controls 32/32 pass.
The healthy comparison passes. The 120 s blackout preserves all 180 commands
and ACKs, with no duplicates/unexpected IDs and 14.124 ms worst fresh RTT.
Final contact state is correct and all reliable queues drain. First state
catch-up is 19.110 s: **the 10 s recovery gate remains failed**, not waived.
See [results and evidence](README.md#measured-results-2026-11-12).

Next: profile remaining contact-state/disposal repair costs and compare packing,
repair request windows and class budgets without weakening required Event
retention or changing the live mesh. Qualify durability/restart/late joining
separately before proposing a production wire-contract migration.

## Controlled timing investigation (2026-11-12)

Twelve full two-minute outage trials now distinguish configuration changes from
run variability. Original captures attribute 5.891 s of the historical 6.181 s
delta to additional long gaps between contact repair requests. The identical
three-topic no-MultiChannel configuration repeats at 13.869 and 19.331 s.
MultiChannel-retaining, KEEP_ALL-reader trials preserve every required Event but
remain variable; disabling Contacts heartbeat suppression alone reaches
12.227/11.781 s, still missing the 10 s gate and retaining repair-request stalls.
One unchanged control reaches 28.210 s and breaches the 2 s command RTT gate.

Keep MultiChannel available as `bands_all`; its removal was a simplification,
not a requirement or a proven explanation for the slowdown. No defaults or live
mesh settings are changed. See the [complete comparison and limitations](README.md#why-129-s-became-191-s-controlled-investigation).
Twenty-two host tests and all nine native controls pass.

Next: isolate heartbeat-response delay ranges and historical-data repair
request pacing in separate XML-only trials, retaining MultiChannel and required
Event history. Do not equate observed five-second pauses with the five-second
NACK period without a controlled test, or claim zero suppression is a complete fix.
