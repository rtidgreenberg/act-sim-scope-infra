# Fresh Instance Setup

Use this procedure for a new Ubuntu 22.04 or 24.04 VM. It provisions no secrets from the
repository: obtain `rti_license.dat` through the approved RTI license channel first.

## Host Storage

Docker shares the VM root filesystem unless its data root is moved. The Connext and
EMANE image build needs temporary layers as well as its final image, so attach and mount
a dedicated volume with at least 60 GiB free before setup. On a fresh host, run:

```bash
scripts/setup_instance.sh --docker-data-root /mnt/docker-data \
  --license-file /secure/path/rti_license.dat
```

The command writes `/etc/docker/daemon.json` and needs `sudo`. It intentionally refuses
to overwrite an existing Docker data-root configuration or use a nonempty target. For a
VM already in use, migrate `/var/lib/docker` through a planned operator procedure rather
than running this bootstrap option.

Restart Docker after this command, verify `docker info --format '{{.DockerRootDir}}'`
reports `/mnt/docker-data`, then rerun the setup command without `--docker-data-root`.
The bootstrap checks free space on Docker's active data root, not always `/var/lib/docker`.

Install Docker Engine with its Compose plugin before running the script. Add the intended
operator to the `docker` group, then start a new shell. The host must expose `/dev/net/tun`
for the later EMANE virtual transport topology.

## Build And Verify

From the cloned repository, use the externally supplied license directory and run:

```bash
scripts/setup_instance.sh --license-file /secure/path/rti_license.dat --build-image
scripts/setup_instance.sh --build-router
```

The first command initializes submodules, builds the Ubuntu 22.04 Connext/EMANE image,
and verifies the mounted license, `rti.connextdds`, and `emane --version`. The second
builds the router inside that image and runs its C++ unit suite; it mounts the checkout
writable and updates `router/build`. The image downloads its
RTI packages, Python binding, and checksum-pinned EMANE bundle during its build, so the
VM needs outbound access to `packages.rti.com`, PyPI, and `adjacentlink.com`.

Before any DDS or EMANE run, ensure no old mesh/router processes or `/dev/shm/RTI*` and
`/dev/shm/dds*` entries remain. The setup script prepares the host and image only; it does
not start a mesh or create runtime artifacts.