#!/bin/bash
set -euo pipefail

: "${NODE_ROLE:?NODE_ROLE is required (control or platform)}"
: "${NODE_ID:?NODE_ID is required}"
: "${NODE_NAME:?NODE_NAME is required}"
: "${NODE_DEBUG_DIR:?NODE_DEBUG_DIR is required}"

WORKSPACE="${ACT_WORKSPACE:-/workspace}"
ROUTER_BINARY="${WORKSPACE}/router/build/router_main"
CONFIG_PATH="${NODE_CONFIG_PATH:-/run/node-config.yaml}"
LOG_DIR="${NODE_DEBUG_DIR}/logs"
PIDS=()
RUN_ID="${ACT_RUN_ID:-}"
export PYTHONUNBUFFERED=1

if [[ -z "$RUN_ID" && -f "$NODE_DEBUG_DIR/run_id" ]]; then
    RUN_ID="$(<"$NODE_DEBUG_DIR/run_id")"
fi

mkdir -p "$LOG_DIR"

start_emane() {
        : "${EMANE_NEM_ID:?EMANE_NEM_ID is required when EMANE_ENABLED=1}"
        : "${EMANE_IP:?EMANE_IP is required when EMANE_ENABLED=1}"
        local config_dir=/tmp/emane
        mkdir -p "$config_dir"

        cat > "$config_dir/platform.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE platform SYSTEM "file:///usr/share/emane/dtd/platform.dtd">
<platform name="${NODE_NAME}-emane">
    <param name="controlportendpoint" value="0.0.0.0:47000"/>
    <param name="eventservicedevice" value="eth0"/>
    <param name="eventservicegroup" value="224.1.2.8:45702"/>
    <param name="otamanagerdevice" value="eth0"/>
    <param name="otamanagergroup" value="224.1.2.8:45703"/>
    <nem id="$EMANE_NEM_ID" definition="nem.xml"/>
</platform>
EOF
        cat > "$config_dir/nem.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE nem SYSTEM "file:///usr/share/emane/dtd/nem.dtd">
<nem name="${NODE_NAME} RF Pipe NEM">
    <transport definition="transvirtual.xml"/>
    <shim definition="commeffectshim.xml"/>
    <mac definition="rfpipemac.xml"/>
    <phy>
        <param name="fixedantennagain" value="0.0"/>
        <param name="fixedantennagainenable" value="on"/>
        <param name="bandwidth" value="1M"/>
        <param name="noisemode" value="outofband"/>
        <param name="propagationmodel" value="precomputed"/>
        <param name="systemnoisefigure" value="4.0"/>
        <param name="subid" value="2"/>
        <param name="txpower" value="0.0"/>
    </phy>
</nem>
EOF
        cat > "$config_dir/transvirtual.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE transport SYSTEM "file:///usr/share/emane/dtd/transport.dtd">
<transport name="${NODE_NAME} virtual transport" library="transvirtual">
    <param name="bitrate" value="1M"/>
    <param name="devicepath" value="/dev/net/tun"/>
    <param name="device" value="emane0"/>
    <param name="address" value="$EMANE_IP"/>
    <param name="mask" value="255.255.255.0"/>
</transport>
EOF
    cat > "$config_dir/commeffectshim.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE shim SYSTEM "file:///usr/share/emane/dtd/shim.dtd">
<shim name="CommEffect shim" library="commeffectshim">
    <param name="defaultconnectivitymode" value="on"/>
    <param name="enablepromiscuousmode" value="off"/>
</shim>
EOF
        cat > "$config_dir/rfpipemac.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mac SYSTEM "file:///usr/share/emane/dtd/mac.dtd">
<mac name="RF Pipe MAC" library="rfpipemaclayer">
    <param name="enablepromiscuousmode" value="off"/>
    <param name="datarate" value="1M"/>
    <param name="jitter" value="0"/>
    <param name="delay" value="0"/>
    <param name="flowcontrolenable" value="off"/>
    <param name="flowcontroltokens" value="10"/>
    <param name="pcrcurveuri" value="file:///usr/share/emane/xml/models/mac/rfpipe/rfpipepcr.xml"/>
</mac>
EOF
        emane -l 3 "$config_dir/platform.xml" > "$LOG_DIR/emane.log" 2>&1 &
        PIDS+=("$!")
        for _ in {1..30}; do
                ip -4 addr show dev emane0 2>/dev/null | grep -q "$EMANE_IP" && return 0
                sleep 1
        done
        echo "EMANE did not create emane0 with $EMANE_IP" >&2
        return 1
}

if [[ ! -x "$ROUTER_BINARY" ]]; then
    echo "router_main is not executable at $ROUTER_BINARY; build router/build first" >&2
    exit 1
fi

cd "$WORKSPACE"

if [[ "${EMANE_ENABLED:-0}" == "1" ]]; then
    start_emane
fi

stop_children() {
    local pid
    for pid in "${PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait || true
}
trap stop_children TERM INT EXIT

bash -c 'exec -a "$0" "$@"' "${NODE_NAME}-router-main" \
    "$ROUTER_BINARY" --config "$CONFIG_PATH" --role "$NODE_ROLE" \
    --node-name "$NODE_NAME" \
    --admin-participant "${NODE_ROLE}_lan" > "$LOG_DIR/router.log" 2>&1 &
PIDS+=("$!")

ACT_PARTICIPANT_NAME="${NODE_NAME}/router_journal_subscriber" \
ACT_PARTICIPANT_ROLE="act.journal_subscriber" \
    bash -c 'exec -a "$0" python3 "$@"' "${NODE_NAME}-journal-subscriber" \
    "$WORKSPACE/debug/scripts/router_journal_subscriber.py" \
    --domain "$NODE_ID" --node-name "$NODE_NAME" \
    --participant-name "${NODE_NAME}/router_journal_subscriber" \
    --participant-role "act.journal_subscriber" \
    --output "$NODE_DEBUG_DIR/journal.jsonl" \
    > "$LOG_DIR/journal_subscriber.log" 2>&1 &
PIDS+=("$!")

if [[ "$NODE_ROLE" == "control" ]]; then
    ACT_PROCESS_NAME="${NODE_NAME}-control-sim" \
    ACT_PARTICIPANT_NAME="${NODE_NAME}/control_sim" \
    ACT_PARTICIPANT_ROLE="act.control_sim" \
        bash "$WORKSPACE/harness_v2/scripts/start_control_sim.sh" --id "$NODE_ID" \
        --destination "${SIM_DESTINATION:-Platform_30}" --verbosity "${SIM_VERBOSITY:-1}" \
        --run-id "$RUN_ID" \
        --audit-start-file "$NODE_DEBUG_DIR/audit-start" \
        > "$LOG_DIR/simulator.log" 2>&1 &
    PIDS+=("$!")
else
    : "${NODE_ROUTER_NAME:?NODE_ROUTER_NAME is required for platform nodes}"
    ACT_PROCESS_NAME="${NODE_NAME}-platform-sim" \
    ACT_PARTICIPANT_NAME="${NODE_NAME}/platform_sim" \
    ACT_PARTICIPANT_ROLE="act.platform_sim" \
        bash "$WORKSPACE/harness_v2/scripts/start_platform_sim.sh" --id "$NODE_ID" \
        --destination "${SIM_DESTINATION:-Control_20}" --verbosity "${SIM_VERBOSITY:-1}" \
        --run-id "$RUN_ID" \
        --audit-start-file "$NODE_DEBUG_DIR/audit-start" \
        > "$LOG_DIR/simulator.log" 2>&1 &
    PIDS+=("$!")

    NDDS_QOS_PROFILES="${WORKSPACE}/harness_v2/qos/act_qos_profiles.xml;${WORKSPACE}/harness_v2/datamodel/gen/ActTypes.xml" \
    ACT_PARTICIPANT_NAME="${NODE_NAME}/platform_mesh_control" \
    ACT_PARTICIPANT_ROLE="act.platform_mesh_control" \
        bash -c 'exec -a "$0" python3 "$@"' "${NODE_NAME}-mesh-control" \
        "$WORKSPACE/harness_v2/scripts/platform_mesh_control.py" \
        --domain "$NODE_ID" --node "$NODE_NAME" --router-name "$NODE_ROUTER_NAME" \
        > "$LOG_DIR/mesh_control.log" 2>&1 &
    PIDS+=("$!")
fi

if [[ "${MESH_DASHBOARD:-0}" == "1" ]]; then
    ACT_PARTICIPANT_NAME="${NODE_NAME}/mesh_dashboard_bridge" \
    ACT_PARTICIPANT_ROLE="act.mesh_dashboard_bridge" \
        bash -c 'exec -a "$0" python3 "$@"' "${NODE_NAME}-mesh-dashboard" \
        "$WORKSPACE/gui/mesh_dashboard/server/mesh_bridge.py" --domain 20 \
        --port "${MESH_DASHBOARD_PORT:-8080}" \
        --participant-qos-profile ACT_QOS_LIB::lan_control_participant \
        --traffic-observers "${MESH_TRAFFIC_OBSERVERS:-}" \
        > "$LOG_DIR/mesh_dashboard.log" 2>&1 &
    PIDS+=("$!")
fi

if [[ "${EMANE_TRAFFIC_MONITOR:-0}" == "1" ]]; then
    python3 "$WORKSPACE/debug/scripts/domain_traffic_monitor.py" --domains 200 \
        --interface emane0 --observer "$NODE_NAME" \
        --emane-nem-id "${EMANE_NEM_ID:?EMANE_NEM_ID is required for EMANE monitoring}" \
        --dashboard-url "${MESH_DASHBOARD_URL:?MESH_DASHBOARD_URL is required}" \
        --interval "${EMANE_TRAFFIC_MONITOR_INTERVAL:-1}" \
        --output "$NODE_DEBUG_DIR/traffic_stats.jsonl" \
        > "$LOG_DIR/traffic_monitor.log" 2>&1 &
    PIDS+=("$!")
fi

wait -n "${PIDS[@]}"
status=$?
echo "node process exited with status $status" >&2
exit "$status"