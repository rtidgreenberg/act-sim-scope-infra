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

if [[ ! -x "$ROUTER_BINARY" ]]; then
    echo "router_main is not executable at $ROUTER_BINARY; build router/build first" >&2
    exit 1
fi

stop_children() {
    local pid
    for pid in "${PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait || true
}
trap stop_children TERM INT EXIT

"$ROUTER_BINARY" --config "$CONFIG_PATH" --role "$NODE_ROLE" \
    --node-name "$NODE_NAME" --name "${NODE_NAME,,}-container" \
    --admin-participant "${NODE_ROLE}_lan" > "$LOG_DIR/router.log" 2>&1 &
PIDS+=("$!")

if [[ "$NODE_ROLE" == "control" ]]; then
    bash "$WORKSPACE/harness_v2/scripts/start_control_sim.sh" --id "$NODE_ID" \
        --destination "${SIM_DESTINATION:-Platform_30}" --verbosity "${SIM_VERBOSITY:-1}" \
        --run-id "$RUN_ID" \
        --audit-start-file "$NODE_DEBUG_DIR/audit-start" \
        > "$LOG_DIR/simulator.log" 2>&1 &
    PIDS+=("$!")
else
    bash "$WORKSPACE/harness_v2/scripts/start_platform_sim.sh" --id "$NODE_ID" \
        --destination "${SIM_DESTINATION:-Control_20}" --verbosity "${SIM_VERBOSITY:-1}" \
        --run-id "$RUN_ID" \
        --audit-start-file "$NODE_DEBUG_DIR/audit-start" \
        > "$LOG_DIR/simulator.log" 2>&1 &
    PIDS+=("$!")

    NDDS_QOS_PROFILES="${WORKSPACE}/harness_v2/qos/act_qos_profiles.xml;${WORKSPACE}/harness_v2/datamodel/gen/ActTypes.xml" \
        python3 "$WORKSPACE/harness_v2/scripts/platform_mesh_control.py" \
        --domain "$NODE_ID" --node "$NODE_NAME" > "$LOG_DIR/mesh_control.log" 2>&1 &
    PIDS+=("$!")
fi

wait -n "${PIDS[@]}"
status=$?
echo "node process exited with status $status" >&2
exit "$status"