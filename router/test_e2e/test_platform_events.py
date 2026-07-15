"""End-to-end 7c: multi-type forwarding from one router process via wire-learned types
(D62 as reshaped by D64/D70; plan's E4).

ONE router_main process (config/e2e_platform_events.yaml), ONE route (events_r1) with
TWO topics of DIFFERENT types:

  - PlatformCommandAck: ExampleCommand from router/config/example_types.xml — loaded by
    the TEST PEERS only (the router's config names no types.xml at all);
  - ContactReport: a type this test builds PROGRAMMATICALLY (dds.StructType) that exists
    in no XML anywhere — the strongest form of the reference-only data model (D35): the
    router forwards a type it has never seen outside discovery.

Both types reach the router exclusively as inline SEDP type objects
(type_learned_from_discovery, D70); `router.type_name` is retired and one
DynamicRouteFactory serves both topics. Asserts:

  E4a  both topics build once their writers teach their types, and BOTH reach
       TOPIC_FORWARDING inside one route in one process;
  E4b  a sample of each type forwards end-to-end (per-topic matched counts >= 1);
  E4c  the two topics resolve independently — the ExampleCommand topic forwards while
       the programmatic topic's writer does not exist yet.

Run from the repo root (see router/test_e2e/README.md).
"""

import sys
import time
from pathlib import Path

import rti.connextdds as dds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import start_router, render_config  # noqa: E402
from util.dds_probe import (  # noqa: E402
    Probe, reader_qos, writer_qos, wait_for_route)

ROUTE = "events_r1"
ACK_TOPIC = "PlatformCommandAck"
ACK_TYPE = "ExampleCommand"
REPORT_TOPIC = "ContactReport"
EXAMPLE_TYPES_XML = "router/config/example_types.xml"


def _report_type():
    """A type that exists in NO XML: built programmatically, taught to the router only
    through its inline SEDP type object (D70)."""
    t = dds.StructType("ContactReportType")
    t.add_member(dds.Member("source", dds.StringType(64)))
    t.add_member(dds.Member("track_id", dds.Int32Type()))
    t.add_member(dds.Member("bearing_deg", dds.Float64Type()))
    return t


def _forward_one(alive, writer, sample, reader, timeout_s=15.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        assert alive(), "router exited early"
        writer.write(sample)
        for s in reader.take():
            if s.info.valid:
                return s.data
        time.sleep(0.2)
    return None


def test_two_types_forward_from_one_process(
        router_binary, admin_types_xml, e2e_tmp_dir, unique_domains):
    config_path = render_config("e2e_platform_events.yaml", unique_domains, e2e_tmp_dir)
    router = start_router(router_binary, config_path, "platform", e2e_tmp_dir,
                          admin_participant="wan_in")

    in_domain = unique_domains["control_lan"]    # wan_in (route input + admin/status)
    out_domain = unique_domains["platform_lan"]  # lan_out (route output)

    ack_type = dds.QosProvider(EXAMPLE_TYPES_XML).type(ACK_TYPE)
    report_type = _report_type()
    status_type = dds.QosProvider(str(admin_types_xml)).type("RouterStatus")

    # The route's "default"-alias legs are RELIABLE+TRANSIENT_LOCAL — app endpoints
    # must offer/request compatibly (D67 lesson).
    compatible_w = writer_qos(reliability="reliable", durability="transient_local")
    compatible_r = reader_qos(reliability="reliable", durability="transient_local")

    in_probe = Probe(in_domain)
    out_probe = Probe(out_domain)
    alive = lambda: router.is_alive()  # noqa: E731
    try:
        status_reader = in_probe.reader(
            "ActRouterStatus", "RouterStatus",
            qos=reader_qos(reliability="reliable", durability="transient_local"),
            dtype=status_type)

        # (E4c) The first topic's writer appears alone: PlatformCommandAck builds and
        # forwards while ContactReport (no writer yet, no type yet) stays IDLE.
        ack_writer = in_probe.writer(ACK_TOPIC, ACK_TYPE, qos=compatible_w,
                                     dtype=ack_type)
        ack_reader = out_probe.reader(ACK_TOPIC, ACK_TYPE, qos=compatible_r,
                                      dtype=ack_type)
        first = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["topics"].get(ACK_TOPIC, {}).get("topic_state")
            == "TOPIC_FORWARDING", check_alive=alive)
        assert first is not None, \
            f"{ACK_TOPIC} never built after its writer taught the type; " \
            f"log {router.log_path}"
        assert first["topics"][REPORT_TOPIC]["topic_state"] == "TOPIC_IDLE", (
            f"{REPORT_TOPIC} built without a wire-learned type (D70 gate leaked); "
            f"got {first}; log {router.log_path}")

        ack_sample = dds.DynamicData(ack_type)
        ack_sample["msg.destination"] = "Control_20"
        ack_sample["msg.seq"] = 1
        got = _forward_one(alive, ack_writer, ack_sample, ack_reader)
        assert got is not None, \
            f"no {ACK_TOPIC} sample forwarded; log {router.log_path}"
        assert got["msg.seq"] == 1

        # (E4a/E4b) The programmatic type's endpoints appear: the router learns a type
        # that exists in no XML and forwards it — both topics now FORWARDING in the one
        # route/process.
        report_writer = in_probe.writer(REPORT_TOPIC, "ContactReportType",
                                        qos=compatible_w, dtype=report_type)
        report_reader = out_probe.reader(REPORT_TOPIC, "ContactReportType",
                                         qos=compatible_r, dtype=report_type)
        both = wait_for_route(
            status_reader, ROUTE,
            lambda f: all(f["topics"].get(t, {}).get("topic_state") == "TOPIC_FORWARDING"
                          for t in (ACK_TOPIC, REPORT_TOPIC)), check_alive=alive)
        assert both is not None, \
            f"{REPORT_TOPIC} never built from its programmatic wire type; " \
            f"log {router.log_path}"

        report_sample = dds.DynamicData(report_type)
        report_sample["source"] = "Platform_30"
        report_sample["track_id"] = 42
        report_sample["bearing_deg"] = 271.5
        got = _forward_one(alive, report_writer, report_sample, report_reader)
        assert got is not None, \
            f"no {REPORT_TOPIC} sample forwarded (programmatic type); " \
            f"log {router.log_path}"
        assert got["track_id"] == 42 and got["source"] == "Platform_30"

        final = wait_for_route(status_reader, ROUTE, lambda f: True, check_alive=alive)
        assert final["topics"][ACK_TOPIC]["input_matched"] >= 1
        assert final["topics"][REPORT_TOPIC]["input_matched"] >= 1
    finally:
        in_probe.close()
        out_probe.close()
        router.stop()
