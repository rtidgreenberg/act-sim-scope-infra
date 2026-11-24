#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
case "${1:-}" in
    "") suffix=; entry=runner.py ;;
    --cft) suffix=_cft; entry=cft.py ;;
    *) echo "Usage: $0 [--cft]" >&2; exit 1 ;;
esac
REPORT="debug/test_reports/routing_service_lbed${suffix}"
CAPTURE="debug/pcap/routing_service_lbed${suffix}"
name="act-routing-service-lbed-$$"
mkdir -p "$REPORT" "$CAPTURE"
if [[ -n "$(sg docker -c "docker ps -q --filter name=^/act-routing-service-lbed-")" ]]; then
    echo "An LBED spike already owns these report paths" >&2
    exit 1
fi
find "$REPORT" "$CAPTURE" -mindepth 1 -maxdepth 1 -type f -delete
cleanup() {
    sg docker -c "docker rm -f '$name'" >/dev/null 2>&1 || true
}
trap cleanup EXIT
sg docker -c 'docker image inspect connext:7.7.0 --format "{{.Id}}"' > "$REPORT/image_id.txt"
sg docker -c "docker run --rm --name '$name' \
  --network none --hostname localhost --ipc private --cap-add NET_RAW --cap-add NET_ADMIN \
  --tmpfs /tmp:exec \
  -v '$PWD':/workspace:ro \
  -v '$PWD/$REPORT':/reports \
  -v '$PWD/$CAPTURE':/captures \
  -v /home/dgreenberg/rti_connext_dds-7.7.0:/shared:ro \
  -w /workspace/spikes/routing_service_lbed \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e RTI_LICENSE_FILE=/shared/rti_license.dat \
  -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
  connext:7.7.0 python3 '$entry'" 2>&1 | tee "$REPORT/console.log"
