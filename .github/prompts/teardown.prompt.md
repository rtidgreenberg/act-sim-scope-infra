---
name: "teardown"
description: "Tear down the containerized ACT router mesh and dashboard"
agent: "agent"
---
Tear down only the ACT router mesh launched by `/launch`. The repository root is the current workspace root, the container is `connext-7.7`, and the harness workdir inside the container is `/tmp/act_mesh_launch`.

Follow this sequence:
1. If the `connext-7.7` container exists and is running, invoke only the harness lifecycle command:
   ```bash
   docker exec -w /workspace connext-7.7 bash harness_v2/scripts/run_mesh.sh down --workdir /tmp/act_mesh_launch
   ```
   Do not use `pkill`, `kill`, or manually stop individual mesh processes.
2. Stop and remove the Docker Compose container from the repository root:
   ```bash
   CONNEXT_WORKSPACE_DIR="$PWD" CONNEXT_SHARED_DIR=/home/dgreenberg/rti_connext_dds-7.7.0 docker compose -f docker/connext-7.7/compose.yaml down
   ```
3. Verify port `18081` is no longer listening and `/dev/shm` has no entries matching `RTI*` or `dds*`.

Do not edit files or affect any process outside this launch. Report concise teardown status and any remaining blocker.
