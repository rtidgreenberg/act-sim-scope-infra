#!/bin/bash
# Internal Compose implementation of the canonical EMANE mesh lifecycle.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V2_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$V2_ROOT/.." && pwd)"
ACTION="${1:-}"
[[ $# -gt 0 ]] && shift
PLATFORMS=2
PLATFORMS_EXPLICIT=0
WORKDIR=
VERBOSITY=1
TEST_ID=container-baseline
EMANE_ENABLED=0
TOPOLOGY=
WITH_DASHBOARD=0
DASHBOARD_PORT=8080
PLATFORM_IDS=()
declare -A EMANE_NEM_ID EMANE_IP EMANE_CONTROL_IP

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platforms) PLATFORMS="$2"; PLATFORMS_EXPLICIT=1; shift 2 ;;
        --topology) TOPOLOGY="$2"; shift 2 ;;
        --workdir) WORKDIR="$2"; shift 2 ;;
        --verbosity) VERBOSITY="$2"; shift 2 ;;
        --test-id) TEST_ID="$2"; shift 2 ;;
        --emane) EMANE_ENABLED=1; shift ;;
        --with-dashboard) WITH_DASHBOARD=1; shift ;;
        --dashboard-port) DASHBOARD_PORT="$2"; shift 2 ;;
        --help) echo "Usage: $0 {up|render|smoke|audit|isolation|down} --emane [--topology FILE|--platforms N] [--with-dashboard] [--dashboard-port PORT] [--workdir DIR] [--test-id ID] [--verbosity 0-3]"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

load_topology() {
    PLATFORM_IDS=()
    EMANE_NEM_ID=()
    EMANE_IP=()
    EMANE_CONTROL_IP=()
    local identifier role lan_domain nem_id rf_ip control_ip id
    if [[ -z "$TOPOLOGY" ]]; then
        EMANE_NEM_ID[control_20]=1
        EMANE_IP[control_20]=10.88.0.1
        EMANE_CONTROL_IP[control_20]=172.29.250.11
        for id in $(seq 30 $((29 + PLATFORMS))); do
            PLATFORM_IDS+=("$id")
            identifier="platform_${id}"
            EMANE_NEM_ID["$identifier"]=$((id - 28))
            EMANE_IP["$identifier"]="10.88.0.$((id - 28))"
            EMANE_CONTROL_IP["$identifier"]="172.29.250.$((id - 18))"
        done
        return
    fi
    [[ "$EMANE_ENABLED" == 1 ]] || {
        echo "--topology requires --emane" >&2
        exit 1
    }
    [[ "$PLATFORMS_EXPLICIT" == 0 ]] || {
        echo "--topology and --platforms cannot be combined" >&2
        exit 1
    }
    while IFS=$'\t' read -r identifier role lan_domain nem_id rf_ip control_ip; do
        EMANE_NEM_ID["$identifier"]=$nem_id
        EMANE_IP["$identifier"]=$rf_ip
        EMANE_CONTROL_IP["$identifier"]=$control_ip
        if [[ "$role" == platform ]]; then
            PLATFORM_IDS+=("$lan_domain")
        fi
    done < <(python3 "$V2_ROOT/scripts/compile_mesh_topology.py" --topology "$TOPOLOGY")
    PLATFORMS=${#PLATFORM_IDS[@]}
}

load_topology

if [[ -z "$WORKDIR" ]]; then
    if [[ "$EMANE_ENABLED" == 1 ]]; then
        WORKDIR=/tmp/act_emane_container_baseline
    else
        WORKDIR=/tmp/act_container_baseline
    fi
fi

compose() {
    local project=act-container-baseline
    [[ "$EMANE_ENABLED" == 1 ]] && project=act-emane-container-baseline
    docker compose -p "$project" -f "$WORKDIR/compose.yaml" "$@"
}

platform_ids() {
    printf '%s\n' "${PLATFORM_IDS[@]}"
}

ensure_no_active_baseline() {
    local project=act-container-baseline
    [[ "$EMANE_ENABLED" == 1 ]] && project=act-emane-container-baseline
    if docker ps -q --filter "label=com.docker.compose.project=$project" | grep -q .; then
        echo "Error: an $project run is active; run its owned down action first" >&2
        exit 1
    fi
}

clear_debug_dir() {
    local directory=$1
    mkdir -p "$directory"
    if [[ "$EMANE_ENABLED" == 1 ]]; then
        docker run --rm -v "$directory":/node-debug connext:7.7.0 \
            find /node-debug -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    else
        find "$directory" -mindepth 1 -delete
    fi
}

render_config() {
    local id="$1" output="$2"
    sed -e "s/Platform_30/Platform_${id}/g" \
        -e "s/platform-30-control-platform/platform-${id}-control-platform/g" \
        -e "0,/domain: 30/s//domain: ${id}/" \
        "$REPO_ROOT/router/config/control-platform.yaml" > "$output"
    if [[ "$EMANE_ENABLED" == 1 ]]; then
        sed -i 's/qos: wan_participant\b/qos: wan_participant_emane/' "$output"
    fi
}

render_control_config() {
    local output=$1
    cp "$REPO_ROOT/router/config/control-platform.yaml" "$output"
    if [[ "$EMANE_ENABLED" == 1 ]]; then
        sed -i 's/qos: wan_participant\b/qos: wan_participant_emane/' "$output"
    fi
}

emane_runtime_yaml() {
    [[ "$EMANE_ENABLED" == 1 ]] || return 0
    printf '%s\n' '    cap_add:' '      - NET_ADMIN' '    devices:' '      - /dev/net/tun:/dev/net/tun'
}

container_user_yaml() {
    [[ "$EMANE_ENABLED" == 1 ]] && return 0
    printf '    user: "%s:%s"\n' "${ACT_CONTAINER_UID:-$(id -u)}" \
        "${ACT_CONTAINER_GID:-$(id -g)}"
}

wan_peer_yaml() {
    printf '      WAN_PEER: %s\n' "$wan_peer"
}

emane_environment_yaml() {
    local nem_id=$1 emane_ip=$2
    [[ "$EMANE_ENABLED" == 1 ]] || return 0
    printf '%s\n' '      EMANE_ENABLED: "1"' "      EMANE_NEM_ID: \"$nem_id\"" \
        "      EMANE_IP: $emane_ip"
}

emane_network_yaml() {
    local address=$1
    [[ "$EMANE_ENABLED" == 1 ]] || return 0
    printf '%s\n' '    networks:' '      emane_ctrl:' "        ipv4_address: $address"
}

dashboard_environment_yaml() {
    [[ "$WITH_DASHBOARD" == 1 ]] || return 0
    printf '%s\n' '      MESH_DASHBOARD: "1"' '      MESH_DASHBOARD_PORT: "8080"'
}

dashboard_port_yaml() {
    [[ "$WITH_DASHBOARD" == 1 ]] || return 0
    printf '%s\n' '    ports:' "      - \"${DASHBOARD_PORT}:8080\""
}

write_compose() {
    local compose_file="$WORKDIR/compose.yaml" wan_peer='10@control-20'
    [[ "$EMANE_ENABLED" == 1 ]] && wan_peer="builtin.udpv4://${EMANE_IP[control_20]}"
    cat > "$compose_file" <<EOF
services:
  control-20:
    image: connext:7.7.0
    init: true
$(emane_runtime_yaml)
$(container_user_yaml)
    entrypoint: ["bash", "/workspace/docker/connext-7.7/node-entrypoint.sh"]
    environment:
      NODE_ROLE: control
      NODE_ID: "20"
      NODE_NAME: Control_20
      NODE_DEBUG_DIR: /node-debug
      NODE_CONFIG_PATH: /run/node-config.yaml
      SIM_DESTINATION: Platform_30
      SIM_VERBOSITY: "${VERBOSITY}"
      NDDSHOME: /opt/rti.com/rti_connext_dds-7.7.0
      RTI_LICENSE_FILE: /shared/rti_license.dat
      CONTROL_LAN_PEER1: 127.0.0.1
      CONTROL_LAN_PEER2: 127.0.0.1
      CONTROL_LAN_PEER3: 127.0.0.1
      PLATFORM_LAN_PEER1: 127.0.0.1
      PLATFORM_LAN_PEER2: 127.0.0.1
      PLATFORM_LAN_PEER3: 127.0.0.1
$(wan_peer_yaml)
      WAN_RECEIVE_MULTICAST: "0"
$(emane_environment_yaml "${EMANE_NEM_ID[control_20]}" "${EMANE_IP[control_20]}")
$(dashboard_environment_yaml)
    volumes:
      - ${REPO_ROOT}:/workspace:ro
      - ${CONNEXT_SHARED_DIR:?Set CONNEXT_SHARED_DIR to the license directory}:/shared:ro
      - ${WORKDIR}/control-platform-20.yaml:/run/node-config.yaml:ro
      - ${REPO_ROOT}/debug/control_20_debug:/node-debug
$(dashboard_port_yaml)
$(emane_network_yaml "${EMANE_CONTROL_IP[control_20]}")
EOF
    local id
    for id in $(platform_ids); do
        cat >> "$compose_file" <<EOF
  platform-${id}:
    image: connext:7.7.0
    init: true
$(emane_runtime_yaml)
$(container_user_yaml)
    entrypoint: ["bash", "/workspace/docker/connext-7.7/node-entrypoint.sh"]
    environment:
      NODE_ROLE: platform
      NODE_ID: "${id}"
      NODE_NAME: Platform_${id}
      NODE_DEBUG_DIR: /node-debug
      NODE_CONFIG_PATH: /run/node-config.yaml
      SIM_DESTINATION: Control_20
      SIM_VERBOSITY: "${VERBOSITY}"
      NDDSHOME: /opt/rti.com/rti_connext_dds-7.7.0
      RTI_LICENSE_FILE: /shared/rti_license.dat
      CONTROL_LAN_PEER1: 127.0.0.1
      CONTROL_LAN_PEER2: 127.0.0.1
      CONTROL_LAN_PEER3: 127.0.0.1
      PLATFORM_LAN_PEER1: 127.0.0.1
      PLATFORM_LAN_PEER2: 127.0.0.1
      PLATFORM_LAN_PEER3: 127.0.0.1
$(wan_peer_yaml)
      WAN_RECEIVE_MULTICAST: "0"
$(emane_environment_yaml "${EMANE_NEM_ID[platform_${id}]}" "${EMANE_IP[platform_${id}]}")
    volumes:
      - ${REPO_ROOT}:/workspace:ro
      - ${CONNEXT_SHARED_DIR:?Set CONNEXT_SHARED_DIR to the license directory}:/shared:ro
      - ${WORKDIR}/control-platform-${id}.yaml:/run/node-config.yaml:ro
      - ${REPO_ROOT}/debug/platform_${id}_debug:/node-debug
$(emane_network_yaml "${EMANE_CONTROL_IP[platform_${id}]}")
EOF
    done
    if [[ "$EMANE_ENABLED" == 1 ]]; then
        cat >> "$compose_file" <<'EOF'
networks:
  emane_ctrl:
    name: act-emane-container-baseline
    ipam:
      config:
        - subnet: 172.29.250.0/24
EOF
    fi
}

establish_nominal_rf_links() {
    [[ "$EMANE_ENABLED" == 1 ]] || return 0
    local id
    for id in $(platform_ids); do
        compose exec -T control-20 emaneevent-pathloss -i eth0 -g 224.1.2.8 -p 45702 \
            "${EMANE_NEM_ID[control_20]}:${EMANE_NEM_ID[platform_${id}]}" 0
    done
}

wait_for_emane_interfaces() {
    [[ "$EMANE_ENABLED" == 1 ]] || return 0
    local deadline=$((SECONDS + 30)) service id ready
    while (( SECONDS < deadline )); do
        ready=true
        for service in control-20; do
            compose exec -T "$service" ip -4 addr show dev emane0 2>/dev/null \
                | grep -q "${EMANE_IP[control_20]}" || ready=false
        done
        for id in $(platform_ids); do
            service="platform-${id}"
            compose exec -T "$service" ip -4 addr show dev emane0 2>/dev/null \
                | grep -q "${EMANE_IP[platform_${id}]}" || ready=false
        done
        [[ "$ready" == true ]] && return 0
        sleep 1
    done
    echo "Timed out waiting for EMANE virtual interfaces" >&2
    return 1
}

release_audit_start() {
    local deadline=$((SECONDS + 45)) id ready=true
    while (( SECONDS < deadline )); do
        ready=true
        [[ -f "$REPO_ROOT/debug/control_20_debug/simulator_ready" ]] || ready=false
        for id in $(platform_ids); do
            [[ -f "$REPO_ROOT/debug/platform_${id}_debug/simulator_ready" ]] || ready=false
        done
        local log
        for log in "$REPO_ROOT/debug/control_20_debug/logs/router.log"; do
            grep -q 'route_entities_created route=control_command' "$log" 2>/dev/null || ready=false
            grep -q 'route_entities_created route=platform_init_status' "$log" 2>/dev/null || ready=false
        done
        for id in $(platform_ids); do
            log="$REPO_ROOT/debug/platform_${id}_debug/logs/router.log"
            grep -q 'route_entities_created route=control_command' "$log" 2>/dev/null || ready=false
            grep -q 'route_entities_created route=platform_init_status' "$log" 2>/dev/null || ready=false
        done
        [[ "$ready" == true ]] && break
        sleep 1
    done
    [[ "$ready" == true ]] || { echo "Timed out waiting for routers and simulators to become audit-ready" >&2; exit 1; }
    sleep 2
    printf 'start\n' > "$REPO_ROOT/debug/control_20_debug/audit-start"
    for id in $(platform_ids); do
        printf 'start\n' > "$REPO_ROOT/debug/platform_${id}_debug/audit-start"
    done
}

up() {
    [[ "$PLATFORMS" =~ ^[0-9]+$ ]] && (( PLATFORMS >= 1 && PLATFORMS <= 70 )) || {
        echo "--platforms must be 1-70" >&2; exit 1; }
    if [[ "$WITH_DASHBOARD" == 1 ]] && ss -tln 2>/dev/null | grep -q ":${DASHBOARD_PORT} "; then
        echo "Dashboard port $DASHBOARD_PORT is already in use" >&2
        exit 1
    fi
    : "${CONNEXT_SHARED_DIR:?Set CONNEXT_SHARED_DIR to the license directory}"
    [[ -x "$REPO_ROOT/router/build/router_main" ]] || {
        echo "Build router/build/router_main before launching the container baseline" >&2; exit 1; }
    ensure_no_active_baseline
    rm -rf "$WORKDIR"
    mkdir -p "$WORKDIR" "$REPO_ROOT/debug/control_20_debug" "$REPO_ROOT/debug/test_controller_debug"
    printf 'container-%(%Y%m%dT%H%M%S)T-%s\n' -1 "$$" > "$WORKDIR/run_id"
    clear_debug_dir "$REPO_ROOT/debug/control_20_debug"
    clear_debug_dir "$REPO_ROOT/debug/test_controller_debug"
    cp "$WORKDIR/run_id" "$REPO_ROOT/debug/control_20_debug/run_id"
    printf '{"schema_version":1,"run_id":"%s","test_id":"%s","nodes":["control_20"' \
        "$(cat "$WORKDIR/run_id")" "$TEST_ID" > "$REPO_ROOT/debug/test_controller_debug/manifest.json"
    render_control_config "$WORKDIR/control-platform-20.yaml"
    local id
    for id in $(platform_ids); do
        mkdir -p "$REPO_ROOT/debug/platform_${id}_debug"
        clear_debug_dir "$REPO_ROOT/debug/platform_${id}_debug"
        cp "$WORKDIR/run_id" "$REPO_ROOT/debug/platform_${id}_debug/run_id"
        render_config "$id" "$WORKDIR/control-platform-${id}.yaml"
        printf ',"platform_%s"' "$id" >> "$REPO_ROOT/debug/test_controller_debug/manifest.json"
    done
    printf '],"topics":{"ControlCommand":{"priority":"high","minimum_sent":3,"completion_threshold_percent":100},"PlatformInitStatus":{"priority":"normal","minimum_sent":3,"completion_threshold_percent":100}}}\n' \
        >> "$REPO_ROOT/debug/test_controller_debug/manifest.json"
    python3 "$V2_ROOT/scripts/compile_delivery_expectations.py" \
        --manifest "$REPO_ROOT/debug/test_controller_debug/manifest.json" \
        --output "$REPO_ROOT/debug/test_controller_debug/expectations.json"
    write_compose
    compose up -d --remove-orphans
    trap 'compose down --remove-orphans >/dev/null 2>&1 || true' ERR
    wait_for_emane_interfaces
    establish_nominal_rf_links
    release_audit_start
    trap - ERR
    compose ps
}

render() {
    [[ "$PLATFORMS" =~ ^[0-9]+$ ]] && (( PLATFORMS >= 1 && PLATFORMS <= 70 )) || {
        echo "--platforms must be 1-70" >&2; exit 1; }
    : "${CONNEXT_SHARED_DIR:?Set CONNEXT_SHARED_DIR to the license directory}"
    rm -rf "$WORKDIR"
    mkdir -p "$WORKDIR"
    render_control_config "$WORKDIR/control-platform-20.yaml"
    local id
    for id in $(platform_ids); do
        render_config "$id" "$WORKDIR/control-platform-${id}.yaml"
    done
    write_compose
    echo "$WORKDIR/compose.yaml"
}

smoke() {
    compose ps --status running --format '{{.Service}}' | sort > "$WORKDIR/running-services.txt"
    local expected=$((PLATFORMS + 1)) actual
    actual=$(wc -l < "$WORKDIR/running-services.txt")
    [[ "$actual" -eq "$expected" ]] || {
        compose ps >&2; echo "Expected $expected running nodes, found $actual" >&2; exit 1; }
    grep -qx 'control-20' "$WORKDIR/running-services.txt"
    local id
    for id in $(platform_ids); do
        grep -qx "platform-${id}" "$WORKDIR/running-services.txt"
        [[ -s "$REPO_ROOT/debug/platform_${id}_debug/logs/router.log" ]] || {
            echo "platform-${id} router log is missing" >&2; exit 1; }
    done
    [[ -s "$REPO_ROOT/debug/control_20_debug/logs/router.log" ]] || {
        echo "control router log is missing" >&2; exit 1; }
    if [[ "$WITH_DASHBOARD" == 1 ]]; then
        compose exec -T control-20 python3 -c \
            'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/api/mesh_status", timeout=3)'
    fi
    echo "container baseline smoke: PASS ($expected nodes running)"
}

wait_for_audit_samples() {
    local deadline=$((SECONDS + 15)) topic minimum count id
    local event_paths=("$REPO_ROOT/debug/control_20_debug/events.jsonl")
    for id in $(platform_ids); do
        event_paths+=("$REPO_ROOT/debug/platform_${id}_debug/events.jsonl")
    done
    while (( SECONDS < deadline )); do
        local ready=true
        while IFS=$'\t' read -r topic minimum; do
            count=$(grep -h -E "\"event\": \"sent\".*\"topic\": \"$topic\"" \
                "${event_paths[@]}" 2>/dev/null | wc -l)
            (( count >= minimum )) || ready=false
        done < <(python3 - "$REPO_ROOT/debug/test_controller_debug/manifest.json" <<'PY'
import json
import sys

for topic, requirement in json.load(open(sys.argv[1], encoding="utf-8"))["topics"].items():
    print(f"{topic}\t{requirement['minimum_sent']}")
PY
)
        [[ "$ready" == true ]] && return 0
        sleep 1
    done
    echo "Timed out waiting for manifest-declared audit sample minima" >&2
    return 1
}

audit() {
    [[ -f "$WORKDIR/run_id" ]] || { echo "No run identity at $WORKDIR/run_id" >&2; exit 1; }
    wait_for_audit_samples
    python3 "$V2_ROOT/scripts/delivery_audit_report.py" --debug-root "$REPO_ROOT/debug" \
        --run-id "$(cat "$WORKDIR/run_id")" --test-id "$TEST_ID" \
        --manifest "$REPO_ROOT/debug/test_controller_debug/manifest.json" \
        --expectations "$REPO_ROOT/debug/test_controller_debug/expectations.json"
}

isolation() {
    [[ "$EMANE_ENABLED" == 1 ]] || {
        echo "The isolation capture requires --emane" >&2
        exit 1
    }

    local service debug_dir capture output id
    for service in control-20; do
        debug_dir="$REPO_ROOT/debug/control_20_debug"
        capture="$debug_dir/emane0-isolation.pcap"
        compose exec -T "$service" dumpcap -i emane0 -a duration:6 -w /tmp/emane0-isolation.pcap
        compose exec -T "$service" cp /tmp/emane0-isolation.pcap /node-debug/emane0-isolation.pcap
        output=$(compose exec -T "$service" tshark -n -r /tmp/emane0-isolation.pcap \
            -Y 'rtps && rtps.domain_id' -T fields -e rtps.domain_id 2>/dev/null | sort -un)
        [[ "$output" == '200' ]] || {
            echo "$service capture contained RTPS domains other than WAN domain 200: $output" >&2
            exit 1
        }
        printf '%s\n' "$output" > "$debug_dir/emane0-isolation-domains.txt"
    done
    for id in $(platform_ids); do
        service="platform-${id}"
        debug_dir="$REPO_ROOT/debug/platform_${id}_debug"
        capture="$debug_dir/emane0-isolation.pcap"
        compose exec -T "$service" dumpcap -i emane0 -a duration:6 -w /tmp/emane0-isolation.pcap
        compose exec -T "$service" cp /tmp/emane0-isolation.pcap /node-debug/emane0-isolation.pcap
        output=$(compose exec -T "$service" tshark -n -r /tmp/emane0-isolation.pcap \
            -Y 'rtps && rtps.domain_id' -T fields -e rtps.domain_id 2>/dev/null | sort -un)
        [[ "$output" == '200' ]] || {
            echo "$service capture contained RTPS domains other than WAN domain 200: $output" >&2
            exit 1
        }
        printf '%s\n' "$output" > "$debug_dir/emane0-isolation-domains.txt"
    done
    echo "EMANE interface isolation: PASS (WAN domain 200 only)"
}

down() {
    [[ -f "$WORKDIR/compose.yaml" ]] || { echo "No generated compose file at $WORKDIR" >&2; exit 1; }
    compose down --remove-orphans
}

case "$ACTION" in
    up) up ;;
    render) render ;;
    smoke) smoke ;;
    audit) audit ;;
    isolation) isolation ;;
    down) down ;;
    *) echo "Usage: $0 {up|render|smoke|audit|isolation|down} [--platforms N] [--workdir DIR] [--test-id ID] [--verbosity 0-3] [--emane]" >&2; exit 1 ;;
esac