"""End-to-end 7m create-and-observe (D64/D66; plan's E-M).

ONE router_main process (config/e2e_auto_qos.yaml — auto QoS both legs, route auto_r1
enabled at startup) on otherwise-empty domains proves the matching-authority pivot:

  1. the route builds immediately at startup — no discovery gate — and reports
     ROUTE_ENABLED / TOPIC_FORWARDING with input_matched == output_matched == 0 and
     match_reason == "input_unmatched,output_unmatched" (created-but-unmatched is an
     observable zero, not a wait state and not a teardown).
  2. when an app writer and reader appear, the live build's own matched counts rise
     (DDS is the matching authority — SUBSCRIPTION_MATCHED/PUBLICATION_MATCHED via the
     entity StatusConditions), match_reason clears, and a sample forwards end-to-end.

Run from the repo root (see router/test_e2e/README.md).
"""

import sys
import time
from pathlib import Path

import rti.connextdds as dds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import start_router, render_config  # noqa: E402
from util.dds_probe import (  # noqa: E402
    Probe, reader_qos, wait_for_route)

TOPIC = "AutoQosCmd"
TYPE = "ExampleCommand"
ROUTE = "auto_r1"
EXAMPLE_TYPES_XML = "router/config/example_types.xml"


def _cmd(cmd_type, destination, seq):
    d = dds.DynamicData(cmd_type)
    d["msg.destination"] = destination
    d["msg.seq"] = seq
    return d


def test_route_builds_unmatched_then_observes_matches(
        router_binary, admin_types_xml, e2e_tmp_dir, unique_domains):
    config_path = render_config("e2e_auto_qos.yaml", unique_domains, e2e_tmp_dir)
    router = start_router(router_binary, config_path, "platform", e2e_tmp_dir,
                          admin_participant="wan_in")

    in_domain = unique_domains["control_lan"]    # wan_in (router input + admin/status)
    out_domain = unique_domains["platform_lan"]  # lan_out (router output)

    cmd_type = dds.QosProvider(EXAMPLE_TYPES_XML).type(TYPE)
    status_type = dds.QosProvider(str(admin_types_xml)).type("RouterStatus")

    in_probe = Probe(in_domain)
    out_probe = Probe(out_domain)
    alive = lambda: router.is_alive()  # noqa: E731
    try:
        status_reader = in_probe.reader(
            "ActRouterStatus", "RouterStatus",
            qos=reader_qos(reliability="reliable", durability="transient_local"),
            dtype=status_type)

        # (1) Built immediately, both legs an observable zero.
        zero = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["state"] == "ROUTE_ENABLED"
            and f["topic_state"] == "TOPIC_FORWARDING", check_alive=alive)
        assert zero is not None, \
            f"route never built at startup; log {router.log_path}"
        assert zero["input_matched"] == 0 and zero["output_matched"] == 0, \
            f"expected created-but-unmatched zeros; got {zero}; log {router.log_path}"
        assert zero["match_reason"] == "input_unmatched,output_unmatched", \
            f"match_reason {zero['match_reason']!r}; log {router.log_path}"

        # (2) Peers appear: the live entities' own matched counts rise; reason clears.
        src_writer = in_probe.writer(TOPIC, TYPE, dtype=cmd_type)
        sink = out_probe.reader(TOPIC, TYPE, dtype=cmd_type)

        ready = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["input_matched"] >= 1 and f["output_matched"] >= 1,
            check_alive=alive)
        assert ready is not None, \
            f"matched counts never rose after peers appeared; log {router.log_path}"
        assert ready["match_reason"] == "", \
            f"match_reason should clear once both legs match; got {ready}; " \
            f"log {router.log_path}"
        assert ready["state"] == "ROUTE_ENABLED", f"{ready}; log {router.log_path}"

        # A sample forwards through the observed-matched route.
        got = None
        deadline = time.monotonic() + 15.0
        seq = 1
        while time.monotonic() < deadline and got is None:
            assert alive(), f"router exited early; log {router.log_path}"
            src_writer.write(_cmd(cmd_type, "Platform_30", seq))
            for sample in sink.take():
                if sample.info.valid:
                    got = sample.data
                    break
            time.sleep(0.2)
        assert got is not None, \
            f"no sample forwarded through '{ROUTE}'; log {router.log_path}"
        assert got["msg.seq"] == seq
    finally:
        in_probe.close()
        out_probe.close()
        router.stop()
