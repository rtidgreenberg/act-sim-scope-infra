#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
work=$(mktemp -d /tmp/act-tdma-rs6v6.XXXXXX)
project="act-tdma-rs6v6-${work##*.}"
project=${project,,}
export EMANE_FEASIBILITY_NETWORK="$project"
compose=(docker compose -p "$project" -f harness_v2/emane_feasibility/compose.yaml
         -f spikes/wan_topic_discovery/emane.compose.yaml -f spikes/wan_topic_discovery/tdma.compose.yaml
         -f spikes/wan_topic_discovery/rs_six_v6.compose.yaml)
cleanup() {
    local status=$?
    if ((status != 0)); then
        "${compose[@]}" logs --tail 8 >&2 || true
        for log in "$work/rs_ctrl.log" "$work/rs_plat.log" "$work/rx.log" "$work/"*.capture.log; do
            [[ ! -f $log ]] || tail -n 15 "$log" >&2
        done
    fi
    "${compose[@]}" down >&2 || status=1
    rm -f -- "$work/rs_ctrl.log" "$work/rs_plat.log" "$work/rx.log" \
        "$work/queues-before.txt" "$work/queues.txt" \
        "$work/tx.capture.log" "$work/rx.capture.log" "$work/tx.marks" "$work/rx.marks"
    rmdir -- "$work"
    exit "$status"
}
trap cleanup EXIT

"${compose[@]}" up -d >&2
for id in 1 2; do
    for attempt in {1..20}; do
        if "${compose[@]}" exec -T "node_$id" ip -4 addr show dev emane0 2>/dev/null | grep -q "10.88.0.$id/"; then
            break
        fi
        sleep .25
    done
    "${compose[@]}" exec -T "node_$id" ip -6 addr add "fd42:88::$id/64" dev emane0 nodad
done
for id in 1 2; do
    peer=$((3 - id))
    mac=$("${compose[@]}" exec -T "node_$peer" cat /sys/class/net/emane0/address)
    "${compose[@]}" exec -T "node_$id" ip -6 neigh replace "fd42:88::$peer" lladdr "$mac" dev emane0 nud permanent
done
"${compose[@]}" exec -T node_1 emaneevent-pathloss -i eth0 -g 224.1.2.8 -p 45702 1:2 0
"${compose[@]}" exec -T node_1 emaneevent-tdmaschedule -i eth0 -g 224.1.2.8 -p 45702 /probe/tdma-rs-six-topic.xml

"${compose[@]}" exec -T node_1 python3 /probe/tdma_rs_six_v6.py config --out /tmp/rs_ctrl.yaml --role control
"${compose[@]}" exec -T node_2 python3 /probe/tdma_rs_six_v6.py config --out /tmp/rs_plat.yaml --role platform

common_env=(
    -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0
    -e CONTROL_LAN_PEER1=127.0.0.1 -e CONTROL_LAN_PEER2=127.0.0.1 -e CONTROL_LAN_PEER3=127.0.0.1
    -e PLATFORM_LAN_PEER1=127.0.0.1 -e PLATFORM_LAN_PEER2=127.0.0.1 -e PLATFORM_LAN_PEER3=127.0.0.1
    -e WAN_HB_PERIOD_SEC=1 -e WAN_MAX_BLOCKING_SEC=1
    -e WAN_TIMEOUT_SEC=100 -e WAN_TTL=1 -e WAN_RECEIVE_MULTICAST=0 -e RS_FLOW_BYTES_PER_PERIOD=2500
)

"${compose[@]}" exec -T "${common_env[@]}" -e WAN_PEER_V6=fd42:88::2 node_1 \
    python3 /workspace/router/routing_service/run.py \
    --config /tmp/rs_ctrl.yaml --role control --node-name Control_20 --name rs-ctrl \
    --build-dir /workspace/router/build/routing_service \
    >"$work/rs_ctrl.log" 2>&1 &

"${compose[@]}" exec -T "${common_env[@]}" -e WAN_PEER_V6=fd42:88::1 node_2 \
    python3 /workspace/router/routing_service/run.py \
    --config /tmp/rs_plat.yaml --role platform --node-name Platform_30 --name rs-plat \
    --build-dir /workspace/router/build/routing_service \
    >"$work/rs_plat.log" 2>&1 &

"${compose[@]}" exec -T node_2 emanesh localhost get table 2 mac QueueStatusTable >"$work/queues-before.txt"
"${compose[@]}" exec -T "${common_env[@]}" -e WAN_PEER_V6=fd42:88::2 node_1 python3 /probe/tdma_rs_six_v6.py rx_ctrl --count 200 >"$work/rx.log" 2>&1 &
receiver=$!

for side in tx rx; do
    node=node_2
    [[ $side == rx ]] && node=node_1
    "${compose[@]}" exec -T "$node" dumpcap -i emane0 \
        -f 'ip6 and udp and src host fd42:88::2 and dst host fd42:88::1' \
        -a duration:22 -w /tmp/rs6v6.pcapng >"$work/$side.capture.log" 2>&1 &
    if [[ $side == tx ]]; then tx_capture=$!; else rx_capture=$!; fi
done

for attempt in {1..80}; do
    if grep -q '^Ready$' "$work/rx.log" && grep -q 'File:' "$work/tx.capture.log" &&
        grep -q 'File:' "$work/rx.capture.log"; then
        break
    fi
    sleep .25
done
grep -q '^Ready$' "$work/rx.log"
grep -q 'File:' "$work/tx.capture.log"
grep -q 'File:' "$work/rx.capture.log"

"${compose[@]}" exec -T "${common_env[@]}" node_2 python3 /probe/tdma_rs_six_v6.py tx_plat --count 200
wait "$receiver"
wait "$tx_capture"
wait "$rx_capture"

for side in tx rx; do
    node=node_2
    [[ $side == rx ]] && node=node_1
    grep -q 'pcap:0/dumpcap:0' "$work/$side.capture.log"
    "${compose[@]}" exec -T "$node" tshark -n -r /tmp/rs6v6.pcapng \
        -T fields -e ipv6.tclass.dscp -e frame.len >"$work/$side.marks"
done
"${compose[@]}" exec -T node_2 emanesh localhost get table 2 mac QueueStatusTable >"$work/queues.txt"

mkdir -p debug/test_reports
report=$(mktemp "$PWD/debug/test_reports/emane_tdma_rs_six_v6.XXXXXX.json")
python3 spikes/wan_topic_discovery/tdma_rs_six_v6.py report --work "$work" | tee "$report"
printf 'Report: %s\n' "$report" >&2
