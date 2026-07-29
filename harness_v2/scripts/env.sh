#!/bin/bash
# harness_v2 simulator environment (trimmed v2).
#
# The v2 node sims are LAN-side participants (LAN_QOS_LIB::*_lan_participant_qos), so they
# need only the LAN QoS library plus the ACT datamodel on NDDS_QOS_PROFILES. harness/act's
# params/system_params.sh additionally pulled in the WAN and remote-admin QoS libs, the
# legacy routing_service_config.xml, and a large set of WAN/peer/channel vars — none of
# which the LAN sims consume — so they are intentionally omitted here (add later if needed).
#
# Paths resolve relative to this file, so it works from any cwd. Source it, don't exec it.

_V2_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export NDDS_QOS_PROFILES="${_V2_ROOT}/qos/lan_qos_lib.xml;${_V2_ROOT}/datamodel/gen/ActTypes.xml"

# lan_qos_lib.xml expands these LAN initial-peer vars in <initial_peers> (all overridable).
# Values mirror harness/act/params/system_params.sh: multicast + UDP loopback + shmem.
export LAN_MULTICAST_ADDRESS="${LAN_MULTICAST_ADDRESS:-builtin.udpv4://239.255.0.1}"
export PLATFORM_LAN_PEER1="${PLATFORM_LAN_PEER1:-$LAN_MULTICAST_ADDRESS}"
export PLATFORM_LAN_PEER2="${PLATFORM_LAN_PEER2:-builtin.udpv4://127.0.0.1}"
export PLATFORM_LAN_PEER3="${PLATFORM_LAN_PEER3:-builtin.shmem://}"
export CONTROL_LAN_PEER1="${CONTROL_LAN_PEER1:-$LAN_MULTICAST_ADDRESS}"
export CONTROL_LAN_PEER2="${CONTROL_LAN_PEER2:-builtin.udpv4://127.0.0.1}"
export CONTROL_LAN_PEER3="${CONTROL_LAN_PEER3:-builtin.shmem://}"

: "${NDDSHOME:=/home/rti/rti_connext_dds-7.7.0}"; export NDDSHOME
: "${RTI_LICENSE_FILE:=${NDDSHOME}/rti_license.dat}"; export RTI_LICENSE_FILE
