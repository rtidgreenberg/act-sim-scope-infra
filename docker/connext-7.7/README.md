# Connext DDS 7.7 container

This example builds an Ubuntu 22.04 image from the local Connext DDS 7.7
installation at `/home/dgreenberg/rti_connext_dds-7.7.0` and clones
`https://github.com/rtidgreenberg/act-sim-scope-infra.git` into
`/opt/act-sim-scope-infra`. The Connext license is not copied into the image;
it is mounted only when the container runs.

## Prepare the files

Place the license and any shared configuration at:

```text
docker/connext-7.7/shared/rti_license.dat
docker/connext-7.7/shared/
```

The `shared` directory is mounted at `/shared`; it is not copied into the
image. Keep the license file out of source control.

## Build and start

From this directory:

```bash
docker compose build
docker compose run --rm connext bash
```

The host installation path is configured in `compose.yaml` as an additional
build context. Change that path if Connext 7.7 is installed elsewhere.

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
```

## Run one platform

Build the router once inside the container:

```bash
cmake -S router -B router/build -DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0
cmake --build router/build -j"$(nproc)"
```

Then launch one control node, one platform router, and one platform simulator. Runtime
files remain on the container's local `/tmp` filesystem:

```bash
bash harness_v2/scripts/run_mesh.sh up --platforms 1
```

When finished, tear down every process tracked by the harness:

```bash
bash harness_v2/scripts/run_mesh.sh down
```

To use different host directories:

```bash
CONNEXT_SHARED_DIR=/path/to/shared \
CONNEXT_WORKSPACE_DIR=/path/to/workspace \
docker compose run --rm connext bash
```

Host networking is enabled in `compose.yaml` for both the image build and the
running container. This allows Ubuntu package DNS and DDS discovery to use the
host network namespace. Host networking is supported by Docker Engine on Linux.