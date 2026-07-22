#!/bin/bash
# harness_v2 CONTROL simulator launcher (trimmed v2 copy of harness/act/scripts).
#
# Usage:
#   ./start_control_sim.sh --id <control_id> [options]
# Options:
#   --id <num>            Control ID (required, range: 10-29)
#   --destination <name>  Platform destination (default: Platform_30)
#   --domain <num>        Override control domain ID (default: same as control ID)
#   --router-name <name>  Override source/router name (default: Control_<id>)
#   --verbosity <0-3>     Simulator verbosity (default: 2)
#   --print-config        Print resolved config and exit
#   --help                Show this help

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V2_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

CONTROL_ID=""
DESTINATION="Platform_30"
CONTROL_DOMAIN=""
ROUTER_NAME=""
VERBOSITY=2
PRINT_CONFIG=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --id) CONTROL_ID="$2"; shift 2 ;;
        --destination) DESTINATION="$2"; shift 2 ;;
        --domain) CONTROL_DOMAIN="$2"; shift 2 ;;
        --router-name) ROUTER_NAME="$2"; shift 2 ;;
        --verbosity) VERBOSITY="$2"; shift 2 ;;
        --print-config) PRINT_CONFIG=true; shift ;;
        --help) sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "Error: Unknown option '$1'"; echo "Use --help for usage"; exit 1 ;;
    esac
done

if [[ -z "$CONTROL_ID" ]]; then
    echo "Error: --id <control_id> is required"; echo "Use --help for usage"; exit 1
fi
if [[ "$CONTROL_ID" -lt 10 ]] || [[ "$CONTROL_ID" -gt 29 ]]; then
    echo "Error: Control ID must be in range 10-29 (got: $CONTROL_ID)"; exit 1
fi
[[ -z "$CONTROL_DOMAIN" ]] && CONTROL_DOMAIN=$CONTROL_ID
[[ -z "$ROUTER_NAME" ]] && ROUTER_NAME="Control_${CONTROL_ID}"

LAN_QOS_PROFILE="LAN_QOS_LIB::control_lan_participant_qos"
DOMAIN_ID=$CONTROL_DOMAIN

echo "
================================ CONTROL SIM CONFIG ================================
CONTROL_ID:        $CONTROL_ID
ROUTER_NAME:       $ROUTER_NAME
DOMAIN_ID:         $DOMAIN_ID
DESTINATION:       $DESTINATION
LAN_QOS_PROFILE:   $LAN_QOS_PROFILE
NDDS_QOS_PROFILES: $NDDS_QOS_PROFILES
VERBOSITY:         $VERBOSITY
===================================================================================="
[[ "$PRINT_CONFIG" == true ]] && exit 0

exec python3 "${V2_ROOT}/sims/control_sim.py" --qos_profile "${LAN_QOS_PROFILE}" \
    --domain_id "${DOMAIN_ID}" \
    --source "${ROUTER_NAME}" \
    --destination "${DESTINATION}" \
    --session "${CONTROL_ID}" \
    --verbosity "${VERBOSITY}"
