#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
REPORT=debug/test_reports/participant_preemptive_acknack_20261120
CAPTURE=debug/pcap/participant_preemptive_acknack_20261120
option=""
case "${1:-}" in
  "") ;;
  --matrix)
    option="--matrix"
    REPORT=debug/test_reports/participant_preemptive_acknack_matrix_20261120
    CAPTURE=debug/pcap/participant_preemptive_acknack_matrix_20261120
    ;;
  *) echo "Usage: $0 [--matrix]" >&2; exit 2 ;;
esac
if [[ $# -gt 1 ]]; then
  echo "Usage: $0 [--matrix]" >&2
  exit 2
fi
name="act-participant-preemptive-$$"
mkdir -p "$REPORT" "$CAPTURE"
cleanup() {
  sg docker -c "docker rm -f '$name'" >/dev/null 2>&1 || true
}
trap cleanup EXIT
sg docker -c 'docker image inspect connext:7.7.0 --format "{{.Id}}"' > "$REPORT/image_id.txt"
sg docker -c "docker run --rm --name '$name' \
  --network none --ipc private --cap-add NET_RAW --cap-add NET_ADMIN \
  --tmpfs /tmp:exec \
  -v '$PWD':/workspace:ro -v '$PWD/$REPORT':/reports -v '$PWD/$CAPTURE':/captures \
  -v /home/dgreenberg/rti_connext_dds-7.7.0:/shared:ro \
  -w /workspace/spikes/preemptive_acknack -e PYTHONDONTWRITEBYTECODE=1 \
  -e TMPDIR=/tmp/act-participant-preemptive \
  -e RTI_LICENSE_FILE=/shared/rti_license.dat \
  -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
  connext:7.7.0 bash -c \
  'mkdir -p \"\$TMPDIR\" &&
   gcc -shared -fPIC -Wall -Wextra -O2 drop.c -o \"\$TMPDIR/drop.so\" -ldl &&
   python3 -m unittest -v test_wire && python3 participant.py $option'" 2>&1 | tee "$REPORT/console.log"
