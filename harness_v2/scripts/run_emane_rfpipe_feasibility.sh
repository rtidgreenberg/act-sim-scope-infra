#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly FIXTURE_DIR="$REPO_ROOT/harness_v2/emane_feasibility"
readonly PROJECT_NAME="act-emane-feasibility"
readonly COMPOSE=(docker compose -p "$PROJECT_NAME" -f "$FIXTURE_DIR/compose.yaml")

cleanup() {
    "${COMPOSE[@]}" down --remove-orphans >/dev/null 2>&1 || true
}

require_clean_runtime() {
    if "${COMPOSE[@]}" ps -q | grep -q .; then
        echo "EMANE feasibility topology is already active; run $0 down first." >&2
        exit 1
    fi
    if [[ ! -c /dev/net/tun ]]; then
        echo "/dev/net/tun is required for EMANE virtual transport." >&2
        exit 1
    fi
}

wait_for_interface() {
    local service=$1
    local attempt
    for attempt in {1..30}; do
        if "${COMPOSE[@]}" exec -T "$service" ip -4 addr show dev emane0 2>/dev/null | grep -q '10\.88\.0\.'; then
            return 0
        fi
        sleep 1
    done
    echo "$service did not create emane0." >&2
    "${COMPOSE[@]}" logs >&2 || true
    return 1
}

establish_nominal_link() {
    "${COMPOSE[@]}" exec -T node_1 \
        emaneevent-pathloss -i eth0 -g 224.1.2.8 -p 45702 1:2 0
}

run_probe() {
    local receiver_log receiver_pid
    receiver_log=$(mktemp /tmp/act-emane-rfpipe-receiver.XXXXXX)
    "${COMPOSE[@]}" exec -T node_1 python3 -c '
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("10.88.0.1", 49000))
sock.settimeout(12)
payload, address = sock.recvfrom(1024)
assert payload == b"act-emane-rfpipe-probe", payload
print(f"received={payload.decode()} source={address[0]}")
' > "$receiver_log" 2>&1 &
    receiver_pid=$!
    trap 'kill "$receiver_pid" 2>/dev/null || true; wait "$receiver_pid" 2>/dev/null || true; rm -f "$receiver_log"' RETURN
    sleep 1
    "${COMPOSE[@]}" exec -T node_2 sh -lc '
ip route get 10.88.0.1 | grep -q "dev emane0"
python3 -c "import socket; sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock.bind((\"10.88.0.2\", 0)); sock.sendto(b\"act-emane-rfpipe-probe\", (\"10.88.0.1\", 49000)); print(\"sent via=\" + sock.getsockname()[0])"
'
    wait "$receiver_pid"
    cat "$receiver_log"
}

case "${1:-}" in
    up)
        require_clean_runtime
        "${COMPOSE[@]}" up -d
        wait_for_interface node_1
        wait_for_interface node_2
        establish_nominal_link
        ;;
    probe)
        run_probe
        ;;
    down)
        cleanup
        ;;
    run)
        require_clean_runtime
        trap cleanup EXIT
        "${COMPOSE[@]}" up -d
        wait_for_interface node_1
        wait_for_interface node_2
        establish_nominal_link
        run_probe
        ;;
    *)
        echo "Usage: $0 {up|probe|down|run}" >&2
        exit 2
        ;;
esac