#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
REPORT=debug/test_reports/preemptive_acknack_20261119
CAPTURE=debug/pcap/preemptive_acknack_20261119
name="act-preemptive-acknack-$$"
mkdir -p "$REPORT" "$CAPTURE"
rm -f "$REPORT/PASS"
cleanup() {
  sg docker -c "docker rm -f '$name'" >/dev/null 2>&1 || true
}
trap cleanup EXIT
sg docker -c 'docker image inspect connext:7.7.0 --format "{{.Id}}"' > "$REPORT/image_id.txt"
sg docker -c "docker run --rm --name '$name' \
  --network none --ipc private --cap-add NET_RAW --cap-add NET_ADMIN \
  --tmpfs /tmp:exec \
  -v '$PWD':/workspace:ro \
  -v '$PWD/$REPORT':/reports \
  -v '$PWD/$CAPTURE':/captures \
  -v /home/dgreenberg/rti_connext_dds-7.7.0:/shared:ro \
  -w /workspace/spikes/preemptive_acknack \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e TMPDIR=/tmp/act-preemptive-acknack \
  -e RTI_LICENSE_FILE=/shared/rti_license.dat \
  -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
  connext:7.7.0 bash -c \
  'mkdir -p \"\$TMPDIR\" &&
   gcc -shared -fPIC -Wall -Wextra -O2 drop.c -o \"\$TMPDIR/drop.so\" -ldl &&
   python3 -m unittest -v test_wire && python3 runner.py'" 2>&1 | tee "$REPORT/console.log"
