#!/usr/bin/env bash
set -euo pipefail
extra=
for argument in "$@"; do
    case "$argument" in
        --extended|--transient-local) extra="$extra $argument" ;;
        *) echo "Usage: run.sh [--extended] [--transient-local]" >&2; exit 1 ;;
    esac
done
cd "$(dirname "$0")/../.."
root="$PWD"
name="act-topic-subscriptions-$$"
lock_id="$(stat -c '%d-%i' .)"
exec {lock_fd}>"/tmp/act-topic-subscriptions-${lock_id}.lock"
flock -n "$lock_fd" || { echo "Subscription spike already running" >&2; exit 1; }
sg docker -c 'docker image inspect connext:7.7.0 --format "{{.Id}}"'
work="$(mktemp -d /tmp/topic-subscriptions.XXXXXX)"
chmod 755 "$work"
printf 'WORKDIR=%s\n' "$work"
report="$root/debug/test_reports/topic_subscriptions_$(date -u +%Y%m%dT%H%M%SZ)"
capture="$root/debug/pcap/topic_subscriptions_$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$report" "$capture"
native_pid=
cleanup() {
    if [[ -n "$native_pid" ]] && kill -0 "$native_pid" 2>/dev/null; then
        sg docker -c "docker rm -f '$name'" || echo "Owned container cleanup failed: $name" >&2
        wait "$native_pid" || true
    fi
    cp "$work/console.log" "$report/console.log"
    if [[ -f "$work/extended_report.json" ]]; then
        cp "$work/extended_report.json" "$report/"
    fi
    printf 'REPORT=%s\nCAPTURE=%s\nWORKDIR=%s\n' "$report" "$capture" "$work"
}
trap cleanup EXIT
(
    status=0
    sg docker -c "docker run --rm --name '$name' --network none --hostname localhost \
      --ipc private --cap-add NET_RAW --cap-add NET_ADMIN --tmpfs /tmp:exec \
      -v '$root':/workspace:ro -v '$work':/work \
      -v /home/dgreenberg/rti_connext_dds-7.7.0:/shared:ro \
      -e RTI_LICENSE_FILE=/shared/rti_license.dat \
      -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
      -e PYTHONDONTWRITEBYTECODE=1 \
      connext:7.7.0 python3 /workspace/spikes/topic_subscriptions/native.py $extra" \
      > "$work/console.log" 2>&1 || status=$?
    touch "$work/native_exited"
    exit "$status"
) &
native_pid=$!
PYTHONDONTWRITEBYTECODE=1 python3 spikes/topic_subscriptions/browser.py "$work"
wait "$native_pid"
native_pid=
cp "$work/report.json" "$work/browser_report.json" "$work/progress.json" \
    "$work/graph-user.png" "$work/graph-auto.png" "$work/graph-degraded.png" \
    "$work/degraded_state.json" "$report/"
cp "$work/A.xml" "$work/B.xml" "$work/provider.xml" "$work/A.log" "$work/B.log" \
    "$work/B-restart.log" "$report/"
for log in "$work/"*-stress-*.log; do
    [[ ! -f "$log" ]] || cp "$log" "$report/"
done
cp "$work/wan.pcapng" "$work/capture.log" "$capture/"
if [[ -f "$work/extended_report.json" ]]; then
    cp "$work/extended_report.json" "$report/"
fi
cat "$work/console.log"
