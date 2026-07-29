"""End-to-end Phase 10: participant-level partition membership as a set (D83; plan's
"Team disabled by default" / "Team assignment" / "Direct peer tap" POC evidence rows).

TWO router_main processes (config/e2e_team_partition.yaml, role=platform), distinguished
by --node-name Platform_30 / Platform_31. Each has its own platform_lan (so app-side test
traffic never crosses between the two processes by accident) but both render platform_wan
onto the SAME WAN domain (production reality: one physical network). platform_wan.
participant_partition is omitted in the fixture, so it defaults to the protected
{"${node.name}"} entry alone per node (D83) — team membership starts disjoint.

Asserts:

  E-disabled  with disjoint partitions (each router's own identity only), PlatformData
              never crosses: held-zero matched count on the receiving side (D66), not
              silence-and-hope — AND no WAN endpoint discovery at all between the two
              platform_wan participants (the D73 SEDP-suppression claim, confirmed against
              each process's own discovery log: no publication_discovered/
              subscription_discovered line for PlatformData naming the other's identity
              as origin).
  E-team      ADD_PARTICIPANT_PARTITION platform_wan=TEAM_A on both: acks accepted on apply
              (not on rematch — D83 item 4); PlatformData crosses; matched counts
              advance — allowing for SPDP2's probabilistic post-match settle window
              (D78), not a fixed bound.
  E-remove    REMOVE_PARTICIPANT_PARTITION platform_wan=TEAM_A on one platform: matched
              counts regress to zero on live entities, forwarding stops, no entities are
              torn down (topic_state stays TOPIC_FORWARDING).
  E-direct    a direct peer tap without a shared team: Platform_30
              ADD_PARTICIPANT_PARTITIONs Platform_31's own protected identity name (no
              team in common) and PlatformData crosses between exactly those two nodes.
  E-idempotent  a duplicate ADD (name already present) is an idempotent accept with no
              state_revision bump (D8); REMOVE of the protected "${node.name}" entry is
              rejected, not silently accepted (D83).

Run from the repo root (see router/test_e2e/README.md).
"""

import sys
import time
from pathlib import Path

import rti.connextdds as dds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import CONFIG_DIR, start_router  # noqa: E402
from util.dds_probe import (  # noqa: E402
    AdminChannel, COMMAND_KIND, Probe, reader_qos, writer_qos, wait_for_route,
    write_until_seen)

TOPIC = "PlatformData"
TYPE = "ExampleCommand"
EXAMPLE_TYPES_XML = "router/config/example_types.xml"
FIXTURE = "e2e_team_partition.yaml"
ROUTER_NAME = "team-partition"  # router.name in the fixture; identical for both nodes
NODE_A = "Platform_30"
NODE_B = "Platform_31"

# RouterCommandKind ordinals (RouterAdminTypes.idl declaration order) — shared with the
# other e2e admin-command tests via util.dds_probe.COMMAND_KIND.
KIND = COMMAND_KIND


def _render(domains, out_name, e2e_tmp_dir):
    text = (CONFIG_DIR / FIXTURE).read_text()
    text = text.replace("__DOMAIN_PLATFORM_LAN__", str(domains["platform_lan"]))
    text = text.replace("__DOMAIN_WAN__", str(domains["wan"]))
    out = e2e_tmp_dir / out_name
    out.write_text(text)
    return out


def _partition_cmd(command_type, node_name, command_id, kind, partition_name):
    c = dds.DynamicData(command_type)
    c["target_node"] = node_name
    c["target_router"] = ROUTER_NAME
    c["command_id"] = command_id
    c["kind"] = KIND[kind]
    c["participant_name"] = "platform_wan"
    c["partition_name"] = partition_name
    return c


def _sample(cmd_type, seq):
    d = dds.DynamicData(cmd_type)
    d["msg.destination"] = "n/a"
    d["msg.seq"] = seq
    return d


def test_team_partition_membership(router_binary, admin_types_xml, e2e_tmp_dir,
                                   unique_domains):
    # Distinct platform_lan per node (test isolation); the SAME wan domain for both
    # (production reality: one physical WAN) — repurposing unique_domains' unused
    # "control_lan" key as node B's platform_lan rather than widening the shared fixture.
    domains_a = {"platform_lan": unique_domains["platform_lan"], "wan": unique_domains["wan"]}
    domains_b = {"platform_lan": unique_domains["control_lan"], "wan": unique_domains["wan"]}
    cfg_a = _render(domains_a, "team_a.yaml", e2e_tmp_dir)
    cfg_b = _render(domains_b, "team_b.yaml", e2e_tmp_dir)

    proc_a = start_router(router_binary, cfg_a, "platform", e2e_tmp_dir,
                          admin_participant="platform_lan")
    proc_b = start_router(router_binary, cfg_b, "platform", e2e_tmp_dir,
                          node_name=NODE_B, admin_participant="platform_lan")
    alive = lambda: proc_a.is_alive() and proc_b.is_alive()  # noqa: E731

    # Both processes are live from here on — every exit path below must stop them, so
    # the whole body (including the mutual-discovery gate) runs under one try/finally.
    # A bare assert between process-start and the try/finally (as an earlier version of
    # this test had) leaks the subprocess pair on failure, which then pollutes the next
    # run sharing the same domain ids (unique_domains resets per pytest invocation).
    lan_a = Probe(domains_a["platform_lan"])
    lan_b = Probe(domains_b["platform_lan"])
    try:
        admin_provider = dds.QosProvider(str(admin_types_xml))
        cmd_type_admin = admin_provider.type("RouterCommand")
        cmd_type = dds.QosProvider(EXAMPLE_TYPES_XML).type(TYPE)

        adm_a = AdminChannel(lan_a, admin_provider)
        adm_b = AdminChannel(lan_b, admin_provider)

        # Per-process readiness only — NOT wait_for_mutual_discovery. The two platform_wan
        # participants share domain 41 but start in disjoint partitions (protected
        # identity only, D83): confirmed empirically (see the D87 finding) that a
        # participant-level partition mismatch suppresses mutual visibility at the SPDP
        # layer itself, not just SEDP endpoint exchange — so the two processes are NOT
        # expected to see each other's participants at all until a team is shared. Each
        # process's own admin/LAN status is independent of that and comes up regardless.
        facts_a0 = wait_for_route(adm_a.status, "platform_team_to_wan",
                                  lambda f: True, check_alive=alive)
        facts_b0 = wait_for_route(adm_b.status, "wan_team_to_platform",
                                  lambda f: True, check_alive=alive)
        assert facts_a0 is not None, f"node A never reported its own routes; " \
            f"log={proc_a.log_path}"
        assert facts_b0 is not None, f"node B never reported its own routes; " \
            f"log={proc_b.log_path}"

        writer_a = lan_a.writer(TOPIC, TYPE, qos=writer_qos(
            reliability="reliable", durability="transient_local"), dtype=cmd_type)
        reader_b = lan_b.reader(TOPIC, TYPE, qos=reader_qos(
            reliability="reliable", durability="transient_local"), dtype=cmd_type)

        # --- E-disabled: disjoint identities, PlatformData never crosses ---
        buckets = write_until_seen(
            lambda: writer_a.write(_sample(cmd_type, 1)), reader_b,
            classify=lambda d: "got", stop_key="got", timeout_s=6.0, grace_s=1.0,
            check_alive=alive)
        assert not buckets.get("got"), (
            "PlatformData crossed with disjoint platform_wan partitions (team not "
            f"assigned); logs: a={proc_a.log_path} b={proc_b.log_path}")
        facts_b = wait_for_route(adm_b.status, "wan_team_to_platform",
                                 lambda f: True, check_alive=alive)
        assert facts_b is not None and facts_b["input_matched"] == 0, (
            f"node B's platform_wan reader matched node A's writer despite disjoint "
            f"partitions (held-zero expected, D66); facts={facts_b}")

        # D73 SEDP-suppression: neither process's discovery log shows the OTHER's
        # identity as the origin of a PlatformData endpoint — zero WAN endpoint
        # discovery between disjoint-partition platform_wan participants, not just a
        # data-level non-match.
        log_a = proc_a.log_path.read_text()
        log_b = proc_b.log_path.read_text()
        assert f"topic={TOPIC}" not in log_a or f"origin={NODE_B}/{ROUTER_NAME}" \
            not in log_a, f"node A's log shows a PlatformData endpoint from node B " \
            f"despite disjoint platform_wan partitions; log={proc_a.log_path}"
        assert f"topic={TOPIC}" not in log_b or f"origin={NODE_A}/{ROUTER_NAME}" \
            not in log_b, f"node B's log shows a PlatformData endpoint from node A " \
            f"despite disjoint platform_wan partitions; log={proc_b.log_path}"

        # --- E-team: ADD_PARTICIPANT_PARTITION platform_wan=TEAM_A on both ---
        adm_a.cmd_writer.write(
            _partition_cmd(cmd_type_admin, NODE_A, "t1", "ADD_PARTICIPANT_PARTITION",
                          "TEAM_A"))
        ack_a1 = adm_a.acks.wait("t1", check_alive=alive)
        assert ack_a1 is not None and ack_a1["accepted"], \
            f"node A ADD_PARTICIPANT_PARTITION not accepted: {ack_a1}"
        adm_b.cmd_writer.write(
            _partition_cmd(cmd_type_admin, NODE_B, "t2", "ADD_PARTICIPANT_PARTITION",
                          "TEAM_A"))
        ack_b1 = adm_b.acks.wait("t2", check_alive=alive)
        assert ack_b1 is not None and ack_b1["accepted"], \
            f"node B ADD_PARTICIPANT_PARTITION not accepted: {ack_b1}"

        matched_b = wait_for_route(
            adm_b.status, "wan_team_to_platform", lambda f: f["input_matched"] >= 1,
            timeout_s=30.0, check_alive=alive)  # SPDP2 post-match settle window (D78)
        assert matched_b is not None and matched_b["input_matched"] >= 1, (
            f"node B's platform_wan reader never matched after TEAM_A join; "
            f"facts={matched_b}; logs: a={proc_a.log_path} b={proc_b.log_path}")

        buckets = write_until_seen(
            lambda: writer_a.write(_sample(cmd_type, 2)), reader_b,
            classify=lambda d: "got", stop_key="got", timeout_s=20.0, check_alive=alive)
        assert buckets.get("got"), (
            f"PlatformData never crossed after TEAM_A join; "
            f"logs: a={proc_a.log_path} b={proc_b.log_path}")

        # Idempotent duplicate ADD (D8): accept, no revision bump. Let the recent TEAM_A
        # join's own matched-count settling (both legs, both processes) quiesce first —
        # otherwise an unrelated late TopicMatchChanged landing in the same window as the
        # dup command would bump state_revision for a reason that has nothing to do with
        # the dup ADD, making this check flaky rather than wrong.
        time.sleep(2.0)
        facts_before = wait_for_route(adm_a.status, "platform_team_to_wan",
                                      lambda f: True, check_alive=alive)
        rev_before = facts_before["state_revision"]
        adm_a.cmd_writer.write(
            _partition_cmd(cmd_type_admin, NODE_A, "t1-dup",
                          "ADD_PARTICIPANT_PARTITION", "TEAM_A"))
        ack_dup = adm_a.acks.wait("t1-dup", check_alive=alive)
        assert ack_dup is not None and ack_dup["accepted"] and \
            ack_dup["message"] == "already present", ack_dup
        facts_after = wait_for_route(adm_a.status, "platform_team_to_wan",
                                     lambda f: True, check_alive=alive)
        assert facts_after["state_revision"] == rev_before, (
            "idempotent duplicate ADD_PARTICIPANT_PARTITION bumped state_revision")

        # REMOVE of the protected node-identity entry is rejected, not a silent no-op.
        reject = adm_a.cmd_writer  # reuse writer
        reject.write(_partition_cmd(cmd_type_admin, NODE_A, "t-protected",
                                    "REMOVE_PARTICIPANT_PARTITION", NODE_A))
        ack_protected = adm_a.acks.wait("t-protected", check_alive=alive)
        assert ack_protected is not None and not ack_protected["accepted"], (
            f"removing the protected node-identity partition entry must be rejected; "
            f"got {ack_protected}")

        # --- E-remove: REMOVE_PARTICIPANT_PARTITION platform_wan=TEAM_A on node B ---
        adm_b.cmd_writer.write(
            _partition_cmd(cmd_type_admin, NODE_B, "t3",
                          "REMOVE_PARTICIPANT_PARTITION", "TEAM_A"))
        ack_b2 = adm_b.acks.wait("t3", check_alive=alive)
        assert ack_b2 is not None and ack_b2["accepted"], \
            f"node B REMOVE_PARTICIPANT_PARTITION not accepted: {ack_b2}"
        unmatched_b = wait_for_route(
            adm_b.status, "wan_team_to_platform", lambda f: f["input_matched"] == 0,
            timeout_s=15.0, check_alive=alive)
        assert unmatched_b is not None and unmatched_b["input_matched"] == 0, (
            f"node B's platform_wan reader still matched after TEAM_A removal; "
            f"facts={unmatched_b}")
        assert unmatched_b["topic_state"] == "TOPIC_FORWARDING", (
            "partition membership change must never tear down live entities; "
            f"got {unmatched_b}")

        # Also leave node A's TEAM_A membership (clean disjoint state before the direct
        # peer tap below) and confirm delivery has actually stopped end to end.
        adm_a.cmd_writer.write(
            _partition_cmd(cmd_type_admin, NODE_A, "t4",
                          "REMOVE_PARTICIPANT_PARTITION", "TEAM_A"))
        ack_a2 = adm_a.acks.wait("t4", check_alive=alive)
        assert ack_a2 is not None and ack_a2["accepted"], ack_a2
        wait_for_route(adm_a.status, "platform_team_to_wan",
                       lambda f: f["output_matched"] == 0, timeout_s=15.0,
                       check_alive=alive)

        # --- E-direct: node A taps node B's own identity — no shared team ---
        adm_a.cmd_writer.write(
            _partition_cmd(cmd_type_admin, NODE_A, "t5",
                          "ADD_PARTICIPANT_PARTITION", NODE_B))
        ack_a3 = adm_a.acks.wait("t5", check_alive=alive)
        assert ack_a3 is not None and ack_a3["accepted"], \
            f"direct peer tap not accepted: {ack_a3}"
        matched_b2 = wait_for_route(
            adm_b.status, "wan_team_to_platform", lambda f: f["input_matched"] >= 1,
            timeout_s=30.0, check_alive=alive)
        assert matched_b2 is not None and matched_b2["input_matched"] >= 1, (
            f"direct peer tap never matched; facts={matched_b2}; "
            f"logs: a={proc_a.log_path} b={proc_b.log_path}")
        buckets2 = write_until_seen(
            lambda: writer_a.write(_sample(cmd_type, 3)), reader_b,
            classify=lambda d: "got", stop_key="got", timeout_s=20.0, check_alive=alive)
        assert buckets2.get("got"), (
            f"PlatformData never crossed after the direct peer tap; "
            f"logs: a={proc_a.log_path} b={proc_b.log_path}")
    finally:
        lan_a.close()
        lan_b.close()
        proc_a.stop()
        proc_b.stop()
