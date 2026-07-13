"""End-to-end D15 same-node loop-safety: the router ignores publications from a same-node
router and routes only genuine application publications.

Python port of the distinctive coverage of the retired C++ test/test_runtime_spine.cxx,
now driven through the real router_main + real DynamicRouteFactory (config/
e2e_same_node_ignore.yaml — one router, node TestNode, route ignore_r1, explicit "default"
QoS so a discovered input publication alone drives ROUTE_ENABLED, no auto-QoS gate).

Two behaviors, both D15:
  A. a writer on a participant tagged act.router=TestNode/<other> (SAME node as the router)
     is recognized as a same-node router publication and ignored (dds::pub::ignore) — the
     router logs endpoint_ignored_same_node and the route never leaves
     ROUTE_WAITING_FOR_DISCOVERY.
  B. a genuine, untagged application writer IS routed — the route reaches ROUTE_ENABLED.

runtime_spine asserted A via a fake factory's create-count (creates == 0); here it is
asserted against the real router: the route status never reaches ENABLED for the same-node
writer, and does once a real app writer appears.

Run from the repo root (see router/test_e2e/README.md).
"""

import sys
import time
from pathlib import Path

import rti.connextdds as dds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import start_router, render_config, _log_contains  # noqa: E402
from util.dds_probe import (  # noqa: E402
    Probe, reader_qos, read_route_facts, wait_for_route)

TOPIC = "SameNodeCmd"
TYPE = "ExampleCommand"
ROUTE = "ignore_r1"
NODE = "TestNode"
EXAMPLE_TYPES_XML = "router/config/example_types.xml"


def test_same_node_publication_ignored_real_publication_routed(
        router_binary, admin_types_xml, e2e_tmp_dir, unique_domains):
    config_path = render_config("e2e_same_node_ignore.yaml", unique_domains, e2e_tmp_dir)
    router = start_router(router_binary, config_path, "platform", e2e_tmp_dir,
                          node_name=NODE, admin_participant="lan_in")

    in_domain = unique_domains["control_lan"]   # lan_in (router input + admin/status)
    out_domain = unique_domains["platform_lan"]  # lan_out (router output)

    cmd_type = dds.QosProvider(EXAMPLE_TYPES_XML).type(TYPE)
    status_type = dds.QosProvider(str(admin_types_xml)).type("RouterStatus")
    alive = lambda: router.is_alive()  # noqa: E731

    status_probe = Probe(in_domain)
    # A probe posing as a SAME-NODE peer router (same node "TestNode" as the router).
    peer_router = Probe(in_domain, user_data=f"act.router={NODE}/peer-router")
    app_in = Probe(in_domain)     # genuine application publisher (untagged)
    app_out = Probe(out_domain)   # genuine application subscriber (untagged)
    held = []
    try:
        # RELIABLE+TRANSIENT_LOCAL so the observer sees the latest published snapshot on
        # join even when no further status change follows (the ignored-writer case, D26).
        status_reader = status_probe.reader(
            "ActRouterStatus", "RouterStatus",
            qos=reader_qos(reliability="reliable", durability="transient_local"),
            dtype=status_type)

        # --- A) same-node router writer: must be ignored, route must NOT enable ---
        held.append(peer_router.writer(TOPIC, TYPE, dtype=cmd_type))

        ignored = False
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not ignored:
            assert alive(), f"router exited early; log {router.log_path}"
            ignored = _log_contains(router.log_path, "endpoint_ignored_same_node")
            time.sleep(0.2)
        assert ignored, (
            "router never logged endpoint_ignored_same_node for the same-node peer "
            f"writer (D15); log {router.log_path}")

        # Grace: with only the ignored same-node writer present, the route must stay
        # WAITING_FOR_DISCOVERY (no application input was routed).
        time.sleep(2.0)
        facts = read_route_facts(status_reader, ROUTE)
        assert facts is not None, f"route {ROUTE} absent from status; log {router.log_path}"
        assert facts["state"] != "ROUTE_ENABLED", (
            f"route enabled off a same-node router publication — D15 ignore failed; "
            f"facts={facts}; log {router.log_path}")

        # --- B) genuine application traffic: route must reach ROUTE_ENABLED ---
        held.append(app_out.reader(TOPIC, TYPE, dtype=cmd_type))
        held.append(app_in.writer(TOPIC, TYPE, dtype=cmd_type))

        enabled = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["state"] == "ROUTE_ENABLED", check_alive=alive)
        assert enabled is not None and enabled["state"] == "ROUTE_ENABLED", (
            f"route never enabled off a genuine application publication; got {enabled}; "
            f"log {router.log_path}")
    finally:
        for entity in held:
            entity.close()
        status_probe.close()
        peer_router.close()
        app_in.close()
        app_out.close()
        router.stop()
