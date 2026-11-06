# Passive MAC / RTPS accounting verification

Use the existing container mesh read-only. Do not launch DDS participants, inject
traffic, change impairments, or restart any mesh process.

1. Capture every node's `emane0` Ethernet frames with owned, bounded `dumpcap`
   processes in non-promiscuous mode. Keep capture working files under `/tmp`.
2. Read MAC and CommEffect counters and drop tables before and after a common
   measurement window. Record query timing rather than assuming atomic snapshots.
3. Independently decode classic pcap Ethernet/IPv4/UDP headers using Python's
   standard library. Count physical frames, not RTPS submessages or logical samples.
   Identify domain-200 RTPS, including subsequent IPv4 fragments.
4. Compare captured frames with existing production monitor intervals, and compare
   each radio's MAC RX with the other radios' MAC TX and captured Ethernet TX.
5. Separate destination-MAC rejection, RF/SINR rejection, CommEffect drops, model
   encapsulation bytes, and non-RTPS Ethernet traffic.
6. Retain captures and a compact JSON report under the repository debug tree.

Acceptance: no capture drops, stable experiment configuration, no counter reset,
and physical-frame/rate comparisons within 2% or two boundary packets (whichever
is larger). MAC query timing and in-flight frames make exact short-window equality
unreasonable. Report discrepancies explicitly; do not redefine a layer or subtract
an assumed overhead merely to make numbers agree.

MAC packet-byte counters are model-layer accounting, not complete RF airtime,
PHY waveform bytes, or DDS application-payload bytes.
