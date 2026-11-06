# Connext DDS 7.7 and EMANE container

This example builds an Ubuntu 22.04 image from RTI's public Debian repository
and clones `https://github.com/rtidgreenberg/act-sim-scope-infra.git` into
`/opt/act-sim-scope-infra`. The build installs the full
`rti-connext-dds-7.7.0` SDK, matching `rti.connext` Python binding, and checksum-pinned
EMANE 1.5.3 Ubuntu 22.04 bundle. The
Connext license is not copied into the image; it is mounted only when the
container runs.

## Prepare the files

Place the license and any shared configuration in a host directory, for example:

```text
docker/connext-7.7/shared/rti_license.dat
docker/connext-7.7/shared/
```

The `shared` directory is mounted at `/shared`; it is not copied into the
image. It needs only `rti_license.dat`; the Docker image installs the Connext
SDK itself. Keep the license file out of source control.

Set `CONNEXT_SHARED_DIR` to any license-only host directory containing `rti_license.dat`.
The fresh-host procedure is in [../../docs/fresh-instance.md](../../docs/fresh-instance.md).

## Build and start

From this directory:

```bash
docker compose build
docker compose run --rm connext bash
```

The Dockerfile configures RTI's APT repository, preseeds the RTI license agreement, and
verifies the EMANE bundle checksum before installing it. The build requires network access to
`packages.rti.com`, PyPI, and `adjacentlink.com`; it does not require a host Connext or EMANE
installation. Allocate at least 60 GiB free on Docker's active data root before building.

The container starts in `/opt/act-sim-scope-infra`. To build a specific branch
or tag:

```bash
docker compose build --build-arg ACT_REPO_REF=main
```

The repository is cloned during the image build, so rebuilding is required to
pick up later repository changes. The `/workspace` bind mount remains available
for local files and does not hide the cloned repository.

Verify the installation from inside the container:

```bash
echo "$NDDSHOME"
test -r "$RTI_LICENSE_FILE"
python3 -c 'import rti.connextdds'
emane --version
```

## Current harnesses

Build the router once inside the container:

```bash
cmake -S router -B router/build -DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0
cmake --build router/build -j"$(nproc)"
```

The canonical EMANE mesh launches its Compose-managed nodes from an explicit topology file.
Every node's WAN DDS participant uses its `emane0` interface; the dashboard is served from
the control node:

```bash
CONNEXT_SHARED_DIR=/path/to/license-dir \
bash harness_v2/scripts/run_mesh.sh up --emane \
	--topology harness_v2/topologies/three_node_emane.json --dashboard-port 8080
```

Validate delivery and RF interface isolation, then tear down the owned topology:

```bash
CONNEXT_SHARED_DIR=/path/to/license-dir \
bash harness_v2/scripts/run_mesh.sh isolation --emane \
	--topology harness_v2/topologies/three_node_emane.json
CONNEXT_SHARED_DIR=/path/to/license-dir \
bash harness_v2/scripts/run_mesh.sh down --emane \
	--topology harness_v2/topologies/three_node_emane.json
```

Without `--emane`, `run_mesh.sh` retains the host-process mesh as a legacy local diagnostic
mode. `run_container_baseline.sh` is the internal Compose implementation, not a second public
harness.

To use different host directories:

```bash
CONNEXT_SHARED_DIR=/path/to/shared \
CONNEXT_WORKSPACE_DIR=/path/to/workspace \
docker compose run --rm connext bash
```

Host networking is enabled in `compose.yaml` for both the image build and the
running container. This allows Ubuntu package DNS and DDS discovery to use the
host network namespace. Host networking is supported by Docker Engine on Linux.