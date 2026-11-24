# ContactReport disposal timing

Run two current Routing Service gateways (platform -> WAN State -> C2) inside
one disposable `connext:7.7.0` container with no network and private IPC.
Use the persistent deployed plugin/config helper without rebuilding them.
Keep all changes and scratch state in this spike; never access live DDS.

1. Prepare isolated three-topic gateway bundles from the production development
   YAML, retaining only ContactReport application routes. Use full monitoring,
   static receive interests, native independent LAN auto-QoS, UDPv4 transport,
   and domains 211 (platform), 212 (WAN), 213 (C2).
2. Build only a native timing probe. Write a completely populated original
   `contact_report`, with a deliberately incorrect producer `source`. Require
   full DynamicData equality after replacing only source with `Platform_30`.
3. On a shared monotonic clock, measure immediately before writer dispose
   through first C2 invalid `NOT_ALIVE_DISPOSED` observation. Verify original
   two-field key and same instance handle.
4. Repeat three trials for the inherited production `lan_event` C2 reader,
   explicit zero instance-delay, and zero delay with minimum-state retention
   disabled. Vary notification hold time (250/750/1500 ms); hold the take loan
   another 200 ms. Inspect native cache counts, per-key lookup and key recovery
   before/after loan release. Default retention observation lasts 5500 ms
   after release; every probe must still resolve the disposed instance.
5. Try a separate two-second instance-delay reader. Prior art says 7.7 rejects
   this setting; report actual rejection, never call a sample-delay timer an
   instance purge timer. Zero controls demonstrate the release/purge ordering.
6. Emit structured per-trial failures and return nonzero for unexpected results.
   Archive actual YAML/XML/type inputs and hashes, summarize timing and cleanup.

Acceptance deadlines are 20 seconds for startup/materialization, 5 seconds for
dispose arrival, 2 seconds for zero-delay purge, sampled every approximately
1 ms. These are bounded spike failure criteria, not production guarantees.
No GUI/cache expiry or full regression/live mission testing is included.

## Delayed WAN follow-up (2026-11-19)

Completed eight isolated cases: writer cleanup zero/ten seconds, each with
three-second one-way WAN delay and with/without a two-second blackout spanning
dispose, twice each. Keep reader instance/sample retention infinite. Calibrate
WAN-only netem filtering with independent in-range/out-of-range UDP controls
and prove the blackout with a dropped control packet and queue counters.

Measure actual C2 invalid disposal and existing native WAN State writer cache
retention/cleanup. Read effective writer QoS and unacknowledged counts; require
ten-second minimum retention and no unacknowledged samples at reclamation.
Use a startup-gated, shared-SDK-linked cache observer, stopped before teardown.
Retain exact XML/inputs/logs/monotonic observations in a separate delayed report.
Do not treat writer cleanup as a delivery timeout or copy the reference's
finite reader-sample purge into a correctness-critical receive path.
