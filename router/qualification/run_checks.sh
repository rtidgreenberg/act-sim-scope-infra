#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
license_dir="${CONNEXT_SHARED_DIR:-/home/dgreenberg/rti_connext_dds-7.7.0}"
if [[ ! -r "$license_dir/rti_license.dat" ]]; then
    echo "Connext license is not readable: $license_dir/rti_license.dat" >&2
    exit 1
fi
docker image inspect connext:7.7.0 >/dev/null

workdir="$(mktemp -d /tmp/act-two-topic-qualification.XXXXXX)"
container="act-two-topic-qualification-$$"

cleanup() {
    if docker container inspect "$container" >/dev/null 2>&1; then
        docker rm -f "$container" >/dev/null
    fi
    docker run --rm --network none -v "$workdir:/work" connext:7.7.0 \
        sh -c 'rm -rf /work/wheels'
    rmdir "$workdir"
}
trap cleanup EXIT

mkdir -p "$workdir/wheels" "$root/debug/test_reports"

if ! docker run --rm --network none connext:7.7.0 \
    python3 -c 'import importlib.util, sys; sys.exit(importlib.util.find_spec("pytest") is None)'; then
    echo "Downloading missing pytest for offline installation in the test container."
    docker run --rm --network bridge -v "$workdir:/work" connext:7.7.0 \
        python3 -m pip download pytest -d /work/wheels
fi

docker run --rm --network none --hostname localhost --cap-add NET_ADMIN \
    --name "$container" \
    -v "$root:/workspace:ro" \
    -v "$workdir:/work:ro" \
    -v "$root/debug/test_reports:/results" \
    -v "$license_dir:/shared:ro" \
    -w /workspace \
    -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
    -e RTI_LICENSE_FILE=/shared/rti_license.dat \
    -e PYTHONPYCACHEPREFIX=/tmp/pycache \
    -e TMPDIR=/tmp \
    -e ACT_TWO_TOPIC_ISOLATED=1 \
    connext:7.7.0 sh -lc '
        set -e
        if ! python3 -c "import importlib.util, sys; sys.exit(importlib.util.find_spec(\"pytest\") is None)"; then
            python3 -m pip install --quiet --no-index --find-links /work/wheels pytest
        fi
        python3 -m pytest -q -p no:cacheprovider \
            router/qualification/test_two_topic_qualification.py \
            --junitxml=/results/two_topic_union_qualification.xml "$@"
    ' sh "$@"
