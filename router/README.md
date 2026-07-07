# ACT C++ Router

Buildout of the dynamic DDS router described in [`docs/cpp_router/`](../docs/cpp_router/).
Start with [Thesis & Tenets](../docs/cpp_router/thesis-and-tenets.md) (authoritative), then
the [Implementation Plan](../docs/cpp_router/implementation-plan.md).

## Status: Phase 0 — Build skeleton and admin IDL

Phase 0 delivers a buildable, testable skeleton. It creates **no DDS entities**; that begins
in Phase 3 (see the plan). Delivered here:

- `router_main` — parses `--config <path> [--name <name>]`, validates the config, prints the
  router identity as one structured log line, and exits cleanly.
- `admin/RouterAdminTypes.idl` — the admin command/status types, transcribed from
  [command-status.md](../docs/cpp_router/command-status.md); generated at build time.
- `src/core/Log.hpp` — the single structured log stream (logfmt, `source=router`). The
  Connext logger bridge (`source=connext`) is added in a later DDS phase.
- `src/config/RouterIdentity.{hpp,cxx}` — a **minimal** identity reader (node/router fields
  only), no external YAML dependency and no DDS dependency. The full route/QoS parser
  (`RouteConfigParser`) is a later phase.
- `config/control-platform.yaml`, `config/platform-team.yaml` — sample configs matching
  [configuration.md](../docs/cpp_router/configuration.md).
- Tests: `test_config_identity` (no-DDS unit test) and `test_admin_types` (generated types
  compile + are usable).

## Build & test

Connext 7.7.0, arch `x64Linux4gcc7.3.0` (see the repo `CLAUDE.md`).

```bash
export NDDSHOME=/home/rti/rti_connext_dds-7.7.0
cmake -B build -DCONNEXTDDS_ARCH=x64Linux4gcc7.3.0
cmake --build build
ctest --test-dir build --output-on-failure

# run the skeleton
./build/router_main --config config/control-platform.yaml
./build/router_main --config config/platform-team.yaml --name platform-30-team
```

Building in-tree on the vboxsf share is fine (sources + executables). Per the repo
filesystem rules, any **runtime** artifacts (SQLite/DWH, FIFOs, logs) must go to a local
fs — the identity unit test already writes its temp fixtures under `/tmp`.
