#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
license_dir="${CONNEXT_SHARED_DIR:-/home/dgreenberg/rti_connext_dds-7.7.0}"
container="act-reader-instance-purge-$$"
report="$root/debug/test_reports/reader_instance_purge.json"
[[ -r "$license_dir/rti_license.dat" ]] || {
    echo "Missing readable license: $license_dir/rti_license.dat" >&2
    exit 1
}
docker image inspect connext:7.7.0 >/dev/null
if [[ -n "$(find /dev/shm -maxdepth 1 \( -name 'RTI*' -o -name 'dds*' \) -print -quit)" ]]; then
    echo "Resolve existing DDS shared-memory artifact ownership before running." >&2
    exit 1
fi
cleanup() {
    if docker container inspect "$container" >/dev/null 2>&1; then
        docker rm -f "$container" >/dev/null
    fi
}
trap cleanup EXIT
mkdir -p "$root/debug/test_reports"
rm -f "$report"
# Network and IPC isolation allow this probe without contacting the live mesh.
docker run --rm --network none --hostname localhost --name "$container" \
    -v "$root:/workspace:ro" \
    -v "$root/debug/test_reports:/results" \
    -v "$license_dir:/shared:ro" \
    -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
    -e RTI_LICENSE_FILE=/shared/rti_license.dat \
    -e PYTHONDONTWRITEBYTECODE=1 \
    connext:7.7.0 bash -lc '
        set -euo pipefail
        work="$(mktemp -d /tmp/reader-instance-purge.XXXXXX)"
        cmake -S /workspace/spikes/reader_instance_purge -B "$work/build" \
            -DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0
        cmake --build "$work/build" -j2
        python3 /workspace/spikes/reader_instance_purge/run_matrix.py \
            "$work/build/reader_instance_purge" \
            /workspace/spikes/reader_instance_purge/qos.xml \
            /results/reader_instance_purge.json
        test -z "$(find /dev/shm -maxdepth 1 \( -name "RTI*" -o -name "dds*" \) -print -quit)"
    '
if [[ -n "$(find /dev/shm -maxdepth 1 \( -name 'RTI*' -o -name 'dds*' \) -print -quit)" ]]; then
    echo "DDS shared-memory artifacts found after spike teardown." >&2
    exit 1
fi
