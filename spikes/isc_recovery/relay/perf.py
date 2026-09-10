#!/usr/bin/env python3
"""
Performance characterization — Python ISC relay vs Routing Service vs direct.

Why this exists alongside RTI Perftest: stock perftest uses its own TestData_t type and
its own per-topic QoS, so (a) it cannot traverse our ActState-typed Python relay, and
(b) matching its per-topic QoS through RS is fiddly. This harness measures all THREE
paths on the SAME type (ActState), SAME QoS, and SAME methodology — a true apples-to-apples
comparison. Use RTI Perftest for the absolute raw-DDS baseline; use this for relay-vs-RS.

Paths (all localhost):
  direct : pub -> sub                       (1 hop, baseline)
  relay  : pub -> [Python ISC relay] -> sub (2 hops)
  RS     : pub -> [Routing Service]  -> sub (2 hops)   — needs perf_rs.xml running

Metrics:
  throughput   — pub blasts N samples; sub counts delivered over the receive window
                 -> samples/s, Mbps, loss%.
  one-way lat  — pub sends at low rate, stamping wall-clock ns (same host => same clock);
                 sub computes recv-send -> p50/p90/p99/mean (us).

Run:
  export RTI_LICENSE_FILE=/home/rti/rti_connext_dds-7.6.0/rti_license.dat
  # optional RS path:
  export NDDSHOME=/home/rti/rti_connext_dds-7.6.0
  $NDDSHOME/bin/rtiroutingservice -cfgFile perf_rs.xml -cfgName PerfBridge &
  python3 perf.py
"""

import statistics
import sys
import time

import rti.connextdds as dds

import act_state
from isc_relay import IscRelay

TOPIC = "PerfState"
DOM_DIRECT = 50
DOM_RELAY_UP, DOM_RELAY_DOWN = 51, 52
DOM_RS_UP, DOM_RS_DOWN = 13, 14   # perf_rs.xml bridges these

HIST = 5000


def perf_wqos():
    q = dds.DataWriterQos()
    q.reliability.kind = dds.ReliabilityKind.RELIABLE
    q.durability.kind = dds.DurabilityKind.VOLATILE
    q.history.kind = dds.HistoryKind.KEEP_LAST; q.history.depth = HIST
    return q


def perf_rqos():
    q = dds.DataReaderQos()
    q.reliability.kind = dds.ReliabilityKind.RELIABLE
    q.durability.kind = dds.DurabilityKind.VOLATILE
    q.history.kind = dds.HistoryKind.KEEP_LAST; q.history.depth = HIST
    return q


def _pub(domain):
    dp = dds.DomainParticipant(domain, act_state.participant_qos())
    t = dds.Topic(dp, TOPIC, act_state.ActState)
    return dp, dds.DataWriter(dds.Publisher(dp), t, perf_wqos())


def _sub(domain):
    dp = dds.DomainParticipant(domain, act_state.participant_qos())
    t = dds.Topic(dp, TOPIC, act_state.ActState)
    return dp, dds.DataReader(dds.Subscriber(dp), t, perf_rqos())


# --------------------------------------------------------------------------- #
def throughput(wdp, w, rdp, r, n, payload_bytes):
    pad = "x" * payload_bytes
    sample_bytes = payload_bytes + 24   # payload + key/seq/send_ns overhead (approx)
    # let discovery settle
    time.sleep(1.5)
    received = [0]
    t_first = [None]; t_last = [None]

    for _ in range(n):
        w.write(act_state.ActState(key_id="K", seq=0, payload=pad))
    # drain until idle
    idle = 0.0
    while idle < 2.0:
        got = r.take()
        if got:
            if t_first[0] is None:
                t_first[0] = time.time()
            t_last[0] = time.time()
            received[0] += len(got)
            idle = 0.0
        else:
            time.sleep(0.05); idle += 0.05

    rc = received[0]
    if rc < 2 or t_first[0] is None:
        return None
    dur = t_last[0] - t_first[0]
    sps = (rc) / dur if dur > 0 else 0
    mbps = sps * sample_bytes * 8 / 1e6
    loss = 100.0 * (n - rc) / n
    return dict(sent=n, recv=rc, sps=sps, mbps=mbps, loss=loss)


def latency(wdp, w, rdp, r, m, payload_bytes):
    pad = "x" * payload_bytes
    time.sleep(1.5)
    samples = []
    for i in range(m):
        w.write(act_state.ActState(key_id="K", seq=i, send_ns=time.time_ns(), payload=pad))
        # poll for the echo of this one-way sample
        deadline = time.time() + 0.5
        while time.time() < deadline:
            for data, info in r.take():
                if info.valid and data.send_ns:
                    samples.append((time.time_ns() - data.send_ns) / 1000.0)  # us
            if samples and len(samples) >= i + 1:
                break
            time.sleep(0.0005)
        time.sleep(0.001)
    if len(samples) < 10:
        return None
    samples.sort()
    def pct(p): return samples[min(len(samples) - 1, int(len(samples) * p))]
    return dict(n=len(samples), p50=pct(0.50), p90=pct(0.90), p99=pct(0.99),
                mean=statistics.mean(samples), mn=samples[0])


# --------------------------------------------------------------------------- #
def run_direct(fn, *a):
    wdp, w = _pub(DOM_DIRECT); rdp, r = _sub(DOM_DIRECT)
    try:
        return fn(wdp, w, rdp, r, *a)
    finally:
        rdp.close(); wdp.close()


def run_relay(fn, *a):
    wdp, w = _pub(DOM_RELAY_UP)
    relay = IscRelay(DOM_RELAY_UP, DOM_RELAY_DOWN, TOPIC, isc=False,
                     rqos=perf_rqos(), wqos=perf_wqos())
    relay.start()
    rdp, r = _sub(DOM_RELAY_DOWN)
    try:
        return fn(wdp, w, rdp, r, *a)
    finally:
        rdp.close(); relay.stop(); wdp.close()


def run_rs(fn, *a):
    wdp, w = _pub(DOM_RS_UP); rdp, r = _sub(DOM_RS_DOWN)
    try:
        time.sleep(1.5)
        w.write(act_state.ActState(key_id="probe", payload="p"))
        time.sleep(1.0)
        if len(r.take()) == 0:
            return "SKIP"
        return fn(wdp, w, rdp, r, *a)
    finally:
        rdp.close(); wdp.close()


PATHS = [("direct", run_direct), ("relay ", run_relay), ("RS    ", run_rs)]


def main():
    N = 50000        # throughput samples
    PL_TP = 1024     # throughput payload bytes
    M = 1500         # latency samples
    PL_LAT = 64      # latency payload bytes

    print("Performance: Python ISC relay vs Routing Service vs direct (ActState, identical QoS)")
    print(f"  throughput: {N} samples x {PL_TP}B ; latency: {M} samples x {PL_LAT}B one-way\n")

    print("THROUGHPUT (RELIABLE, KEEP_LAST %d, VOLATILE)" % HIST)
    print(f"  {'path':7} {'recv/sent':>14} {'samples/s':>12} {'Mbps':>9} {'loss%':>7}")
    tp = {}
    for name, runner in PATHS:
        res = runner(throughput, N, PL_TP)
        tp[name] = res
        if res == "SKIP":
            print(f"  {name}  RS not reachable (start perf_rs.xml) — skipped")
        elif res is None:
            print(f"  {name}  no data")
        else:
            print(f"  {name} {res['recv']:>6}/{res['sent']:<7} {res['sps']:>12,.0f} "
                  f"{res['mbps']:>9.1f} {res['loss']:>7.1f}")

    print("\nONE-WAY LATENCY (us)")
    print(f"  {'path':7} {'n':>6} {'p50':>8} {'p90':>8} {'p99':>8} {'mean':>8} {'min':>7}")
    for name, runner in PATHS:
        res = runner(latency, M, PL_LAT)
        if res == "SKIP":
            print(f"  {name}  RS not reachable — skipped")
        elif res is None:
            print(f"  {name}  insufficient samples")
        else:
            print(f"  {name} {res['n']:>6} {res['p50']:>8.1f} {res['p90']:>8.1f} "
                  f"{res['p99']:>8.1f} {res['mean']:>8.1f} {res['mn']:>7.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
