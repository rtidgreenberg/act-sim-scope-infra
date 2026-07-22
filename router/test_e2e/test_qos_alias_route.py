"""End-to-end Phase 7a QoS-alias XML resolution (D60; plan's E1/E2).

Python port of the mechanism spikes/qos_alias/qos_alias_spike.py proved standalone, now
driven through the real router_main binary (config/e2e_qos_alias.yaml): ONE router, route
qos_alias_r1, named XML aliases on BOTH legs — reader_qos: wan_status / writer_qos:
wan_event — plus both wan_*_udpv4_qos participant profiles applied to wan_in/wan_out. Uses
the real production QoS libraries (harness_v2/qos/{lan,wan}_qos_lib.xml,
relay/qos_isc.xml) and real alias names, not a synthetic stand-in.

Asserts the plan's E1/E2 evidence:
  1. a route using wan_status/wan_event aliases forwards a sample end-to-end.
  2. the resolved QoS summaries riding RouterStatus show the named profiles' actual
     policies (BEST_EFFORT for the wan_status-resolved reader, RELIABLE for the
     wan_event-resolved writer) — not the D39 auto-derived baseline.

Run from the repo root (see router/test_e2e/README.md).
"""

import sys
import time
from pathlib import Path

# The three qos_libraries: files this config loads are templated with 14 env vars (peer
# locators + WAN tuning) — set before importing rti.connextdds / launching the router
# subprocess (which inherits this process's env). Shared defaults live in conftest
# (WAN_QOS_ENV_DEFAULTS); see docs/cpp_router/design-decisions.md D60/D65.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import set_wan_qos_env, start_router, render_config, REPO_ROOT  # noqa: E402
set_wan_qos_env()

import rti.connextdds as dds  # noqa: E402
from util.dds_probe import Probe, reader_qos, wait_for_route  # noqa: E402

TOPIC = "QosAliasCmd"
TYPE = "ExampleCommand"
ROUTE = "qos_alias_r1"
EXAMPLE_TYPES_XML = "router/config/example_types.xml"
WAN_QOS_LIB_FILES = [
    "harness_v2/qos/lan_qos_lib.xml",
    "harness_v2/qos/wan_qos_lib.xml",
    "relay/qos_isc.xml",
]


def _cmd(cmd_type, destination, seq):
    d = dds.DynamicData(cmd_type)
    d["msg.destination"] = destination
    d["msg.seq"] = seq
    return d


def test_qos_alias_route_resolves_and_forwards(
        router_binary, admin_types_xml, e2e_tmp_dir, unique_domains):
    config_path = render_config("e2e_qos_alias.yaml", unique_domains, e2e_tmp_dir)
    router = start_router(router_binary, config_path, "platform", e2e_tmp_dir,
                          admin_participant="wan_in")

    in_domain = unique_domains["control_lan"]    # wan_in (router input + admin/status)
    out_domain = unique_domains["platform_lan"]  # wan_out (router output)

    cmd_type = dds.QosProvider(EXAMPLE_TYPES_XML).type(TYPE)
    status_type = dds.QosProvider(str(admin_types_xml)).type("RouterStatus")

    # Same production QoS-library provider the router resolves aliases against — building
    # the app-side peer QoS from the SAME named profiles (not hand-guessed policies)
    # guarantees RxO compatibility, the technique spikes/qos_alias/ already validated.
    qos_files = [str(REPO_ROOT / f) for f in WAN_QOS_LIB_FILES]
    qos_prov = dds.QosProvider(";".join(qos_files))
    app_writer_qos = qos_prov.datawriter_qos_from_profile("WAN_QOS_LIB::status_qos")
    app_reader_qos = qos_prov.datareader_qos_from_profile("WAN_QOS_LIB::event_qos")

    in_probe = Probe(in_domain)
    out_probe = Probe(out_domain)
    alive = lambda: router.is_alive()  # noqa: E731
    try:
        status_reader = in_probe.reader(
            "ActRouterStatus", "RouterStatus",
            qos=reader_qos(reliability="reliable", durability="transient_local"),
            dtype=status_type)

        # App writer: same wan_status profile the router's input reader (wan_status alias)
        # resolves to (E1/E2's "a route using wan_status/wan_event aliases forwards").
        src_writer = in_probe.writer(TOPIC, TYPE, qos=app_writer_qos, dtype=cmd_type)
        # App reader: same wan_event profile the router's output writer (wan_event alias)
        # resolves to.
        dst_reader = out_probe.reader(TOPIC, TYPE, qos=app_reader_qos, dtype=cmd_type)

        facts = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["state"] in ("ROUTE_ENABLED", "ROUTE_DEGRADED"),
            check_alive=alive)
        assert facts is not None, (
            f"route '{ROUTE}' never reported a state; router logs: {router.log_path}")
        assert facts["state"] == "ROUTE_ENABLED", (
            f"expected ROUTE_ENABLED, got {facts} — router logs: {router.log_path}")

        # The resolved QoS summaries ride the status (D45): wan_status resolves to a
        # BEST_EFFORT reader, wan_event resolves to a RELIABLE writer — the named
        # profile's actual policies, not the D39 auto-derived baseline (RELIABLE input /
        # derived-deadline output).
        assert "BEST_EFFORT" in facts["reader_summary"], facts
        assert "RELIABLE" in facts["writer_summary"], facts

        # Forward a sample end-to-end.
        seq = 1
        deadline = time.monotonic() + 15.0
        got = None
        while time.monotonic() < deadline and got is None:
            assert alive(), f"router exited early; logs: {router.log_path}"
            src_writer.write(_cmd(cmd_type, "Control_20", seq))
            for sample in dst_reader.take():
                if sample.info.valid:
                    got = sample.data
                    break
            time.sleep(0.2)
        assert got is not None, (
            f"no sample forwarded through '{ROUTE}' — router logs: {router.log_path}")
        assert got["msg.seq"] == seq
    finally:
        in_probe.close()
        out_probe.close()
        router.stop()
