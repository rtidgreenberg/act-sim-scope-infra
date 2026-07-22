#!/usr/bin/env python3
"""Instrumented cross-process replay for the SPDP2 cold-start-race reassessment.

Adds the measurements the original cross_process_participant.py lacked, to test whether
the "asymmetric, never-self-healing" failures are a RIG ARTIFACT of the default localhost
peer descriptor rather than a real discovery defect:

  1. Logs each participant's ASSIGNED participant_id after enable. The default peer list
     (NDDS_DISCOVERY_PEERS unset, UDPv4-only, no multicast on `lo`) resolves to
     builtin.udpv4://127.0.0.1 probing participant IDs 0..4 ONLY (per Connext 7.7 docs /
     MCP). If the later process (B) is assigned participant_id > 4, it falls OUTSIDE A's
     candidate probe range -> A structurally can never find B -> exactly the observed
     asymmetric, permanent, non-healing failure, as a pure artifact of the default
     descriptor (a real WAN router uses explicit initial_peers with full addresses).
  2. Polls discovery in BOTH directions and KEEPS polling past first sighting, recording
     the LATEST state, so a late self-heal (which the first-sighting-break original could
     not see) is caught.
  3. Records load average per rep so the load confound is attributed per-outcome.

Modes:
  worker: python3 xproc_index_probe.py worker <role> <domain> <delay_s> <hold_s> \
            <spdp2:0|1> <enable_mode:delayed|immediate> <announce_period_s|-> <resultfile>
  orchestrate (default): python3 xproc_index_probe.py [reps] [spdp2:0|1] [announce_period_s|-] \
            [hold_s] [base_domain]
"""
import os
import sys
import time
import json
import subprocess

os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

SELF = os.path.abspath(__file__)


def run_worker():
    import rti.connextdds as dds
    (role, domain, delay_s, hold_s, spdp2, enable_mode, announce, resultfile) = (
        sys.argv[2], int(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5]),
        bool(int(sys.argv[6])), sys.argv[7], sys.argv[8], sys.argv[9])
    announce_s = None if announce == "-" else float(announce)
    SPDP2_SEDP = (dds.DiscoveryConfigBuiltinPluginKindMask.SPDP2
                  | dds.DiscoveryConfigBuiltinPluginKindMask.SEDP)

    def build_qos():
        q = dds.DomainParticipantQos()
        q.transport_builtin = dds.TransportBuiltin.udpv4
        q.partition = dds.Partition(["SHARED"])
        dc = q.discovery_config
        if spdp2:
            dc.builtin_discovery_plugins = SPDP2_SEDP
        if announce_s is not None:
            dc.participant_announcement_period = dds.Duration.from_seconds(announce_s)
        q.discovery_config = dc
        return q

    if enable_mode == "immediate":
        time.sleep(delay_s)
        p = dds.DomainParticipant(domain, build_qos())
    else:
        factory = dds.DomainParticipant.participant_factory_qos
        ef = factory.entity_factory
        ef.autoenable_created_entities = False
        factory.entity_factory = ef
        dds.DomainParticipant.participant_factory_qos = factory
        p = dds.DomainParticipant(domain, build_qos())
        factory2 = dds.DomainParticipant.participant_factory_qos
        ef2 = factory2.entity_factory
        ef2.autoenable_created_entities = True
        factory2.entity_factory = ef2
        dds.DomainParticipant.participant_factory_qos = factory2
        time.sleep(delay_s)
        p.enable()

    my_index = p.qos.wire_protocol.participant_id
    t0 = time.monotonic()
    first_seen = None
    last_seen_count = 0
    deadline = t0 + hold_s
    while time.monotonic() < deadline:
        n = len(p.discovered_participants())
        if n > 0 and first_seen is None:
            first_seen = time.monotonic() - t0
        last_seen_count = n
        time.sleep(0.05)
    # final read
    last_seen_count = len(p.discovered_participants())

    result = {
        "role": role,
        "participant_id": my_index,
        "first_seen_ms": None if first_seen is None else round(first_seen * 1000),
        "final_peer_count": last_seen_count,
    }
    with open(resultfile, "w") as f:
        json.dump(result, f)
    p.close()


def loadavg():
    with open("/proc/loadavg") as f:
        return float(f.read().split()[0])


def orchestrate():
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    spdp2 = sys.argv[2] if len(sys.argv) > 2 else "1"
    announce = sys.argv[3] if len(sys.argv) > 3 else "2.0"
    hold_s = sys.argv[4] if len(sys.argv) > 4 else "20"
    base_domain = int(sys.argv[5]) if len(sys.argv) > 5 else 4200
    proto = "SPDP2" if spdp2 == "1" else "plainSPDP"
    print(f"xproc index-probe: {reps} reps, {proto}, announce_period={announce}s, "
          f"hold={hold_s}s, base_domain={base_domain}, enable_mode=delayed")
    print(f"{'rep':>3} {'dom':>5} {'load':>5}  {'A.idx':>5} {'A.sees':>7} {'A.first':>8}  "
          f"{'B.idx':>5} {'B.sees':>7} {'B.first':>8}  verdict")
    outcomes = []
    for rep in range(reps):
        dom = base_domain + rep
        rf_a = f"/tmp/claude-1000/-home-rti-act-sim-scope-infra/94024dc9-c16b-4ee8-86b0-1d0964c2d666/scratchpad/xa_{rep}.json"
        rf_b = f"/tmp/claude-1000/-home-rti-act-sim-scope-infra/94024dc9-c16b-4ee8-86b0-1d0964c2d666/scratchpad/xb_{rep}.json"
        for rf in (rf_a, rf_b):
            try:
                os.remove(rf)
            except FileNotFoundError:
                pass
        ld = loadavg()
        common = [sys.executable, SELF, "worker"]
        pa = subprocess.Popen(common + ["A", str(dom), "0", hold_s, spdp2, "delayed",
                                        announce, rf_a])
        pb = subprocess.Popen(common + ["B", str(dom), "5", hold_s, spdp2, "delayed",
                                        announce, rf_b])
        pa.wait()
        pb.wait()
        try:
            with open(rf_a) as f:
                ra = json.load(f)
            with open(rf_b) as f:
                rb = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"{rep:>3} {dom:>5} {ld:>5.2f}  <worker crash: {e}>")
            outcomes.append(("crash", None, None))
            continue
        # SUCCESS METRIC = "ever saw the peer within the hold" (first_seen populated),
        # NOT final_peer_count: the two workers hold hold_s from their OWN enable, so A
        # (delay 0) exits ~delay_s before B (delay 5) and B's final read can see A already
        # closed -> a false negative. first_seen is immune to that. final_peer_count is kept
        # only as a diagnostic (may legitimately be 0 if the peer exited first).
        a_sees = ra["first_seen_ms"] is not None
        b_sees = rb["first_seen_ms"] is not None
        verdict = "PASS" if (a_sees and b_sees) else "FAIL"
        anyhigh = (ra["participant_id"] > 4 or rb["participant_id"] > 4)
        tag = verdict + (" [idx>4!]" if (verdict == "FAIL" and anyhigh) else "")
        print(f"{rep:>3} {dom:>5} {ld:>5.2f}  {ra['participant_id']:>5} "
              f"{str(a_sees):>7} {str(ra['first_seen_ms']):>8}  "
              f"{rb['participant_id']:>5} {str(b_sees):>7} {str(rb['first_seen_ms']):>8}  {tag}")
        outcomes.append((verdict, ra, rb))

    fails = [o for o in outcomes if o[0] == "FAIL"]
    highidx_fails = [o for o in fails if o[1] and (o[1]["participant_id"] > 4 or o[2]["participant_id"] > 4)]
    print(f"\n{proto}: {len(fails)}/{reps} FAIL; of those, {len(highidx_fails)} had a "
          f"participant_id > 4 (outside default 0..4 probe range).")
    if fails and not highidx_fails:
        print("  -> failures occurred with BOTH indices in 0..4: NOT the index-range artifact; "
              "points at genuine no-resend / packet-loss behavior.")
    elif highidx_fails and len(highidx_fails) == len(fails):
        print("  -> EVERY failure had an index > 4: failures are the default-peer-descriptor "
              "RIG ARTIFACT, not a discovery defect. Explicit initial_peers would fix it.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        run_worker()
    else:
        orchestrate()
