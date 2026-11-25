# Topic names versus endpoint count

## Objective

Measure native Connext 7.7 discovery and filter-update overhead for:

1. Six topics, six writers and six filtered readers per participant.
2. Three topics, six writers and six filtered readers per participant.
3. Three topics, six writers and three filtered readers per participant.

The second case preserves six independent receive queues. The third deliberately
removes that isolation and measures the separate effect of reducing reader count.
All cases use exactly the same union-containing type and six writer DSCP classes.
No production profiles, router binaries or live mesh are changed.

## Controls and gates

- Private network/IPC container, loopback UDPv4 only, domain 102.
- Two- and seven-participant cases; repeated orders are reversed.
- Repeat the full matrix with the production reader-scoped preemptive-NACK
  suppression property; do not infer production startup costs from defaults.
- A fresh Python process per trial, with the same XML, type, transport settings,
  reliable endpoint policies, filter shape and offered payloads.
- Each participant has six writers. Two priority classes map to each family.
  Partition isolation excludes local endpoints without changing between layouts.
- Record participant/endpoint handles and exact matched endpoint counts.
- Capture before participant creation and retain explicit phase timestamps:
  fixed startup, idle, one reader update, restoration, all-reader update,
  restoration, and offered data.
- Verify filter changes with exact native data delivery, including no duplicate
  samples and full original DynamicData equality.
- Verify six distinct writer IP DS fields in the actual capture.
- Count discovery packets/IP bytes once per packet, distinct endpoint discovery
  sequence numbers, SEDP DATA bytes and user reliability submessages separately.
  Shared packets are not counted once for every contained submessage.
- Require complete bounded capture, zero capture drops, no unexpected SDK errors
  and clean owned shutdown. Keep failures explicitly failed.

## Boundaries

This is native DDS, not production Routing Service, radio or mission integration.
Participants share one process per trial but communicate through verified UDP.
The test compares discovery/filter propagation, not throughput, cache memory or
CPU guarantees. Endpoint matching and actual packets are measured separately:
matched endpoints do not imply that excluded samples are delivered.

## Completion

Both four-repetition matrices passed (48 trials). The
[results](README.md) separate topic names, reader counts, matching associations,
discovery records, physical packet bytes and startup reliability traffic.
