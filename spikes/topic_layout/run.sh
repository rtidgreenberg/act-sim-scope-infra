#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
root="$PWD"
name="act-topic-layout-$$"
repetitions="${REPETITIONS:-4}"
case "$repetitions" in
    1|2|3|4|5|6) ;;
    *) echo "REPETITIONS must be between 1 and 6" >&2; exit 1 ;;
esac
extra=
case "${SUPPRESS_PREEMPTIVE:-0}" in
    0) ;;
    1) extra=--suppress-preemptive ;;
    *) echo "SUPPRESS_PREEMPTIVE must be 0 or 1" >&2; exit 1 ;;
esac
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
report="$root/debug/test_reports/topic_layout_$stamp"
capture="$root/debug/pcap/topic_layout_$stamp"
lock_id="$(stat -c '%d-%i' .)"
exec {lock_fd}>"/tmp/act-topic-layout-${lock_id}.lock"
flock -n "$lock_fd" || { echo "Topic-layout spike already running" >&2; exit 1; }
sg docker -c 'docker image inspect connext:7.7.0 --format "{{.Id}}"'
mkdir -p "$report" "$capture"
printf 'REPORT=%s\nCAPTURE=%s\n' "$report" "$capture"
cleanup() {
    if sg docker -c "docker container inspect '$name'" >/dev/null 2>&1; then
        sg docker -c "docker rm -f '$name'"
    fi
}
trap cleanup EXIT
sg docker -c "docker run --rm --name '$name' --network none --hostname localhost \
  --ipc private --cap-add NET_RAW --cap-add NET_ADMIN --tmpfs /tmp:exec \
  -v '$root':/workspace:ro -v '$report':/reports -v '$capture':/captures \
  -v /home/dgreenberg/rti_connext_dds-7.7.0:/shared:ro \
  -e RTI_LICENSE_FILE=/shared/rti_license.dat \
  -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
  -e PYTHONDONTWRITEBYTECODE=1 -e TARGET_UID=$(id -u) -e TARGET_GID=$(id -g) \
  connext:7.7.0 python3 /workspace/spikes/topic_layout/runner.py --repetitions '$repetitions' $extra" \
  2>&1 | tee "$report/console.log"
