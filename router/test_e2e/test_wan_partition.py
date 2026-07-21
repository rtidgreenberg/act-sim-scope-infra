"""End-to-end 7b: endpoint Publisher/Subscriber PARTITION application + runtime change
(D61 as refined by D64/D66; SET_ROUTE_PARTITION, D69; plan's E3).

ONE router_main process (config/e2e_partition.yaml) with route part_r1 publishing its
output into PARTITION "PLATFORM". Explicit "default" QoS on both legs so the partition is
the only matching variable. Asserts:

  E3a  a default-partition app reader NEVER matches the PLATFORM-partitioned route
       writer: output_matched holds 0 with match_reason set, and qos_warning stays empty
       (a partition mismatch is a non-match, NOT an incompatible-QoS event — D61).
  E3b  a PLATFORM-partitioned app reader matches (output_matched rises) and receives a
       forwarded sample.
  RT   SET_ROUTE_PARTITION retargets the output partition to "TEAM_B" at runtime:
       ack accepted, state_revision bumps, the PLATFORM reader unmatches, a TEAM_B
       reader matches and receives samples — all IN PLACE (topic_state stays
       TOPIC_FORWARDING, QoS summaries unchanged: set_qos rematch per D15, no rebuild).
       A repeat with the same values acks "partition unchanged" with no revision bump.

Run from the repo root (see router/test_e2e/README.md).
"""

import sys
import time
from pathlib import Path

import rti.connextdds as dds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import start_router, render_config  # noqa: E402
from util.dds_probe import (  # noqa: E402
    AckCollector, COMMAND_KIND, Probe, reader_qos, writer_qos, read_status_revision,
    wait_for_route)

TOPIC = "PartCmd"
TYPE = "ExampleCommand"
ROUTE = "part_r1"
NODE = "Platform_30"
ROUTER = "partition"
EXAMPLE_TYPES_XML = "router/config/example_types.xml"

# RouterCommandKind ordinals (RouterAdminTypes.idl declaration order).
KIND = COMMAND_KIND


def _cmd(cmd_type, destination, seq):
    d = dds.DynamicData(cmd_type)
    d["msg.destination"] = destination
    d["msg.seq"] = seq
    return d


def _partition_command(command_type, command_id, sub_partition, pub_partition):
    c = dds.DynamicData(command_type)
    c["target_node"] = NODE
    c["target_router"] = ROUTER
    c["command_id"] = command_id
    c["kind"] = KIND["SET_ROUTE_PARTITION"]
    c["route_name"] = ROUTE
    c["route.input.subscriber_partition"] = sub_partition
    c["route.output.publisher_partition"] = pub_partition
    return c


def _forward_one(alive, src_writer, cmd_type, reader, seq, timeout_s=15.0):
    """Write until `reader` takes one valid sample; returns it (or None on timeout)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        assert alive(), "router exited early"
        src_writer.write(_cmd(cmd_type, "Control_20", seq))
        for sample in reader.take():
            if sample.info.valid:
                return sample.data
        time.sleep(0.2)
    return None


def test_partition_gates_matching_and_changes_at_runtime(
        router_binary, admin_types_xml, e2e_tmp_dir, unique_domains):
    config_path = render_config("e2e_partition.yaml", unique_domains, e2e_tmp_dir)
    router = start_router(router_binary, config_path, "platform", e2e_tmp_dir,
                          admin_participant="wan_in")

    in_domain = unique_domains["control_lan"]    # wan_in (route input + admin/status)
    out_domain = unique_domains["platform_lan"]  # lan_out (route output, partitioned)

    cmd_type = dds.QosProvider(EXAMPLE_TYPES_XML).type(TYPE)
    admin_provider = dds.QosProvider(str(admin_types_xml))
    status_type = admin_provider.type("RouterStatus")
    command_type = admin_provider.type("RouterCommand")
    ack_type = admin_provider.type("RouterCommandAck")

    # The route's "default"-alias legs are RELIABLE+TRANSIENT_LOCAL: app endpoints must
    # be RxO-compatible so the partition stays the ONLY matching variable (D67 lesson).
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
        cmd_writer = in_probe.writer(
            "ActRouterCommand", "RouterCommand",
            qos=writer_qos(reliability="reliable", durability="volatile"),
            dtype=command_type)
        ack_reader = in_probe.reader(
            "ActRouterCommandAck", "RouterCommandAck",
            qos=reader_qos(reliability="reliable", durability="volatile"),
            dtype=ack_type)
        acks = AckCollector(ack_reader)

        # Source writer on the input leg (default partition — matches the route input).
        src_writer = in_probe.writer(TOPIC, TYPE, qos=compatible_w, dtype=cmd_type)
        in_matched = wait_for_route(
            status_reader, ROUTE, lambda f: f["input_matched"] >= 1, check_alive=alive)
        assert in_matched is not None and in_matched["input_matched"] >= 1, \
            f"source writer never matched the route input; got {in_matched}; " \
            f"log {router.log_path}"

        # (E3a) A default-partition reader NEVER matches the PLATFORM-partitioned route
        # writer — held-zero matched count + reason, and NO incompatible-QoS event.
        default_reader = out_probe.reader(  # noqa: F841 (must stay alive)
            TOPIC, TYPE, qos=compatible_r, dtype=cmd_type)
        time.sleep(2.0)  # grace: give a wrong match time to appear
        facts = wait_for_route(status_reader, ROUTE, lambda f: True, check_alive=alive)
        assert facts is not None, f"route absent from status; log {router.log_path}"
        assert facts["output_matched"] == 0, (
            f"default-partition reader matched a PLATFORM-partitioned writer; "
            f"facts={facts}; log {router.log_path}")
        assert "output_unmatched" in facts["match_reason"], facts
        assert facts["qos_warning"] == "", (
            f"partition mismatch must NOT surface as incompatible QoS (D61); "
            f"facts={facts}; log {router.log_path}")

        # (E3b) A PLATFORM-partitioned reader matches and receives forwarded samples.
        platform_reader = out_probe.reader(
            TOPIC, TYPE, qos=compatible_r, dtype=cmd_type, partition="PLATFORM")
        matched = wait_for_route(
            status_reader, ROUTE, lambda f: f["output_matched"] >= 1, check_alive=alive)
        assert matched is not None and matched["output_matched"] >= 1, \
            f"PLATFORM reader never matched; got {matched}; log {router.log_path}"
        got = _forward_one(alive, src_writer, cmd_type, platform_reader, seq=1)
        assert got is not None, \
            f"no sample forwarded to the PLATFORM reader; log {router.log_path}"
        summaries = (matched["reader_summary"], matched["writer_summary"])
        rev_before = read_status_revision(status_reader)
        assert rev_before is not None

        # (RT) Runtime retarget: output partition PLATFORM -> TEAM_B, in place.
        cmd_writer.write(_partition_command(command_type, "part-1", "", "TEAM_B"))
        ack = acks.wait("part-1", check_alive=alive)
        assert ack is not None and ack["accepted"], \
            f"SET_ROUTE_PARTITION not accepted: {ack}; log {router.log_path}"

        # The PLATFORM reader unmatches (the still-present default reader stays
        # unmatched too): output_matched returns to 0 until a TEAM_B reader appears.
        unmatched = wait_for_route(
            status_reader, ROUTE, lambda f: f["output_matched"] == 0, check_alive=alive)
        assert unmatched is not None, \
            f"PLATFORM reader never unmatched after retarget; log {router.log_path}"
        assert unmatched["topic_state"] == "TOPIC_FORWARDING", (
            f"partition change must be in place — no rebuild/teardown (D15/D69); "
            f"got {unmatched}; log {router.log_path}")
        rev_after = read_status_revision(status_reader)
        assert rev_after is not None and rev_after > rev_before, \
            f"SET_ROUTE_PARTITION did not bump state_revision " \
            f"({rev_before} -> {rev_after}); log {router.log_path}"

        team_reader = out_probe.reader(
            TOPIC, TYPE, qos=compatible_r, dtype=cmd_type, partition="TEAM_B")
        rematched = wait_for_route(
            status_reader, ROUTE, lambda f: f["output_matched"] >= 1, check_alive=alive)
        assert rematched is not None and rematched["output_matched"] >= 1, \
            f"TEAM_B reader never matched after retarget; log {router.log_path}"
        assert rematched["topic_state"] == "TOPIC_FORWARDING", rematched
        assert (rematched["reader_summary"], rematched["writer_summary"]) == summaries, (
            f"entity QoS summaries changed across the partition retarget — this should "
            f"be the SAME build, adjusted in place; log {router.log_path}")
        got = _forward_one(alive, src_writer, cmd_type, team_reader, seq=2)
        assert got is not None, \
            f"no sample forwarded to the TEAM_B reader after retarget; " \
            f"log {router.log_path}"

        # Idempotent repeat (same values, new command_id): "partition unchanged", no bump.
        rev_stable = read_status_revision(status_reader)
        cmd_writer.write(_partition_command(command_type, "part-2", "", "TEAM_B"))
        ack2 = acks.wait("part-2", check_alive=alive)
        assert ack2 is not None and ack2["accepted"], f"{ack2}; log {router.log_path}"
        assert ack2["message"] == "partition unchanged", \
            f"expected idempotent accept, got {ack2}; log {router.log_path}"
        assert read_status_revision(status_reader) == rev_stable, \
            f"idempotent partition command bumped revision; log {router.log_path}"
    finally:
        in_probe.close()
        out_probe.close()
        router.stop()
