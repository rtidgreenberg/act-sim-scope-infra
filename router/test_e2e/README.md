# Router Python end-to-end tests

Real black-box tests: launch two real `router_main` subprocesses (control-role,
platform-role) against a trimmed config, then simulate app-side publish/subscribe in
Python (`util/dds_probe.py`) and assert data actually flows through the running router
binary and the real WAN hop between them.

This is a different layer than `router/test/*` (CTest, C++): those construct the router's
library pieces directly in one process; this suite drives the real, separately-built
executable as two OS processes, closer to a real deployment.

## Prerequisites

```bash
export NDDSHOME=/home/rti/rti_connext_dds-7.7.0
cd router
cmake -B build -DCONNEXTDDS_ARCH=x64Linux4gcc7.3.0
cmake --build build
```

`rti.connextdds` (the Python API) must be importable — already installed in this
environment.

## Run

From the **repo root** (paths in the configs are repo-root-relative):

```bash
pytest router/test_e2e -v
```

## What this covers today

Both tests use dedicated fixtures (`router/config/e2e_control_command.yaml`,
`router/config/e2e_platform_status.yaml`), not the production `control-platform.yaml`/
`platform-team.yaml` — see `docs/cpp_router/design-decisions.md` D50 for why:

- **`test_control_command_route.py`** — control app publishes `ControlCommand`; asserts
  it's forwarded to the addressed platform and filtered out for every other destination
  (the `msg.destination` ContentFilteredTopic).
- **`test_platform_status_route.py`** — platform app publishes `PlatformPrimaryStatus`;
  asserts it's forwarded to the control app.
- **`test_discovery_startup.py`** — regression test for the D52 disabled-startup fix:
  launches the router pair on fresh domains and asserts both sides discover each other's
  participant *promptly* (within 10s, far below the 30s SPDP retry), parametrized over 6
  iterations to expose the race if it regresses. Deliberately bypasses `router_pair`'s
  `wait_for_mutual_discovery` so the mitigation can't mask a regression.

## Explicitly NOT covered here

- **Phase 6** (command/status DDS control loop) — `ENABLE_ROUTE`/`DISABLE_ROUTE` over DDS,
  aggregate status after commands. Not implemented yet.
- **Phase 7** (QoS-library/XML alias lookup, D45) — named QoS aliases (`wan_event`,
  `wan_status`, ...) are unresolvable; the e2e fixtures use `qos: ""` (the real Phase 5
  auto-QoS), not the production aliases.
- **D34/D35 multi-type dispatch** — `DynamicRouteFactory` binds one type per process, so
  the production `control-platform.yaml`'s multi-topic `platform_events` route
  (`PlatformCommandAck` + `ContactReport`, two types) can't run as a single process yet.
- **Phase 8** (team partitions) — `platform-team.yaml`'s flat route shape isn't parsed by
  `RouteConfigParser` yet.
- Phase 9/10 (serialized-CDR fast path, keyed lifecycle mirroring).

## Real bugs this suite found (D51)

Getting these two fixtures to actually forward data surfaced two genuine bugs, not
test-fixture quirks — see `docs/cpp_router/design-decisions.md` D51 for the full analysis:

1. **Auto-QoS output-readiness deadlock.** A Phase 5 auto-QoS ("") output writer won't
   build until it has already discovered a matched reader, which two *independent*
   `router_main` processes each waiting on the other's not-yet-built WAN entity can never
   satisfy. Both fixtures route around it with `writer_qos: default` on the WAN-crossing
   output leg instead of `""` — a real, general limitation (not just these fixtures), though
   `control-platform.yaml` doesn't hit it today since its WAN legs already use non-empty
   named aliases.
2. **A real discovery bug in the router's own code — FIXED in D52.** Two `router_main`
   processes started back-to-back sometimes had one never see the other's participant at
   all (~50%+ flake). This was initially suspected to be Connext's default 30s SPDP
   re-announcement period colliding with ordinary process-spawn timing — but two standalone
   Python probes (no router code at all, one matching `router_main`'s exact
   2-participant/LAN+WAN-domain shape) discovered each other in under 1.1s in 11/11 runs,
   ruling out the environment. Root cause (confirmed in D52): `router_main` enabled its
   participants at construction, *before* attaching the builtin-reader conditions and
   starting the `AsyncWaitSet` — a peer's SPDP arriving in that window set a condition
   trigger the edge-triggered AWS never dispatched, stranding the participant. Fixed by
   disabled startup: create participants disabled, attach conditions, `aws.start()`, then
   `enable_all()`. `wait_for_mutual_discovery()` remains in `conftest.py` as
   belt-and-suspenders (no longer load-bearing); `test_discovery_startup.py` guards against
   regression. See `docs/cpp_router/design-decisions.md` D52.

## Notes

- Runtime artifacts (rendered per-test configs, subprocess logs) go under `/tmp/router_e2e/`
  — never the repo/share, per the repo's filesystem-safety rule.
- **Test isolation is domain-id-only for now.** Each test gets unique DDS domain ids
  (`conftest.py`'s `unique_domains` fixture) so tests within one pytest run don't collide.
  A stronger, DomainParticipant-level **PARTITION** per test run (an RTI extension to
  `DomainParticipantQos` — mismatched participant partitions block discovery entirely,
  so it also guards against two *concurrent pytest processes* landing on the same
  domains) is deferred to a later pass, once the router_main wiring + e2e suite are
  confirmed working end-to-end.
- Router processes are UDPv4-only and torn down with `SIGTERM` (falling back to `SIGKILL`)
  on teardown — no stray `/dev/shm` segments expected.
- `write_until_seen`'s `check_alive` callback (both tests pass
  `lambda: control_proc.is_alive() and platform_proc.is_alive()`) fails a test immediately
  if either router process crashes, instead of waiting out the full poll timeout.
