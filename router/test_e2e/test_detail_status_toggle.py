"""End-to-end Phase 7d E7: platform_detail_status toggled via the Phase 6 command loop.

Same production control-platform.yaml pair as test_control_platform_full.py. With D120
the destination (control) side ships `enabled: true` while the source (platform) side
remains `enabled: false`. End-to-end flow only begins once the platform router's source
leg is enabled (independent gating):

  1. platform router reports the route DISABLED; control router reports it ENABLED (D120).
     PlatformDetailStatus written on the platform LAN never reaches the control LAN
     because the source leg is still disabled.
  2. ENABLE_ROUTE at the PLATFORM router: its source-side leg builds and forwards to the
     WAN; the control router's destination leg is already live — data flows end-to-end.

Run from the repo root (see router/test_e2e/README.md).
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import set_wan_qos_env  # noqa: E402
set_wan_qos_env()

import rti.connextdds as dds  # noqa: E402

from util.dds_probe import AdminChannel, Probe, wait_for_route  # noqa: E402

CONFIG = "control-platform.yaml"
# Domain isolation (review 2026-08-11, H3): the production config declares the LIVE
# domains 20/200/30 and has no __DOMAIN_*__ placeholders, so this test used to run on
# them and could collide with a concurrent run_mesh.sh mesh. conftest.render_config()
# now rewrites those literals onto this test's unique_domains triple; read the fixture,
# never the literals.

ROUTE = "platform_detail_status"
TOPIC = "PlatformDetailStatus"
TYPE = "platform_detail_status"

# Router identities the D47 command CFTs key on: the platform process runs the file's
# own node/router names; the control process runs the overrides conftest.router_pair
# launches it with.
PLATFORM_NODE, PLATFORM_ROUTER = "Platform_30", "platform-30-control-platform"
CONTROL_NODE, CONTROL_ROUTER = "Control_20", "e2e-control-role"

KIND_ENABLE_ROUTE = 0  # RouterCommandKind ordinal (RouterAdminTypes.idl order)


def _enable(cmd_type, command_id, target_node, target_router):
    c = dds.DynamicData(cmd_type)
    c["target_node"] = target_node
    c["target_router"] = target_router
    c["command_id"] = command_id
    c["kind"] = KIND_ENABLE_ROUTE
    c["route_name"] = ROUTE
    return c


def _assert_no_flow(writer, reader, sample, alive, window_s, why):
    """Write for window_s and assert nothing valid arrives at the reader."""
    deadline = time.monotonic() + window_s
    while time.monotonic() < deadline:
        assert alive(), f"router exited during a no-flow window ({why})"
        writer.write(sample)
        for s in reader.take():
            assert not s.info.valid, (
                f"PlatformDetailStatus arrived at the control LAN while {why}")
        time.sleep(0.2)


def test_detail_status_flow_starts_from_the_target_only(
        router_pair, admin_types_xml, unique_domains):
    control_proc, platform_proc, _ = router_pair(CONFIG, unique_domains)
    alive = lambda: control_proc.is_alive() and platform_proc.is_alive()  # noqa: E731

    provider = dds.QosProvider(str(admin_types_xml))
    cmd_type = provider.type("RouterCommand")

    control_app = Probe(unique_domains["control_lan"])
    platform_app = Probe(unique_domains["platform_lan"])
    try:
        control_admin = AdminChannel(control_app, provider)
        platform_admin = AdminChannel(platform_app, provider)

        # The detail-status app endpoints exist from the start — their inline types
        # teach both routers the topic (7c/D70) whether or not the route is enabled.
        detail_writer = platform_app.writer(TOPIC, TYPE)
        detail_reader = control_app.reader(TOPIC, TYPE)
        sample = platform_app.sample(TYPE, **{"source": PLATFORM_NODE})

        # (1) D120: platform source side starts DISABLED, control destination side
        # starts ENABLED. No end-to-end flow because the source leg is still off.
        facts = wait_for_route(platform_admin.status, ROUTE,
                               lambda f: f["state"] == "ROUTE_DISABLED",
                               check_alive=alive)
        assert facts is not None and facts["state"] == "ROUTE_DISABLED", (
            f"{ROUTE} should start DISABLED on the platform router, got "
            f"{facts}; log {platform_proc.log_path}")

        ctrl_facts = wait_for_route(control_admin.status, ROUTE,
                                    lambda f: f["state"] == "ROUTE_ENABLED",
                                    timeout_s=30.0, check_alive=alive)
        assert ctrl_facts is not None and ctrl_facts["state"] == "ROUTE_ENABLED", (
            f"{ROUTE} should start ENABLED on the control router (D120), got "
            f"{ctrl_facts}; log {control_proc.log_path}")

        _assert_no_flow(detail_writer, detail_reader, sample, alive, 2.0,
                        "the platform source leg is disabled")

        # (2) ENABLE_ROUTE at the platform router: source leg builds, and since the
        # control destination leg is already live (D120), data flows end-to-end
        # immediately.
        platform_admin.cmd_writer.write(_enable(
            cmd_type, "detail-enable-platform", PLATFORM_NODE, PLATFORM_ROUTER))
        ack = platform_admin.acks.wait("detail-enable-platform", check_alive=alive)
        assert ack is not None and ack["accepted"], (
            f"platform ENABLE_ROUTE not accepted: {ack}; "
            f"log {platform_proc.log_path}")
        facts = wait_for_route(
            platform_admin.status, ROUTE,
            lambda f: f["state"] == "ROUTE_ENABLED" and f["input_matched"] >= 1,
            timeout_s=30.0, check_alive=alive)
        assert facts is not None and facts["state"] == "ROUTE_ENABLED", (
            f"platform {ROUTE} never built after ENABLE_ROUTE: {facts}; "
            f"log {platform_proc.log_path}")

        # Data should now flow end-to-end (control side was already enabled).
        got = None
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline and got is None:
            assert alive(), (f"router exited waiting for detail flow; logs: "
                             f"control={control_proc.log_path} "
                             f"platform={platform_proc.log_path}")
            detail_writer.write(sample)
            for s in detail_reader.take():
                if s.info.valid:
                    got = s.data
                    break
            time.sleep(0.2)
        assert got is not None, (
            f"PlatformDetailStatus never reached the control LAN after enabling the "
            f"platform source leg; logs: control={control_proc.log_path} "
            f"platform={platform_proc.log_path}")
        assert got["source"] == PLATFORM_NODE
    finally:
        control_app.close()
        platform_app.close()
