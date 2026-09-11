#!/usr/bin/env bash
# Provision and verify a fresh Ubuntu 22.04 ACT/Connext/EMANE build host.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIN_FREE_GIB=60
DOCKER_DATA_ROOT=""
LICENSE_FILE=""
LICENSE_DIR="${HOME}/.local/share/act-connext-license"
BUILD_IMAGE=false
BUILD_ROUTER=false

usage() {
    cat <<'EOF'
Usage: scripts/setup_instance.sh [options]

Run after cloning this repository on a fresh Ubuntu 22.04 or 24.04 instance.

Options:
  --docker-data-root DIR  Configure Docker to store data in DIR (requires sudo).
  --license-file FILE     Copy the externally supplied rti_license.dat to --license-dir.
  --license-dir DIR       License-only directory mounted into containers.
  --build-image           Build and verify the Connext/EMANE image after checks pass.
  --build-router          Build and run router unit tests in the verified image.
  --help                  Show this help.

The script refuses to configure an existing Docker data root. It never downloads,
prints, or commits an RTI license. After adding a user to the docker group, start a
new shell before using Docker without sg(1).
EOF
}

fail() {
    echo "Error: $*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

free_gib() {
    df -Pk "$1" | awk 'NR == 2 { print int($4 / 1024 / 1024) }'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --docker-data-root) DOCKER_DATA_ROOT="${2:?missing directory}"; shift 2 ;;
        --license-file) LICENSE_FILE="${2:?missing file}"; shift 2 ;;
        --license-dir) LICENSE_DIR="${2:?missing directory}"; shift 2 ;;
        --build-image) BUILD_IMAGE=true; shift ;;
        --build-router) BUILD_ROUTER=true; BUILD_IMAGE=true; shift ;;
        --help) usage; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done

[[ -f /etc/os-release ]] || fail "cannot identify operating system"
. /etc/os-release
[[ "$ID" == "ubuntu" && ( "$VERSION_ID" == "22.04" || "$VERSION_ID" == "24.04" ) ]] || \
    fail "Ubuntu 22.04 or 24.04 is required; found ${PRETTY_NAME:-unknown}"

require_command git
require_command python3

if [[ -n "$DOCKER_DATA_ROOT" ]]; then
    require_command sudo
    [[ -d "$DOCKER_DATA_ROOT" ]] || fail "Docker data-root does not exist: $DOCKER_DATA_ROOT"
    [[ "$(free_gib "$DOCKER_DATA_ROOT")" -ge "$MIN_FREE_GIB" ]] || \
        fail "Docker data-root needs at least ${MIN_FREE_GIB} GiB free: $DOCKER_DATA_ROOT"
    [[ ! -e /etc/docker/daemon.json ]] || \
        fail "/etc/docker/daemon.json already exists; migrate Docker deliberately instead"
    [[ -z "$(find "$DOCKER_DATA_ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]] || \
        fail "Docker data-root must be empty on a fresh host: $DOCKER_DATA_ROOT"
    printf '{"data-root":"%s"}\n' "$DOCKER_DATA_ROOT" | \
        sudo install -D -m 0644 /dev/stdin /etc/docker/daemon.json
    echo "Docker data-root configured. Restart Docker, then rerun this script before building."
    exit 0
fi

require_command docker
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin is required"
[[ -r /dev/net/tun ]] || fail "/dev/net/tun is required for EMANE virtual transport"
getent group docker >/dev/null || fail "Docker group is missing"
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
    fail "Docker group exists but $USER is not a member; run: sudo usermod -aG docker $USER, then start a new shell"
fi

if [[ -n "$LICENSE_FILE" ]]; then
    [[ -r "$LICENSE_FILE" ]] || fail "license file is unreadable: $LICENSE_FILE"
    mkdir -p "$LICENSE_DIR"
    install -m 0600 "$LICENSE_FILE" "$LICENSE_DIR/rti_license.dat"
fi
[[ -r "$LICENSE_DIR/rti_license.dat" ]] || \
    fail "supply --license-file or place rti_license.dat in $LICENSE_DIR"

if [[ "$BUILD_IMAGE" == true ]]; then
    ACTIVE_DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}')"
    [[ "$(free_gib "$ACTIVE_DOCKER_ROOT")" -ge "$MIN_FREE_GIB" ]] || \
        fail "Docker filesystem needs at least ${MIN_FREE_GIB} GiB free before image build: $ACTIVE_DOCKER_ROOT"
fi

if [[ -s "$REPO_ROOT/.gitmodules" ]]; then
    git -C "$REPO_ROOT" submodule update --init --recursive
fi

if [[ "$BUILD_IMAGE" == true ]]; then
    CONNEXT_WORKSPACE_DIR="$REPO_ROOT" CONNEXT_SHARED_DIR="$LICENSE_DIR" \
        docker compose -f "$REPO_ROOT/docker/connext-7.7/compose.yaml" build
    docker run --rm -v "$LICENSE_DIR:/shared:ro" connext:7.7.0 sh -lc \
        'test -r "$RTI_LICENSE_FILE" && python3 -c "import rti.connextdds" && emane --version'
fi

if [[ "$BUILD_ROUTER" == true ]]; then
    docker run --rm -v "$REPO_ROOT:/workspace" -v "$LICENSE_DIR:/shared:ro" \
        -w /workspace -e NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0 \
        -e RTI_LICENSE_FILE=/shared/rti_license.dat connext:7.7.0 sh -lc \
        'cmake -S router -B router/build -DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0 && cmake --build router/build -j2 && bash router/run_tests.sh'
fi

echo "Fresh-instance setup checks passed. License directory: $LICENSE_DIR"