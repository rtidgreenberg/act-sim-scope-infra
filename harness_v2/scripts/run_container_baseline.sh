#!/bin/bash
# Launch a minimal Docker-bridge baseline: one control node plus N platform nodes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V2_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$V2_ROOT/.." && pwd)"
ACTION="${1:-}"
[[ $# -gt 0 ]] && shift
PLATFORMS=2
WORKDIR=/tmp/act_container_baseline
VERBOSITY=1
TEST_ID=container-baseline

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platforms) PLATFORMS="$2"; shift 2 ;;
        --workdir) WORKDIR="$2"; shift 2 ;;
        --verbosity) VERBOSITY="$2"; shift 2 ;;
        --test-id) TEST_ID="$2"; shift 2 ;;
        --help) sed -n '2,11p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

compose() {
    docker compose -p act-container-baseline -f "$WORKDIR/compose.yaml" "$@"
}

platform_ids() {
    seq 30 $((29 + PLATFORMS))
}

ensure_no_active_baseline() {
    if docker ps -q --filter label=com.docker.compose.project=act-container-baseline | grep -q .; then
        echo "Error: an act-container-baseline run is active; run its owned down action first" >&2
        exit 1
    fi
}

render_config() {
    local id="$1" output="$2"
    sed -e "s/Platform_30/Platform_${id}/g" \
        -e "s/platform-30-control-platform/platform-${id}-control-platform/g" \
        -e "0,/domain: 30/s//domain: ${id}/" \
        "$REPO_ROOT/router/config/control-platform.yaml" > "$output"
}

write_compose() {
    local compose_file="$WORKDIR/compose.yaml"
    cat > "$compose_file" <<EOF
services:
  control-20:
    image: connext:7.7.0
    init: true
    user: "${ACT_CONTAINER_UID:-$(id -u)}:${ACT_CONTAINER_GID:-$(id -g)}"
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
      WAN_PEER: 10@control-20
      WAN_RECEIVE_MULTICAST: "0"
    volumes:
      - ${REPO_ROOT}:/workspace:ro
      - ${CONNEXT_SHARED_DIR:?Set CONNEXT_SHARED_DIR to the license directory}:/shared:ro
      - ${WORKDIR}/control-platform-20.yaml:/run/node-config.yaml:ro
      - ${REPO_ROOT}/debug/control_20_debug:/node-debug
EOF
    local id
    for id in $(platform_ids); do
        cat >> "$compose_file" <<EOF
  platform-${id}:
    image: connext:7.7.0
    init: true
    user: "${ACT_CONTAINER_UID:-$(id -u)}:${ACT_CONTAINER_GID:-$(id -g)}"
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
      WAN_PEER: 10@control-20
      WAN_RECEIVE_MULTICAST: "0"
    volumes:
      - ${REPO_ROOT}:/workspace:ro
      - ${CONNEXT_SHARED_DIR:?Set CONNEXT_SHARED_DIR to the license directory}:/shared:ro
      - ${WORKDIR}/control-platform-${id}.yaml:/run/node-config.yaml:ro
      - ${REPO_ROOT}/debug/platform_${id}_debug:/node-debug
EOF
    done
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
    : "${CONNEXT_SHARED_DIR:?Set CONNEXT_SHARED_DIR to the license directory}"
    [[ -x "$REPO_ROOT/router/build/router_main" ]] || {
        echo "Build router/build/router_main before launching the container baseline" >&2; exit 1; }
    ensure_no_active_baseline
    rm -rf "$WORKDIR"
    mkdir -p "$WORKDIR" "$REPO_ROOT/debug/control_20_debug" "$REPO_ROOT/debug/test_controller_debug"
    printf 'container-%(%Y%m%dT%H%M%S)T-%s\n' -1 "$$" > "$WORKDIR/run_id"
    find "$REPO_ROOT/debug/control_20_debug" -mindepth 1 -delete
    find "$REPO_ROOT/debug/test_controller_debug" -mindepth 1 -delete
    cp "$WORKDIR/run_id" "$REPO_ROOT/debug/control_20_debug/run_id"
    printf '{"schema_version":1,"run_id":"%s","test_id":"%s","nodes":["control_20"' \
        "$(cat "$WORKDIR/run_id")" "$TEST_ID" > "$REPO_ROOT/debug/test_controller_debug/manifest.json"
    cp "$REPO_ROOT/router/config/control-platform.yaml" "$WORKDIR/control-platform-20.yaml"
    local id
    for id in $(platform_ids); do
        mkdir -p "$REPO_ROOT/debug/platform_${id}_debug"
        find "$REPO_ROOT/debug/platform_${id}_debug" -mindepth 1 -delete
        cp "$WORKDIR/run_id" "$REPO_ROOT/debug/platform_${id}_debug/run_id"
        render_config "$id" "$WORKDIR/control-platform-${id}.yaml"
        printf ',"platform_%s"' "$id" >> "$REPO_ROOT/debug/test_controller_debug/manifest.json"
    done
    printf '],"topics":{"ControlCommand":{"priority":"high","minimum_sent":3,"completion_threshold_percent":100},"PlatformInitStatus":{"priority":"normal","minimum_sent":3,"completion_threshold_percent":100}}}\n' \
        >> "$REPO_ROOT/debug/test_controller_debug/manifest.json"
    write_compose
    compose up -d --remove-orphans
    release_audit_start
    compose ps
}

render() {
    [[ "$PLATFORMS" =~ ^[0-9]+$ ]] && (( PLATFORMS >= 1 && PLATFORMS <= 70 )) || {
        echo "--platforms must be 1-70" >&2; exit 1; }
    : "${CONNEXT_SHARED_DIR:?Set CONNEXT_SHARED_DIR to the license directory}"
    rm -rf "$WORKDIR"
    mkdir -p "$WORKDIR"
    cp "$REPO_ROOT/router/config/control-platform.yaml" "$WORKDIR/control-platform-20.yaml"
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
    echo "container baseline smoke: PASS ($expected nodes running)"
}

audit() {
    [[ -f "$WORKDIR/run_id" ]] || { echo "No run identity at $WORKDIR/run_id" >&2; exit 1; }
    python3 "$V2_ROOT/scripts/delivery_audit_report.py" --debug-root "$REPO_ROOT/debug" \
        --run-id "$(cat "$WORKDIR/run_id")" --test-id "$TEST_ID" \
        --manifest "$REPO_ROOT/debug/test_controller_debug/manifest.json"
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
    down) down ;;
    *) echo "Usage: $0 {up|render|smoke|audit|down} [--platforms N] [--workdir DIR] [--test-id ID] [--verbosity 0-3]" >&2; exit 1 ;;
esac