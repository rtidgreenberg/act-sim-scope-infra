#!/bin/bash
# harness_v2 PLATFORM simulator launcher (trimmed v2 copy of harness/act/scripts).
#
# Usage:
#   ./start_platform_sim.sh --id <platform_id> [options]
# Options:
#   --id <num>            Platform ID (required, range: 30-99)
#   --destination <name>  Control station destination (default: Control_20)
#   --domain <num>        Override platform domain ID (default: same as platform ID)
#   --router-name <name>  Override source/router name (default: Platform_<id>)
#   --verbosity <0-3>     Simulator verbosity (default: 2)
#   --print-config        Print resolved config and exit
#   --help                Show this help

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V2_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=env.sh
source "${SCRIPT_DIR}/env.sh"

PLATFORM_ID=""
DESTINATION="Control_20"
PLATFORM_DOMAIN=""
ROUTER_NAME=""
VERBOSITY=2
PRINT_CONFIG=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --id) PLATFORM_ID="$2"; shift 2 ;;
        --destination) DESTINATION="$2"; shift 2 ;;
        --domain) PLATFORM_DOMAIN="$2"; shift 2 ;;
        --router-name) ROUTER_NAME="$2"; shift 2 ;;
        --verbosity) VERBOSITY="$2"; shift 2 ;;
        --print-config) PRINT_CONFIG=true; shift ;;
        --help) sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "Error: Unknown option '$1'"; echo "Use --help for usage"; exit 1 ;;
    esac
done

if [[ -z "$PLATFORM_ID" ]]; then
    echo "Error: --id <platform_id> is required"; echo "Use --help for usage"; exit 1
fi
if [[ "$PLATFORM_ID" -lt 30 ]] || [[ "$PLATFORM_ID" -gt 99 ]]; then
    echo "Error: Platform ID must be in range 30-99 (got: $PLATFORM_ID)"; exit 1
fi
[[ -z "$PLATFORM_DOMAIN" ]] && PLATFORM_DOMAIN=$PLATFORM_ID
[[ -z "$ROUTER_NAME" ]] && ROUTER_NAME="Platform_${PLATFORM_ID}"

LAN_QOS_PROFILE="LAN_QOS_LIB::platform_lan_participant_qos"
DOMAIN_ID=$PLATFORM_DOMAIN

echo "
================================ PLATFORM SIM CONFIG ================================
PLATFORM_ID:       $PLATFORM_ID
ROUTER_NAME:       $ROUTER_NAME
DOMAIN_ID:         $DOMAIN_ID
DESTINATION:       $DESTINATION
LAN_QOS_PROFILE:   $LAN_QOS_PROFILE
NDDS_QOS_PROFILES: $NDDS_QOS_PROFILES
VERBOSITY:         $VERBOSITY
===================================================================================="
[[ "$PRINT_CONFIG" == true ]] && exit 0

exec python3 "${V2_ROOT}/sims/platform_sim.py" --qos_profile "${LAN_QOS_PROFILE}" \
    --domain_id "${DOMAIN_ID}" \
    --source "${ROUTER_NAME}" \
    --destination "${DESTINATION}" \
    --session "${PLATFORM_ID}" \
    --verbosity "${VERBOSITY}"
