# harness/ — Test/Sim Harness (internal only)

Stands up and perturbs an EMANE-emulated ACT network for scenario-driven and manual testing.
**Internal tooling** — not deployed to customers.

May import the shared aggregation library from `scope/` (one-way dependency); subscribes
independently to the sniffer's event bus for topology/GUIDs + traffic-based scenario triggers.

Planned contents:
- `act/` — **git submodule** → public `rticommunity/rticonnextdds-usecases-act` (kept pristine).
- `overrides/` — private QoS/sim overrides layered over ACT: LAN→UDP-only, seq#/timestamp
  payload instrumentation, `emane0`/loopback interface pinning, Monitoring Library 2.0.
- `docker/` — node image (`Dockerfile.node`, `entrypoint.sh`); `Dockerfile.remote-admin`
  is **reference only** (control uses Python `rti.connext` remote admin).
- `compose/` — `docker-compose.yml` (M0 plain bridge; EMANE control net added later).
- `emane/` — EMANE platform/NEM configs (CommEffect + RF Pipe), EEL scenario timelines.
- `backend/` — FastAPI control-plane (docker-py, EMANE events, remote admin) + scenario runner.
- `gui/` — NiceGUI control console (GUI A).
- `scenarios/` — S1–S6 definitions.

> ⚠️ The current `docker/` + `compose/` files are early drafts and **not yet buildable** —
> they predate the ACT-submodule + `overrides/` layout. See plan §2.4.

See [../docs/EMANE_SIMULATION_PLAN.md](../docs/EMANE_SIMULATION_PLAN.md).
