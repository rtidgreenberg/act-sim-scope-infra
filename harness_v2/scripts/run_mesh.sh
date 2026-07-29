#!/bin/bash
# run_mesh.sh — launch/tear down a control node + N platform routers (control-platform.yaml,
# per-platform domain-substituted per node.README/run-mesh-dashboard skill's step 5c) plus a
# real platform_sim.py per platform (harness_v2/scripts/start_platform_sim.sh), for exercising
# the mesh dashboard / router mesh against more than the 1-platform default.
#
# Usage:
#   ./run_mesh.sh up --platforms <N> [--workdir <dir>] [--verbosity <0-3>] \
#                    [--with-dashboard] [--dashboard-port <port>]
#   ./run_mesh.sh down [--workdir <dir>]
#
# Both `up` and `down` default to a fixed WORKDIR (/tmp/act_mesh_run) -- `up` deletes and
# recreates it each time, so `down` (with no args) always knows where to look. Override
# with --workdir <dir> if you need a custom path.
#
# --with-dashboard also generates the WIS config (gui/mesh_dashboard/config/
# generate_wis_config.sh) and launches rtiwebintegrationservice serving the mesh dashboard
# on --dashboard-port (default 8080, http://localhost:<port>/). Its PIDs -- both the shell
# wrapper AND the actual app child that holds the port (rtiwebintegrationservice is a
# wrapper script; killing only it leaves the port bound, gui/mesh_dashboard/README.md) --
# are added to the same pids.txt, so a single `down` tears down routers + sims + dashboard.
#
# All runtime artifacts (logs, generated per-platform yaml) go under --workdir (default:
# /tmp/act_mesh_run), which MUST be on a local filesystem -- never point it at this repo's
# vboxsf share (repo CLAUDE.md filesystem-safety rule).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V2_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${V2_ROOT}/.." && pwd)"

ACTION="${1:-}"
[[ $# -gt 0 ]] && shift

PLATFORMS=""
WORKDIR="/tmp/act_mesh_run"
VERBOSITY=2
WITH_DASHBOARD=true
DASHBOARD_PORT=8080

while [[ $# -gt 0 ]]; do
    case $1 in
        --platforms) PLATFORMS="$2"; shift 2 ;;
        --workdir) WORKDIR="$2"; shift 2 ;;
        --verbosity) VERBOSITY="$2"; shift 2 ;;
        --with-dashboard) WITH_DASHBOARD=true; shift ;;
        --dashboard-port) DASHBOARD_PORT="$2"; shift 2 ;;
        --help) sed -n '2,26p' "$0"; exit 0 ;;
        *) echo "Error: unknown option '$1'" >&2; exit 1 ;;
    esac
done

do_up() {
    if [[ -z "$PLATFORMS" ]]; then
        echo "Error: --platforms <N> is required for 'up'" >&2; exit 1
    fi
    if [[ "$PLATFORMS" -lt 1 ]] || [[ "$PLATFORMS" -gt 70 ]]; then
        # platform id range is 30-99 (start_platform_sim.sh) -- 30 + 70 - 1 = 99
        echo "Error: --platforms must be 1-70 (got: $PLATFORMS)" >&2; exit 1
    fi

    if [[ -z "$WORKDIR" ]]; then
        echo "Error: --workdir cannot be empty" >&2; exit 1
    fi
    # Wipe and recreate -- ensures a clean slate each run.
    rm -rf "$WORKDIR"
    mkdir -p "$WORKDIR"
    # filesystem-safety guard: prove $WORKDIR is a real local fs, not the vboxsf share.
    if ! (mkfifo "$WORKDIR/.probe" 2>/dev/null && rm -f "$WORKDIR/.probe"); then
        echo "Error: $WORKDIR failed the mkfifo probe -- refusing to use a vboxsf/non-local path" >&2
        exit 1
    fi
    : > "$WORKDIR/pids.txt"

    if [[ ! -x "$REPO_ROOT/router/build/router_main" ]]; then
        echo "Error: $REPO_ROOT/router/build/router_main not found -- build it first" >&2
        exit 1
    fi

    # Fail fast, before launching anything, if --dashboard-port is already bound -- this
    # VM runs concurrent sessions, and two `up --with-dashboard` runs defaulting to the
    # same port (or a stray leftover WIS from a prior run) would otherwise let the
    # WIS_APP_PID poll further down latch onto a PID this invocation never started, which
    # `down` would later kill as if it were its own. Checked here (not just right before
    # launching WIS) so a doomed run doesn't waste time standing up the mesh first.
    if [[ "$WITH_DASHBOARD" == true ]] && ss -tln 2>/dev/null | grep -q ":${DASHBOARD_PORT} "; then
        echo "Error: --dashboard-port $DASHBOARD_PORT is already in use -- pick a" >&2
        echo "  different --dashboard-port, or find and stop whatever's using it" >&2
        echo "  ('ss -tlnp | grep :$DASHBOARD_PORT') before retrying" >&2
        exit 1
    fi

    export NDDSHOME="${NDDSHOME:-/home/rti/rti_connext_dds-7.7.0}"
    export RTI_LICENSE_FILE="${RTI_LICENSE_FILE:-$NDDSHOME/rti_license.dat}"
    # CONTROL_WAN_PEER1/PLATFORM_WAN_PEER1 seed discovery on domain 200 (platform_wan +
    # control_wan -- team_wan retired, its role folded into platform_wan, D103), which
    # every platform shares -- a bare "127.0.0.1" peer descriptor implies Connext's
    # default max participant id of 4 (probes ids 0..4 only, confirmed against RTI's
    # KB). Each platform contributes 1 domain-200 participant post-D103 (was 2 before
    # team_wan's retirement), so ids climb past 4 as --platforms grows, just half as
    # fast as before. Two platforms that BOTH land with every one of their domain-200
    # participants above id 4 go mutually invisible -- reproduced live pre-D103: a
    # 5-platform run (2 domain-200 participants/platform then) put Platform_32 (ids
    # 9,10) and Platform_33 (ids 5,6) outside the window and they never discovered each
    # other, while every other pair (each having at least one participant inside 0..4)
    # was fine. "10@127.0.0.1" raises the probed range to ids 0..10 (11 slots) -- covers
    # control_wan (1) + up to 10 platforms x 1 domain-200 participant each post-D103
    # (was up to 5 platforms x 2 before); kept at 10 rather than shrunk since a larger
    # window is harmless headroom, not a tightness requirement.
    export CONTROL_LAN_PEER1=127.0.0.1 CONTROL_LAN_PEER2=127.0.0.1 CONTROL_LAN_PEER3=127.0.0.1 CONTROL_WAN_PEER1=10@127.0.0.1
    export PLATFORM_LAN_PEER1=127.0.0.1 PLATFORM_LAN_PEER2=127.0.0.1 PLATFORM_LAN_PEER3=127.0.0.1 PLATFORM_WAN_PEER1=10@127.0.0.1
    export WAN_HB_PERIOD_SEC=1 WAN_HB_RETRIES=10 WAN_MAX_BLOCKING_SEC=1
    export WAN_TIMEOUT_SEC=100 WAN_TTL=1 WAN_RECEIVE_MULTICAST=0

    echo "[run_mesh up] workdir: $WORKDIR"
    echo "[run_mesh up] launching control node..."
    # No subshell/"cd &&" wrapping around these nohup calls -- bash forks an extra layer to
    # run a backgrounded compound list, so $! ends up naming that wrapper, not the real
    # router_main/python3 process (found the hard way: pids.txt pointed at stale wrapper
    # shells while the actual router_main processes ran one PID higher, untracked). Plain
    # absolute-path nohup calls at this level exec directly, so $! is the real PID.
    nohup "$REPO_ROOT/router/build/router_main" --config "$REPO_ROOT/router/config/control-platform.yaml" \
        --role control --node-name Control_20 --name control-platform-run \
        --admin-participant control_lan > "$WORKDIR/control.log" 2>&1 &
    echo "control $!" >> "$WORKDIR/pids.txt"

    LAST_ID=$((30 + PLATFORMS - 1))
    echo "[run_mesh up] launching $PLATFORMS platform router(s): Platform_30..Platform_${LAST_ID}..."
    for ID in $(seq 30 "$LAST_ID"); do
        sed -e "s/Platform_30/Platform_${ID}/g" \
            -e "s/platform-30-control-platform/platform-${ID}-control-platform/g" \
            -e "0,/domain: 30/s//domain: ${ID}/" \
            "$REPO_ROOT/router/config/control-platform.yaml" > "$WORKDIR/control-platform-${ID}.yaml"
        # Verify the substitutions actually fired -- sed exits 0 on a no-op match just as
        # happily as a real one, so a reformatted control-platform.yaml (whitespace change,
        # a second "domain: 30"-shaped string added earlier in the file) could otherwise
        # silently leave a platform on the wrong name/domain with no error anywhere, e.g.
        # two "isolated" platforms colliding on the same platform_lan domain.
        if ! grep -q "Platform_${ID}" "$WORKDIR/control-platform-${ID}.yaml" \
           || ! grep -q "platform-${ID}-control-platform" "$WORKDIR/control-platform-${ID}.yaml" \
           || ! grep -q "domain: ${ID}" "$WORKDIR/control-platform-${ID}.yaml"; then
            echo "Error: templating control-platform-${ID}.yaml didn't produce the expected" >&2
            echo "  Platform_${ID} / platform-${ID}-control-platform / domain: ${ID} strings --" >&2
            echo "  control-platform.yaml may have changed shape; check the sed patterns in $0" >&2
            exit 1
        fi
        nohup "$REPO_ROOT/router/build/router_main" --config "$WORKDIR/control-platform-${ID}.yaml" \
            --role platform --node-name "Platform_${ID}" --name "platform-${ID}-control-platform" \
            --admin-participant platform_lan > "$WORKDIR/platform${ID}.log" 2>&1 &
        echo "platform${ID}_router $!" >> "$WORKDIR/pids.txt"
    done

    echo "[run_mesh up] launching $PLATFORMS platform_sim(s)..."
    for ID in $(seq 30 "$LAST_ID"); do
        # start_platform_sim.sh ships without the execute bit in this repo (same as its
        # sibling scripts) -- invoke via `bash`, not direct exec, or nohup fails silently
        # with "Permission denied" (found testing this script: 3/3 sims failed that way,
        # while router_main -- which IS +x -- launched fine).
        nohup bash "$V2_ROOT/scripts/start_platform_sim.sh" --id "$ID" --destination Control_20 \
            --verbosity "$VERBOSITY" > "$WORKDIR/platform${ID}_sim.log" 2>&1 &
        echo "platform${ID}_sim $!" >> "$WORKDIR/pids.txt"
    done

    echo "[run_mesh up] launching $PLATFORMS platform_control process(es)..."
    # platform_control.py needs NDDS_QOS_PROFILES for the LAN QoS profiles (same as the
    # platform_sim, which gets it via start_platform_sim.sh -> env.sh). Export it here so
    # the nohup child inherits it.
    export NDDS_QOS_PROFILES="${V2_ROOT}/qos/lan_qos_lib.xml;${V2_ROOT}/datamodel/gen/ActTypes.xml"
    for ID in $(seq 30 "$LAST_ID"); do
        # Platform control process (team-control-topic-plan.md §3): subscribes to
        # TeamAssignment on platform_lan and translates into RouterCommand
        # (ADD/REMOVE_PARTICIPANT_PARTITION) for the local router. Needs the same
        # NDDS_QOS_PROFILES as the platform_sim (LAN QoS lib + types).
        nohup python3 "$V2_ROOT/scripts/platform_control.py" \
            --domain "$ID" --node "Platform_${ID}" \
            > "$WORKDIR/platform${ID}_control.log" 2>&1 &
        echo "platform${ID}_control $!" >> "$WORKDIR/pids.txt"
    done

    if [[ "$WITH_DASHBOARD" == true ]]; then
        echo "[run_mesh up] generating WIS config..."
        bash "$REPO_ROOT/gui/mesh_dashboard/config/generate_wis_config.sh" "$WORKDIR/wis" \
            > "$WORKDIR/generate_wis_config.log" 2>&1

        echo "[run_mesh up] launching dashboard (WIS) on port $DASHBOARD_PORT..."
        nohup "$NDDSHOME/bin/rtiwebintegrationservice" \
            -cfgFile "$WORKDIR/wis/wis_config.xml" -cfgName mesh_dashboard \
            -listeningPorts "$DASHBOARD_PORT" -enableWebSockets \
            -documentRoot "$REPO_ROOT/gui/mesh_dashboard/static" \
            > "$WORKDIR/wis.log" 2>&1 &
        WIS_WRAPPER_PID=$!
        echo "wis_wrapper $WIS_WRAPPER_PID" >> "$WORKDIR/pids.txt"

        # rtiwebintegrationservice is a shell wrapper around a second process
        # (rtiwebintegrationserviceapp) that actually holds the port -- the wrapper's own
        # PID alone isn't enough for `down` to free the port (gui/mesh_dashboard/README.md,
        # confirmed manually testing this exact stack). `pgrep -P <wrapper>` is NOT reliable
        # here -- it can catch a short-lived intermediate child that's already exited by the
        # time `down` runs (found testing this script: the recorded "wis_app" pid was dead
        # a few seconds later, while a DIFFERENT pid was the one `ss` showed actually bound
        # to the port). Read the real pid straight off the listening socket instead -- that's
        # the process that matters for freeing the port, however many hops of forking got it
        # there. Poll rather than a fixed sleep, since binding can take a few seconds.
        WIS_APP_PID=""
        for _ in $(seq 1 15); do
            # `|| true` -- under `set -e -o pipefail`, grep finding nothing (normal on early
            # iterations, before WIS has bound the port yet) fails the whole command
            # substitution and aborts the script; that's a poll-not-ready case, not an error.
            WIS_APP_PID="$(ss -tlnp 2>/dev/null | grep ":${DASHBOARD_PORT} " | grep -oP 'pid=\K[0-9]+' | head -1 || true)"
            [[ -n "$WIS_APP_PID" ]] && break
            sleep 1
        done
        if [[ -n "$WIS_APP_PID" ]]; then
            echo "wis_app $WIS_APP_PID" >> "$WORKDIR/pids.txt"
        else
            echo "  WARN: couldn't find the pid bound to port $DASHBOARD_PORT -- down may leave it bound" >&2
        fi

        echo "[run_mesh up] dashboard: http://localhost:${DASHBOARD_PORT}/"
    fi

    sleep 3
    echo "[run_mesh up] done. control.log tail:"
    tail -5 "$WORKDIR/control.log"
    echo "[run_mesh up] WORKDIR=$WORKDIR"
}

do_down() {
    if [[ ! -f "$WORKDIR/pids.txt" ]]; then
        echo "Error: $WORKDIR/pids.txt not found -- wrong --workdir or no prior 'up'?" >&2; exit 1
    fi

    echo "[run_mesh down] killing tracked PIDs from $WORKDIR/pids.txt..."
    while read -r label pid; do
        [[ -z "$pid" ]] && continue
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null && echo "  killed $label (pid $pid)" \
                || echo "  WARN: failed to kill $label (pid $pid)"
        else
            echo "  $label (pid $pid) already gone"
        fi
    done < "$WORKDIR/pids.txt"

    # DDS participant teardown (writer/reader cleanup) can take a couple seconds past the
    # SIGTERM -- poll instead of guessing a fixed delay (found testing this script: a plain
    # `sleep 1` still showed all 4 router_main processes alive in a ps check right after).
    for _ in $(seq 1 10); do
        any_alive=false
        while read -r _ pid; do
            [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && any_alive=true
        done < "$WORKDIR/pids.txt"
        [[ "$any_alive" == false ]] && break
        sleep 1
    done

    echo "[run_mesh down] stray /dev/shm segments:"
    ls /dev/shm 2>/dev/null | grep -E "^(RTI|dds)" || echo "  none"
    echo "[run_mesh down] done. logs/configs left in $WORKDIR for inspection (rm -rf it when done)."
}

case "$ACTION" in
    up) do_up ;;
    down) do_down ;;
    *) echo "Usage: $0 {up --platforms <N> [--workdir <dir>] [--verbosity <0-3>] [--with-dashboard] [--dashboard-port <port>] | down [--workdir <dir>]}" >&2
       exit 1 ;;
esac
