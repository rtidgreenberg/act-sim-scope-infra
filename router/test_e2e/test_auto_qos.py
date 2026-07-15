"""End-to-end Phase 5 asymmetric auto-QoS under create-and-observe (D39/D42/D45; D64/D66).

Python port of the retired C++ test/test_auto_qos.cxx, now driven through the real
router_main binary (config/e2e_auto_qos.yaml): ONE router, route auto_r1, auto ("") QoS
on both legs. The observable route facts are read off the router's RouterStatus stream
(RELIABLE+TRANSIENT_LOCAL, D26) as DynamicData, using a RouterStatus type generated from
the admin IDL at test time (admin_types_xml fixture).

Migrated by 7m (D66): the D51 output-readiness gate is retired — the route builds
immediately at startup with the strong baseline (no readers known yet), and the old
"route waits for a compatible reader" evidence becomes "route is ENABLED with an
observable zero" (created-but-unmatched counts + match_reason). Asserts:

  1. create-and-observe zero — the route is ENABLED with input/output matched counts 0
     and match_reason naming both unmatched legs, before any app endpoint exists.
  2. a BEST_EFFORT+VOLATILE app writer matches the weakest-request auto input reader
     (input_matched rises; the F5 no-match case) and a plain RELIABLE+VOLATILE reader
     matches the strong-offer baseline output writer (output_matched rises; reason
     clears); a sample forwards end-to-end.
  3. the resolved QoS summaries ride the status: weakest-request input; the output
     writer offers the BASELINE (no derived deadline/liveliness — no readers were known
     at build time, D66 best-effort derivation).
  4. incompatible-QoS warnings: a TRANSIENT-requesting reader => writer:DURABILITY; an
     EXCLUSIVE-ownership source writer => reader:OWNERSHIP (equality RxO); and the D66
     liveliness residual — a finite-lease liveliness-requesting reader arriving AFTER
     creation draws writer:LIVELINESS (immutable policy; remedy is a named alias or a
     DISABLE/ENABLE re-arm).
  5. a later, tighter-deadline reader tightens the writer offer in place (set_qos) — the
     route stays ENABLED, no teardown cycle.

Run from the repo root (see router/test_e2e/README.md).
"""

import sys
import time
from pathlib import Path

import rti.connextdds as dds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import start_router, render_config, REPO_ROOT  # noqa: E402
from util.dds_probe import (  # noqa: E402
    Probe, reader_qos, writer_qos, read_route_facts, wait_for_route)

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


def test_auto_qos_create_and_observe_summaries_warnings_and_tightening(
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
            qos=reader_qos(reliability="reliable", durability="transient_local"),
            dtype=status_type)

        # (1) Create-and-observe zero: the route builds immediately (no gate, D66) and
        # is ENABLED with both legs unmatched — an observable zero, not a wait state.
        zero = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["state"] == "ROUTE_ENABLED", check_alive=alive)
        assert zero is not None and zero["state"] == "ROUTE_ENABLED", \
            f"route never reached ROUTE_ENABLED at startup; got {zero}; log {router.log_path}"
        assert zero["topic_state"] == "TOPIC_FORWARDING", \
            f"expected a live build, got {zero}; log {router.log_path}"
        assert zero["input_matched"] == 0 and zero["output_matched"] == 0, \
            f"expected created-but-unmatched zeros, got {zero}; log {router.log_path}"
        assert zero["match_reason"] == "input_unmatched,output_unmatched", \
            f"match_reason {zero['match_reason']!r}; log {router.log_path}"

        # (2a) BEST_EFFORT + VOLATILE source writer matches the weakest-request input
        # reader (the F5 case): input_matched rises.
        src_writer = in_probe.writer(
            TOPIC, TYPE, qos=writer_qos(reliability="best_effort", durability="volatile"),
            dtype=cmd_type)
        in_matched = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["input_matched"] >= 1, check_alive=alive)
        assert in_matched is not None and in_matched["input_matched"] >= 1, \
            f"BEST_EFFORT writer never matched the input reader; got {in_matched}; " \
            f"log {router.log_path}"
        assert in_matched["match_reason"] == "output_unmatched", \
            f"match_reason {in_matched['match_reason']!r}; log {router.log_path}"

        # (2b/3) A plain RELIABLE+VOLATILE reader matches the strong-offer BASELINE
        # output writer (built with no readers known — no derived deadline/liveliness).
        sink = out_probe.reader(
            TOPIC, TYPE,
            qos=reader_qos(reliability="reliable", durability="volatile"),
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
            f"writer summary should be the underived baseline (D66); " \
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

        # (5) A later, tighter-deadline reader tightens the writer offer in place.
        tight = out_probe.reader(
            TOPIC, TYPE,
            qos=reader_qos(reliability="reliable", durability="volatile", deadline_ms=500),
            dtype=cmd_type)
        tightened = wait_for_route(
            status_reader, ROUTE,
            lambda f: "deadline=500ms" in f["writer_summary"], check_alive=alive)
        assert tightened is not None and "deadline=500ms" in tightened["writer_summary"], \
            f"writer offer never tightened; got {tightened}; log {router.log_path}"
        assert tightened["state"] == "ROUTE_ENABLED", \
            f"route left ENABLED during in-place tighten; got {tightened}; log {router.log_path}"

        # (4a) A TRANSIENT-requesting reader draws a loud writer:DURABILITY warning.
        # Keep the reference: a dropped DataReader is GC'd and closed immediately (router
        # would log subscription_discovered then endpoint_lost in the same instant).
        transient_reader = out_probe.reader(  # noqa: F841 (must stay alive)
            TOPIC, TYPE, qos=reader_qos(reliability="reliable", durability="transient"),
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
            TOPIC, TYPE, qos=writer_qos(ownership="exclusive"), dtype=cmd_type)
        own_warn = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["qos_warning"] == "reader:OWNERSHIP", check_alive=alive)
        assert own_warn is not None and own_warn["qos_warning"] == "reader:OWNERSHIP", \
            f"no reader:OWNERSHIP warning; got {own_warn}; log {router.log_path}"
        assert own_warn["state"] == "ROUTE_ENABLED", "OWNERSHIP should warn only, not disable"

        # (4c) D66 liveliness residual: the writer was built before any reader existed,
        # so it offers default (AUTOMATIC, infinite-lease) liveliness — immutable. A
        # finite-lease liveliness-requesting reader arriving now is RxO-incompatible and
        # draws writer:LIVELINESS (warn-only; the pinned remedy is a named alias or a
        # DISABLE/ENABLE re-arm, which would re-derive from the now-known readers).
        liveliness_reader = out_probe.reader(  # noqa: F841 (must stay alive)
            TOPIC, TYPE,
            qos=reader_qos(reliability="reliable", durability="volatile",
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
