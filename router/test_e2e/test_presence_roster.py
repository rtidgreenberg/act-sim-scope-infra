"""Phase 8 presence & health e2e, re-keyed by the router NAME (D75/D76 + the D79/D80
rework; design: docs/cpp_router/presence-and-health.md).

Two router_main processes (control + platform roles, config/e2e_presence.yaml) heartbeat
RouterHealth on their role's WAN participant (shared WAN domain) and each publishes the
ActRouterMeshStatus aggregate on its admin (LAN) participant. Asserts the Phase 8
evidence items re-proven on the name key (D79 addendum's E-R map):

  E-R1  RouterHealth samples are keyed by "<node>/<router>", carry no router_id, and the
        key equals the publishing participant's participant_name (the D74 join,
        observable off builtin DCPSParticipant data on the same WAN domain).
  E-R2  the Phase 8 roster/mesh evidence on the new key (old E-P1/E-P2/E-P4):
        each router's mesh aggregate names the other ALIVE by name with a fresh
        last_seen_delta; SIGKILL -> DEAD by name within the RouterHealth liveliness
        window (the WAN participants run the DEFAULT participant lease of 100 s, so a
        DEAD inside seconds is health-topic liveliness, not participant purge — D16);
        restart under the SAME name re-enters ALIVE with a fresh heartbeat_seq.
  E-R3  (test_config_hash_drift) every RouterHealth sample carries config_hash equal to
        a digest computed independently from the same file; two routers loading the
        same file publish EQUAL hashes; a router loading a modified copy publishes a
        DIFFERENT one (D80 drift detection end-to-end).
  E-P3  (unchanged) a probe asserting liveliness but withholding heartbeats past the
        2 s DEADLINE is marked STALE, not DEAD, and nothing is torn down.

D77 extensions ride E-R2: each router's RouterHealth heartbeat carries the roster as a
compact peers_seen edge list keyed by name, so a single WAN observer (C2) can build the
who-sees-who node map — the bidirectional edge is asserted from one reader, and after
the SIGKILL the survivor's edge FLIPS to DEAD rather than disappearing.

The D74 identity flip itself (participant_name detection, same-node ignore off the new
field) is asserted by test_discovery_smoke.py and test_same_node_ignore.py (E-R6 —
untouched by the rework).

Run from the repo root (see router/test_e2e/README.md).
"""

import hashlib
import sys
import time
from pathlib import Path

import rti.connextdds as dds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import start_router  # noqa: E402
from util.dds_probe import Probe, reader_qos  # noqa: E402

MESH_TOPIC = "ActRouterMeshStatus"
HEALTH_TOPIC = "RouterHealth"
# "<node>/<router>" identities (D79): node half from e2e_presence.yaml / the conftest
# --node-name override, router half from router.name / the --name override.
PLATFORM = "Platform_30/e2e-presence"
CONTROL = "Control_20/e2e-control-role"

ALIVE, STALE, DEAD = 0, 1, 2  # RouterPresenceState ordinals (declaration order)
PRESENCE = {0: "ALIVE", 1: "STALE", 2: "DEAD"}

# D75-pinned demo numbers (must mirror router/src/core/PresenceMonitor.hpp).
HEARTBEAT_PERIOD_S = 1.0
HEALTH_DEADLINE_S = 2.0
HEALTH_LEASE_S = 3.0


def sha256_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def mesh_reader(probe, provider):
    """RELIABLE+TRANSIENT_LOCAL reader for the mesh aggregate (KEEP_LAST(1) state topic,
    same shape as ActRouterStatus)."""
    return probe.reader(
        MESH_TOPIC, "RouterMeshStatus",
        qos=reader_qos(reliability="reliable", durability="transient_local"),
        dtype=provider.type("RouterMeshStatus"))


def health_reader(probe, provider):
    return probe.reader(
        HEALTH_TOPIC, "RouterHealth",
        qos=reader_qos(reliability="reliable", durability="transient_local"),
        dtype=provider.type("RouterHealth"))


def latest_health(reader):
    """{router_name: {peer_router_name: presence}} from the newest RouterHealth sample
    per router (TL KEEP_LAST(1) keyed by the name, D79) — the C2 node map's edges (D77):
    nodes are the heartbeats themselves, edges each sample's peers_seen."""
    edges = {}
    for sample in reader.read():
        if not sample.info.valid:
            continue
        edges[str(sample.data["router"])] = {
            str(ref["router"]): int(ref["presence"])
            for ref in sample.data["peers_seen"]}
    return edges


def latest_health_fields(reader):
    """{router_name: {field: value}} for the non-edge payload of the newest RouterHealth
    sample per router (config_hash for E-R3, heartbeat_seq, ...)."""
    out = {}
    for sample in reader.read():
        if not sample.info.valid:
            continue
        out[str(sample.data["router"])] = {
            "config_hash": str(sample.data["config_hash"]),
            "heartbeat_seq": int(sample.data["heartbeat_seq"]),
        }
    return out


def wait_edges(reader, predicate, timeout_s, alive=None, poll_s=0.2):
    deadline = time.monotonic() + timeout_s
    edges = {}
    while time.monotonic() < deadline:
        if alive is not None:
            assert alive(), "a router process exited early"
        edges = latest_health(reader)
        if predicate(edges):
            return edges
        time.sleep(poll_s)
    return edges


def wait_health_fields(reader, predicate, timeout_s, alive=None, poll_s=0.2):
    deadline = time.monotonic() + timeout_s
    fields = {}
    while time.monotonic() < deadline:
        if alive is not None:
            assert alive(), "a router process exited early"
        fields = latest_health_fields(reader)
        if predicate(fields):
            return fields
        time.sleep(poll_s)
    return fields


def latest_mesh(reader):
    """{router_name: {presence, last_seen_delta_ms, n_routes, heartbeat_seq}} from the
    newest mesh sample, or None if none seen yet. read() — TL KEEP_LAST(1) state."""
    out = None
    for sample in reader.read():
        if not sample.info.valid:
            continue
        peers = {}
        for peer in sample.data["peers"]:
            peers[str(peer["health.router"])] = {
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
    wan_probe = None
    stale_probe = None
    restarted = None
    try:
        control_view = mesh_reader(control_mesh_probe, provider)
        platform_view = mesh_reader(platform_mesh_probe, provider)
        both_alive = lambda: control.is_alive() and platform.is_alive()  # noqa: E731

        # --- E-R2: each side's aggregate names the other ALIVE (by name) with a fresh
        # delta ---
        peers = wait_mesh(control_view,
                          lambda p: p.get(PLATFORM, {}).get("presence") == ALIVE,
                          timeout_s=20.0, alive=both_alive)
        assert peers and peers.get(PLATFORM, {}).get("presence") == ALIVE, (
            f"control mesh never showed platform ({PLATFORM}) ALIVE: {peers}; "
            f"log {control.log_path}")
        assert peers[PLATFORM]["n_routes"] == 1, peers  # the config's one route
        # The delta is stamped at mesh write time; the aggregate republishes on roster
        # change, so the retained sample's delta reflects a recent heartbeat.
        assert peers[PLATFORM]["last_seen_delta_ms"] < 5000, peers
        peers_p = wait_mesh(platform_view,
                            lambda p: p.get(CONTROL, {}).get("presence") == ALIVE,
                            timeout_s=20.0, alive=both_alive)
        assert peers_p and peers_p.get(CONTROL, {}).get("presence") == ALIVE, (
            f"platform mesh never showed control ({CONTROL}) ALIVE: {peers_p}; "
            f"log {platform.log_path}")

        # --- E-R2 extension (D77): the heartbeats themselves carry the who-sees-who
        # edges (by name), so a single WAN observer (C2) can draw the node map — both
        # directions of the control<->platform edge visible from one topic.
        wan_probe = Probe(wan, spdp2=True)  # WAN domain: router WAN participants are SPDP2 (D94)
        health_view = health_reader(wan_probe, provider)
        edges = wait_edges(
            health_view,
            lambda e: (e.get(CONTROL, {}).get(PLATFORM) == ALIVE
                       and e.get(PLATFORM, {}).get(CONTROL) == ALIVE),
            timeout_s=15.0, alive=both_alive)
        assert edges.get(CONTROL, {}).get(PLATFORM) == ALIVE \
            and edges.get(PLATFORM, {}).get(CONTROL) == ALIVE, (
            f"heartbeat peers_seen never showed the bidirectional edge (D77): {edges}; "
            f"logs {control.log_path} {platform.log_path}")

        # --- E-R1: the RouterHealth key IS the D74 participant_name — every name seen
        # as a heartbeat key on the WAN also appears as a discovered participant's
        # EntityName on the same domain (the discovery-plane join, no side table).
        discovered = {name for name, _role
                      in wan_probe.discovered_participant_names().values()}
        for key in (CONTROL, PLATFORM):
            assert key in discovered, (
                f"heartbeat key {key!r} not among discovered participant names "
                f"{sorted(discovered)} — the D74/D79 name join is broken")

        # --- E-P3 first (needs the platform router still alive as a bystander): a
        # heartbeat-withholding probe goes STALE, never DEAD, nothing torn down ---
        stale_probe = Probe(wan, spdp2=True)  # WAN domain: router WAN participants are SPDP2 (D94)
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
        beat["router"] = "ProbeNode/probe"
        beat["role"] = "probe"
        for seq in range(3):  # a few heartbeats, then silence (liveliness stays: the
            beat["heartbeat_seq"] = seq  # probe's participant is alive => AUTOMATIC
            probe_writer.write(beat)     # liveliness keeps asserting)
            time.sleep(HEARTBEAT_PERIOD_S)
        peers = wait_mesh(control_view,
                          lambda p: p.get("ProbeNode/probe", {}).get("presence") == STALE,
                          timeout_s=HEALTH_DEADLINE_S + 6, alive=both_alive)
        assert peers and peers.get("ProbeNode/probe", {}).get("presence") == STALE, (
            f"control mesh never marked the silent probe STALE: {peers}; "
            f"log {control.log_path}")
        # Hold past the liveliness lease: STALE must not escalate (policy flag, not an
        # action) and the real routers must be untouched.
        time.sleep(HEALTH_LEASE_S + 2)
        peers = latest_mesh(control_view)
        assert peers["ProbeNode/probe"]["presence"] == STALE, (
            f"silent-but-live probe escalated to "
            f"{PRESENCE.get(peers['ProbeNode/probe']['presence'])}")
        assert peers[PLATFORM]["presence"] == ALIVE, peers
        stale_probe.close()  # graceful close -> instance NO_WRITERS; probe goes DEAD, fine
        stale_probe = None

        # --- E-R2: SIGKILL the platform router -> DEAD (by name) inside the liveliness
        # window ---
        t0 = time.monotonic()
        platform.kill()
        peers = wait_mesh(control_view,
                          lambda p: p.get(PLATFORM, {}).get("presence") == DEAD,
                          timeout_s=HEALTH_LEASE_S + 7,
                          alive=lambda: control.is_alive())
        t_dead = time.monotonic() - t0
        assert peers and peers.get(PLATFORM, {}).get("presence") == DEAD, (
            f"control mesh never marked the killed platform router DEAD: {peers}; "
            f"log {control.log_path}")
        # The WAN participants run the DEFAULT participant lease (100 s), so a DEAD
        # observed within seconds is liveliness-driven — the participant is still in the
        # discovery DB (D16 ordering; purge trailing was measured in spikes/presence/).
        assert t_dead < HEALTH_LEASE_S + 7, f"DEAD took {t_dead:.1f}s"
        # E-R2 extension (D77): the edge FLIPS to DEAD on the survivor's heartbeat
        # rather than disappearing — C2 can tell "lost this peer" from "never saw it".
        edges = wait_edges(
            health_view,
            lambda e: e.get(CONTROL, {}).get(PLATFORM) == DEAD,
            timeout_s=10.0, alive=lambda: control.is_alive())
        assert edges.get(CONTROL, {}).get(PLATFORM) == DEAD, (
            f"control heartbeat peers_seen never flipped the platform edge to DEAD "
            f"(D77): {edges}; log {control.log_path}")

        # --- E-R2: restart under the same identity -> rejoins ALIVE, same name ---
        restarted = start_router(router_binary, config_path, "platform", e2e_tmp_dir,
                                 admin_participant="platform_lan")
        peers = wait_mesh(control_view,
                          lambda p: p.get(PLATFORM, {}).get("presence") == ALIVE,
                          timeout_s=25.0,
                          alive=lambda: control.is_alive() and restarted.is_alive())
        assert peers and peers.get(PLATFORM, {}).get("presence") == ALIVE, (
            f"restarted platform router never re-entered the roster ALIVE: {peers}; "
            f"logs {control.log_path} {restarted.log_path}")
        # New process restarts heartbeat_seq near zero — evidence this ALIVE is the NEW
        # incarnation (new GUID) joined under the same name, not a stale sample.
        assert peers[PLATFORM]["heartbeat_seq"] < 20, peers

        # The own-heartbeat/duplicate split now rides the publication handle (D79 —
        # same key means same instance, so content can't tell them apart): a healthy
        # run must never mistake its own reflection for a duplicate identity.
        for proc in (control, restarted):
            log_text = proc.log_path.read_text()
            assert "presence_duplicate_identity" not in log_text, (
                f"router warned presence_duplicate_identity for its own heartbeat — "
                f"the publication-handle self-check is broken; log {proc.log_path}")
    finally:
        if stale_probe is not None:
            stale_probe.close()
        if restarted is not None:
            restarted.stop()
        if wan_probe is not None:
            wan_probe.close()
        control_mesh_probe.close()
        platform_mesh_probe.close()


def test_config_hash_drift(
        router_binary, admin_types_xml, e2e_tmp_dir, unique_domains, router_pair):
    """E-R3 (D80): config_hash is observable end-to-end — both routers of the pair load
    the SAME file and publish EQUAL hashes matching an independently computed SHA-256 of
    that file's bytes; a third router loading a modified copy publishes a DIFFERENT hash
    (drift visible to a single WAN observer on the topic C2 already watches)."""
    control, platform, config_path = router_pair("e2e_presence.yaml", unique_domains)
    provider = dds.QosProvider(str(admin_types_xml))

    expected = sha256_of(config_path)
    assert len(expected) == 64

    # A modified copy: one appended comment line — different bytes, same semantics.
    DRIFTER = "Platform_31/e2e-presence"
    drift_path = config_path.parent / "e2e_presence_drift.yaml"
    drift_path.write_text(config_path.read_text() + "# drift marker (E-R3)\n")
    drift_hash = sha256_of(drift_path)
    assert drift_hash != expected

    wan_probe = Probe(unique_domains["wan"], spdp2=True)  # WAN domain: router WAN participants are SPDP2 (D94)
    drifter = None
    try:
        health_view = health_reader(wan_probe, provider)
        both_alive = lambda: control.is_alive() and platform.is_alive()  # noqa: E731

        fields = wait_health_fields(
            health_view,
            lambda f: CONTROL in f and PLATFORM in f,
            timeout_s=20.0, alive=both_alive)
        assert CONTROL in fields and PLATFORM in fields, (
            f"never heard both routers' heartbeats: {sorted(fields)}; "
            f"logs {control.log_path} {platform.log_path}")
        # Same file -> EQUAL hashes, both matching the independent digest.
        assert fields[CONTROL]["config_hash"] == expected, fields
        assert fields[PLATFORM]["config_hash"] == expected, fields

        drifter = start_router(router_binary, drift_path, "platform", e2e_tmp_dir,
                               node_name="Platform_31",
                               admin_participant="platform_lan")
        fields = wait_health_fields(
            health_view,
            lambda f: DRIFTER in f,
            timeout_s=25.0,
            alive=lambda: both_alive() and drifter.is_alive())
        assert DRIFTER in fields, (
            f"drift router never heartbeated: {sorted(fields)}; log {drifter.log_path}")
        # Modified copy -> DIFFERENT hash, matching ITS independent digest.
        assert fields[DRIFTER]["config_hash"] == drift_hash, fields
        assert fields[DRIFTER]["config_hash"] != expected, fields
    finally:
        if drifter is not None:
            drifter.stop()
        wan_probe.close()
