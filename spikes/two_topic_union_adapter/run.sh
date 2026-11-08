#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
license_dir="${CONNEXT_SHARED_DIR:-/home/dgreenberg/rti_connext_dds-7.7.0}"
if [[ ! -r "$license_dir/rti_license.dat" ]]; then
    echo "Connext license is not readable: $license_dir/rti_license.dat" >&2
    exit 1
fi
docker image inspect connext:7.7.0 >/dev/null
if docker ps --format '{{.Names}}' | grep -Eq '^act-.*(mesh|emane)'; then
    echo "Stop the owned mesh before running this DDS spike." >&2
    exit 1
fi
if ps -eo comm= | grep -Eq '^(router_main|state_reader|state_writer|emane|pytest)$'; then
    echo "A DDS/test process is already running; stop its owned harness first." >&2
    exit 1
fi
if [[ -n "$(find /dev/shm -maxdepth 1 \( -name 'RTI*' -o -name 'dds*' \) -print -quit)" ]]; then
    echo "Stray DDS shared-memory artifacts exist; resolve their ownership first." >&2
    exit 1
fi

container="act-two-topic-adapter-$$"
cleanup() {
    if docker container inspect "$container" >/dev/null 2>&1; then
        docker rm -f "$container" >/dev/null
    fi
}
trap cleanup EXIT
mkdir -p "$root/debug/test_reports"
rm -f "$root/debug/test_reports/two_topic_union_adapter.txt"
docker run --rm --network none --hostname localhost --name "$container" \
    -v "$root:/workspace:ro" \
    -v "$root/debug/test_reports:/results" \
    -v "$license_dir:/shared:ro" \
    -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
    -e RTI_LICENSE_FILE=/shared/rti_license.dat \
    connext:7.7.0 bash -lc '
        set -euo pipefail
        cmake -S /workspace/spikes/two_topic_union_adapter -B /tmp/adapter-build \
            -DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0
        cmake --build /tmp/adapter-build -j2
        /tmp/adapter-build/union_adapter \
            /tmp/adapter-build/types/adapter.xml \
            /workspace/harness_v2/qos/act_qos_profiles.xml \
            /workspace/spikes/two_topic_union_adapter/mapping.tsv \
            | tee /results/two_topic_union_adapter.txt
    '
if [[ -n "$(find /dev/shm -maxdepth 1 \( -name 'RTI*' -o -name 'dds*' \) -print -quit)" ]]; then
    echo "DDS shared-memory artifacts exist after teardown." >&2
    exit 1
fi
