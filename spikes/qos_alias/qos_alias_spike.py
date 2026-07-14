#!/usr/bin/env python3
"""QoS-alias spike — validate Phase 7a against the REAL production QoS libraries.

Phase 7a (D60) resolves the named QoS aliases in control-platform.yaml
(wan_event -> WAN_QOS_LIB::event_qos, etc.) from the loaded `qos_libraries` XML. D60's
API specifics were MCP-sourced and never build-verified, and the MCP has been wrong 3x on
this exact Python/XML QoS surface (see docs/connext-ai-issues). This proves the mechanism
against the actual files the config names.

Findings this spike locks in (all verified against Connext 7.7.0, rti.connextdds):
  1. The production QoS libs are TEMPLATED with 14 environment variables (peers + WAN
     tuning). They do NOT parse unless those are defined — a real deployment requirement
     7a/router_main must satisfy (the ACT harness sets them before launch). This spike sets
     loopback/test values so it is self-contained.
  2. Multi-file load is `QosProvider(";".join(paths))`; profiles resolve via
     `datareader_qos_from_profile` / `datawriter_qos_from_profile` /
     `participant_qos_from_profile` (NOT D60's `datareader_qos(...)`).
  3. control-platform.yaml's `lan_status_1hz` alias ORIGINALLY pointed at
     `LAN_QOS_LIB::status_1hz_qos`, a profile that DOES NOT EXIST (the lib defines
     `status_1sec_qos`). This spike caught it; the config is now fixed. 7a's
     validate_qos_aliases (D44/D60) must still add a profile-EXISTENCE check (not just the
     string rule) so this class of error is caught at load time. The resolve loop reports
     any alias that fails to resolve as a config defect, not a mechanism failure.

Parts:
  1. Load the three real qos_libraries (with env vars set).
  2. Resolve every profile control-platform.yaml's qos_profiles map references; assert the
     real ones resolve and surface the broken lan_status_1hz reference.
  3. Apply a resolved endpoint profile (event_qos) to a writer+reader and prove they match
     and forward end-to-end (the resolved QoS is actually usable, not just parseable).
  4. Create a DomainParticipant from a resolved participant profile
     (control_participant_udpv4_qos) — proves participant-level `qos:` application.

Exit 0 = the 7a mechanism works (parts 1-4); the broken-alias detection is reported but
does not fail the run (it is a config bug, not a mechanism bug).

Run:  python3 spikes/qos_alias/qos_alias_spike.py [base_domain_id]
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

# The production QoS libs are templated; define loopback/test values so they parse. In a
# real deployment the ACT harness supplies these (peer locators + WAN tuning) before launch.
_ENV_DEFAULTS = {
    "CONTROL_LAN_PEER1": "127.0.0.1", "CONTROL_LAN_PEER2": "127.0.0.1",
    "CONTROL_LAN_PEER3": "127.0.0.1", "CONTROL_WAN_PEER1": "127.0.0.1",
    "PLATFORM_LAN_PEER1": "127.0.0.1", "PLATFORM_LAN_PEER2": "127.0.0.1",
    "PLATFORM_LAN_PEER3": "127.0.0.1", "PLATFORM_WAN_PEER1": "127.0.0.1",
    "WAN_HB_PERIOD_SEC": "1", "WAN_HB_RETRIES": "10", "WAN_MAX_BLOCKING_SEC": "1",
    # WAN_TIMEOUT_SEC feeds participant_liveliness_lease_duration, and the WAN profile
    # HARDCODES participant_liveliness_assert_period = 30s. Connext requires assert < lease,
    # so WAN_TIMEOUT_SEC MUST be > 30 (the XML documents DEFAULT: 100). A value <= 30 yields
    # an inconsistent participant QoS that fails to create — a real 7a/deployment constraint.
    "WAN_TIMEOUT_SEC": "100", "WAN_TTL": "1", "WAN_RECEIVE_MULTICAST": "0",
}
for k, v in _ENV_DEFAULTS.items():
    os.environ.setdefault(k, v)

import rti.connextdds as dds  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
ACT_TYPES_XML = str(REPO_ROOT / "harness/act/node_sim/datamodel/act_types.xml")

# The qos_libraries and qos_profiles map exactly as control-platform.yaml declares them.
QOS_LIBS = [str(REPO_ROOT / p) for p in (
    "harness/act/config/qos/lan_qos_lib.xml",
    "harness/act/config/qos/wan_qos_lib.xml",
    "relay/qos_isc.xml",
)]
# alias -> LIB::profile, and whether it is used as a participant or endpoint QoS.
ENDPOINT_ALIASES = {
    "wan_event": "WAN_QOS_LIB::event_qos",
    "wan_status": "WAN_QOS_LIB::status_qos",
    # Was LAN_QOS_LIB::status_1hz_qos — a broken alias this spike caught (the lib has no such
    # profile); fixed in control-platform.yaml to status_1sec_qos. The resolve loop below
    # still flags ANY alias that fails to resolve, so a future break is caught the same way.
    "lan_status_1hz": "LAN_QOS_LIB::status_1sec_qos",
}
PARTICIPANT_ALIASES = {
    "control_wan_udpv4_qos": "WAN_QOS_LIB::control_participant_udpv4_qos",
    "platform_wan_udpv4_qos": "WAN_QOS_LIB::platform_participant_udpv4_qos",
}

TOPIC = "ControlCommand"
TYPE = "control_command"
DOMAIN = int(sys.argv[1]) if len(sys.argv) > 1 else 71


class SpikeError(AssertionError):
    pass


def load_provider():
    print("Part 1: load the three production qos_libraries (env vars set)")
    prov = dds.QosProvider(";".join(QOS_LIBS))
    n = len(prov.qos_profile_libraries)
    if n == 0:
        raise SpikeError("[1] no QoS libraries loaded")
    print(f"  [1] loaded {n} libraries from {len(QOS_LIBS)} files  PASS")
    return prov


def resolve_all(prov):
    print("Part 2: resolve every profile control-platform.yaml references")
    broken = []
    for alias, profile in ENDPOINT_ALIASES.items():
        try:
            prov.datawriter_qos_from_profile(profile)
            prov.datareader_qos_from_profile(profile)
            print(f"  [2] endpoint alias {alias} -> {profile}: OK")
        except Exception as e:
            broken.append((alias, profile, repr(e)[:80]))
            print(f"  [2] endpoint alias {alias} -> {profile}: NOT FOUND")
    for alias, profile in PARTICIPANT_ALIASES.items():
        try:
            prov.participant_qos_from_profile(profile)
            print(f"  [2] participant alias {alias} -> {profile}: OK")
        except Exception as e:
            broken.append((alias, profile, repr(e)[:80]))
            print(f"  [2] participant alias {alias} -> {profile}: NOT FOUND")
    return broken


def apply_and_forward(prov, domain):
    print("Part 3: apply a resolved endpoint profile (event_qos) and forward end-to-end")
    types = dds.QosProvider(ACT_TYPES_XML)
    dtype = types.type(TYPE)
    wqos = prov.datawriter_qos_from_profile("WAN_QOS_LIB::event_qos")
    rqos = prov.datareader_qos_from_profile("WAN_QOS_LIB::event_qos")

    def udp():
        q = dds.DomainParticipantQos()
        q.transport_builtin = dds.TransportBuiltin.udpv4
        return q

    pub_p = dds.DomainParticipant(domain, udp())
    sub_p = dds.DomainParticipant(domain, udp())
    try:
        wtopic = dds.DynamicData.Topic(pub_p, TOPIC, dtype)
        rtopic = dds.DynamicData.Topic(sub_p, TOPIC, dtype)
        writer = dds.DynamicData.DataWriter(dds.Publisher(pub_p), wtopic, wqos)
        reader = dds.DynamicData.DataReader(dds.Subscriber(sub_p), rtopic, rqos)

        got = 0
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and got == 0:
            s = dds.DynamicData(dtype)
            s["msg.destination"] = "Platform_30"
            writer.write(s)
            time.sleep(0.15)
            for sample in reader.take():
                if sample.info.valid:
                    got += 1
        if got == 0:
            raise SpikeError("[3] resolved event_qos writer/reader never matched/forwarded "
                             "(profile applied but not functional)")
        print(f"  [3] event_qos-profiled writer/reader matched and forwarded ({got})  PASS")
    finally:
        sub_p.close()
        pub_p.close()


def participant_from_profile(prov, domain):
    print("Part 4: create a DomainParticipant from a resolved participant profile")
    pqos = prov.participant_qos_from_profile(
        "WAN_QOS_LIB::control_participant_udpv4_qos")
    try:
        p = dds.DomainParticipant(domain, pqos)
    except dds.Error as e:
        raise SpikeError(
            "[4] participant creation from control_participant_udpv4_qos failed: "
            + repr(e)[:100] + " (check WAN_TIMEOUT_SEC > 30 — assert_period is hardcoded "
            "30s and lease must exceed it)")
    try:
        # Round-trip a discovery read to confirm the participant is live/enabled.
        _ = p.publication_reader
        print("  [4] participant created from control_participant_udpv4_qos  PASS")
    finally:
        p.close()


def main():
    print(f"qos-alias spike on domain {DOMAIN} (UDPv4-only, real ACT QoS libs)\n")
    failures = []
    try:
        prov = load_provider()
        print()
        broken = resolve_all(prov)
        print()
        apply_and_forward(prov, DOMAIN + 1)
        print()
        participant_from_profile(prov, DOMAIN + 2)
        print()
    except SpikeError as e:
        failures.append(str(e))
        print(f"  FAIL: {e}\n")
        broken = []

    if broken:
        print("DETECTED CONFIG DEFECTS (mechanism OK; these are bugs 7a's "
              "validate_qos_aliases must catch at load time):")
        for alias, profile, err in broken:
            print(f"  - control-platform.yaml alias '{alias}' -> '{profile}' does not exist")
        print()

    if failures:
        print(f"SPIKE FAILED ({len(failures)} mechanism failure(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SPIKE PASSED: the 7a QoS-alias mechanism works against the real production QoS "
          "libraries (load + resolve + apply + match + participant-from-profile). Note the "
          "env-var requirement above (and see PLAN.md for the WAN_TIMEOUT_SEC>30 constraint "
          "and the now-fixed lan_status_1hz alias this spike caught).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
