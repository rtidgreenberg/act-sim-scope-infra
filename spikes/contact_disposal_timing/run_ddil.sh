#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"
results=debug/test_reports/instance_state_ddil_20261119
name="act-instance-ddil-$$"
license_dir="${CONNEXT_SHARED_DIR:-/home/dgreenberg/rti_connext_dds-7.7.0}"
[[ -r "$license_dir/rti_license.dat" ]]
mkdir -p "$results"
cleanup() {
    sg docker -c "docker rm -f '$name'" >/dev/null 2>&1 || true
}
trap cleanup EXIT
sg docker -c "docker run --rm --hostname localhost --name '$name' \
    --network none --ipc private --cap-add NET_ADMIN --tmpfs /tmp:exec \
    -v '$root:/workspace:ro' -v '$root/$results:/results' \
    -v '$license_dir:/shared:ro' -w /workspace \
    -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
    -e RTI_LICENSE_FILE=/shared/rti_license.dat -e PYTHONDONTWRITEBYTECODE=1 \
    -e DDIL_SCENARIOS \
    -e TMPDIR=/tmp -e HOME=/tmp \
    connext:7.7.0 python3 /workspace/spikes/contact_disposal_timing/ddil.py"
