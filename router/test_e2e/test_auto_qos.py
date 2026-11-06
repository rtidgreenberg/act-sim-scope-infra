"""Empty aliases use fixed production profiles, including under incompatible peers.

Normal participants and endpoints load the shared ACT QoS XML. Only deliberately
incompatible endpoints override those profiles to exercise deadline, durability,
ownership and liveliness warnings. Discovery never retunes the router's writer.
"""

import sys
import time
from pathlib import Path

import rti.connextdds as dds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import start_router, render_config, REPO_ROOT  # noqa: E402
from util.dds_probe import (  # noqa: E402
    Probe, reader_qos, writer_qos, incompatible_reader_qos, incompatible_writer_qos,
    read_route_facts, wait_for_route)

TOPIC = "AutoQosCmd"
TYPE = "ExampleCommand"
ROUTE = "auto_r1"
EXAMPLE_TYPES_XML = "router/config/example_types.xml"


def _cmd(cmd_type, destination, seq):
    d = dds.DynamicData(cmd_type)
    d["msg.destination"] = destination
    d["msg.seq"] = seq
    return d


def _drain_seen(reader):
    n = 0
    for s in reader.take():
        if s.info.valid:
            n += 1
    return n


def test_default_profiles_create_and_observe_and_incompatibility_warnings(
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
        # Status observer on the admin (input) domain; RELIABLE+TL to get the latest
        # KEEP_LAST state sample (D26).
        status_reader = in_probe.reader(
            "ActRouterStatus", "RouterStatus",
            qos=reader_qos("router_admin_status"),
            dtype=status_type)

        # (1) Wait-for-type (7c/D70): no endpoint exists, so no wire type and no build.
        waiting = wait_for_route(status_reader, ROUTE, lambda f: True, check_alive=alive)
        assert waiting is not None and waiting["state"] == "ROUTE_WAITING_FOR_DISCOVERY", \
            f"route should wait for its wire type; got {waiting}; log {router.log_path}"

        # (2a) BEST_EFFORT + VOLATILE source writer teaches the type AND matches the
        # weakest-request input reader (the F5 case): the route builds, input_matched
        # rises, output leg is the observable unmatched zero.
        src_writer = in_probe.writer(
            TOPIC, TYPE, qos=writer_qos("lan_status"),
            dtype=cmd_type)
        in_matched = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["input_matched"] >= 1, check_alive=alive)
        assert in_matched is not None and in_matched["input_matched"] >= 1, \
            f"BEST_EFFORT writer never matched the input reader; got {in_matched}; " \
            f"log {router.log_path}"
        assert in_matched["state"] == "ROUTE_ENABLED", in_matched
        assert in_matched["output_matched"] == 0, in_matched
        assert in_matched["match_reason"] == "output_unmatched", \
            f"match_reason {in_matched['match_reason']!r}; log {router.log_path}"

        # (2b/3) A plain RELIABLE+VOLATILE reader matches the strong-offer BASELINE
        # output writer (built with no readers known — no derived deadline/liveliness).
        sink = out_probe.reader(
            TOPIC, TYPE,
            qos=reader_qos("lan_event"),
            dtype=cmd_type)
        ready = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["output_matched"] >= 1, check_alive=alive)
        assert ready is not None and ready["output_matched"] >= 1, \
            f"sink reader never matched the output writer; got {ready}; log {router.log_path}"
        assert ready["match_reason"] == "", \
            f"match_reason should clear when both legs match; got {ready}; " \
            f"log {router.log_path}"
        assert ready["reader_summary"] == "BEST_EFFORT,VOLATILE", \
            f"reader summary {ready['reader_summary']!r}; log {router.log_path}"
        assert ready["writer_summary"] \
            == "RELIABLE,TRANSIENT_LOCAL,deadline=inf,liveliness=AUTOMATIC:inf", \
            f"writer summary should match the production default profile; " \
            f"got {ready['writer_summary']!r}; log {router.log_path}"

        # (2c) forwarding works through the auto route.
        received = 0
        for i in range(80):
            src_writer.write(_cmd(cmd_type, "Platform_30", i))
            time.sleep(0.1)
            received += _drain_seen(sink)
            if received:
                break
            assert alive(), f"router exited early; log {router.log_path}"
        assert received > 0, f"no sample forwarded through auto route; log {router.log_path}"

        # A deliberately incompatible reader cannot retune the production profile.
        tight = out_probe.reader(
            TOPIC, TYPE,
            qos=incompatible_reader_qos("lan_event", deadline_ms=500),
            dtype=cmd_type)
        deadline_warning = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["qos_warning"] == "writer:DEADLINE", check_alive=alive)
        assert deadline_warning is not None, f"DEADLINE warning missing; log {router.log_path}"
        assert deadline_warning["writer_summary"] == ready["writer_summary"], deadline_warning
        assert deadline_warning["state"] == "ROUTE_ENABLED", deadline_warning
        assert deadline_warning["output_matched"] == 1, deadline_warning
        assert "deadline_tightened" not in router.log_path.read_text()

        # (4a) A TRANSIENT-requesting reader draws a loud writer:DURABILITY warning.
        # Keep the reference: a dropped DataReader is GC'd and closed immediately (router
        # would log subscription_discovered then endpoint_lost in the same instant).
        transient_reader = out_probe.reader(  # noqa: F841 (must stay alive)
            TOPIC, TYPE, qos=incompatible_reader_qos("lan_event", durability="transient"),
            dtype=cmd_type)
        dur_warn = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["qos_warning"] == "writer:DURABILITY", check_alive=alive)
        assert dur_warn is not None and dur_warn["qos_warning"] == "writer:DURABILITY", \
            f"no writer:DURABILITY warning; got {dur_warn}; log {router.log_path}"
        assert dur_warn["state"] == "ROUTE_ENABLED", "DURABILITY should warn only, not disable"

        # (4b) An EXCLUSIVE-ownership source writer draws a reader:OWNERSHIP warning
        # (ownership RxO is equality; the SHARED route reader never matches it).
        exclusive_writer = in_probe.writer(  # noqa: F841 (must stay alive)
            TOPIC, TYPE, qos=incompatible_writer_qos("lan_event", ownership="exclusive"), dtype=cmd_type)
        own_warn = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["qos_warning"] == "reader:OWNERSHIP", check_alive=alive)
        assert own_warn is not None and own_warn["qos_warning"] == "reader:OWNERSHIP", \
            f"no reader:OWNERSHIP warning; got {own_warn}; log {router.log_path}"
        assert own_warn["state"] == "ROUTE_ENABLED", "OWNERSHIP should warn only, not disable"

        # A finite lease is incompatible with the XML default's infinite lease.
        # Recreating entities never derives a different policy from discovered peers.
        liveliness_reader = out_probe.reader(  # noqa: F841 (must stay alive)
            TOPIC, TYPE,
            qos=incompatible_reader_qos("lan_event",
                           liveliness_automatic_lease_ms=2000),
            dtype=cmd_type)
        liv_warn = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["qos_warning"] == "writer:LIVELINESS", check_alive=alive)
        assert liv_warn is not None and liv_warn["qos_warning"] == "writer:LIVELINESS", \
            f"no writer:LIVELINESS warning; got {liv_warn}; log {router.log_path}"
        assert liv_warn["state"] == "ROUTE_ENABLED", \
            "LIVELINESS should warn only, not disable"
    finally:
        in_probe.close()
        out_probe.close()
        router.stop()
