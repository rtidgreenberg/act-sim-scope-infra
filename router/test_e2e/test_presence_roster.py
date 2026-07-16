"""Phase 8 presence & health e2e (D75/D76; design: docs/cpp_router/presence-and-health.md).

Two router_main processes (control + platform roles, config/e2e_presence.yaml) heartbeat
RouterHealth on their role's WAN participant (shared WAN domain) and each publishes the
ActRouterMeshStatus aggregate on its admin (LAN) participant. Asserts the Phase 8
evidence items (implementation-plan.md, mapped 1:1):

  E-P1  each router's mesh aggregate names the other ALIVE with a fresh last_seen_delta
        and the compact rollup (n_routes from the config's one route).
  E-P2  SIGKILL the platform router: the control router marks it DEAD within the
        RouterHealth liveliness window (3 s lease + assert-interval margin) and
        republishes the mesh. The WAN participants here run the DEFAULT participant
        lease (100 s), so a DEAD inside seconds is proof the health-topic liveliness —
        not participant purge — was the signal (the D16 ordering, spike-proven with the
        purge backstop observed in spikes/presence/).
  E-P3  a probe asserting liveliness but withholding heartbeats past the 2 s DEADLINE is
        marked STALE, not DEAD, and nothing is torn down (the routers stay ALIVE to each
        other; the probe stays STALE across a hold longer than the liveliness lease).
  E-P4  the killed router restarted under the same identity (new process, new GUID)
        re-enters the roster ALIVE under the same router_id.

The D74 identity flip itself (participant_name detection, same-node ignore off the new
field) is asserted by test_discovery_smoke.py and test_same_node_ignore.py.

Run from the repo root (see router/test_e2e/README.md).
"""

import sys
import time
from pathlib import Path

import rti.connextdds as dds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import start_router  # noqa: E402
from util.dds_probe import Probe, reader_qos  # noqa: E402

MESH_TOPIC = "ActRouterMeshStatus"
HEALTH_TOPIC = "RouterHealth"
PLATFORM_ID = 30   # e2e_presence.yaml router.id (platform keeps the file's default)
CONTROL_ID = 20    # conftest router_pair's --router-id override for the control role

ALIVE, STALE, DEAD = 0, 1, 2  # RouterPresenceState ordinals (declaration order)
PRESENCE = {0: "ALIVE", 1: "STALE", 2: "DEAD"}

# D75-pinned demo numbers (must mirror router/src/core/PresenceMonitor.hpp).
HEARTBEAT_PERIOD_S = 1.0
HEALTH_DEADLINE_S = 2.0
HEALTH_LEASE_S = 3.0


def mesh_reader(probe, provider):
    """RELIABLE+TRANSIENT_LOCAL reader for the mesh aggregate (KEEP_LAST(1) state topic,
    same shape as ActRouterStatus)."""
    return probe.reader(
        MESH_TOPIC, "RouterMeshStatus",
        qos=reader_qos(reliability="reliable", durability="transient_local"),
        dtype=provider.type("RouterMeshStatus"))


def latest_mesh(reader):
    """{router_id: {presence, last_seen_delta_ms, n_routes, heartbeat_seq}} from the
    newest mesh sample, or None if none seen yet. read() — TL KEEP_LAST(1) state."""
    out = None
    for sample in reader.read():
        if not sample.info.valid:
            continue
        peers = {}
        for peer in sample.data["peers"]:
            peers[int(peer["health.router_id"])] = {
                "presence": int(peer["presence"]),
                "last_seen_delta_ms": int(peer["last_seen_delta_ms"]),
                "n_routes": int(peer["health.n_routes"]),
                "heartbeat_seq": int(peer["health.heartbeat_seq"]),
            }
        out = peers
    return out


def wait_mesh(reader, predicate, timeout_s, alive=None, poll_s=0.2):
    """Poll latest_mesh until predicate(peers) is truthy; return peers (last seen on
    timeout)."""
    deadline = time.monotonic() + timeout_s
    peers = None
    while time.monotonic() < deadline:
        if alive is not None:
            assert alive(), "a router process exited early"
        peers = latest_mesh(reader)
        if peers is not None and predicate(peers):
            return peers
        time.sleep(poll_s)
    return peers


def test_presence_roster_mesh_dead_and_rejoin(
        router_binary, admin_types_xml, e2e_tmp_dir, unique_domains, router_pair):
    control, platform, config_path = router_pair("e2e_presence.yaml", unique_domains)
    provider = dds.QosProvider(str(admin_types_xml))

    control_lan = unique_domains["control_lan"]
    wan = unique_domains["wan"]

    control_mesh_probe = Probe(control_lan)
    platform_mesh_probe = Probe(unique_domains["platform_lan"])
    stale_probe = None
    restarted = None
    try:
        control_view = mesh_reader(control_mesh_probe, provider)
        platform_view = mesh_reader(platform_mesh_probe, provider)
        both_alive = lambda: control.is_alive() and platform.is_alive()  # noqa: E731

        # --- E-P1: each side's aggregate names the other ALIVE with a fresh delta ---
        peers = wait_mesh(control_view,
                          lambda p: p.get(PLATFORM_ID, {}).get("presence") == ALIVE,
                          timeout_s=20.0, alive=both_alive)
        assert peers and peers.get(PLATFORM_ID, {}).get("presence") == ALIVE, (
            f"control mesh never showed platform ({PLATFORM_ID}) ALIVE: {peers}; "
            f"log {control.log_path}")
        assert peers[PLATFORM_ID]["n_routes"] == 1, peers  # the config's one route
        # The delta is stamped at mesh write time; the aggregate republishes on roster
        # change, so the retained sample's delta reflects a recent heartbeat.
        assert peers[PLATFORM_ID]["last_seen_delta_ms"] < 5000, peers
        peers_p = wait_mesh(platform_view,
                            lambda p: p.get(CONTROL_ID, {}).get("presence") == ALIVE,
                            timeout_s=20.0, alive=both_alive)
        assert peers_p and peers_p.get(CONTROL_ID, {}).get("presence") == ALIVE, (
            f"platform mesh never showed control ({CONTROL_ID}) ALIVE: {peers_p}; "
            f"log {platform.log_path}")

        # --- E-P3 first (needs the platform router still alive as a bystander): a
        # heartbeat-withholding probe goes STALE, never DEAD, nothing torn down ---
        stale_probe = Probe(wan)
        health_qos = dds.DataWriterQos()
        health_qos.reliability = dds.Reliability(kind=dds.ReliabilityKind.RELIABLE)
        health_qos.durability = dds.Durability(
            kind=dds.DurabilityKind.TRANSIENT_LOCAL)
        health_qos.deadline = dds.Deadline(
            period=dds.Duration.from_milliseconds(int(HEALTH_DEADLINE_S * 1000)))
        health_qos.liveliness = dds.Liveliness(
            dds.LivelinessKind.AUTOMATIC,
            dds.Duration.from_milliseconds(int(HEALTH_LEASE_S * 1000)))
        health_type = provider.type("RouterHealth")
        probe_writer = stale_probe.writer(HEALTH_TOPIC, "RouterHealth",
                                          qos=health_qos, dtype=health_type)
        beat = dds.DynamicData(health_type)
        beat["router_id"] = 77
        beat["node_name"] = "ProbeNode"
        beat["role"] = "probe"
        for seq in range(3):  # a few heartbeats, then silence (liveliness stays: the
            beat["heartbeat_seq"] = seq  # probe's participant is alive => AUTOMATIC
            probe_writer.write(beat)     # liveliness keeps asserting)
            time.sleep(HEARTBEAT_PERIOD_S)
        peers = wait_mesh(control_view,
                          lambda p: p.get(77, {}).get("presence") == STALE,
                          timeout_s=HEALTH_DEADLINE_S + 6, alive=both_alive)
        assert peers and peers.get(77, {}).get("presence") == STALE, (
            f"control mesh never marked the silent probe STALE: {peers}; "
            f"log {control.log_path}")
        # Hold past the liveliness lease: STALE must not escalate (policy flag, not an
        # action) and the real routers must be untouched.
        time.sleep(HEALTH_LEASE_S + 2)
        peers = latest_mesh(control_view)
        assert peers[77]["presence"] == STALE, (
            f"silent-but-live probe escalated to {PRESENCE.get(peers[77]['presence'])}")
        assert peers[PLATFORM_ID]["presence"] == ALIVE, peers
        stale_probe.close()  # graceful close -> instance NO_WRITERS; 77 goes DEAD, fine
        stale_probe = None

        # --- E-P2: SIGKILL the platform router -> DEAD inside the liveliness window ---
        t0 = time.monotonic()
        platform.kill()
        peers = wait_mesh(control_view,
                          lambda p: p.get(PLATFORM_ID, {}).get("presence") == DEAD,
                          timeout_s=HEALTH_LEASE_S + 7,
                          alive=lambda: control.is_alive())
        t_dead = time.monotonic() - t0
        assert peers and peers.get(PLATFORM_ID, {}).get("presence") == DEAD, (
            f"control mesh never marked the killed platform router DEAD: {peers}; "
            f"log {control.log_path}")
        # The WAN participants run the DEFAULT participant lease (100 s), so a DEAD
        # observed within seconds is liveliness-driven — the participant is still in the
        # discovery DB (D16 ordering; purge trailing was measured in spikes/presence/).
        assert t_dead < HEALTH_LEASE_S + 7, f"DEAD took {t_dead:.1f}s"

        # --- E-P4: restart under the same identity -> rejoins ALIVE, same router_id ---
        restarted = start_router(router_binary, config_path, "platform", e2e_tmp_dir,
                                 admin_participant="platform_lan")
        peers = wait_mesh(control_view,
                          lambda p: p.get(PLATFORM_ID, {}).get("presence") == ALIVE,
                          timeout_s=25.0,
                          alive=lambda: control.is_alive() and restarted.is_alive())
        assert peers and peers.get(PLATFORM_ID, {}).get("presence") == ALIVE, (
            f"restarted platform router never re-entered the roster ALIVE: {peers}; "
            f"logs {control.log_path} {restarted.log_path}")
        # New process restarts heartbeat_seq near zero — evidence this ALIVE is the NEW
        # incarnation (new GUID) joined under the same router_id, not a stale sample.
        assert peers[PLATFORM_ID]["heartbeat_seq"] < 20, peers
    finally:
        if stale_probe is not None:
            stale_probe.close()
        if restarted is not None:
            restarted.stop()
        control_mesh_probe.close()
        platform_mesh_probe.close()
