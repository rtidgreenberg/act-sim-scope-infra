#!/usr/bin/env python3
"""Platform control process — translates TeamAssignment into RouterCommand.

Subscribes to TeamAssignment on platform_lan (the router's control_event route
delivers it there from control_lan via WAN) and publishes ADD_PARTICIPANT_PARTITION /
REMOVE_PARTICIPANT_PARTITION commands on the same domain, targeting the local router's
admin channel (ActRouterCommand).

Design: docs/cpp_router/team-control-topic-plan.md §3.

Usage:
    python3 platform_control.py --domain 30 --node Platform_30

Environment:
    NDDSHOME              — Connext install
    NDDS_QOS_PROFILES     — must include the LAN QoS lib (set by env.sh)
    RTI_LICENSE_FILE      — Connext license
"""

import argparse
import os
import signal
import sys
from pathlib import Path

import rti.connextdds as dds

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
TYPES_XML = REPO_ROOT / "harness_v2" / "datamodel" / "gen" / "ActTypes.xml"

# RouterCommandKind ordinals (RouterAdminTypes.idl declaration order)
ADD_PARTICIPANT_PARTITION = 3
REMOVE_PARTICIPANT_PARTITION = 4

ROUTER_NAME_FMT = "platform-{domain}-control-platform"


def get_types_xml():
    """Return the committed generated XML type description."""
    if not TYPES_XML.exists():
        sys.exit(f"Types XML not found at {TYPES_XML} — run: "
                 f"rtiddsgen -convertToXml -d harness_v2/datamodel/gen "
                 f"harness_v2/datamodel/ActTypes.idl")
    return TYPES_XML


def _qos_provider_for(types_xml):
    """Avoid double-parsing types_xml if NDDS_QOS_PROFILES already includes it.

    ActTypes.idl's global `struct base_type` collides with an RTI-internal reserved
    name (`RTITypes::base_type`) if the SAME types XML gets parsed twice in one
    process. run_mesh.sh exports NDDS_QOS_PROFILES including this exact file for
    platform_control.py's benefit; Connext auto-loads that at process init, so if
    this process inherited that env var, loading the file again explicitly hits the
    collision. Reuse QosProvider.default (which already has it loaded) instead.
    Same fix as mesh_bridge.py._qos_provider_for().
    """
    resolved = Path(types_xml).resolve()
    for entry in os.environ.get("NDDS_QOS_PROFILES", "").split(";"):
        entry = entry.strip()
        if entry and Path(entry).resolve() == resolved:
            return dds.QosProvider.default
    return dds.QosProvider(str(types_xml))


def main():
    parser = argparse.ArgumentParser(description="Platform control process")
    parser.add_argument("--domain", type=int, required=True,
                        help="Platform LAN domain ID (e.g. 30)")
    parser.add_argument("--node", type=str, required=True,
                        help="Platform node name (e.g. Platform_30)")
    args = parser.parse_args()

    router_name = ROUTER_NAME_FMT.format(domain=args.domain)

    # Load committed generated XML types
    types_xml = get_types_xml()
    print(f"[platform_control] types: {types_xml}", flush=True)

    # Load types via QosProvider (avoid double-parse if NDDS_QOS_PROFILES has it)
    provider = _qos_provider_for(types_xml)
    team_assignment_type = provider.type("TeamAssignment")
    router_command_type = provider.type("RouterCommand")

    # Create participant on platform_lan domain
    qos_provider = dds.QosProvider.default
    participant = dds.DomainParticipant(
        args.domain,
        qos_provider.participant_qos_from_profile(
            "LAN_QOS_LIB::platform_lan_participant_qos"),
    )

    # TeamAssignment reader (RELIABLE + VOLATILE)
    team_topic = dds.DynamicData.Topic(participant, "ActTeamAssignment",
                                       team_assignment_type)
    reader_qos = (dds.QosProvider.default
                  .datareader_qos_from_profile("LAN_QOS_LIB::event_qos"))
    reader_qos << dds.Reliability.reliable()
    reader_qos << dds.Durability.volatile
    team_reader = dds.DynamicData.DataReader(
        dds.Subscriber(participant), team_topic, reader_qos)

    # RouterCommand writer (ActRouterCommand — same topic name the router listens on)
    cmd_topic = dds.DynamicData.Topic(participant, "ActRouterCommand",
                                      router_command_type)
    writer_qos = (dds.QosProvider.default
                  .datawriter_qos_from_profile("LAN_QOS_LIB::event_qos"))
    writer_qos << dds.Reliability.reliable()
    writer_qos << dds.Durability.volatile
    cmd_writer = dds.DynamicData.DataWriter(
        dds.Publisher(participant), cmd_topic, writer_qos)

    # Reaction state
    tracked_team = ""
    cmd_seq = 0

    def make_cmd(kind, partition):
        nonlocal cmd_seq
        cmd_seq += 1
        cmd = dds.DynamicData(router_command_type)
        cmd["target_node"] = args.node
        cmd["target_router"] = router_name
        cmd["command_id"] = f"team-ctrl-{cmd_seq}"
        cmd["kind"] = kind
        cmd["participant_name"] = "platform_wan"
        cmd["partition_name"] = partition
        return cmd

    # Graceful shutdown
    running = True

    def on_signal(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    # Read condition on the team_reader
    read_condition = dds.ReadCondition(team_reader, dds.DataState.any_data)
    waitset = dds.WaitSet()
    waitset += read_condition

    print(f"[platform_control] {args.node} domain={args.domain} "
          f"router={router_name} — waiting for TeamAssignment samples", flush=True)

    while running:
        try:
            waitset.dispatch(dds.Duration.from_milliseconds(500))
        except dds.TimeoutError:
            continue

        for sample in team_reader.take():
            if not sample.info.valid:
                continue
            data = sample.data
            platform_node = data["platform_node"]
            team_name = data["team_name"]

            # Only act on samples addressed to this platform
            if platform_node != args.node:
                continue

            if team_name == tracked_team:
                print(f"[platform_control] no-op: already on team '{team_name}'",
                      flush=True)
                continue

            # Remove old team partition if we had one
            if tracked_team:
                cmd = make_cmd(REMOVE_PARTICIPANT_PARTITION, tracked_team)
                cmd_writer.write(cmd)
                print(f"[platform_control] REMOVE_PARTICIPANT_PARTITION "
                      f"partition='{tracked_team}'", flush=True)

            # Add new team partition if non-empty
            if team_name:
                cmd = make_cmd(ADD_PARTICIPANT_PARTITION, team_name)
                cmd_writer.write(cmd)
                print(f"[platform_control] ADD_PARTICIPANT_PARTITION "
                      f"partition='{team_name}'", flush=True)

            tracked_team = team_name

    print("[platform_control] shutting down", flush=True)
    participant.close()


if __name__ == "__main__":
    main()
