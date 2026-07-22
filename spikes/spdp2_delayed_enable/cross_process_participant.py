#!/usr/bin/env python3
"""Part C helper: one SPDP2 participant in its own OS process, created disabled then
enable()d after a delay -- run twice (role A, role B) from spdp2_delayed_enable_spike.py's
"How to run" cross-process invocation to replay the money test across genuinely separate
processes instead of two participants in one Python process.

Usage: python3 cross_process_participant.py <role:A|B> <domain> <delay_s> <hold_s>
       [announce_period_s] [spdp2:0|1] [enable_mode:delayed|immediate]

announce_period_s (optional, "-" to skip): overrides discovery_config.
participant_announcement_period (default AUTO -> tracks participant_liveliness_assert_
period, 30s) -- a candidate workaround for the observed cross-process race, since the wire
capture showed a participant's own SPDP2 bootstrap burst is bounded (~5 sends over ~1.7s)
and does NOT self-initiate again afterward without external stimulus, unlike plain SPDP's
indefinite periodic re-announce.

spdp2 (optional, default 1): 0 = plain SPDP instead of SPDP2|SEDP, to check whether the
same cross-process race exists under plain SPDP too and whether plain SPDP's periodic
re-announce recovers from it where SPDP2 doesn't.

enable_mode (optional, default "delayed"):
  "delayed"   = the real D52 router lifecycle -- create the participant DISABLED (factory
                ManuallyEnable), sleep delay_s, then enable(). Participant goes live at
                ~t0+delay_s.
  "immediate" = NO disabled-then-enable dance -- sleep delay_s FIRST, then create the
                participant with the default autoenable (live at construction). Same
                t0/t0+delay_s liveness stagger, but never goes through the ManuallyEnable/
                enable() sequence. Isolates whether the cross-process race is caused by the
                disabled-then-enable mechanism itself or just by the staggered cold start.
"""
import os
import sys
import time

os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

import rti.connextdds as dds  # noqa: E402

SPDP2_SEDP = dds.DiscoveryConfigBuiltinPluginKindMask.SPDP2 | dds.DiscoveryConfigBuiltinPluginKindMask.SEDP


def main():
    role, domain, delay_s, hold_s = (sys.argv[1], int(sys.argv[2]), float(sys.argv[3]),
                                      float(sys.argv[4]))
    announce_period_s = (float(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] != "-"
                          else None)
    use_spdp2 = bool(int(sys.argv[6])) if len(sys.argv) > 6 else True
    enable_mode = sys.argv[7] if len(sys.argv) > 7 else "delayed"

    def build_qos():
        q = dds.DomainParticipantQos()
        q.transport_builtin = dds.TransportBuiltin.udpv4
        q.partition = dds.Partition(["SHARED"])
        dc = q.discovery_config
        if use_spdp2:
            dc.builtin_discovery_plugins = SPDP2_SEDP
        if announce_period_s is not None:
            dc.participant_announcement_period = dds.Duration.from_seconds(announce_period_s)
        q.discovery_config = dc
        return q

    if enable_mode == "immediate":
        # No disabled-then-enable dance: sleep first, then create the participant with the
        # default autoenable so it goes live at construction. Same t0/t0+delay_s liveness
        # stagger as "delayed", but the participant never passes through ManuallyEnable/
        # enable(). autoenable is the process default here (factory untouched).
        time.sleep(delay_s)
        p = dds.DomainParticipant(domain, build_qos())
        sys.stderr.write(f"[{role}] created+autoenabled at wall {time.time():.3f}\n")
    else:
        # "delayed": the real D52 router lifecycle -- create DISABLED, sleep, then enable().
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
        sys.stderr.write(f"[{role}] enabled at wall {time.time():.3f}\n")

    t0 = time.monotonic()
    sees_peer = None
    deadline = t0 + hold_s
    while time.monotonic() < deadline:
        if sees_peer is None and len(p.discovered_participants()) > 0:
            sees_peer = time.monotonic() - t0
            sys.stderr.write(f"[{role}] discovered peer at +{sees_peer*1000:.0f}ms after "
                              f"own enable\n")
            break
        time.sleep(0.02)

    print(f"{role} sees_peer={'never' if sees_peer is None else f'{sees_peer*1000:.0f}ms'}")
    p.close()


if __name__ == "__main__":
    main()
