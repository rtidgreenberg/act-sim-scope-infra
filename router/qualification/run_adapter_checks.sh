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
    echo "Stop the owned mesh before running adapter tests." >&2
    exit 1
fi
if ps -eo comm= | grep -Eq '^(router_main|state_reader|state_writer|emane|pytest)$'; then
    echo "A DDS/test process is already running; stop its owned harness first." >&2
    exit 1
fi
if [[ -n "$(find /dev/shm -maxdepth 1 \( -name 'RTI*' -o -name 'dds*' \) -print -quit)" ]]; then
    echo "Stray DDS shared-memory artifacts exist; resolve ownership first." >&2
    exit 1
fi

workdir="$(mktemp -d /tmp/act-wan-adapter-checks.XXXXXX)"
container="act-wan-adapter-checks-$$"
cleanup() {
    if docker container inspect "$container" >/dev/null 2>&1; then
        docker rm -f "$container" >/dev/null
    fi
    docker run --rm --network none -v "$workdir:/work" connext:7.7.0 \
        sh -c 'rm -rf /work/build /work/wheels'
    rmdir "$workdir"
}
trap cleanup EXIT
mkdir -p "$workdir/build" "$workdir/wheels" "$root/debug/test_reports"
if ! docker run --rm --network none connext:7.7.0 \
    python3 -c 'import pytest, rti.connextdds'; then
    docker run --rm --network bridge -v "$workdir:/work" connext:7.7.0 \
        python3 -m pip download pytest -d /work/wheels
fi
yaml_source="$root/router/build/_deps/yaml-cpp-src"
if [[ ! -f "$yaml_source/CMakeLists.txt" ]]; then
    echo "Missing verified yaml-cpp cache: $yaml_source" >&2
    exit 1
fi
docker run --rm --network none --hostname localhost --name "$container" \
    -v "$root:/workspace:ro" \
    -v "$workdir/build:/workspace/router/build" \
    -v "$workdir/wheels:/wheels:ro" \
    -v "$yaml_source:/deps/yaml:ro" \
    --tmpfs /workspace/tmp:rw \
    --tmpfs /workspace/debug/logs/router_e2e:rw \
    -v "$root/debug/test_reports:/results" \
    -v "$license_dir:/shared:ro" \
    -w /workspace \
    -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
    -e RTI_LICENSE_FILE=/shared/rti_license.dat \
    -e PYTHONPYCACHEPREFIX=/tmp/pycache \
    -e TMPDIR=/tmp \
    connext:7.7.0 bash -lc '
        set -euo pipefail
        if ! python3 -c "import pytest"; then
            python3 -m pip install --quiet --no-index --find-links /wheels pytest
        fi
        cmake -S router -B router/build -DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0 \
            -DFETCHCONTENT_SOURCE_DIR_YAML-CPP=/deps/yaml
        cmake --build router/build -j2
        bash router/run_tests.sh | tee /results/two_topic_shared_adapter_cpp.txt
        python3 -m pytest -q -p no:cacheprovider \
            router/test_e2e/test_wan_adapter_pilot.py \
            router/test_e2e/test_two_topic_controller.py \
            router/test_e2e/test_control_command_route.py \
            router/test_e2e/test_platform_status_route.py \
            router/test_e2e/test_same_node_ignore.py \
            --junitxml=/results/two_topic_shared_adapter.xml "$@"
    ' bash "$@"
if [[ -n "$(find /dev/shm -maxdepth 1 \( -name 'RTI*' -o -name 'dds*' \) -print -quit)" ]]; then
    echo "DDS shared-memory artifacts exist after teardown." >&2
    exit 1
fi
