---
name: run-mesh-dashboard
description: Launch a real router mesh + WIS + the Phase 16 mesh dashboard, then drive it with headless Playwright for actual browser verification (screenshots, click-through), not just hand-reading mesh_graph.js.
---

# Running gui/mesh_dashboard/ end to end

Captured 2026-07-22 after doing this by hand once (see
`/home/rti/.claude/projects/-home-rti-act-sim-scope-infra/memory/gui-visualization-wis-spike.md`
for the full narrative). This VM has no browser and no `chromium-cli` — Playwright's
Python package + its own bundled headless Chromium fill that gap with no sudo / system
package changes.

Design/background: `gui/mesh_dashboard/README.md` (production config, known limitations,
prior bugs found). This skill is the "make it actually render and prove it" recipe on top
of that doc's "Running it against a real mesh" section.

## Fast path: `harness_v2/scripts/run_mesh.sh` (use this — don't hand-run steps 1-3/5c/6)

Wraps steps 1-3, 5c, and 6 below into one script: builds the control node + N platform
routers (each on its own `platform_lan` domain per step 5c's convention) + N matching
`platform_sim`s, generates the WIS config, and launches the dashboard — all runtime
artifacts under one `--workdir` on local disk, all launched PIDs tracked in its
`pids.txt` for a precise teardown (never a name-based `pkill`, see step 6's warning).

```bash
cd /home/rti/act-sim-scope-infra
export NDDSHOME=/home/rti/rti_connext_dds-7.7.0
[[ -x router/build/router_main ]] || cmake --build router/build   # build once if missing

./harness_v2/scripts/run_mesh.sh up --platforms 5 --with-dashboard
# ^ prints "WORKDIR=<path>" on its last line — capture it, `down` needs the SAME one
```

Dashboard: `http://localhost:8080/`. Still do step 5 (Playwright) for an actual render +
console-error check — a 200 response doesn't prove the canvas drew anything.

Teardown (swap in the `WORKDIR` your `up` printed):

```bash
./harness_v2/scripts/run_mesh.sh down --workdir /tmp/act_mesh_run.XXXXXX
```

`--help` on the script prints the full flag reference (`--platforms` 1-70, `--workdir`,
`--verbosity`, `--dashboard-port`). Reach for the manual steps below only when this script
doesn't fit — a non-default topology, driving the admin channel directly (step 5b), or
debugging why the script itself failed.

## 0. One-time: get a headless browser in this env

Already done as of 2026-07-22 (persists in `~/.local` and `~/.cache` across sessions) —
skip straight to step 1 unless a fresh box:

```bash
python3 -m pip install --user playwright   # pure-Python wheel, no sudo
export PATH="$HOME/.local/bin:$PATH"
python3 -m playwright install chromium     # downloads to ~/.cache/ms-playwright, no sudo
```

## 1. Prerequisites

```bash
export NDDSHOME=/home/rti/rti_connext_dds-7.7.0
cd /home/rti/act-sim-scope-infra
cmake -B router/build -DCONNEXTDDS_ARCH=x64Linux4gcc7.3.0 -S router
cmake --build router/build   # only if router/build/router_main is missing/stale
```

## 2. Launch a real 2-node mesh (control-platform.yaml, verbatim production config)

Runtime artifacts (logs, generated WIS config) go local, never the vboxsf share
(repo `CLAUDE.md` filesystem-safety rule):

```bash
WORK="$(mktemp -d /tmp/mesh_dashboard_run.XXXXXX)"
```

`harness_v2/qos/{lan,wan}_qos_lib.xml` are templated and fail to parse without these env
vars — this is the exact validated set `router/test_e2e/conftest.py`'s
`WAN_QOS_ENV_DEFAULTS` uses (loopback peers, short WAN timeouts):

```bash
export NDDSHOME=/home/rti/rti_connext_dds-7.7.0
export RTI_LICENSE_FILE="$NDDSHOME/rti_license.dat"
export CONTROL_LAN_PEER1=127.0.0.1 CONTROL_LAN_PEER2=127.0.0.1 CONTROL_LAN_PEER3=127.0.0.1 CONTROL_WAN_PEER1=127.0.0.1
export PLATFORM_LAN_PEER1=127.0.0.1 PLATFORM_LAN_PEER2=127.0.0.1 PLATFORM_LAN_PEER3=127.0.0.1 PLATFORM_WAN_PEER1=127.0.0.1
export WAN_HB_PERIOD_SEC=1 WAN_HB_RETRIES=10 WAN_MAX_BLOCKING_SEC=1
export WAN_TIMEOUT_SEC=100 WAN_TTL=1 WAN_RECEIVE_MULTICAST=0
```

```bash
cd /home/rti/act-sim-scope-infra
nohup ./router/build/router_main --config router/config/control-platform.yaml \
    --role control --node-name Control_20 --name control-platform-run \
    --admin-participant control_lan > "$WORK/control.log" 2>&1 &
CONTROL_PID=$!

nohup ./router/build/router_main --config router/config/control-platform.yaml \
    --role platform --node-name Platform_30 --name platform-30-control-platform \
    --admin-participant platform_lan > "$WORK/platform.log" 2>&1 &
PLATFORM_PID=$!

sleep 3
tail -5 "$WORK/control.log"   # expect: router.start.ok ... presence_peer_alive
```

Both processes load the SAME file; identity comes entirely from `--role`/`--node-name`/
`--name`/`--admin-participant` (D79: one router per node, one shared config).

## 3. Generate + launch WIS

```bash
gui/mesh_dashboard/config/generate_wis_config.sh "$WORK/wis"

nohup "$NDDSHOME/bin/rtiwebintegrationservice" \
    -cfgFile "$WORK/wis/wis_config.xml" -cfgName mesh_dashboard \
    -listeningPorts 8080 -enableWebSockets \
    -documentRoot gui/mesh_dashboard/static \
    > "$WORK/wis.log" 2>&1 &
sleep 6
ss -tlnp | grep 8080   # confirm listening
```

## 4. Fast smoke check (curl, before spending time on a browser driver)

```bash
curl -s "http://localhost:8080/dds/rest1/applications/MeshDashboardApp/domain_participants/MeshParticipant/subscribers/MeshSubscriber/data_readers/MeshStatusReader?sampleFormat=json&removeFromReaderCache=false"
```

Expect a JSON array with `data.observer_node`, `data.peers[0].health.router`, etc.

**`[]` does NOT necessarily mean something is broken** (2026-07-22 finding, see README's
"Browser verification" section for full detail): WIS's REST seed only ever seems to
return brand-new/undelivered samples. If the mesh has been idle since the last roster/team
change, `[]` is expected — reproduced even right after a from-scratch WIS restart, and
confirmed NOT a QoS/config regression (a plain `rti.connextdds` Python reader built from
the same generated profile gets the TRANSIENT_LOCAL replay instantly every time; only
WIS's own reader reports 0 cached samples). Don't chase this as a bug — instead, trigger a
real change (e.g. an `ADD_PARTICIPANT_PARTITION` over the admin channel, see step 5b) and
confirm it arrives over the WebSocket instead; that path reliably works.

## 5. Drive it with Playwright — actual render + interaction proof

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    page.goto("http://localhost:8080/", wait_until="load")
    page.wait_for_timeout(3000)          # REST seed + vis-network layout settle
    page.screenshot(path="dashboard.png")

    # click a node (find its canvas coords from the screenshot first — vis-network's
    # force layout doesn't put nodes at fixed positions), then screenshot the detail panel
    page.mouse.click(768, 647)
    page.wait_for_timeout(1000)
    page.screenshot(path="dashboard_detail.png")

    print("console errors:", errors)     # must be empty
    browser.close()
```

Run with `PATH="$HOME/.local/bin:$PATH" python3 drive_dashboard.py` from anywhere (only
needs network access to `localhost:8080`). There's no `chromium-cli` in this environment,
so this is a plain Playwright script rather than the `chromium-cli`-stdin pattern the
general `run` skill defaults to for browser-driven apps.

Look at the screenshots, don't just check exit code — a blank canvas or a JS-error page
still returns 200 and an empty console-error list if nothing threw.

## 5b. Trigger a real live change (team assignment over the admin channel)

Useful both to prove the WebSocket-push path (see step 4's caveat) and to test the
ring/legend/filter-chip features, which need a team actually assigned. Reuses the same
`AdminChannel`/`COMMAND_KIND` helpers the e2e suite uses
(`router/test_e2e/util/dds_probe.py`) and the type XML already generated for WIS:

```python
import sys, time
sys.path.insert(0, "/home/rti/act-sim-scope-infra/router/test_e2e")
sys.path.insert(0, "/home/rti/act-sim-scope-infra/router/test_e2e/util")
import rti.connextdds as dds
from dds_probe import Probe, AdminChannel, COMMAND_KIND

provider = dds.QosProvider("/tmp/mesh_dashboard_run/wis/RouterAdminTypes.xml")  # reuse generated types
cmd_type = provider.type("RouterCommand")

def partition_cmd(node_name, router_name, command_id, partition_name):
    c = dds.DynamicData(cmd_type)
    c["target_node"] = node_name
    c["target_router"] = router_name
    c["command_id"] = command_id
    c["kind"] = COMMAND_KIND["ADD_PARTICIPANT_PARTITION"]
    c["participant_name"] = "platform_wan"  # D103: team_wan retired, absorbed into platform_wan
    c["partition_name"] = partition_name
    return c

probe = Probe(31)  # the TARGET platform's own platform_lan domain, not control_lan
chan = AdminChannel(probe, provider)
time.sleep(1.0)  # let cmd_writer/reader discover the router's admin participant
chan.cmd_writer.write(partition_cmd("Platform_31", "platform-31-control-platform", "t1", "TEAM_A"))
print(chan.acks.wait("t1", timeout_s=10.0))   # {'accepted': True, ...} on success
probe.close()
```

A page with an already-open WebSocket connection receives this live (confirmed:
`page.inner_text("body")` flips from the seed message to `"Live — last update ..."`).

## 5c. Scaling to N platforms

`control-platform.yaml` hardcodes `platform_lan: domain: 30` — fine for exactly one
platform process, but N platform processes on one machine need N distinct `platform_lan`
domains (they're meant to simulate physically separate LANs; reusing one domain would
merge them into the same multicast group). `control_wan`/`platform_wan` stay
domain 200 for all of them — that's the shared WAN, correctly common.

```bash
for ID in 31 32; do
  sed -e "s/Platform_30/Platform_${ID}/g" \
      -e "s/platform-30-control-platform/platform-${ID}-control-platform/g" \
      -e "0,/domain: 30/s//domain: ${ID}/" \
      router/config/control-platform.yaml > "$WORK/control-platform-${ID}.yaml"
  nohup ./router/build/router_main --config "$WORK/control-platform-${ID}.yaml" \
      --role platform --node-name "Platform_${ID}" --name "platform-${ID}-control-platform" \
      --admin-participant platform_lan > "$WORK/platform${ID}.log" 2>&1 &
done
```

For real app-layer traffic (not just router heartbeats) alongside each router, launch the
matching sim (`--id` must match the `platform_lan` domain by this convention):

```bash
for ID in 30 31 32; do
  ./harness_v2/scripts/start_platform_sim.sh --id "$ID" --destination Control_20 \
      > "$WORK/platform_sim_${ID}.log" 2>&1 &
done
```

**Caveat found scaling to 3 real sims, since FIXED (D96):** `PlatformPrimaryStatus` was
unkeyed — reading it on `control_lan` showed exactly 1 matched publication/1 visible value
at a time regardless of how many platforms were actively publishing, each overwriting the
last. Fixed by keying `platform_primary_status` on `msg.source` in
`harness_v2/datamodel/act_types.xml` (see `design-decisions.md` D96) — verified live,
`control_lan` now shows all N platforms' sources coexisting. **Every other
`base_type`-wrapped topic is still unkeyed** (deliberately, narrow fix) — same collapse
risk applies to `control_command`/`platform_status`/`platform_detail_status`/
`platform_data`/`contact_report` if a future scenario needs more than one source of those
visible at once. A live process must be **restarted** to pick up any type-XML change — it
parses the file once at startup, doesn't hot-reload.

## 6. Teardown

**This VM runs concurrent Claude sessions — a name-based `pkill -x router_main` or
`pkill -f platform_sim.py` can and has (2026-07-22) killed a DIFFERENT session's
`router_main` processes** (a separate git worktree's e2e test run, in that incident).
Track every PID at launch (`echo "label $!" >> "$WORK/pids.txt"`, as steps 2/3/5c above
already do) and kill exactly those:

```bash
kill $(awk '{print $2}' "$WORK/pids.txt")
```

`rtiwebintegrationservice` is a shell wrapper around a child process
(`rtiwebintegrationserviceapp`) that actually holds the port — killing only the wrapper
(the PID you launched with `nohup ... &`) leaves the port bound; you need the child PID
too (`ps aux | grep rtiwebintegrationservice` to find it, since the child isn't the PID
`$!` gave you for the wrapper).

Then check `/dev/shm` for stray `RTI*` segments per the repo's DDS-hygiene rule.
