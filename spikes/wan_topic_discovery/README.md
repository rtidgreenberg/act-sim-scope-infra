# WAN discovery: three versus six physical topics

Run `bash spikes/wan_topic_discovery/run.sh` from the checkout. The runner uses
the local `connext:7.7.0` image, private network and IPC namespaces, and a
read-only checkout. Set `CONNEXT_SHARED_DIR` to override the license directory.
The runner prints the path to its JSON report under `debug/test_reports`.

## Measured results

Five cold starts per case, seven Connext participants, SPDP2 plus SEDP over
unicast loopback. Each participant creates one reader/writer pair for each
physical topic, using the existing WAN envelope types. The six-topic case
splits Event, State and Periodic into High and Normal topics. Every reader
and writer had to match all six remote peers.

| Transport | Topics | All endpoints matched, median (range) | Discovery packets, median | Discovery IP bytes, median | SEDP IP bytes, median |
|---|---:|---:|---:|---:|---:|
| UDPv4 | 3 | 1,013 ms (820-1,073) | 1,071 | 210,980 | 122,584 |
| UDPv4 | 6 | 1,028 ms (1,022-1,061) | 1,079 | 288,264 | 199,056 |
| UDPv6 | 3 | 1,021 ms (878-1,058) | 1,066 | 231,904 | 131,224 |
| UDPv6 | 6 | 1,038 ms (1,023-1,094) | 1,087 | 311,244 | 210,152 |

On UDPv6, six topics added **34%** total discovery bytes and **60%** endpoint
discovery (SEDP) bytes. Packet count rose by about **2%**; median time to match
increased by **17 ms**, small relative to the spread across trials. Byte counts
cover the first three seconds, including IP/UDP headers but excluding link
headers and user-port reliability traffic. SEDP counts include discovery
datagrams containing a publication or subscription built-in writer ID.

A separate two-writer UDPv6 probe delivered and captured normal and high test
samples marked with Traffic Class `0x00` (DSCP 0) and `0xB8` (DSCP 46).
These are test values, not a proposed production DSCP policy.

The discovery comparison uses standalone DDS participants, not Routing
Service, and does not send application samples, enable MultiChannel, or
replicate the full production QoS. The marking probe shows that writer-level
TransportPriority can set an IPv6 header on loopback; it does not show
preferential forwarding, prove six topics are required, or qualify IPv6 on
EMANE. Before changing the WAN contract, test whether the actual WAN path
preserves and acts on the markings under contention, then compare startup
and repair under loss with the real Routing Service profiles.

## UDPv6 TransportPriority over EMANE RF Pipe

Run `bash spikes/wan_topic_discovery/run_emane.sh`. This starts and removes
its own two-node RF Pipe fixture on a private Docker bridge; it does not modify
the live mesh. The fixture uses 2 Mbps RF Pipe, no TAP rate cap, static IPv6
neighbors on `emane0`, and no DSCP-aware queue configured. Connext uses UDPv6
only. Equal-size best-effort samples are written synchronously to isolate packet
marking from the gateway's sample scheduler. The two overload bursts each
offer about 10.7 Mbps in sample payloads, excluding headers, for 1.5 seconds.
The second burst swaps the DSCP values between the two topics.

| Workload | DSCP 46 delivered | DSCP 0 delivered |
|---|---:|---:|
| Uncongested control | 60/60 | 60/60 |
| Congested, topic A marked 46 | 224/1,000 | 222/1,000 |
| Congested, topic B marked 46 | 222/1,000 | 224/1,000 |

Connext marked the outgoing IPv6 packets, and the marks were unchanged on
every matched receiver capture. Under congestion, both classes had about
105 ms median latency among packets captured at both ends. Delivery counts
come from DDS receipts: 24 of those receipts did not appear in the receiver
capture in the run shown above. A second completed run likewise showed no
consistent delivery or latency advantage for DSCP 46.

**Result:** RF Pipe preserved the marks, but marking alone did not improve
delivery or latency in this fixture. Do not add WAN topics for RF Pipe
priority alone. A follow-up should compare an explicitly DSCP-aware queue
with the current sample-level prioritization; neither that queue nor the full
Routing Service workload was tested here.

## TDMA six-topic connectivity smoke test

Run `bash spikes/wan_topic_discovery/run_tdma_smoke.sh` to launch a private
two-node TDMA fixture. Six distinct synthetic topics each have one writer and
one reader, all in the same traffic class. The UDPv6 run matched all six
endpoint pairs and delivered 60/60 samples (10 per topic).

This verifies basic discovery and delivery only. The test did not compare
priority classes or apply congestion, and did not change the live mesh.

## TDMA two-stream DSCP priority test

Run `bash spikes/wan_topic_discovery/run_tdma_smoke.sh priority`. The private
two-node test sends short bursts of equal-size, synthetic UDPv6 packets. It
starts with both streams marked DSCP 0, then gives DSCP 8 to A and finally
to B. The transmitting node's slot serves queue 1 first. Both `emane0`
captures must contain every test packet with its expected DSCP; the
receiver checks the marks independently, and EMANE queue counters verify
classification.

| Marking | First 96 arrivals (A / B) | Median latency (A / B) |
|---|---:|---:|
| Both DSCP 0 | 48 / 48 | 755 / 756 ms |
| A marked DSCP 8 | 96 / 0 | 372 / 1,166 ms |
| B marked DSCP 8 | 0 / 96 | 1,140 / 346 ms |

These are results from one run; a second run showed the same preference
following the mark. All 576 packets arrived in each run. Queue 1 handled
exactly the 192 DSCP 8 packets, with no overflow. This verifies earlier
delivery across one TDMA hop when both queues have a backlog. It does not
measure delivery under sustained overload or loss, cover multiple hops,
or validate the live DDS configuration.

## Connext Routing Service six-topic DSCP priority over EMANE TDMA

Run `bash spikes/wan_topic_discovery/run_tdma_rs_six_topic.sh baseline` and
`bash spikes/wan_topic_discovery/run_tdma_rs_six_topic.sh dscp`. Two
`rtiroutingservice` gateways bridge six synthetic LAN topics across a
two-node UDPv4 EMANE TDMA link (`2M` datarate, `20 ms` slots, slot 0
`class="3"` on `node_1`). Each round writes 48 bursts of 1 KiB samples
lowest-priority first.

- **Priority 1 (`CS6`, DSCP 48 -> TOS 192):** `WAN_P1_Event` (Reliable `KEEP_ALL`) and `WAN_P1_Periodic` (Best Effort `KEEP_LAST(1)`).
- **Priority 2 (`AF41`, DSCP 34 -> TOS 136):** `WAN_P2_Event` (Reliable `KEEP_ALL`), `WAN_P2_State` (Reliable, keyed `KEEP_LAST(1)` writer / `KEEP_ALL` reader), and `WAN_P2_Periodic` (Best Effort `KEEP_LAST(1)`).
- **Priority 3 (`AF31`, DSCP 26 -> TOS 104):** `WAN_P3_Periodic` (Best Effort `KEEP_LAST(1)`).

| Topic | Tier | Baseline (`0/0/0`) received / median | DSCP (`48/34/26`) received / first-half / median |
|---|---|---:|---:|
| `P1_Event` | 1 | 48/48 / 2,995 ms | 48/48 / 48 / 490 ms |
| `P1_Periodic` | 1 | 43/48 / 1,279 ms | 24/48 / 24 / 490 ms |
| `P2_Event` | 2 | 48/48 / 3,119 ms | 48/48 / 14 / 1,650 ms |
| `P2_State` | 2 | 48/48 / 2,959 ms | 48/48 / 14 / 1,650 ms |
| `P2_Periodic` | 2 | 42/48 / 1,258 ms | 42/48 / 13 / 1,611 ms |
| `P3_Periodic` | 3 | 42/48 / 1,258 ms | 17/48 / 0 / 2,553 ms |

In the DSCP run, both `emane0` captures recorded matching IPv4 DSCP packet
counts (`48`: 106/106, `34`: 228/228, `26`: 49/49). EMANE `QueueStatusTable`
deltas confirmed separation into Queue 3 (`113` enqueued, `0` overflow),
Queue 2 (`228` enqueued, `0` overflow), and Queue 0 (`195` enqueued with
DSCP 0 traffic, `0` overflow). In the unmarked baseline, Queue 0 overflowed
by `33` packets, delaying reliable repair behind best-effort traffic.

This validates the 2/3/1 synthetic Routing Service topic split and IPv4 DSCP
queueing over one TDMA hop. Best-effort `KEEP_LAST(1)` periodic topics
supersede samples under an unpaced burst, and tactical C2/ISR routes and the
live mesh were not modified.
