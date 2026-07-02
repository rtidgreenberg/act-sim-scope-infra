#!/bin/bash
# ACT node entrypoint.
# Launches the Routing Service and the simulator for this node, selected by ROLE.
# M0: plain bridge (no EMANE). Phase 1 will start emane first and gate on emane0.
#
# Env:
#   ROLE       platform | control   (required)
#   ID         node id: platform 30-99, control 10-29 (required)
#   VERBOSITY  routing verbosity (default ERROR:ERROR)
#   SIM_VERBOSITY  sim verbosity 0-3 (default 2)
set -euo pipefail

ROLE="${ROLE:?ROLE must be set (platform|control)}"
ID="${ID:?ID must be set}"
VERBOSITY="${VERBOSITY:-ERROR:ERROR}"
SIM_VERBOSITY="${SIM_VERBOSITY:-2}"

cd /act/scripts

echo "=== ACT node: ROLE=$ROLE ID=$ID ==="

# --- Phase 1 placeholder: start EMANE and wait for emane0 before DDS binds ---
# if [[ "${EMANE_ENABLE:-false}" == "true" ]]; then
#   emane /act/sim/emane/platform.xml --realtime -d ... &
#   until ip link show emane0 &>/dev/null; do sleep 0.5; done
# fi

shutdown() { echo "shutting down..."; kill 0; wait; }
trap shutdown SIGTERM SIGINT

case "$ROLE" in
  platform)
    ./start_platform_router.sh --id "$ID" --verbosity "$VERBOSITY" &
    sleep 3   # let the router establish before the sim publishes
    ./start_platform_sim.sh --id "$ID" --verbosity "$SIM_VERBOSITY" &
    ;;
  control)
    ./start_control_router.sh --id "$ID" --verbosity "$VERBOSITY" &
    sleep 3
    ./start_control_sim.sh --id "$ID" --verbosity "$SIM_VERBOSITY" &
    ;;
  *)
    echo "Error: unknown ROLE '$ROLE' (expected platform|control)" >&2
    exit 1
    ;;
esac

wait -n   # exit if either process dies (compose will restart the container)
