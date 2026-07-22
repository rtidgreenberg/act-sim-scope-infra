"""RouterHealth.team_partition end-to-end (gui/mesh_dashboard's team-grouping follow-on
to Phase 16 v1). Not a new isolation mechanism -- D83/D87's ADD_PARTICIPANT_PARTITION
path is already proven by test_team_partition.py; this asserts the NEW read-side field
that lets a mesh-wide observer (the dashboard) see team membership at all.

Three router_main processes (config/e2e_presence_team.yaml): one control-role, two
platform-role (Platform_30 / Platform_31, distinct platform_lan domains, shared WAN
domain incl. team_wan -- production reality, one physical network).

Asserts:

  E-untagged  before any team is assigned, each platform's own RouterHealth carries
              team_partition == {its own protected identity} only -- readable off the
              shared WAN domain directly (control_wan/platform_wan, unconditional match,
              D87), with no team_wan-level discovery needed to see it.
  E-tagged    after ADD_PARTICIPANT_PARTITION team_wan=TEAM_A on both platforms, each
              one's RouterHealth.team_partition becomes {its own identity, "TEAM_A"}.
  E-mesh      the control node's own ActRouterMeshStatus aggregate (what
              gui/mesh_dashboard actually reads) carries the SAME team_partition value
              for each peer -- proving the field survives PresenceMonitor's roster-copy
              path into the mesh sample unmodified, the same pipeline the rest of
              RouterHealth's fields already ride (D75/D76), now carrying one more field.

Run from the repo root (see router/test_e2e/README.md).
"""

import sys
import time
from pathlib import Path

import rti.connextdds as dds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import CONFIG_DIR, start_router  # noqa: E402
from util.dds_probe import (  # noqa: E402
    AdminChannel, COMMAND_KIND, Probe, reader_qos)

FIXTURE = "e2e_presence_team.yaml"
ROUTER_NAME = "e2e-presence-team"
NODE_A = "Platform_30"
NODE_B = "Platform_31"
HEALTH_TOPIC = "RouterHealth"
MESH_TOPIC = "ActRouterMeshStatus"
KIND = COMMAND_KIND


def _render(domains, out_name, e2e_tmp_dir):
    text = (CONFIG_DIR / FIXTURE).read_text()
    text = text.replace("__DOMAIN_CONTROL_LAN__", str(domains["control_lan"]))
    text = text.replace("__DOMAIN_WAN__", str(domains["wan"]))
    text = text.replace("__DOMAIN_PLATFORM_LAN__", str(domains["platform_lan"]))
    out = e2e_tmp_dir / out_name
    out.write_text(text)
    return out


def _partition_cmd(command_type, node_name, command_id, kind, partition_name):
    c = dds.DynamicData(command_type)
    c["target_node"] = node_name
    c["target_router"] = ROUTER_NAME
    c["command_id"] = command_id
    c["kind"] = KIND[kind]
    c["participant_name"] = "team_wan"
    c["partition_name"] = partition_name
    return c


def health_reader(probe, provider):
    return probe.reader(
        HEALTH_TOPIC, "RouterHealth",
        qos=reader_qos(reliability="reliable", durability="transient_local"),
        dtype=provider.type("RouterHealth"))


def mesh_reader(probe, provider):
    return probe.reader(
        MESH_TOPIC, "RouterMeshStatus",
        qos=reader_qos(reliability="reliable", durability="transient_local"),
        dtype=provider.type("RouterMeshStatus"))


def latest_team_partitions(reader):
    """{router_name: set(team_partition)} from the newest RouterHealth sample per
    router (TL KEEP_LAST(1) keyed by name, D79)."""
    out = {}
    for sample in reader.read():
        if not sample.info.valid:
            continue
        out[str(sample.data["router"])] = {
            str(v) for v in sample.data["team_partition"]}
    return out


def latest_mesh_team_partitions(reader):
    """{peer_router_name: set(team_partition)} from the newest mesh sample."""
    out = None
    for sample in reader.read():
        if not sample.info.valid:
            continue
        peers = {}
        for peer in sample.data["peers"]:
            peers[str(peer["health.router"])] = {
                str(v) for v in peer["health.team_partition"]}
        out = peers
    return out


def _wait(fn, predicate, timeout_s, check_alive, poll_s=0.2):
    deadline = time.monotonic() + timeout_s
    value = None
    while time.monotonic() < deadline:
        assert check_alive(), "a router process exited early"
        value = fn()
        if predicate(value):
            return value
        time.sleep(poll_s)
    return value


def test_router_health_team_partition(router_binary, admin_types_xml, e2e_tmp_dir,
                                      unique_domains):
    domains = {
        "control_lan": unique_domains["control_lan"],
        "wan": unique_domains["wan"],
    }
    cfg_a = _render({**domains, "platform_lan": unique_domains["platform_lan"]},
                     "team_a.yaml", e2e_tmp_dir)
    cfg_b = _render({**domains, "platform_lan": unique_domains["platform_lan"] + 1000},
                     "team_b.yaml", e2e_tmp_dir)

    control_proc = start_router(router_binary, cfg_a, "control", e2e_tmp_dir,
                                node_name="Control_20", name="e2e-control-role",
                                admin_participant="control_lan")
    proc_a = start_router(router_binary, cfg_a, "platform", e2e_tmp_dir,
                          node_name=NODE_A, admin_participant="platform_lan")
    proc_b = start_router(router_binary, cfg_b, "platform", e2e_tmp_dir,
                          node_name=NODE_B, admin_participant="platform_lan")
    alive = lambda: (control_proc.is_alive() and proc_a.is_alive()  # noqa: E731
                     and proc_b.is_alive())

    wan_probe = Probe(domains["wan"])
    control_lan_probe = Probe(domains["control_lan"])
    lan_a = Probe(unique_domains["platform_lan"])
    lan_b = Probe(unique_domains["platform_lan"] + 1000)
    try:
        provider = dds.QosProvider(str(admin_types_xml))
        health = health_reader(wan_probe, provider)
        mesh = mesh_reader(control_lan_probe, provider)

        # --- E-untagged: no team assigned yet, protected identity only ---
        parts = _wait(lambda: latest_team_partitions(health),
                      lambda p: NODE_A in p and NODE_B in p,
                      timeout_s=15.0, check_alive=alive)
        assert parts.get(f"{NODE_A}/{ROUTER_NAME}") == {NODE_A}, (
            f"expected node A's team_partition == protected identity only before any "
            f"team assignment; got {parts}; logs: a={proc_a.log_path}")
        assert parts.get(f"{NODE_B}/{ROUTER_NAME}") == {NODE_B}, (
            f"expected node B's team_partition == protected identity only before any "
            f"team assignment; got {parts}; logs: b={proc_b.log_path}")

        # --- E-tagged: ADD_PARTICIPANT_PARTITION team_wan=TEAM_A on both ---
        admin_provider = provider
        cmd_type_admin = admin_provider.type("RouterCommand")
        adm_a = AdminChannel(lan_a, admin_provider)
        adm_b = AdminChannel(lan_b, admin_provider)
        adm_a.cmd_writer.write(
            _partition_cmd(cmd_type_admin, NODE_A, "t1", "ADD_PARTICIPANT_PARTITION",
                          "TEAM_A"))
        ack_a = adm_a.acks.wait("t1", check_alive=alive)
        assert ack_a is not None and ack_a["accepted"], \
            f"node A ADD_PARTICIPANT_PARTITION not accepted: {ack_a}"
        adm_b.cmd_writer.write(
            _partition_cmd(cmd_type_admin, NODE_B, "t2", "ADD_PARTICIPANT_PARTITION",
                          "TEAM_A"))
        ack_b = adm_b.acks.wait("t2", check_alive=alive)
        assert ack_b is not None and ack_b["accepted"], \
            f"node B ADD_PARTICIPANT_PARTITION not accepted: {ack_b}"

        expect_a = {NODE_A, "TEAM_A"}
        expect_b = {NODE_B, "TEAM_A"}
        parts2 = _wait(
            lambda: latest_team_partitions(health),
            lambda p: p.get(f"{NODE_A}/{ROUTER_NAME}") == expect_a
                      and p.get(f"{NODE_B}/{ROUTER_NAME}") == expect_b,
            timeout_s=15.0, check_alive=alive)
        assert parts2.get(f"{NODE_A}/{ROUTER_NAME}") == expect_a, (
            f"node A's RouterHealth.team_partition never reflected the TEAM_A join; "
            f"got {parts2}; logs: a={proc_a.log_path}")
        assert parts2.get(f"{NODE_B}/{ROUTER_NAME}") == expect_b, (
            f"node B's RouterHealth.team_partition never reflected the TEAM_A join; "
            f"got {parts2}; logs: b={proc_b.log_path}")

        # --- E-mesh: the control node's own ActRouterMeshStatus carries the same value
        # (this is the exact data gui/mesh_dashboard's mesh_graph.js reads) ---
        mesh_parts = _wait(
            lambda: latest_mesh_team_partitions(mesh),
            lambda p: p is not None
                      and p.get(f"{NODE_A}/{ROUTER_NAME}") == expect_a
                      and p.get(f"{NODE_B}/{ROUTER_NAME}") == expect_b,
            timeout_s=15.0, check_alive=alive)
        assert mesh_parts is not None, (
            f"control node never saw an ActRouterMeshStatus sample; "
            f"log={control_proc.log_path}")
        assert mesh_parts.get(f"{NODE_A}/{ROUTER_NAME}") == expect_a, (
            f"control's mesh aggregate did not carry node A's team_partition through "
            f"unmodified; got {mesh_parts}")
        assert mesh_parts.get(f"{NODE_B}/{ROUTER_NAME}") == expect_b, (
            f"control's mesh aggregate did not carry node B's team_partition through "
            f"unmodified; got {mesh_parts}")
    finally:
        wan_probe.close()
        control_lan_probe.close()
        lan_a.close()
        lan_b.close()
        control_proc.stop()
        proc_a.stop()
        proc_b.stop()
