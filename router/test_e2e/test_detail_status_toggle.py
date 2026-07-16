"""End-to-end Phase 7d E7: platform_detail_status toggled via the Phase 6 command loop.

Same production control-platform.yaml pair as test_control_platform_full.py. The
platform_detail_status route ships `enabled: false` on BOTH routers, and the detail flow
starts only when ENABLE_ROUTE reaches the addressed router (the D47 CFT keys each
command on target_node/target_router, so each router's route is toggled independently):

  1. both routers report the route DISABLED; PlatformDetailStatus written on the
     platform LAN never reaches the control LAN.
  2. ENABLE_ROUTE at the PLATFORM router only: its source-side leg builds and forwards
     to the WAN (the app writer's inline type taught the topic, 7c/D70) — but the
     control router's destination leg is still disabled, so the flow still does not
     reach the control LAN ("from the target only": each leg gates independently).
  3. ENABLE_ROUTE at the CONTROL router too: the WAN-crossing chain completes and
     detail-status samples arrive at the control LAN.

Run from the repo root (see router/test_e2e/README.md).
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import set_wan_qos_env  # noqa: E402
set_wan_qos_env()

import rti.connextdds as dds  # noqa: E402

from util.dds_probe import (  # noqa: E402
    AckCollector, Probe, reader_qos, writer_qos, wait_for_route)

CONFIG = "control-platform.yaml"
CONTROL_LAN_DOMAIN = 20    # the production config's literal domains
PLATFORM_LAN_DOMAIN = 30

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


class _AdminChannel:
    """Command writer + ack collector + status reader on one router's admin domain."""

    def __init__(self, probe, provider):
        self.status = probe.reader(
            "ActRouterStatus", "RouterStatus",
            qos=reader_qos(reliability="reliable", durability="transient_local"),
            dtype=provider.type("RouterStatus"))
        self.cmd_writer = probe.writer(
            "ActRouterCommand", "RouterCommand",
            qos=writer_qos(reliability="reliable", durability="volatile"),
            dtype=provider.type("RouterCommand"))
        self.acks = AckCollector(probe.reader(
            "ActRouterCommandAck", "RouterCommandAck",
            qos=reader_qos(reliability="reliable", durability="volatile"),
            dtype=provider.type("RouterCommandAck")))


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

    control_app = Probe(CONTROL_LAN_DOMAIN)
    platform_app = Probe(PLATFORM_LAN_DOMAIN)
    try:
        control_admin = _AdminChannel(control_app, provider)
        platform_admin = _AdminChannel(platform_app, provider)

        # The detail-status app endpoints exist from the start — their inline types
        # teach both routers the topic (7c/D70) whether or not the route is enabled.
        detail_writer = platform_app.writer(TOPIC, TYPE)
        detail_reader = control_app.reader(TOPIC, TYPE)
        sample = platform_app.sample(TYPE, **{"msg.source": PLATFORM_NODE})

        # (1) DISABLED everywhere at startup; no flow.
        for admin, router_name in ((platform_admin, "platform"),
                                   (control_admin, "control")):
            facts = wait_for_route(admin.status, ROUTE,
                                   lambda f: f["state"] == "ROUTE_DISABLED",
                                   check_alive=alive)
            assert facts is not None and facts["state"] == "ROUTE_DISABLED", (
                f"{ROUTE} should start DISABLED on the {router_name} router, got "
                f"{facts}; logs: control={control_proc.log_path} "
                f"platform={platform_proc.log_path}")
        _assert_no_flow(detail_writer, detail_reader, sample, alive, 2.0,
                        "the route is disabled on both routers")

        # (2) enable at the platform router ONLY: its source leg forwards (app writer
        # matched), but the control leg is still disabled — still no end-to-end flow.
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
        off_target = wait_for_route(control_admin.status, ROUTE, lambda f: True,
                                    timeout_s=5.0, check_alive=alive)
        assert off_target is not None and off_target["state"] == "ROUTE_DISABLED", (
            f"the platform-addressed ENABLE_ROUTE leaked to the control router "
            f"(D47 CFT): {off_target}; log {control_proc.log_path}")
        _assert_no_flow(detail_writer, detail_reader, sample, alive, 2.0,
                        "only the platform router's route is enabled")

        # (3) enable at the control router too: the chain completes.
        control_admin.cmd_writer.write(_enable(
            cmd_type, "detail-enable-control", CONTROL_NODE, CONTROL_ROUTER))
        ack = control_admin.acks.wait("detail-enable-control", check_alive=alive)
        assert ack is not None and ack["accepted"], (
            f"control ENABLE_ROUTE not accepted: {ack}; log {control_proc.log_path}")
        facts = wait_for_route(
            control_admin.status, ROUTE,
            lambda f: f["state"] == "ROUTE_ENABLED",
            timeout_s=30.0, check_alive=alive)
        assert facts is not None and facts["state"] == "ROUTE_ENABLED", (
            f"control {ROUTE} never built after ENABLE_ROUTE: {facts}; "
            f"log {control_proc.log_path}")

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
            f"PlatformDetailStatus never reached the control LAN after both routes "
            f"were enabled; logs: control={control_proc.log_path} "
            f"platform={platform_proc.log_path}")
        assert got["msg.source"] == PLATFORM_NODE
    finally:
        control_app.close()
        platform_app.close()
