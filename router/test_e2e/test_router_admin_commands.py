"""End-to-end Phase 6 slice 6a: the DDS command/ack/status control loop (D54/D56).

ONE router_main process (config/e2e_admin_commands.yaml, role platform) owns route
admin_r1, which starts DISABLED. A single Python probe on the admin (control_lan) domain
drives the loop over real DDS:

  - writes RouterCommand on ActRouterCommand (the router's D47 ContentFilteredTopic keys on
    target_node/target_router = this router's identity, so an off-target command is dropped
    before the callback);
  - reads RouterCommandAck on ActRouterCommandAck (D48 QoS);
  - reads RouterStatus on ActRouterStatus (D26) as DynamicData for route state/revision.

The admin types (RouterCommand/RouterCommandAck/RouterStatus) come from the admin IDL via
the admin_types_xml fixture, exactly like test_auto_qos.py reads RouterStatus.

Asserts the Phase 6 6a evidence items (D56, E2 reshaped by 7m/D66 + 7c/D70 — ENABLE
builds as soon as the topic's wire type is known):
  E1    disabled route appears in startup status with no entities.
  E-CFT a command addressed to another router never changes state and draws no ack.
  E2    ENABLE_ROUTE with no source writer -> ROUTE_WAITING_FOR_DISCOVERY (no wire type
        yet, the 7c gate); the source writer teaches the type -> the route builds ->
        ROUTE_ENABLED with input_matched >= 1; ack accepted; state_revision bumps.
  E4    duplicate command_id returns the original ack and does not bump state_revision.
  E3    DISABLE_ROUTE -> ROUTE_DISABLED, entities closed; ack accepted.

Run from the repo root (see router/test_e2e/README.md).
"""

import sys
import time
from pathlib import Path

import rti.connextdds as dds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import start_router, render_config  # noqa: E402
from util.dds_probe import (  # noqa: E402
    AdminChannel, COMMAND_KIND, Probe, read_route_facts, read_status_revision,
    wait_for_route)

ROUTE = "admin_r1"
NODE = "Platform_30"      # config node.name
ROUTER = "admincmd"       # config router.name
SRC_TOPIC = "AdminCmdTopic"
EXAMPLE_TYPES_XML = "router/config/example_types.xml"
EX_TYPE = "ExampleCommand"

# RouterCommandKind ordinals (RouterAdminTypes.idl declaration order).
KIND = COMMAND_KIND


def _command(cmd_type, kind, route_name, command_id, target_node, target_router):
    c = dds.DynamicData(cmd_type)
    c["target_node"] = target_node
    c["target_router"] = target_router
    c["command_id"] = command_id
    c["kind"] = KIND[kind]
    c["route_name"] = route_name
    return c


def test_admin_command_control_loop(
        router_binary, admin_types_xml, e2e_tmp_dir, unique_domains):
    config_path = render_config("e2e_admin_commands.yaml", unique_domains, e2e_tmp_dir)
    router = start_router(router_binary, config_path, "platform", e2e_tmp_dir,
                          admin_participant="wan_in")
    admin_domain = unique_domains["control_lan"]  # wan_in: admin channel + route input

    provider = dds.QosProvider(str(admin_types_xml))
    cmd_type = provider.type("RouterCommand")
    ex_type = dds.QosProvider(EXAMPLE_TYPES_XML).type(EX_TYPE)

    probe = Probe(admin_domain)
    alive = lambda: router.is_alive()  # noqa: E731
    try:
        admin = AdminChannel(probe, provider)
        status_reader, cmd_writer, acks = admin.status, admin.cmd_writer, admin.acks

        # (E1) disabled route present in startup status with no entities.
        facts = wait_for_route(status_reader, ROUTE, lambda f: True, check_alive=alive)
        assert facts is not None, f"route {ROUTE} never appeared; log {router.log_path}"
        assert facts["state"] == "ROUTE_DISABLED", \
            f"startup route state {facts['state']!r}, expected ROUTE_DISABLED; " \
            f"log {router.log_path}"
        # No entities: the topic is tracked (one topic_status row) but IDLE — no
        # reader/writer/condition built, and no resolved QoS summaries.
        assert facts["topic_state"] == "TOPIC_IDLE", \
            f"disabled route topic_state {facts['topic_state']!r}, expected TOPIC_IDLE; " \
            f"log {router.log_path}"
        assert facts["reader_summary"] == "" and facts["writer_summary"] == "", \
            f"disabled route already has entity QoS summaries {facts}; " \
            f"log {router.log_path}"
        rev0 = read_status_revision(status_reader)
        assert rev0 is not None, \
            f"no state_revision in startup status; log {router.log_path}"

        # (E-CFT) a command addressed to a different router is dropped by the D47 CFT:
        # no ack, no state change, no revision bump. Done before the real enable so an
        # accidental match would show up as an unexpected transition. check_alive makes the
        # negative meaningful — a crashed router raises here instead of silently producing
        # "no ack" that would masquerade as the CFT correctly dropping the command.
        cmd_writer.write(_command(cmd_type, "ENABLE_ROUTE", ROUTE, "cft-1",
                                  "Platform_99", ROUTER))
        assert acks.wait("cft-1", timeout_s=3.0, check_alive=alive) is None, \
            f"off-target command drew an ack — CFT did not drop it; log {router.log_path}"
        assert router.is_alive(), \
            f"router exited during the CFT-drop check; log {router.log_path}"
        off = read_route_facts(status_reader, ROUTE)
        assert off is not None and off["state"] == "ROUTE_DISABLED", \
            f"off-target command changed state to {off}; log {router.log_path}"
        assert read_status_revision(status_reader) == rev0, \
            f"off-target command bumped state_revision; log {router.log_path}"

        # (E2a) ENABLE_ROUTE with no source writer: accepted, but the topic's wire type
        # is unknown, so the route WAITS (7c/D70 gate); revision up.
        cmd_writer.write(_command(cmd_type, "ENABLE_ROUTE", ROUTE, "enable-1",
                                  NODE, ROUTER))
        ack = acks.wait("enable-1", check_alive=alive)
        assert ack is not None and ack["accepted"], \
            f"ENABLE_ROUTE not accepted: {ack}; log {router.log_path}"
        waiting = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["state"] == "ROUTE_WAITING_FOR_DISCOVERY", check_alive=alive)
        assert waiting is not None and waiting["state"] == "ROUTE_WAITING_FOR_DISCOVERY", \
            f"route should wait for its wire type after ENABLE_ROUTE; got {waiting}; " \
            f"log {router.log_path}"
        rev_after_enable = read_status_revision(status_reader)
        assert rev_after_enable is not None and rev_after_enable > rev0, \
            f"ENABLE_ROUTE did not bump state_revision ({rev0} -> {rev_after_enable}); " \
            f"log {router.log_path}"

        # (E2b) a source writer appears: its inline type object teaches the topic, the
        # route builds and matches it -> ENABLED. Keep the reference alive (a dropped
        # DataWriter is GC'd and the endpoint lost immediately). Waiting for the count
        # also settles the match-driven status publishes before the E4
        # revision-stability window below.
        src_writer = probe.writer(SRC_TOPIC, EX_TYPE, dtype=ex_type)  # noqa: F841
        matched = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["state"] == "ROUTE_ENABLED" and f["input_matched"] >= 1,
            check_alive=alive)
        assert matched is not None and matched["input_matched"] >= 1, \
            f"route never built/matched after the source writer appeared; got {matched}; " \
            f"log {router.log_path}"

        # (E4) duplicate command_id -> cached ack replayed, no state change / revision bump.
        rev_before_dup = read_status_revision(status_reader)
        assert rev_before_dup is not None, \
            f"no state_revision before duplicate; log {router.log_path}"
        cmd_writer.write(_command(cmd_type, "ENABLE_ROUTE", ROUTE, "enable-1",
                                  NODE, ROUTER))
        # The deterministic proof the duplicate was NOT reprocessed is the byte-identical
        # cached ack (a reprocess would mint a fresh ack, D4). state_revision stability
        # corroborates "no state change": poll for ~1.5s so an erroneous bump is caught
        # whether it lands immediately or late, not just within a single fixed sleep.
        dup_ack = acks.wait("enable-1", check_alive=alive)
        assert dup_ack == ack, \
            f"duplicate command_id returned a different ack: {dup_ack} != {ack}; " \
            f"log {router.log_path}"
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            assert alive(), f"router exited during duplicate check; log {router.log_path}"
            rev_now = read_status_revision(status_reader)
            assert rev_now == rev_before_dup, \
                f"duplicate command bumped state_revision " \
                f"({rev_before_dup} -> {rev_now}); log {router.log_path}"
            time.sleep(0.1)

        # (E3) DISABLE_ROUTE -> entities closed, route DISABLED; ack accepted.
        cmd_writer.write(_command(cmd_type, "DISABLE_ROUTE", ROUTE, "disable-1",
                                  NODE, ROUTER))
        dack = acks.wait("disable-1", check_alive=alive)
        assert dack is not None and dack["accepted"], \
            f"DISABLE_ROUTE not accepted: {dack}; log {router.log_path}"
        disabled = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["state"] == "ROUTE_DISABLED" and f["topic_state"] == "TOPIC_IDLE",
            check_alive=alive)
        assert disabled is not None and disabled["state"] == "ROUTE_DISABLED", \
            f"route never returned to ROUTE_DISABLED after DISABLE_ROUTE; got {disabled}; " \
            f"log {router.log_path}"
        assert disabled["topic_state"] == "TOPIC_IDLE", \
            f"route entities not closed after DISABLE_ROUTE ({disabled}); " \
            f"log {router.log_path}"
    finally:
        probe.close()
        router.stop()
