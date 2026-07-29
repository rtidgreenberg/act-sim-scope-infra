#!/usr/bin/env bash
# run_spike.sh — orchestrator for the wis_mesh_dashboard spike (see PLAN.md, README.md).
#
# Proves whether a real router_main mesh's RouterHealth heartbeats show up over RTI Web
# Integration Service's REST and WebSocket APIs. Safely re-runnable: all state (generated
# types XML, spliced WIS config, logs, PIDs) lives in a fresh mktemp -d each run; nothing
# persists between runs except the log directory (left behind on purpose for post-mortem).
#
# Filesystem-safety rule (repo CLAUDE.md / .github/copilot-instructions.md): this repo's
# working tree may be on a vboxsf share, which is unsafe for runtime artifacts (SQLite/DWH,
# FIFOs, logs). All runtime output goes under a local /tmp dir, never spikes/wis_mesh_dashboard/.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SPIKE_DIR="$REPO_ROOT/spikes/wis_mesh_dashboard"
export NDDSHOME="${NDDSHOME:-/home/rti/rti_connext_dds-7.7.0}"
RTIDDSGEN="$NDDSHOME/bin/rtiddsgen"
WIS_BIN="$NDDSHOME/bin/rtiwebintegrationservice"
ROUTER_MAIN="$REPO_ROOT/router/build/router_main"
CONFIG_YAML="$SPIKE_DIR/config/e2e_presence_fixed_domains.yaml"
WIS_TEMPLATE="$SPIKE_DIR/config/wis_config.xml.template"

WIS_PORT=8080
WIS_APP="MeshDashboardApp"
WIS_PARTICIPANT="MeshParticipant"
WIS_SUBSCRIBER="MeshSubscriber"
WIS_READER="RouterHealthReader"
WS_CONN_NAME="MeshWsConn"

READER_URI="/dds/rest1/applications/${WIS_APP}/domain_participants/${WIS_PARTICIPANT}/subscribers/${WIS_SUBSCRIBER}/data_readers/${WIS_READER}"
REST_URL="http://localhost:${WIS_PORT}${READER_URI}"
WS_PATH="/dds/websocket/${WS_CONN_NAME}"

TMPDIR_SPIKE="$(mktemp -d /tmp/wis_mesh_dashboard.XXXXXX)"
echo "[run_spike] local tmp working dir: $TMPDIR_SPIKE"

TYPES_XML="$TMPDIR_SPIKE/ActTypes.xml"
WIS_CONFIG="$TMPDIR_SPIKE/wis_config.xml"
CONTROL_LOG="$TMPDIR_SPIKE/router_control.log"
PLATFORM_LOG="$TMPDIR_SPIKE/router_platform.log"
WIS_LOG="$TMPDIR_SPIKE/wis.log"

PLATFORM_PID=""
CONTROL_PID=""
WIS_PID=""

PASS_XML=0
PASS_WIS_START=0
PASS_REST=0
PASS_WS=0
WS_NOTE=""

cleanup() {
    echo "[run_spike] --- cleanup ---"
    # rtiwebintegrationservice is a /bin/sh WRAPPER that execs a child,
    # rtiwebintegrationserviceapp, which is the actual process holding the DDS
    # participant and the listening socket. Killing only the wrapper PID leaves the
    # real server (and port 8080) alive -- verified the hard way while developing this
    # script (a "killed" WIS kept answering curl afterward). Also: `pkill -x` against
    # either name does NOT reliably match here -- Linux truncates the `comm` field to 15
    # bytes, and both "rtiwebintegrationservice" (24 chars) and
    # "rtiwebintegrationserviceapp" (27 chars) exceed that, so exact-name pkill silently
    # matches nothing. Explicit PIDs (of both the wrapper and its child) are the only
    # reliable way to kill this, consistent with the repo's "-x or explicit PIDs, never
    # pkill -f" rule.
    if [[ -n "$WIS_PID" ]]; then
        WIS_CHILD_PID="$(pgrep -P "$WIS_PID" 2>/dev/null | head -n1)"
        if [[ -n "$WIS_CHILD_PID" ]] && kill -0 "$WIS_CHILD_PID" 2>/dev/null; then
            echo "[run_spike] killing rtiwebintegrationserviceapp (pid $WIS_CHILD_PID, child of wrapper $WIS_PID)"
            kill -9 "$WIS_CHILD_PID" 2>/dev/null
            wait "$WIS_CHILD_PID" 2>/dev/null
        fi
        if kill -0 "$WIS_PID" 2>/dev/null; then
            echo "[run_spike] killing WIS wrapper (pid $WIS_PID)"
            kill -9 "$WIS_PID" 2>/dev/null
            wait "$WIS_PID" 2>/dev/null
        fi
    fi
    # Explicit PIDs, never pkill -f (repo rule: -f can match this very shell script).
    if [[ -n "$CONTROL_PID" ]] && kill -0 "$CONTROL_PID" 2>/dev/null; then
        echo "[run_spike] killing router_main control (pid $CONTROL_PID)"
        kill "$CONTROL_PID" 2>/dev/null
        wait "$CONTROL_PID" 2>/dev/null
    fi
    if [[ -n "$PLATFORM_PID" ]] && kill -0 "$PLATFORM_PID" 2>/dev/null; then
        echo "[run_spike] killing router_main platform (pid $PLATFORM_PID)"
        kill "$PLATFORM_PID" 2>/dev/null
        wait "$PLATFORM_PID" 2>/dev/null
    fi

    STRAY_SHM="$(ls /dev/shm 2>/dev/null | grep -Ei '^(rti|dds)' || true)"
    if [[ -n "$STRAY_SHM" ]]; then
        echo "[run_spike] WARNING: stray /dev/shm segments found: $STRAY_SHM"
    else
        echo "[run_spike] /dev/shm clean (no stray RTI*/dds* segments)"
    fi

    echo ""
    echo "[run_spike] ==== RESULTS ===="
    echo "[run_spike] XML validates + WIS config spliced: $([[ $PASS_XML -eq 1 ]] && echo PASS || echo FAIL)"
    echo "[run_spike] WIS starts against it:              $([[ $PASS_WIS_START -eq 1 ]] && echo PASS || echo FAIL)"
    echo "[run_spike] REST returns real RouterHealth data: $([[ $PASS_REST -eq 1 ]] && echo PASS || echo FAIL)"
    echo "[run_spike] WebSocket push arrives:              $([[ $PASS_WS -eq 1 ]] && echo PASS || echo "FAIL${WS_NOTE:+ ($WS_NOTE)}")"
    echo "[run_spike] REST URL:  $REST_URL"
    echo "[run_spike] WS URL:    ws://localhost:${WIS_PORT}${WS_PATH}"
    echo "[run_spike] logs + generated files: $TMPDIR_SPIKE"
}
trap cleanup EXIT

# --- Step 1: rtiddsgen -convertToXml (into the LOCAL tmp dir, never the share) ---
echo "[run_spike] generating ActTypes.xml via rtiddsgen -convertToXml..."
if ! "$RTIDDSGEN" -convertToXml -d "$TMPDIR_SPIKE" \
        "$REPO_ROOT/harness_v2/datamodel/ActTypes.idl" \
        > "$TMPDIR_SPIKE/rtiddsgen.log" 2>&1; then
    echo "[run_spike] FAIL: rtiddsgen -convertToXml failed; see $TMPDIR_SPIKE/rtiddsgen.log"
    exit 1
fi
if [[ ! -f "$TYPES_XML" ]]; then
    echo "[run_spike] FAIL: rtiddsgen did not produce $TYPES_XML"
    exit 1
fi

# --- Step 2: splice <types>...</types> into the WIS config template ---
if ! python3 - "$TYPES_XML" "$WIS_TEMPLATE" "$WIS_CONFIG" <<'PYEOF'
import re
import sys
import xml.etree.ElementTree as ET

types_xml_path, template_path, out_path = sys.argv[1:4]
types_xml = open(types_xml_path).read()
m = re.search(r"<types>.*?</types>", types_xml, re.S)
if not m:
    print("no <types>...</types> block found in generated XML", file=sys.stderr)
    sys.exit(1)

template = open(template_path).read()
placeholder = "__ROUTER_ADMIN_TYPES__"
count = template.count(placeholder)
if count != 1:
    print(f"expected exactly one {placeholder} placeholder, found {count}", file=sys.stderr)
    sys.exit(1)

spliced = template.replace(placeholder, m.group(0))
open(out_path, "w").write(spliced)

# Basic well-formedness check (not full XSD validation -- WIS's own startup is the
# schema-validity arbiter per the repo's "build/run is the arbiter" rule; this just
# catches gross XML mistakes before spending time launching WIS).
try:
    ET.fromstring(spliced)
except ET.ParseError as e:
    print(f"spliced config is not well-formed XML: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
then
    echo "[run_spike] FAIL: splice step failed"
    exit 1
fi
echo "[run_spike] types XML generated + spliced -> $WIS_CONFIG (well-formed)"
PASS_XML=1

# --- Step 3: start the two router_main processes (cwd = repo root, D50/README.md) ---
echo "[run_spike] starting router_main (platform role)..."
(
    cd "$REPO_ROOT" && exec "$ROUTER_MAIN" \
        --config "$CONFIG_YAML" \
        --role platform \
        --admin-participant platform_lan
) > "$PLATFORM_LOG" 2>&1 &
PLATFORM_PID=$!

echo "[run_spike] starting router_main (control role)..."
(
    cd "$REPO_ROOT" && exec "$ROUTER_MAIN" \
        --config "$CONFIG_YAML" \
        --role control \
        --node-name Control_20 \
        --name e2e-control-role \
        --admin-participant control_lan
) > "$CONTROL_LOG" 2>&1 &
CONTROL_PID=$!

sleep 0.5
if ! kill -0 "$PLATFORM_PID" 2>/dev/null; then
    echo "[run_spike] FAIL: router_main (platform) exited immediately; see $PLATFORM_LOG"
    tail -n 40 "$PLATFORM_LOG"
    exit 1
fi
if ! kill -0 "$CONTROL_PID" 2>/dev/null; then
    echo "[run_spike] FAIL: router_main (control) exited immediately; see $CONTROL_LOG"
    tail -n 40 "$CONTROL_LOG"
    exit 1
fi

echo "[run_spike] waiting ~5s for discovery + a couple of RouterHealth heartbeats (1s period)..."
sleep 5

# --- Step 4: start rtiwebintegrationservice ---
echo "[run_spike] starting rtiwebintegrationservice..."
(
    cd "$TMPDIR_SPIKE" && exec "$WIS_BIN" \
        -cfgFile "$WIS_CONFIG" \
        -cfgName mesh_dashboard \
        -listeningPorts "$WIS_PORT" \
        -enableWebSockets
) > "$WIS_LOG" 2>&1 &
WIS_PID=$!

echo "[run_spike] waiting for WIS to listen on :$WIS_PORT..."
WIS_UP=0
for _ in $(seq 1 20); do
    if ! kill -0 "$WIS_PID" 2>/dev/null; then
        echo "[run_spike] FAIL: rtiwebintegrationservice exited during startup; see $WIS_LOG"
        tail -n 60 "$WIS_LOG"
        break
    fi
    CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${WIS_PORT}/" --max-time 1 || true)"
    if [[ -n "$CODE" && "$CODE" != "000" ]]; then
        WIS_UP=1
        break
    fi
    sleep 1
done

if [[ "$WIS_UP" -ne 1 ]]; then
    echo "[run_spike] FAIL: WIS never came up listening on :$WIS_PORT; see $WIS_LOG"
else
    echo "[run_spike] WIS is listening on :$WIS_PORT"
    PASS_WIS_START=1
fi

# --- Step 5: REST GET the RouterHealth reader ---
if [[ "$PASS_WIS_START" -eq 1 ]]; then
    echo "[run_spike] polling REST endpoint: $REST_URL"
    REST_OK=0
    for attempt in $(seq 1 6); do
        BODY="$(curl -s --max-time 3 "${REST_URL}?sampleFormat=json&removeFromReaderCache=false")"
        echo "[run_spike] REST attempt $attempt raw response:" >> "$TMPDIR_SPIKE/rest_responses.log"
        echo "$BODY" >> "$TMPDIR_SPIKE/rest_responses.log"
        RESULT="$(python3 - "$BODY" <<'PYEOF'
import json
import re
import sys

body = sys.argv[1]
try:
    samples = json.loads(body)
except json.JSONDecodeError:
    print("PARSE_ERROR")
    sys.exit(0)

if not isinstance(samples, list):
    print("NOT_A_LIST")
    sys.exit(0)

for s in samples:
    data = s.get("data") if isinstance(s, dict) else None
    if not data:
        continue
    router = data.get("router", "")
    peers = data.get("peers_seen", [])
    if re.match(r"^[^/]+/[^/]+$", router):
        print(f"FOUND router={router} n_peers={len(peers)}")
        sys.exit(0)

print(f"NO_MATCH n_samples={len(samples)}")
PYEOF
)"
        echo "[run_spike] REST attempt $attempt: $RESULT"
        if [[ "$RESULT" == FOUND* ]]; then
            REST_OK=1
            echo "[run_spike] PASS: $RESULT"
            break
        fi
        sleep 1
    done
    if [[ "$REST_OK" -eq 1 ]]; then
        PASS_REST=1
    else
        echo "[run_spike] FAIL: REST never returned a real RouterHealth sample; see $TMPDIR_SPIKE/rest_responses.log"
    fi
else
    echo "[run_spike] SKIP: REST check skipped (WIS did not start)"
fi

# --- Step 6: WebSocket bind + push check ---
if [[ "$PASS_WIS_START" -eq 1 ]]; then
    echo "[run_spike] creating named WebSocket connection '$WS_CONN_NAME' via REST..."
    WS_CREATE_CODE="$(curl -s -o "$TMPDIR_SPIKE/ws_create_response.log" -w '%{http_code}' \
        --max-time 3 \
        -X POST "http://localhost:${WIS_PORT}/dds/v1/websocket_connections" \
        -H "Content-Type: application/dds-web+json" \
        -d "[{\"name\": \"${WS_CONN_NAME}\"}]")"
    echo "[run_spike] websocket_connections POST -> HTTP $WS_CREATE_CODE (body: $TMPDIR_SPIKE/ws_create_response.log)"

    if [[ "$WS_CREATE_CODE" != "204" ]]; then
        echo "[run_spike] FAIL: websocket_connections creation did not return 204 (got $WS_CREATE_CODE)"
        WS_NOTE="connection creation returned HTTP $WS_CREATE_CODE, see $TMPDIR_SPIKE/ws_create_response.log"
    else
        echo "[run_spike] binding WebSocket to $READER_URI and waiting up to 8s for a push..."
        if python3 "$SPIKE_DIR/ws_probe.py" \
                --host localhost --port "$WIS_PORT" \
                --ws-path "$WS_PATH" \
                --reader-uri "$READER_URI" \
                --bind-id "$WIS_READER" \
                --timeout 8 > "$TMPDIR_SPIKE/ws_probe.log" 2>&1; then
            echo "[run_spike] PASS: WebSocket push arrived"
            cat "$TMPDIR_SPIKE/ws_probe.log"
            PASS_WS=1
        else
            echo "[run_spike] FAIL/INCONCLUSIVE: no WebSocket push observed; see $TMPDIR_SPIKE/ws_probe.log"
            cat "$TMPDIR_SPIKE/ws_probe.log"
            WS_NOTE="see $TMPDIR_SPIKE/ws_probe.log"
        fi
    fi
else
    echo "[run_spike] SKIP: WebSocket check skipped (WIS did not start)"
    WS_NOTE="skipped, WIS did not start"
fi

# cleanup + results summary run via the EXIT trap
if [[ "$PASS_XML" -eq 1 && "$PASS_WIS_START" -eq 1 && "$PASS_REST" -eq 1 ]]; then
    exit 0
else
    exit 1
fi
