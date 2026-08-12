# ACT C++ Router

Buildout of the dynamic DDS router described in [`docs/cpp_router/`](../docs/cpp_router/).
Start with [Thesis & Tenets](../docs/cpp_router/thesis-and-tenets.md) (authoritative), then
the [Implementation Plan](../docs/cpp_router/implementation-plan.md).

## Status: Phases 0-5 shipped, Phase 6 next

Phases 0-5 (build skeleton, controller state machine, discovery, one discovered route,
role-aware DynamicData routes with content filtering, DDS-native asymmetric auto QoS) are
shipped and test-verified (`docs/cpp_router/design-decisions.md` D1-D45). `router_main` is
wired to run them for real (D46):

- `router_main` — parses `--config <path> [--name <name>] [--role <role>]
  [--node-name <name>]`, builds the configured DDS participants and routes
  (`RouteConfigParser` -> `TypeResolver` -> `ParticipantRegistry` -> `DynamicRouteFactory`
  -> `RouterController` -> `DiscoveryDispatcher`), and runs until `SIGINT`/`SIGTERM`.
- `admin/RouterAdminTypes.idl` — the admin command/status types, transcribed from
  [command-status.md](../docs/cpp_router/command-status.md); generated at build time.
- `src/core/Log.hpp` — the single structured log stream (logfmt, `source=router`). The
  Connext logger bridge (`source=connext`) is added in a later DDS phase.
- `src/config/RouteConfigParser.{hpp,cxx}` — full route/participant/type YAML parsing
  (yaml-cpp), with role-aware source/destination-side selection.
- `config/control-platform.yaml` — the production-shape system-wide config (D80) matching
  [configuration.md](../docs/cpp_router/configuration.md): every route family
  (control<->platform and, since Phase 10/D87, platform<->platform team) in one file,
  runnable by `router_main` as literally authored. `config/platform-team.yaml` is kept
  only as a Phase-0 identity-reader fixture — its own flat-shape routes:/participants:
  are historical and unexercised by `RouteConfigParser` (the flat route shape is retired,
  D80/D87).
- `config/e2e_control_command.yaml`, `config/e2e_platform_status.yaml` — trimmed,
  `router_main`-runnable fixtures (single type, `qos: ""`) used by the Python e2e suite
  (`test_e2e/`).
- Tests: 4 C++ unit binaries under `test/` (run via `run_tests.sh`), plus the Python suite
  under `test_e2e/`. (The build tree may still hold binaries for retired targets —
  `test_route_forward`, `test_dynamic_forward`, `test_runtime_spine`, `test_auto_qos`,
  `test_discovery_smoke` — whose coverage moved to `test_e2e/` under D52. Only the four
  registered with `add_test` are the suite.)

## Build & test

Connext 7.7.0, arch `x64Linux4gcc7.3.0` (see the repo `CLAUDE.md`).

```bash
export NDDSHOME=/home/rti/rti_connext_dds-7.7.0
cd router
cmake -B build -DCONNEXTDDS_ARCH=x64Linux4gcc7.3.0
cmake --build build
./run_tests.sh
```

**Use `run_tests.sh`, not `ctest --test-dir build`.** `--test-dir` requires CMake >= 3.20;
this VM has 3.16.3, which ignores the flag, scans the current directory, prints `No tests
were found!!!` — and **exits 0**. A gate using it reports success having run nothing.
`run_tests.sh` cds into the build tree (works on any ctest) and fails a zero-test run.

`router_main` must be run with **cwd = repo root**, not `router/` — the `types.xml`/
`qos_libraries` paths in the config files are repo-root-relative:

```bash
# from the repo root
./router/build/router_main --config router/config/e2e_control_command.yaml --role control
./router/build/router_main --config router/config/e2e_control_command.yaml --role platform
```

Python end-to-end tests (build first, then from the repo root):

```bash
pytest router/test_e2e -v
```

Building in-tree on the vboxsf share is fine (sources + executables). Per the repo
filesystem rules, any **runtime** artifacts (SQLite/DWH, FIFOs, logs) must go to a local
fs — the identity unit test and the Python e2e suite already write their temp fixtures
under `/tmp`.
