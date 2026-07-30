"""End-to-end Phase 7d: the FULL production control-platform.yaml, two-process pair.

Milestone 2 (D59/D63/D66): the real config — not a trimmed e2e fixture — loaded twice by
role, exactly as it is meant to be deployed:

  control-role router_main  (control_lan 20 <-> control_wan 200, admin control_lan)
  platform-role router_main (platform_wan 200 <-> platform_lan 30, admin platform_lan)

Everything Phase 7 delivered is load-bearing here at once: named QoS aliases on every WAN
leg + both *_wan_udpv4_qos participant profiles (7a/D65), CONTROL/PLATFORM publisher/
subscriber partitions on the WAN legs (7b/D69), per-topic wire-learned types with the
multi-topic platform_events route (7c/D70), create-and-observe matching (7m/D67), and the
7d/D63 counter path.

Asserts the plan's E5/E6 evidence:
  E5  all three enabled routes cross the WAN without Routing Service:
      control_command (control app -> platform app, CFT msg.destination),
      platform_init_status (platform app -> control app),
      platform_events (BOTH PlatformCommandAck and ContactReport, one route);
      platform_detail_status rides along DISABLED (toggled in
      test_detail_status_toggle.py).
  E6  per-route samples_forwarded advances in RouterStatus while state_revision does NOT
      move — the D63 periodic refresh republish, the one sanctioned D5 exception.

The config's domains are its literal production values (20/30/200) — no placeholder
rendering happens (render_config passes the file through unchanged), and the suite's
unique_domains spreading starts at 40, so nothing else collides. Run from the repo root.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import set_wan_qos_env, REPO_ROOT  # noqa: E402
set_wan_qos_env()

import rti.connextdds as dds  # noqa: E402

from util.dds_probe import (  # noqa: E402
    Probe, reader_qos, read_route_facts, wait_for_route, write_until_seen)

CONFIG = "control-platform.yaml"
# The production config's literal domains (participants: in the YAML).
CONTROL_LAN_DOMAIN = 20
PLATFORM_LAN_DOMAIN = 30

PLATFORM_NODE = "Platform_30"  # config node.name; the control_command CFT parameter

QOS_LIB_FILES = [
    "harness_v2/qos/lan_qos_lib.xml",
    "harness_v2/qos/wan_qos_lib.xml",
    "relay/qos_isc.xml",
]

ENABLED_ROUTES = ["control_command", "platform_init_status", "platform_events"]


def _all_topics_forwarding(facts):
    return (facts["state"] == "ROUTE_ENABLED"
            and facts["topics"]
            and all(row["topic_state"] == "TOPIC_FORWARDING"
                    for row in facts["topics"].values()))


def test_full_control_platform_config(
        router_pair, admin_types_xml, unique_domains):
    control_proc, platform_proc, _ = router_pair(CONFIG, unique_domains)
    alive = lambda: control_proc.is_alive() and platform_proc.is_alive()  # noqa: E731

    status_type = dds.QosProvider(str(admin_types_xml)).type("RouterStatus")
    # Same production QoS libs the router resolves its aliases against: the app-side
    # PlatformInitStatus writer uses the profile family the route's lan_status_1hz
    # input reader resolves to, guaranteeing RxO compatibility (the 7a technique).
    qos_prov = dds.QosProvider(";".join(str(REPO_ROOT / f) for f in QOS_LIB_FILES))
    status_writer_qos = qos_prov.datawriter_qos_from_profile(
        "LAN_QOS_LIB::status_1sec_qos")

    control_app = Probe(CONTROL_LAN_DOMAIN)
    platform_app = Probe(PLATFORM_LAN_DOMAIN)
    try:
        control_status = control_app.reader(
            "ActRouterStatus", "RouterStatus",
            qos=reader_qos(reliability="reliable", durability="transient_local"),
            dtype=status_type)
        platform_status = platform_app.reader(
            "ActRouterStatus", "RouterStatus",
            qos=reader_qos(reliability="reliable", durability="transient_local"),
            dtype=status_type)

        # App-side endpoints for every route topic, all created up front: each LAN
        # endpoint's inline SEDP type object teaches its local router the topic's type
        # (7c/D70 — writers and readers both teach), so both routers can build all
        # three enabled routes without any local type XML.
        cmd_writer = control_app.writer("ControlCommand", "control_command")
        cmd_reader = platform_app.reader("ControlCommand", "control_command")
        pstatus_writer = platform_app.writer(
            "PlatformInitStatus", "platform_init_status",
            qos=status_writer_qos)
        pstatus_reader = control_app.reader(
            "PlatformInitStatus", "platform_init_status")
        ack_writer = platform_app.writer("PlatformCommandAck", "control_command_ack")
        ack_reader = control_app.reader("PlatformCommandAck", "control_command_ack")
        contact_writer = platform_app.writer("ContactReport", "contact_report")
        contact_reader = control_app.reader("ContactReport", "contact_report")

        # All three enabled routes build and forward on BOTH routers (every topic row
        # TOPIC_FORWARDING — for platform_events that is two wire-learned types in one
        # route/process, E4's mechanism on the production config). Generous timeout:
        # the destination-side router learns each WAN topic's type only after the
        # source-side router's WAN writer appears.
        for reader, router_name in ((platform_status, "platform"),
                                    (control_status, "control")):
            for route in ENABLED_ROUTES:
                facts = wait_for_route(reader, route, _all_topics_forwarding,
                                       timeout_s=45.0, check_alive=alive)
                assert facts is not None and _all_topics_forwarding(facts), (
                    f"route {route!r} never fully forwarding on the {router_name} "
                    f"router; got {facts}; logs: control={control_proc.log_path} "
                    f"platform={platform_proc.log_path}")

        # D120: platform_detail_status source side (platform router) starts DISABLED;
        # destination side (control router) starts enabled but may be WAITING_FOR_DISCOVERY
        # (type not yet propagated since no detail writer exists in this test).
        plat_detail = read_route_facts(platform_status, "platform_detail_status")
        assert plat_detail is not None and plat_detail["state"] == "ROUTE_DISABLED", (
            f"platform_detail_status expected DISABLED on platform router, got "
            f"{plat_detail}")
        ctrl_detail = wait_for_route(control_status, "platform_detail_status",
                                     lambda f: f["state"] != "ROUTE_DISABLED",
                                     timeout_s=30.0, check_alive=alive)
        assert ctrl_detail is not None and ctrl_detail["state"] != "ROUTE_DISABLED", (
            f"platform_detail_status expected non-DISABLED on control router (D120), got "
            f"{ctrl_detail}")

        # (E5) control_command crosses control_lan -> WAN -> platform_lan, through the
        # destination-side CFT (msg.destination = 'Platform_30').
        buckets = write_until_seen(
            lambda: cmd_writer.write(control_app.sample(
                "control_command",
                **{"msg.destination": PLATFORM_NODE, "msg.source": "Control_20"})),
            cmd_reader,
            classify=lambda d: d["msg.destination"], stop_key=PLATFORM_NODE,
            check_alive=alive)
        assert buckets.get(PLATFORM_NODE), (
            f"ControlCommand never crossed the WAN; logs: "
            f"control={control_proc.log_path} platform={platform_proc.log_path}")

        # (E5) platform_init_status crosses platform_lan -> WAN -> control_lan.
        buckets = write_until_seen(
            lambda: pstatus_writer.write(platform_app.sample(
                "platform_init_status", **{"source": PLATFORM_NODE})),
            pstatus_reader,
            classify=lambda d: d["source"], stop_key=PLATFORM_NODE,
            check_alive=alive)
        assert buckets.get(PLATFORM_NODE), (
            f"PlatformInitStatus never crossed the WAN; logs: "
            f"control={control_proc.log_path} platform={platform_proc.log_path}")

        # (E5) platform_events carries BOTH topics through ONE route.
        # control_command_ack still wraps base_type msg; contact_report is now flat.
        for writer, reader, type_name, label, src_field in (
                (ack_writer, ack_reader, "control_command_ack", "PlatformCommandAck", "msg.source"),
                (contact_writer, contact_reader, "contact_report", "ContactReport", "source")):
            buckets = write_until_seen(
                lambda w=writer, t=type_name, sf=src_field: w.write(platform_app.sample(
                    t, **{sf: PLATFORM_NODE})),
                reader,
                classify=lambda d, sf=src_field: d[sf], stop_key=PLATFORM_NODE,
                check_alive=alive)
            assert buckets.get(PLATFORM_NODE), (
                f"{label} never crossed the WAN (platform_events route); logs: "
                f"control={control_proc.log_path} platform={platform_proc.log_path}")

        # (E6) samples_forwarded advances at the D63 refresh cadence while
        # state_revision does NOT move. All entities are up and matched by now, so
        # nothing else should touch the revision inside the window. The route input
        # resolves lan_status_1hz (a 1 s time_based_filter), so keep writing and give
        # the counter a couple of tick periods to visibly advance.
        def _pstatus_count(facts):
            return facts["topics"].get("PlatformInitStatus",
                                       {}).get("samples_forwarded", 0)

        first = wait_for_route(
            platform_status, "platform_init_status",
            lambda f: _pstatus_count(f) >= 1,
            timeout_s=15.0, check_alive=alive)
        assert first is not None, (
            f"samples_forwarded never reached 1 on the platform router — the D63 "
            f"refresh tick is not publishing counters; log {platform_proc.log_path}")
        c1 = _pstatus_count(first)
        r1 = first["state_revision"]

        deadline = time.monotonic() + 12.0
        second = None
        while time.monotonic() < deadline:
            assert alive(), (f"router exited during the counter window; logs: "
                             f"control={control_proc.log_path} "
                             f"platform={platform_proc.log_path}")
            pstatus_writer.write(platform_app.sample(
                "platform_init_status", **{"source": PLATFORM_NODE}))
            facts = read_route_facts(platform_status, "platform_init_status")
            if facts is not None and _pstatus_count(facts) > c1:
                second = facts
                break
            time.sleep(0.2)
        assert second is not None, (
            f"samples_forwarded never advanced past {c1} — the refresh tick stopped "
            f"republishing; log {platform_proc.log_path}")
        assert second["state_revision"] == r1, (
            f"state_revision moved ({r1} -> {second['state_revision']}) during a "
            f"counter-only window — counters must republish WITHOUT a revision bump "
            f"(D63); log {platform_proc.log_path}")

        # The WAN-crossing leg counts too: the control router forwarded the same flow
        # on its destination side.
        control_counts = wait_for_route(
            control_status, "platform_init_status",
            lambda f: _pstatus_count(f) >= 1, timeout_s=10.0, check_alive=alive)
        assert control_counts is not None and _pstatus_count(control_counts) >= 1, (
            f"control router's platform_init_status counter never advanced: "
            f"{control_counts}; log {control_proc.log_path}")
    finally:
        control_app.close()
        platform_app.close()
