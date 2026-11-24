#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
REPORT=debug/test_reports/discovery_startup_20261120
CAPTURE=debug/pcap/discovery_startup_20261120
program=startup.py
if [[ $# == 1 && "$1" == --optimized ]]; then
  program=optimized.py
  REPORT=debug/test_reports/startup_optimization_20261120
  CAPTURE=debug/pcap/startup_optimization_20261120
elif [[ $# != 0 ]]; then
  echo "Usage: $0 [--optimized]" >&2
  exit 2
fi
name="act-discovery-startup-$$"
mkdir -p "$REPORT" "$CAPTURE"
cleanup() {
  sg docker -c "docker rm -f '$name'" >/dev/null 2>&1 || true
}
trap cleanup EXIT
sg docker -c 'docker image inspect connext:7.7.0 --format "{{.Id}}"' > "$REPORT/image_id.txt"
sg docker -c "docker run --rm --name '$name' \
  --network none --ipc private --cap-add NET_RAW --read-only --tmpfs /tmp:exec \
  -v '$PWD':/workspace:ro -v '$PWD/$REPORT':/reports -v '$PWD/$CAPTURE':/captures \
  -v /home/dgreenberg/rti_connext_dds-7.7.0:/shared:ro \
  -w /workspace/spikes/preemptive_acknack -e PYTHONDONTWRITEBYTECODE=1 \
  -e RTI_LICENSE_FILE=/shared/rti_license.dat \
  connext:7.7.0 sh -c 'python3 -m unittest -v test_wire && python3 $program'" \
  2>&1 | tee "$REPORT/console.log"
