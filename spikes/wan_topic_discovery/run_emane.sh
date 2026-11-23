#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
work=$(mktemp -d /tmp/act-rfpriority.XXXXXX)
project="act-rfpriority-${work##*.}"
project=${project,,}
export EMANE_FEASIBILITY_NETWORK="$project"
compose=(docker compose -p "$project" -f harness_v2/emane_feasibility/compose.yaml
         -f spikes/wan_topic_discovery/emane.compose.yaml)
cleanup() {
    local status=$?
    if ((status != 0)); then
        "${compose[@]}" logs --tail 20 >&2 || true
        for log in "$work/"*.log; do
            [[ ! -f $log ]] || tail -n 10 "$log" >&2
        done
    fi
    "${compose[@]}" down >&2 || status=1
    rm -f -- "$work/tx.log" "$work/rx.log" "$work/tx.rows" "$work/rx.rows" "$work/tx.capture.log" "$work/rx.capture.log"
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
"${compose[@]}" exec -T -e PROBE_PEER=fd42:88::1 node_2 python3 /probe/emane_probe.py rx >"$work/rx.log" 2>&1 &
receiver=$!
for side in tx rx; do
    node=node_1
    [[ $side == rx ]] && node=node_2
    "${compose[@]}" exec -T "$node" dumpcap -i emane0 \
        -f 'ip6 and udp and src host fd42:88::1 and dst host fd42:88::2 and dst port 32911' \
        -a duration:35 -w /tmp/probe.pcapng >"$work/$side.capture.log" 2>&1 &
    if [[ $side == tx ]]; then tx_capture=$!; else rx_capture=$!; fi
done
for attempt in {1..40}; do
    if grep -q Ready "$work/rx.log" &&
        grep -q 'File:' "$work/tx.capture.log" && grep -q 'File:' "$work/rx.capture.log"; then
        break
    fi
    sleep .1
done
grep -q Ready "$work/rx.log"
grep -q 'File:' "$work/tx.capture.log"
grep -q 'File:' "$work/rx.capture.log"
"${compose[@]}" exec -T -e PROBE_PEER=fd42:88::2 node_1 python3 /probe/emane_probe.py tx | tee "$work/tx.log" >&2
wait "$tx_capture"
wait "$rx_capture"
wait "$receiver"
for side in tx rx; do
    grep -Eq 'pcap:0/dumpcap:0|Dropped: 0|0 dropped' "$work/$side.capture.log"
    node=node_1
    [[ $side == rx ]] && node=node_2
    "${compose[@]}" exec -T "$node" tshark -n -r /tmp/probe.pcapng -Y 'udp contains "RFTEST-"' \
        -T fields -e frame.time_epoch -e ipv6.tclass.dscp -e udp.payload >"$work/$side.rows"
done
mkdir -p debug/test_reports
report=$(mktemp "$PWD/debug/test_reports/emane_transport_priority.XXXXXX.json")
python3 spikes/wan_topic_discovery/emane_results.py "$work" | tee "$report"
printf 'Report: %s\n' "$report" >&2
