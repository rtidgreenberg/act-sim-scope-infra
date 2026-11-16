#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
license_dir="${CONNEXT_SHARED_DIR:-/home/dgreenberg/rti_connext_dds-7.7.0}"
[[ -r "$license_dir/rti_license.dat" ]]
docker image inspect connext:7.7.0 >/dev/null
container="act-union-auto-qos-$$"
report="$root/debug/test_reports/union_auto_qos.txt"
cleanup() {
    if docker container inspect "$container" >/dev/null 2>&1; then
        docker rm -f "$container" >/dev/null
    fi
}
trap cleanup EXIT
mkdir -p "$root/debug/test_reports"
docker run --rm --network none --ipc private --hostname localhost --name "$container" \
    --workdir /work \
    -v "$root:/workspace:ro" \
    -v "$license_dir:/shared:ro" \
    -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
    -e RTI_LICENSE_FILE=/shared/rti_license.dat \
    -e TMPDIR=/work -e PYTHONDONTWRITEBYTECODE=1 \
    connext:7.7.0 bash -lc '
        set -euo pipefail
        test -z "$(find /dev/shm -maxdepth 1 \( -name "RTI*" -o -name "dds*" \) -print -quit)"
        cmake -S /workspace/spikes/union_auto_qos -B build \
            -DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0
        cmake --build build -j2
        python3 -B /workspace/spikes/union_auto_qos/qualify.py build
        test -z "$(find /dev/shm -maxdepth 1 \( -name "RTI*" -o -name "dds*" \) -print -quit)"
        echo "PASS private shared-memory clean"
    ' 2>&1 | tee "$report"
cleanup
test "$(docker ps -aq --filter "name=^/${container}$")" = ""
echo "PASS owned container removed" | tee -a "$report"
