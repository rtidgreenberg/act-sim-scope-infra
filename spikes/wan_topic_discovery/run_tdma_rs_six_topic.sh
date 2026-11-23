#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
mode=${1:-dscp}
if [[ $# -gt 1 || ( $mode != baseline && $mode != dscp ) ]]; then
    printf 'Usage: %s [baseline|dscp]\n' "$0" >&2
    exit 2
fi
work=$(mktemp -d /tmp/act-tdma-rs6.XXXXXX)
project="act-tdma-rs6-${work##*.}"
project=${project,,}
export EMANE_FEASIBILITY_NETWORK="$project"
compose=(docker compose -p "$project" -f harness_v2/emane_feasibility/compose.yaml
         -f spikes/wan_topic_discovery/emane.compose.yaml -f spikes/wan_topic_discovery/tdma.compose.yaml)
cleanup() {
    local status=$?
    if ((status != 0)); then
        "${compose[@]}" logs --tail 8 >&2 || true
        for log in "$work/rs_tx.log" "$work/rs_rx.log" "$work/rx.log" "$work/"*.capture.log; do
            [[ ! -f $log ]] || tail -n 15 "$log" >&2
        done
    fi
    "${compose[@]}" down >&2 || status=1
    rm -f -- "$work/rs_tx.log" "$work/rs_rx.log" "$work/rx.log" \
        "$work/queues-before.txt" "$work/queues.txt" \
        "$work/tx.capture.log" "$work/rx.capture.log" "$work/tx.marks" "$work/rx.marks"
    rmdir -- "$work"
    exit "$status"
}
trap cleanup EXIT

if [[ $mode == baseline ]]; then
    tos_p1=0 tos_p2=0 tos_p3=0
else
    tos_p1=192 tos_p2=136 tos_p3=104
fi

"${compose[@]}" up -d >&2
for id in 1 2; do
    for attempt in {1..20}; do
        if "${compose[@]}" exec -T "node_$id" ip -4 addr show dev emane0 2>/dev/null | grep -q "10.88.0.$id/"; then
            break
        fi
        sleep .25
    done
done
"${compose[@]}" exec -T node_1 emaneevent-pathloss -i eth0 -g 224.1.2.8 -p 45702 1:2 0
"${compose[@]}" exec -T node_1 emaneevent-tdmaschedule -i eth0 -g 224.1.2.8 -p 45702 /probe/tdma-rs-six-topic.xml

rs_bin="/opt/rti.com/rti_connext_dds-7.7.0/bin/rtiroutingservice"
cfg_files="/probe/rs_six_topic_qos.xml;/probe/rs_six_topic_types.xml;/probe/rs_six_topic.xml"
"${compose[@]}" exec -T -e WAN_PEER=10.88.0.2 -e TOS_P1="$tos_p1" -e TOS_P2="$tos_p2" -e TOS_P3="$tos_p3" \
    node_1 "$rs_bin" -cfgFile "$cfg_files" -cfgName rs_six_tx \
    >"$work/rs_tx.log" 2>&1 &
"${compose[@]}" exec -T -e WAN_PEER=10.88.0.1 -e TOS_P1="$tos_p1" -e TOS_P2="$tos_p2" -e TOS_P3="$tos_p3" \
    node_2 "$rs_bin" -cfgFile "$cfg_files" -cfgName rs_six_rx \
    >"$work/rs_rx.log" 2>&1 &

"${compose[@]}" exec -T node_1 emanesh localhost get table 1 mac QueueStatusTable >"$work/queues-before.txt"
"${compose[@]}" exec -T node_2 python3 /probe/tdma_rs_six_topic.py rx >"$work/rx.log" 2>&1 &
receiver=$!

for side in tx rx; do
    node=node_1
    [[ $side == rx ]] && node=node_2
    "${compose[@]}" exec -T "$node" dumpcap -i emane0 \
        -f 'ip and udp and src host 10.88.0.1 and dst host 10.88.0.2' \
        -a duration:15 -w /tmp/rs6.pcapng >"$work/$side.capture.log" 2>&1 &
    if [[ $side == tx ]]; then tx_capture=$!; else rx_capture=$!; fi
done

for attempt in {1..60}; do
    if grep -q '^Ready$' "$work/rx.log" && grep -q 'File:' "$work/tx.capture.log" &&
        grep -q 'File:' "$work/rx.capture.log"; then
        break
    fi
    sleep .2
done
grep -q '^Ready$' "$work/rx.log"
grep -q 'File:' "$work/tx.capture.log"
grep -q 'File:' "$work/rx.capture.log"

"${compose[@]}" exec -T node_1 python3 /probe/tdma_rs_six_topic.py tx
wait "$receiver"
wait "$tx_capture"
wait "$rx_capture"

for side in tx rx; do
    node=node_1
    [[ $side == rx ]] && node=node_2
    grep -q 'pcap:0/dumpcap:0' "$work/$side.capture.log"
    "${compose[@]}" exec -T "$node" tshark -n -r /tmp/rs6.pcapng \
        -T fields -e ip.dsfield.dscp -e frame.len >"$work/$side.marks"
done
"${compose[@]}" exec -T node_1 emanesh localhost get table 1 mac QueueStatusTable >"$work/queues.txt"

mkdir -p debug/test_reports
report=$(mktemp "$PWD/debug/test_reports/emane_tdma_rs_six_${mode}.XXXXXX.json")
python3 spikes/wan_topic_discovery/tdma_rs_six_topic.py report --work "$work" --mode "$mode" | tee "$report"
printf 'Report: %s\n' "$report" >&2
