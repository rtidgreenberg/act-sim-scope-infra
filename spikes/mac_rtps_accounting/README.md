# Independent MAC / RTPS accounting spike

This is a **passive** measurement of an already-running containerized EMANE mesh.
It never starts DDS participants, modifies experiments, or restarts mesh services.
See the [plan](PLAN.md) for the measurement contract.

```bash
python3 -m unittest discover -s spikes/mac_rtps_accounting -p test_measure.py -v
python3 spikes/mac_rtps_accounting/measure.py --duration 60
```

If queued packets make the header-calibration window unbalanced, the runner
fails explicitly instead of estimating a header by rounding. A previous balanced
window can be supplied with `--calibration-report <report.json>`; its independently
derived header sizes are still checked against the new Ethernet captures.
Raw snapshots and every completed capture are retained even if analysis fails.

The runner discovers the baseline's current containers and captures all nodes'
`emane0` interfaces with `dumpcap -p`. Each capture is bounded and owned by the
runner. Working files live under container `/tmp`; closed captures are retained
under `debug/pcap/router/<test_id>/`, and the JSON result is written to
`debug/test_reports/<test_id>.json`. Existing evidence is never overwritten.

## What is independently checked

- Ethernet frames are decoded from classic pcap with Python `struct`, without
  production `FrameAccounting` or the production RTPS parser.
- WAN RTPS identification uses the UDP domain-port range and RTPS magic. IPv4
  fragments count individually; they are not mistaken for multiple DDS samples.
- Exact production-monitor intervals are replayed against the independent
  captures. Each interval has timestamped RX/TX physical-frame and frame-byte
  checks, separate from the combined-window totals.
- MAC RX is compared with the other radios' MAC TX and their captured Ethernet
  TX in the production MAC monitor's own intervals.
- MAC destination drops, RF/SINR drops, and CommEffect drops are separate.
- Captures must report zero kernel drops. Rate/frame tolerances are explicit in
  the JSON; failing comparisons produce a nonzero exit status.

The report separates `aggregate_passed` (combined totals, capture health and
experiment stability) from `interval_timing_passed` (every individual production
interval). Overall `passed` requires both; compensating errors in neighboring
intervals cannot hide a shifted burst.

The monitor reader excludes any record already partly written at its starting
offset, ignores an unfinished trailing record, and rejects malformed complete
records. Cleanup attempts every node even when one stop/wait/removal fails.
Cleanup errors are logged without replacing an original measurement failure;
otherwise they fail the run. Files still held by an unfinished capture are
retained rather than unlinked.

Model overhead is derived from balanced MAC/CommEffect input/output counters
and then checked against independent Ethernet captures. It is not assumed to be
the earlier two-node lab's 63 bytes. In the first fleet window the measured
RF Pipe header was 6 bytes, with 58 CommEffect bytes on C2 and 59 on platforms:
**64 / 65 total bytes per frame**. This is not a universal RF-overhead constant.

## Why MAC RX and RTPS RX differ

The dashboard's MAC RX is RF Pipe `numUpstreamBytes*Rx0`: model packet bytes arriving
at the MAC, including frames subsequently rejected because their destination MAC
belongs to another node. The shared medium carries other boats' unicasts too.

The dashboard's RTPS RX is domain-200 RTPS Ethernet frames delivered to `emane0`.
Destination filtering, model-header removal, CommEffect losses, other-domain
RTPS, RTPX announcements, ARP and ICMP traffic intervene between those counters.

These are **kbps**, not packet counts. Neither is DDS application-payload
bandwidth or a direct RF-airtime measurement. The graph additionally smooths RTPS
over ten reports while MAC uses its latest report, so a single displayed pair
is not a perfectly synchronized comparison.

## Limits

Counter queries and captures have different boundaries; queued/in-flight packets
can cross them. Query start/end timestamps and the raw counters are retained.
Two percent or two boundary packets is the acceptance band, with a 3000-byte
minimum for frame-byte comparisons. Short-window exact equality is not promised.

This decoder explicitly rejects truncated/VLAN frames. An unresolved fragment is
reported separately, not silently called RTPS. The current fixture covers classic
pcap in both byte orders and both microsecond/nanosecond timestamp resolutions.
No complete-coverage claim is made for RF airtime, uncaptured PHY rejects, or an
experiment changed mid-window. Check the report's actual pass/fail status.

Production counters use the time a decoded row is processed, not the frame's
capture timestamp. A separate live 817-packet probe measured 487 ms median,
885 ms p90, and 996 ms maximum decoding delay. A burst at an interval boundary
can therefore fail the strict per-window comparison without any capture loss.
Do not relax the tolerance to hide that distinction: retain the failed comparison
and inspect the following report and the monitor's own capture.

## Measured result (2026-11-06)

Seven-node passive measurements covered 60 and 73 seconds. Independent captures
reported zero kernel drops. The experiment stayed unchanged, with no active
impairment and no CommEffect Effect drops during these windows.

Platform_30's first-window RX waterfall:

| Measurement layer | kbps |
|---|---:|
| RF Pipe MAC input, including overheard packets | 388.14 |
| MAC output toward CommEffect, after destination filtering | 52.69 |
| CommEffect output toward Ethernet, after header removal | 43.43 |
| Independently captured Ethernet RX | 43.53 |
| Domain-200 RTPS Ethernet RX | 35.52 |

MAC input counted 8,933 packets versus 1,194 forwarded upstream. The MAC table
attributed essentially all rejection to destination MAC, not SINR. The repeat
window measured 406.80 kbps MAC RX versus 37.20 kbps domain-200 RTPS RX. Across
all nodes, repeat MAC RX reconciled with independent peer Ethernet transmissions
plus measured headers to within 0.25% in packet rate and 0.17% in byte rate.

The remaining accepted-Ethernet traffic included RTPS announcements from LAN
domains 20 and 31-35, RTPX default-domain announcements, ARP, and ICMP
port-unreachable replies. Captured participant names identified the C2 router,
control sim, dashboard bridge, and per-node journal subscribers as sources of
LAN discovery on the RF-facing interface. Several of their LAN profiles/default
participants are not restricted to a LAN interface.

RTPX is RTI's separate default-domain announcement mechanism, not SPDP2 for
domain 200. This was checked against installed 7.7 SDK fields and
[RTI's documented packet signature](https://community.rti.com/kb/why-do-i-see-packet-starts-rtpx-instead-rtps).

### Accuracy qualification

The strict production-rate window checks did **not** all pass. In the first
window, independent capture and the monitor's own capture both contained 1,010
Platform_30 RX frames, but the selected reports counted 901. A 129-frame burst
was reported as 32 frames in one interval and 127 in the next, although only 13
frames were captured in the next interval: processing delay moved the burst.
The separate decoder-latency probe corroborated the approximately one-second
capture-to-report lag.

The repeat crossed a normal tshark capture rotation: all seven nodes had a
restart-counter change and a flagged gap. Some rate checks failed again. Node
overlays do not currently gate their ten-report average on these flags; they
can therefore show depressed RTPS rates during/after a capture gap.

Live browser verification loaded `mesh_graph.js?v=20261105-mission-reporting-v5`
and exactly reconciled Platform_30's rendered `MAC TX/RX 38/312 kbps` and
`RTPS TX/RX 53/47 kbps` with graph-processed WebSocket messages. There were no page
errors. RTPS exceeding MAC TX in that particular display is explained by the
different averaging windows, not negative header overhead.

Conclusion: the large RX gap is primarily a real **radio-input versus
node-delivered-WAN-RTPS** layer difference. The counter arithmetic and rendering
are verified, but the labels, averaging windows, capture-processing lag and capture
gaps prevent interpreting the pair as a same-window link-efficiency ratio.

### Post-QoS restart verification (2026-11-06)

After deploying the LAN `emane0` deny-list and disabling RTPX announcements,
a 45-second measurement across all seven radios captured **zero other-domain
RTPS frames and zero RTPX frames**, with zero independent-capture kernel drops.
All six platform peers were alive and the controller had no active impairments.

The repaired interval gate correctly failed 437 individual packet/byte
comparisons rather than hiding neighboring intervals' compensating errors.
Combined totals also failed one C2 TX-byte comparison (11,474 bytes, about
2.03% of the reference). Both `aggregate_passed` and `interval_timing_passed`
were false: this run verifies the QoS deployment, not synchronized production
rate accuracy. The known capture-to-processing timing discrepancy remains.
This run explicitly reused the first fleet report's header calibration.

Retained evidence:

- [60-second fleet report](../../debug/test_reports/mac-rtps-deep-20261106-a.json)
- [73-second repeat report](../../debug/test_reports/mac-rtps-deep-20261106-c.json)
- [Decoder latency](../../debug/test_reports/mac-rtps-decoder-latency-20261106.json)
- [Browser reconciliation](../../debug/test_reports/mac-rtps-browser-20261106.json)
- [Browser screenshot](../../debug/test_reports/mac-rtps-browser-20261106.png)
- [Post-QoS fleet report](../../debug/test_reports/mac-rtps-post-qos-20261106.json)

### Capture-time correction verification (2026-11-06)

The processing-time and ten-report-average limitations above describe the earlier
schema-2 monitor. Schema 3 bins frames by capture timestamp, waits two seconds
before closing a window, and holds pending fragment windows until resolution.
Frames decoded after a window has already closed invalidate rates explicitly.
Capture gaps reset the shared three-second node/chart average; they are not zero
traffic. The network aggregate uses a common capture window across contributors.

Two passive 42-second checks exercised the new code on Control_20 and Platform_30
without restarting or changing the running mesh. Each check used a separate
dumpcap capture and this spike's independent Ethernet/pcap decoder. Eight-second
capture lifetimes deliberately exercised repeated real tshark rotations.
All **264 exact comparisons** passed: TX/RX frame bytes and packets in 33 valid
one-second windows per node. Both independent captures reported zero kernel
drops, no late frames occurred, and gap windows were excluded explicitly.
This validates the new capture-time measurement, not RF airtime or guaranteed
drop-free future captures; ongoing kernel drops remain unreported by the monitor.

- [Platform_30 capture-window verification](../../debug/test_reports/dds-capture-windows-20261106.json)
- [Control_20 capture-window verification](../../debug/test_reports/dds-capture-windows-control-20261106.json)
