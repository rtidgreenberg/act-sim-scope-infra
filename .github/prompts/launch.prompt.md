---
name: "launch"
description: "Launch a containerized ACT router mesh and dashboard"
argument-hint: "platform count, e.g. 3"
agent: "agent"
---
Launch the ACT router mesh with the number of platforms provided as this prompt's argument. Treat a missing, non-integer, or value outside `1..70` as an error and ask for a valid count. The repository root is the current workspace root.

Use the fixed resources below so `/teardown` can clean up precisely:
- Container: `connext-7.7`
- Harness workdir inside the container: `/tmp/act_mesh_launch`
- Dashboard port: `18081`
- License mount source: `/home/dgreenberg/rti_connext_dds-7.7.0`

Follow this sequence exactly:
1. Read `.github/copilot-instructions.md` and preserve its runtime filesystem/DDS safety rules.
2. Preflight without changing anything: verify Docker Engine is reachable and the current user can invoke `docker` without `sudo`; verify `/home/dgreenberg/rti_connext_dds-7.7.0/rti_license.dat` is readable (this mount supplies only the license; the image installs Connext from RTI's APT repository); verify `router/build/router_main` exists or can be built; verify port `18081` is free; verify no `router_main`, `state_reader`, `state_writer`, or mesh Python processes are already running; and verify no `/dev/shm/RTI*` or `/dev/shm/dds*` entries remain. If any check fails, stop and report the precise blocker. Do not use `pkill` or manually kill any process.
   If Docker is installed but this shell has not received the `docker` group membership yet, run Docker commands through `sg docker -c '...'` for this launch; do not use `sudo` for Docker commands. A newly added group membership normally requires a new login session.
3. Build and start the committed Docker environment from the repository root, mounting the current checkout at `/workspace` and the license directory at `/shared`:
   ```bash
   CONNEXT_WORKSPACE_DIR="$PWD" CONNEXT_SHARED_DIR=/home/dgreenberg/rti_connext_dds-7.7.0 docker compose -f docker/connext-7.7/compose.yaml up -d --build
   ```
   Confirm `connext-7.7` is running with `docker inspect` before treating a transient
   `docker compose up` status observation as a failure. Then confirm
   `/shared/rti_license.dat` is readable and `python3 -c 'import rti.connextdds'` works
   inside the container.
4. Build the router inside the container so its C++ runtime matches the image:
   ```bash
   docker exec -w /workspace connext-7.7 bash -lc 'cmake -S router -B router/build -DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0 && cmake --build router/build -j2'
   ```
5. Launch only through the harness from `/workspace`:
   ```bash
   docker exec -w /workspace connext-7.7 bash harness_v2/scripts/run_mesh.sh up --platforms <ARGUMENT> --with-dashboard --dashboard-port 18081 --workdir /tmp/act_mesh_launch
   ```
6. Verify that port `18081` is listening, each router log contains `router.start.ok`, and the dashboard returns HTTP 200. Open the dashboard in the shared browser and confirm it renders live nodes and data, not merely an HTML page.

Do not edit project files, use an ad-hoc launch command, or leave an incomplete mesh running. If a launch step after the harness starts fails, invoke exactly `docker exec -w /workspace connext-7.7 bash harness_v2/scripts/run_mesh.sh down --workdir /tmp/act_mesh_launch` before reporting the failure.

Finish with the dashboard URL as a standalone line:
`http://127.0.0.1:18081/`
