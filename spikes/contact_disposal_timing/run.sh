#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"
spike=spikes/contact_disposal_timing
results=debug/test_reports/contact_disposal_20261119
name="act-contact-disposal-$$"
license_dir="${CONNEXT_SHARED_DIR:-/home/dgreenberg/rti_connext_dds-7.7.0}"
[[ -r "$license_dir/rti_license.dat" ]]
sg docker -c 'docker image inspect connext:7.7.0 >/dev/null'
mkdir -p "$results"
cleanup() {
    sg docker -c "docker rm -f '$name'" >/dev/null 2>&1 || true
}
trap cleanup EXIT
# Clear only this spike's retained outputs, never any shared mesh/raw log roots.
rm -f "$results/report.json" "$results/inputs-and-logs.tar.gz"
sg docker -c "docker run --rm --network none --ipc private --hostname localhost \
    --tmpfs /tmp:exec \
    --user '$(id -u):$(id -g)' \
    --name '$name' -v '$root:/workspace:ro' \
    -v '$root/$results:/results' -v '$license_dir:/shared:ro' \
    -w /workspace -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
    -e RTI_LICENSE_FILE=/shared/rti_license.dat -e PYTHONDONTWRITEBYTECODE=1 \
    -e TMPDIR=/tmp -e HOME=/tmp \
    connext:7.7.0 python3 /workspace/$spike/run.py"
