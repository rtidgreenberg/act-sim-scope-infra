#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mode=""
suffix="catalog"
if [[ $# == 1 && "$1" == "--discovery-only" ]]; then
    mode="--discovery-only"
    suffix="discovery_only"
elif [[ $# != 0 ]]; then
    echo "Usage: bash spikes/discovered_union_catalog/run.sh [--discovery-only]" >&2
    exit 1
fi
license_dir="${CONNEXT_SHARED_DIR:-/home/dgreenberg/rti_connext_dds-7.7.0}"
[[ -r "$license_dir/rti_license.dat" ]] || {
    echo "Missing readable Connext license: $license_dir/rti_license.dat" >&2
    exit 1
}
docker image inspect connext:7.7.0 >/dev/null
container="act-discovered-union-$$"
report="$root/debug/test_reports/discovered_union_${suffix}.txt"
cleanup() {
    if docker container inspect "$container" >/dev/null 2>&1; then
        docker rm -f "$container" >/dev/null
    fi
}
trap cleanup EXIT
mkdir -p "$root/debug/test_reports"
rm -f "$report"
# Private loopback/network/IPC namespaces: never join the live mesh or host DDS.
docker run --rm --network none --hostname localhost --name "$container" \
    -v "$root:/workspace:ro" \
    -v "$license_dir:/shared:ro" \
    -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
    -e RTI_LICENSE_FILE=/shared/rti_license.dat \
    -e SPIKE_MODE="$mode" \
    connext:7.7.0 bash -lc '
        set -euo pipefail
        test -z "$(find /dev/shm -maxdepth 1 \( -name "RTI*" -o -name "dds*" \) -print -quit)"
        cmake -S /workspace/spikes/discovered_union_catalog -B /tmp/catalog-build \
            -DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0
        cmake --build /tmp/catalog-build -j2
        python3 -B /workspace/spikes/discovered_union_catalog/run_processes.py \
            /tmp/catalog-build /workspace/spikes/discovered_union_catalog \
            /workspace/harness_v2/qos/act_qos_profiles.xml ${SPIKE_MODE:+"$SPIKE_MODE"}
        test -z "$(find /dev/shm -maxdepth 1 \( -name "RTI*" -o -name "dds*" \) -print -quit)"
    ' 2>&1 | tee "$report"
