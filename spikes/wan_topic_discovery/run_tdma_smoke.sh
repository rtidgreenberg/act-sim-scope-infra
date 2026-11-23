#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
mode=${1:-smoke}
if [[ $# -gt 1 || ( $mode != smoke && $mode != priority ) ]]; then
    printf 'Usage: %s [smoke|priority]\n' "$0" >&2
    exit 2
fi
work=$(mktemp -d /tmp/act-tdmasmoke.XXXXXX)
project="act-tdmasmoke-${work##*.}"
project=${project,,}
export EMANE_FEASIBILITY_NETWORK="$project"
compose=(docker compose -p "$project" -f harness_v2/emane_feasibility/compose.yaml
         -f spikes/wan_topic_discovery/emane.compose.yaml -f spikes/wan_topic_discovery/tdma.compose.yaml)
cleanup() {
    local status=$?
    if ((status != 0)); then
        "${compose[@]}" logs --tail 8 >&2 || true
        for log in "$work/tx.json" "$work/rx.json" "$work/priority.log" "$work/"*.capture.log; do
            [[ ! -f $log ]] || tail -n 10 "$log" >&2
        done
    fi
    "${compose[@]}" down >&2 || status=1
    rm -f -- "$work/tx.json" "$work/rx.json"
    rm -f -- "$work/priority.log" "$work/queues.txt" "$work/queues-before.txt" \
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
    "${compose[@]}" exec -T "node_$id" ip -6 route get "fd42:88::$peer" | grep 'dev emane0' >&2
done
"${compose[@]}" exec -T node_1 emaneevent-pathloss -i eth0 -g 224.1.2.8 -p 45702 1:2 0
if [[ $mode == priority ]]; then
    "${compose[@]}" exec -T node_1 emaneevent-tdmaschedule -i eth0 -g 224.1.2.8 -p 45702 /probe/tdma-priority.xml
    "${compose[@]}" exec -T node_1 emanesh localhost get table 1 mac QueueStatusTable >"$work/queues-before.txt"
    "${compose[@]}" exec -T node_2 python3 /probe/tdma_priority.py rx >"$work/priority.log" 2>&1 &
    receiver=$!
    for side in tx rx; do
        node=node_1
        [[ $side == rx ]] && node=node_2
        "${compose[@]}" exec -T "$node" dumpcap -i emane0 \
            -f 'ip6 and udp and src host fd42:88::1 and dst host fd42:88::2 and dst port 31999' \
            -a duration:13 -w /tmp/priority.pcapng >"$work/$side.capture.log" 2>&1 &
        if [[ $side == tx ]]; then tx_capture=$!; else rx_capture=$!; fi
    done
    for attempt in {1..40}; do
        if grep -q '^Ready$' "$work/priority.log" && grep -q 'File:' "$work/tx.capture.log" &&
            grep -q 'File:' "$work/rx.capture.log"; then
            break
        fi
        sleep .1
    done
    grep -q '^Ready$' "$work/priority.log"
    grep -q 'File:' "$work/tx.capture.log"
    grep -q 'File:' "$work/rx.capture.log"
    "${compose[@]}" exec -T node_1 python3 /probe/tdma_priority.py tx
    wait "$receiver"
    wait "$tx_capture"
    wait "$rx_capture"
    for side in tx rx; do
        node=node_1
        [[ $side == rx ]] && node=node_2
        grep -q 'pcap:0/dumpcap:0' "$work/$side.capture.log"
        "${compose[@]}" exec -T "$node" tshark -n -r /tmp/priority.pcapng \
            -T fields -e ipv6.tclass.dscp -e udp.payload >"$work/$side.marks"
    done
    "${compose[@]}" exec -T node_1 emanesh localhost get table 1 mac QueueStatusTable >"$work/queues.txt"
    mkdir -p debug/test_reports
    report=$(mktemp "$PWD/debug/test_reports/emane_tdma_priority.XXXXXX.json")
    python3 spikes/wan_topic_discovery/tdma_priority.py report --work "$work" | tee "$report"
    printf 'Report: %s\n' "$report" >&2
    exit 0
fi
"${compose[@]}" exec -T node_1 emaneevent-tdmaschedule -i eth0 -g 224.1.2.8 -p 45702 /probe/tdma-smoke.xml
"${compose[@]}" exec -T -e PROBE_PEER=fd42:88::1 node_2 python3 /probe/tdma_smoke.py rx >"$work/rx.json" 2>&1 &
receiver=$!
"${compose[@]}" exec -T -e PROBE_PEER=fd42:88::2 node_1 python3 /probe/tdma_smoke.py tx >"$work/tx.json" 2>&1
wait "$receiver"
mkdir -p debug/test_reports
report=$(mktemp "$PWD/debug/test_reports/emane_tdma_smoke.XXXXXX.json")
python3 -m json.tool --json-lines "$work/tx.json" >/dev/null
python3 -m json.tool --json-lines "$work/rx.json" >/dev/null
printf '[%s,%s]\n' "$(cat "$work/tx.json")" "$(cat "$work/rx.json")" | tee "$report"
printf 'Report: %s\n' "$report" >&2
