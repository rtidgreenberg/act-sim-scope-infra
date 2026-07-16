#!/usr/bin/env python3
"""Presence spike — validate the Phase 8 presence mechanism + D74 identity.

Claims under test (see PLAN.md):
  A. D74 identity: `participant_name` (name="<node>/<router>", role_name="act.router") is
     readable off the builtin DCPSParticipant sample at discovery; detection keys on
     role_name ALONE (an app with a fancy display name but no sentinel role is not a router).
  B. THE MONEY CLAIM (D16 ordering): SIGKILL a peer -> survivors mark it DEAD via
     RouterHealth liveliness loss INSIDE the liveliness window (~3s), while the peer's data
     writer is STILL MATCHED (participant not yet purged); participant purge trails (~7-10s)
     and only then drives NOT_ALIVE_NO_WRITERS on the co-tested data topic (the backstop).
     The LAN ActRouterMeshStatus aggregate updates ALIVE -> DEAD; other peers stay ALIVE.
  C. STALE is not DEAD: a peer that keeps liveliness asserted but withholds heartbeats past
     the DEADLINE is marked STALE (policy flag); over a hold past the liveliness lease it is
     never marked DEAD and nothing is torn down.
  D. Restart/GUID join (D74): a killed-and-restarted peer re-enters the roster ALIVE under
     the same router_id, with the same participant_name under a NEW participant GUID.

Peers are subprocesses of this script (`peer` subcommand) so SIGKILL is real. UDPv4-only
everywhere (a SIGKILLed participant leaks nothing in /dev/shm). Types are programmatic
dds.StructType mirrors of the presence-and-health.md IDL sketch.

Exit 0 = all parts pass. Run:  python3 spikes/presence/presence_spike.py [base_domain]
"""

import argparse
import os
import select
import subprocess
import sys
import time

os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault(
    "RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

import rti.connextdds as dds  # noqa: E402

# ---- The pinned demo numbers (Phase 8 readiness item 3; D16 ordering: WAN_LEASE > LEASE) --
HEARTBEAT_PERIOD_S = 1.0
HEALTH_DEADLINE_S = 2.0            # 2x heartbeat period
HEALTH_LIVELINESS_LEASE_S = 3.0    # 3x heartbeat period
WAN_PARTICIPANT_LEASE_S = 10       # D16: MUST stay > HEALTH_LIVELINESS_LEASE_S
WAN_PARTICIPANT_ASSERT_S = 3

HEALTH_TOPIC = "RouterHealthSpike"
DATA_TOPIC = "PresenceSpikeData"
MESH_TOPIC = "ActRouterMeshStatusSpike"
ROLE_SENTINEL = "act.router"       # D74 detection key

ALIVE, STALE, DEAD = 0, 1, 2
PRESENCE_NAME = {ALIVE: "ALIVE", STALE: "STALE", DEAD: "DEAD"}

DEFAULT_BASE_DOMAIN = 71


class SpikeError(AssertionError):
    pass


# ---------------------------------------------------------------- types (IDL-sketch mirror)
_types = {}


def _type(name):
    if name in _types:
        return _types[name]
    if name == "RouterHealth":
        t = dds.StructType("RouterHealth")
        t.add_member(dds.Member("router_id", dds.Uint32Type(), is_key=True))
        t.add_member(dds.Member("node_name", dds.StringType(128)))
        t.add_member(dds.Member("role", dds.StringType(64)))
        t.add_member(dds.Member("heartbeat_seq", dds.Uint64Type()))
        t.add_member(dds.Member("send_timestamp", dds.Int64Type()))
        t.add_member(dds.Member("state_revision", dds.StringType(32)))
        t.add_member(dds.Member("n_routes", dds.Uint32Type()))
        t.add_member(dds.Member("n_degraded", dds.Uint32Type()))
        t.add_member(dds.Member("n_error", dds.Uint32Type()))
        t.add_member(dds.Member("overall_state", dds.Int32Type()))
    elif name == "PresenceData":
        # The co-tested data topic: keyed by source so each peer is its own instance and
        # participant purge drives a per-peer NOT_ALIVE_NO_WRITERS (the backstop signal).
        t = dds.StructType("PresenceData")
        t.add_member(dds.Member("source", dds.StringType(128), is_key=True))
        t.add_member(dds.Member("seq", dds.Uint64Type()))
    elif name == "RouterMeshPeer":
        t = dds.StructType("RouterMeshPeer")
        t.add_member(dds.Member("health", _type("RouterHealth")))
        t.add_member(dds.Member("presence", dds.Int32Type()))
        t.add_member(dds.Member("last_seen_delta_ms", dds.Int64Type()))
    elif name == "RouterMeshStatus":
        t = dds.StructType("RouterMeshStatus")
        t.add_member(dds.Member("observer_node", dds.StringType(128), is_key=True))
        t.add_member(dds.Member("observer_router", dds.StringType(128), is_key=True))
        t.add_member(dds.Member("state_revision", dds.StringType(32)))
        t.add_member(dds.Member("peers", dds.SequenceType(_type("RouterMeshPeer"), 32)))
    else:
        raise KeyError(name)
    _types[name] = t
    return t


# ------------------------------------------------------------------------- entity builders
def make_participant(domain, name=None, role=None):
    """UDPv4-only participant with the D16 WAN lease numbers and (optionally) the D74
    EntityName identity."""
    q = dds.DomainParticipantQos()
    q.transport_builtin = dds.TransportBuiltin.udpv4
    if name is not None or role is not None:
        en = q.participant_name
        if name is not None:
            en.name = name
        if role is not None:
            en.role_name = role
        q.participant_name = en
    dc = q.discovery_config
    dc.participant_liveliness_lease_duration = dds.Duration(WAN_PARTICIPANT_LEASE_S)
    dc.participant_liveliness_assert_period = dds.Duration(WAN_PARTICIPANT_ASSERT_S)
    q.discovery_config = dc
    return dds.DomainParticipant(domain, q)


def _rel_tl(q):
    q.reliability = dds.Reliability(kind=dds.ReliabilityKind.RELIABLE)
    q.durability = dds.Durability(kind=dds.DurabilityKind.TRANSIENT_LOCAL)
    return q  # history default = KEEP_LAST(1), the designed depth


def health_writer_qos():
    q = _rel_tl(dds.DataWriterQos())
    q.deadline = dds.Deadline(
        period=dds.Duration.from_milliseconds(int(HEALTH_DEADLINE_S * 1000)))
    q.liveliness = dds.Liveliness(
        dds.LivelinessKind.AUTOMATIC,
        dds.Duration.from_milliseconds(int(HEALTH_LIVELINESS_LEASE_S * 1000)))
    return q


def health_reader_qos():
    q = _rel_tl(dds.DataReaderQos())
    q.deadline = dds.Deadline(
        period=dds.Duration.from_milliseconds(int(HEALTH_DEADLINE_S * 1000)))
    q.liveliness = dds.Liveliness(
        dds.LivelinessKind.AUTOMATIC,
        dds.Duration.from_milliseconds(int(HEALTH_LIVELINESS_LEASE_S * 1000)))
    return q


def data_writer_qos():
    # Per the design: WAN data topics carry NO topic-level liveliness (AUTOMATIC/infinite
    # default) — crash detection is participant presence, i.e. the purge backstop.
    return _rel_tl(dds.DataWriterQos())


def data_reader_qos():
    return _rel_tl(dds.DataReaderQos())


def discovered_names(participant):
    """{participant_guid_str: (participant_name, role_name)} for every discovered
    participant — the D74 identity read off the builtin DCPSParticipant data."""
    out = {}
    for handle in participant.discovered_participants():
        try:
            d = participant.discovered_participant_data(handle)
        except Exception:
            continue  # participant vanished between enumerate and fetch
        pn = d.participant_name
        out[str(d.key.value)] = (pn.name or "", pn.role_name or "")
    return out


# ------------------------------------------------------------------------------- the peer
def run_peer(args):
    """A 'router' peer: heartbeats RouterHealth + writes the co-tested data topic every
    HEARTBEAT_PERIOD_S. --stale-after N: after N heartbeats, withhold heartbeats but keep
    liveliness asserted (the STALE probe). Runs until killed (SIGKILL from the driver) or
    --lifetime as a stray-process backstop."""
    name = f"{args.node}/{args.router}"
    p = make_participant(args.domain, name=name, role=ROLE_SENTINEL)
    try:
        ht = dds.DynamicData.Topic(p, HEALTH_TOPIC, _type("RouterHealth"))
        hw = dds.DynamicData.DataWriter(dds.Publisher(p), ht, health_writer_qos())
        dt = dds.DynamicData.Topic(p, DATA_TOPIC, _type("PresenceData"))
        dw = dds.DynamicData.DataWriter(dds.Publisher(p), dt, data_writer_qos())
        print("READY", flush=True)
        hb = dds.DynamicData(_type("RouterHealth"))
        hb["router_id"] = args.router_id
        hb["node_name"] = name
        hb["role"] = "spike-peer"
        d = dds.DynamicData(_type("PresenceData"))
        d["source"] = name
        seq = 0
        end = time.monotonic() + args.lifetime
        while time.monotonic() < end:
            if args.stale_after < 0 or seq < args.stale_after:
                hb["heartbeat_seq"] = seq
                hb["send_timestamp"] = int(time.time() * 1e9)
                hb["state_revision"] = str(seq)
                hw.write(hb)
                d["seq"] = seq
                dw.write(d)
            else:
                # STALE probe: liveliness stays asserted, heartbeats withheld. AUTOMATIC
                # liveliness already asserts while the process lives; the explicit call
                # matches the plan's spec and is harmless.
                hw.assert_liveliness()
            seq += 1
            time.sleep(HEARTBEAT_PERIOD_S)
    finally:
        p.close()
    return 0


def spawn_peer(domain, node, router, router_id, stale_after=-1, lifetime=90):
    cmd = [sys.executable, os.path.abspath(__file__), "peer",
           "--domain", str(domain), "--node", node, "--router", router,
           "--router-id", str(router_id), "--stale-after", str(stale_after),
           "--lifetime", str(lifetime)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    ready, _, _ = select.select([proc.stdout], [], [], 20.0)
    if not ready or proc.stdout.readline().strip() != "READY":
        proc.kill()
        proc.wait()
        raise SpikeError(f"peer {node}/{router} never reported READY")
    return proc


def kill_peer(proc):
    """SIGKILL — the crash under test. UDPv4-only, so nothing leaks in /dev/shm."""
    if proc.poll() is None:
        proc.kill()
    proc.stdout.close()
    proc.wait()


# ------------------------------------------------------------- the survivor (roster model)
class Survivor:
    """Models the Phase 8 PresenceMonitor: publishes its own RouterHealth heartbeat,
    subscribes to peers', maintains the ALIVE/STALE/DEAD roster off the designed signals
    (valid sample -> ALIVE; REQUESTED_DEADLINE_MISSED -> STALE; instance
    NOT_ALIVE_NO_WRITERS i.e. liveliness lost -> DEAD), and republishes the LAN
    ActRouterMeshStatus aggregate on every roster change."""

    def __init__(self, wan_domain, lan_domain=None,
                 node="nodeS", router="survivor", router_id=99):
        self.node, self.router, self.router_id = node, router, router_id
        self.p = make_participant(wan_domain, name=f"{node}/{router}", role=ROLE_SENTINEL)
        ht = dds.DynamicData.Topic(self.p, HEALTH_TOPIC, _type("RouterHealth"))
        self.hw = dds.DynamicData.DataWriter(dds.Publisher(self.p), ht, health_writer_qos())
        self.hr = dds.DynamicData.DataReader(dds.Subscriber(self.p), ht, health_reader_qos())
        dt = dds.DynamicData.Topic(self.p, DATA_TOPIC, _type("PresenceData"))
        self.dr = dds.DynamicData.DataReader(dds.Subscriber(self.p), dt, data_reader_qos())
        self.roster = {}          # router_id -> {presence, name, seq, last_seen, t_*}
        self._handle_to_id = {}   # health instance handle str -> router_id
        self._deadline_total = 0
        self._seq = 0
        self._next_beat = 0.0
        self._mesh_rev = 0
        self.lan_p = self.mesh_w = None
        if lan_domain is not None:
            self.lan_p = make_participant(lan_domain)
            mt = dds.DynamicData.Topic(self.lan_p, MESH_TOPIC, _type("RouterMeshStatus"))
            self.mesh_w = dds.DynamicData.DataWriter(
                dds.Publisher(self.lan_p), mt, _rel_tl(dds.DataWriterQos()))

    # -- roster servicing ------------------------------------------------------------
    def poll(self):
        now = time.monotonic()
        changed = False
        if now >= self._next_beat:
            self._heartbeat()
            self._next_beat = now + HEARTBEAT_PERIOD_S
        for s in self.hr.take():
            info = s.info
            handle = str(info.instance_handle)
            if info.valid:
                rid = int(s.data["router_id"])
                if rid == self.router_id:
                    continue  # own heartbeat
                self._handle_to_id[handle] = rid
                e = self.roster.setdefault(rid, {"presence": None})
                if e["presence"] != ALIVE:
                    e["t_alive"] = now
                    changed = True
                e.update(presence=ALIVE, name=s.data["node_name"],
                         seq=int(s.data["heartbeat_seq"]), last_seen=now)
            elif info.state.instance_state == dds.InstanceState.NOT_ALIVE_NO_WRITERS:
                rid = self._handle_to_id.get(handle)
                if rid is not None and self.roster[rid]["presence"] != DEAD:
                    self.roster[rid].update(presence=DEAD, t_dead=now)
                    changed = True
        st = self.hr.requested_deadline_missed_status
        if st.total_count > self._deadline_total:
            self._deadline_total = st.total_count
            rid = self._handle_to_id.get(str(st.last_instance_handle))
            # STALE only from ALIVE — deadline misses keep firing for a DEAD instance.
            if rid is not None and self.roster.get(rid, {}).get("presence") == ALIVE:
                self.roster[rid].update(presence=STALE, t_stale=now)
                changed = True
        if changed:
            self._publish_mesh()

    def _heartbeat(self):
        hb = dds.DynamicData(_type("RouterHealth"))
        hb["router_id"] = self.router_id
        hb["node_name"] = f"{self.node}/{self.router}"
        hb["role"] = "spike-survivor"
        hb["heartbeat_seq"] = self._seq
        hb["send_timestamp"] = int(time.time() * 1e9)
        hb["state_revision"] = str(self._seq)
        self.hw.write(hb)
        self._seq += 1

    def _publish_mesh(self):
        if self.mesh_w is None:
            return
        self._mesh_rev += 1
        m = dds.DynamicData(_type("RouterMeshStatus"))
        m["observer_node"] = self.node
        m["observer_router"] = self.router
        m["state_revision"] = str(self._mesh_rev)
        now = time.monotonic()
        peers = []
        for rid, e in sorted(self.roster.items()):
            pe = dds.DynamicData(_type("RouterMeshPeer"))
            pe["health.router_id"] = rid
            pe["health.node_name"] = e.get("name", "")
            pe["health.heartbeat_seq"] = e.get("seq", 0)
            pe["presence"] = e["presence"]
            pe["last_seen_delta_ms"] = int((now - e.get("last_seen", now)) * 1000)
            peers.append(pe)
        m["peers"] = peers
        self.mesh_w.write(m)

    # -- driver helpers --------------------------------------------------------------
    def presence(self, rid):
        e = self.roster.get(rid)
        return None if e is None else e["presence"]

    def spin_until(self, pred, timeout_s, poll_s=0.05):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.poll()
            if pred():
                return True
            time.sleep(poll_s)
        self.poll()
        return pred()

    def spin_for(self, seconds, also=None):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.poll()
            if also:
                also()
            time.sleep(0.05)

    def close(self):
        if self.lan_p is not None:
            self.lan_p.close()
        self.p.close()


class MeshObserver:
    """A LAN consumer of the survivor's ActRouterMeshStatus aggregate."""

    def __init__(self, lan_domain):
        self.p = make_participant(lan_domain)
        mt = dds.DynamicData.Topic(self.p, MESH_TOPIC, _type("RouterMeshStatus"))
        self.r = dds.DynamicData.DataReader(
            dds.Subscriber(self.p), mt, _rel_tl(dds.DataReaderQos()))

    def latest(self):
        """{router_id: presence} from the newest mesh sample, or None if none yet."""
        out = None
        for s in self.r.read():
            if s.info.valid:
                out = {int(pe["health.router_id"]): int(pe["presence"])
                       for pe in s.data["peers"]}
        return out

    def close(self):
        self.p.close()


class DataWatch:
    """Tracks the co-tested data topic's per-source instance states (the purge backstop:
    NOT_ALIVE_NO_WRITERS arrives only when the dead participant is purged)."""

    def __init__(self, reader):
        self.r = reader
        self._h2src = {}
        self.states = {}

    def poll(self):
        for s in self.r.read():
            h = str(s.info.instance_handle)
            if s.info.valid:
                self._h2src[h] = s.data["source"]
            src = self._h2src.get(h)
            if src is not None:
                self.states[src] = s.info.state.instance_state


# ------------------------------------------------------------------------------ the parts
def part_a_identity(base):
    print("Part A: D74 identity — EntityName readable at discovery, detection on "
          "role_name alone")
    obs = make_participant(base)  # plain observer: no EntityName at all
    plain = make_participant(base, name="fancy-display-name")  # app: display name, no role
    peer = None
    try:
        peer = spawn_peer(base, "nodeA", "router1", 1)
        t0 = time.monotonic()
        deadline = t0 + 15.0
        routers, names = {}, {}
        while time.monotonic() < deadline:
            names = discovered_names(obs)
            routers = {k: v for k, v in names.items() if v[1] == ROLE_SENTINEL}
            if routers and any(v[0] == "fancy-display-name" for v in names.values()):
                break
            time.sleep(0.1)
        if len(routers) != 1:
            raise SpikeError(f"[A] expected exactly 1 role-sentinel participant, "
                             f"got {routers} (all: {names})")
        guid, (name, role) = next(iter(routers.items()))
        if name != "nodeA/router1":
            raise SpikeError(f"[A] router participant_name.name = {name!r}, "
                             f"expected 'nodeA/router1'")
        print(f"  [A] router detected via role_name={role!r} with "
              f"name={name!r} (guid {guid[:24]}...) in "
              f"{time.monotonic() - t0:.1f}s")
        plain_rows = [v for v in names.values() if v[0] == "fancy-display-name"]
        if not plain_rows or plain_rows[0][1] == ROLE_SENTINEL:
            raise SpikeError(f"[A] plain app row wrong: {plain_rows}")
        print(f"  [A] plain app discovered as {plain_rows[0]!r} — display name visible, "
              f"NOT classified as a router  PASS")
    finally:
        if peer:
            kill_peer(peer)
        plain.close()
        obs.close()


def part_b_dead_before_purge(base):
    print("Part B: SIGKILL -> DEAD inside the liveliness window, BEFORE participant purge "
          "(D16); purge then drives the data-topic NO_WRITERS backstop; LAN mesh updates")
    wan, lan = base + 1, base + 2
    surv = Survivor(wan, lan_domain=lan)
    mesh = MeshObserver(lan)
    watch = DataWatch(surv.dr)
    p1 = p2 = None
    try:
        p1 = spawn_peer(wan, "nodeA", "router1", 1)
        p2 = spawn_peer(wan, "nodeB", "router2", 2)
        if not surv.spin_until(
                lambda: surv.presence(1) == ALIVE and surv.presence(2) == ALIVE, 15):
            raise SpikeError(f"[B] peers never both ALIVE in roster: {surv.roster}")
        if not surv.spin_until(lambda: len(surv.dr.matched_publications) == 2, 10):
            raise SpikeError("[B] data reader never matched both peers' data writers")
        if not surv.spin_until(lambda: mesh.latest() == {1: ALIVE, 2: ALIVE}, 10):
            raise SpikeError(f"[B] LAN mesh never showed both ALIVE: {mesh.latest()}")
        print("  [B] baseline: 3 RouterHealth publishers, roster {1: ALIVE, 2: ALIVE}, "
              "data writers matched=2, LAN mesh agrees")

        t0 = time.monotonic()
        kill_peer(p1)  # SIGKILL
        if not surv.spin_until(lambda: surv.presence(1) == DEAD,
                               HEALTH_LIVELINESS_LEASE_S + 4):
            raise SpikeError("[B] survivor never marked the killed peer DEAD")
        t_dead = surv.roster[1]["t_dead"] - t0
        matched_at_dead = len(surv.dr.matched_publications)
        t_stale = surv.roster[1].get("t_stale")
        cascade = (f" (STALE at {t_stale - t0:.2f}s first — the deadline fires before "
                   f"the lease)" if t_stale else "")
        print(f"  [B] DEAD at {t_dead:.2f}s after SIGKILL{cascade}; "
              f"data writers still matched = {matched_at_dead}")
        # Detection = lease (3s) + up to one automatic assert interval (lease/3) + jitter.
        if not 1.5 <= t_dead <= HEALTH_LIVELINESS_LEASE_S + 3.5:
            raise SpikeError(f"[B] t_dead {t_dead:.2f}s outside the liveliness window")
        if matched_at_dead != 2:
            raise SpikeError("[B] participant purge beat the DEAD signal — D16 ordering "
                             "VIOLATED")
        if not surv.spin_until(lambda: mesh.latest() is not None
                               and mesh.latest().get(1) == DEAD, 5):
            raise SpikeError(f"[B] LAN mesh never showed peer 1 DEAD: {mesh.latest()}")
        print("  [B] LAN mesh aggregate updated to {1: DEAD, 2: ALIVE}")

        if not surv.spin_until(lambda: len(surv.dr.matched_publications) == 1, 25,
                               poll_s=0.02):
            raise SpikeError("[B] participant purge never dropped the dead data writer")
        t_purge = time.monotonic() - t0
        surv.spin_for(1.0, also=watch.poll)
        dead_state = watch.states.get("nodeA/router1")
        print(f"  [B] purge at {t_purge:.2f}s; data instance nodeA/router1 -> {dead_state}")
        if not t_dead < t_purge:
            raise SpikeError(f"[B] DEAD ({t_dead:.2f}s) did not precede purge "
                             f"({t_purge:.2f}s)")
        # Observed 11-16s against the 10s lease: expiry counts from the LAST SPDP
        # announce before the kill and the purge check has its own granularity — the
        # backstop is lease-ORDER, not lease-exact (README). The D16 claim needs only
        # t_dead << t_purge, asserted above.
        if not WAN_PARTICIPANT_LEASE_S - WAN_PARTICIPANT_ASSERT_S - 1.5 <= t_purge \
                <= 2 * WAN_PARTICIPANT_LEASE_S + 2:
            raise SpikeError(f"[B] t_purge {t_purge:.2f}s not in the participant-lease "
                             f"window")
        if dead_state != dds.InstanceState.NOT_ALIVE_NO_WRITERS:
            raise SpikeError(f"[B] data instance state after purge = {dead_state}, "
                             f"expected NOT_ALIVE_NO_WRITERS")
        if surv.presence(2) != ALIVE:
            raise SpikeError(f"[B] peer 2 no longer ALIVE: {surv.roster.get(2)}")
        print(f"  [B] ordering held: DEAD {t_dead:.2f}s < purge {t_purge:.2f}s "
              f"(lease {WAN_PARTICIPANT_LEASE_S}s); peer 2 ALIVE throughout  PASS")
    finally:
        for proc in (p1, p2):
            if proc:
                kill_peer(proc)
        mesh.close()
        surv.close()


def part_c_stale_not_dead(base):
    print("Part C: withheld heartbeats + asserted liveliness -> STALE, never DEAD, "
          "no teardown")
    wan = base + 3
    surv = Survivor(wan)
    peer = None
    try:
        peer = spawn_peer(wan, "nodeA", "router1", 1, stale_after=4)
        if not surv.spin_until(lambda: surv.presence(1) == ALIVE, 15):
            raise SpikeError("[C] peer never ALIVE in roster")
        if not surv.spin_until(lambda: surv.presence(1) == STALE, 12):
            raise SpikeError(f"[C] peer never marked STALE: {surv.roster.get(1)}")
        e = surv.roster[1]
        stale_delta = e["t_stale"] - e["last_seen"]
        print(f"  [C] STALE {stale_delta:.2f}s after the last heartbeat "
              f"(deadline {HEALTH_DEADLINE_S}s)")
        if not HEALTH_DEADLINE_S - 0.5 <= stale_delta <= HEALTH_DEADLINE_S + 3:
            raise SpikeError(f"[C] STALE delta {stale_delta:.2f}s not near the deadline")
        # Hold well past the liveliness lease: must stay STALE (never DEAD), liveliness
        # intact, data writer still matched (nothing torn down, nothing purged).
        surv.spin_for(HEALTH_LIVELINESS_LEASE_S + 5)
        if surv.presence(1) != STALE:
            raise SpikeError(f"[C] presence became {PRESENCE_NAME.get(surv.presence(1))} "
                             f"during the hold — STALE escalated")
        lc = surv.hr.liveliness_changed_status
        if lc.not_alive_count != 0:
            raise SpikeError(f"[C] liveliness lost (not_alive_count="
                             f"{lc.not_alive_count}) — the probe failed to assert")
        if len(surv.dr.matched_publications) != 1:
            raise SpikeError("[C] data writer unmatched — something tore down/purged")
        print(f"  [C] held STALE for {HEALTH_LIVELINESS_LEASE_S + 5:.0f}s past the last "
              f"heartbeat: liveliness intact, data writer still matched, no DEAD  PASS")
    finally:
        if peer:
            kill_peer(peer)
        surv.close()


def part_d_restart_guid_join(base):
    print("Part D: restart under the same identity -> same router_id rejoins ALIVE, "
          "new participant GUID (D74 roster join by name, not GUID)")
    wan = base + 4
    surv = Survivor(wan)
    peer = None
    try:
        peer = spawn_peer(wan, "nodeA", "router1", 1)
        if not surv.spin_until(lambda: surv.presence(1) == ALIVE, 15):
            raise SpikeError("[D] peer never ALIVE")

        def router_guids():
            return {k for k, v in discovered_names(surv.p).items()
                    if v == ("nodeA/router1", ROLE_SENTINEL)}
        g1 = router_guids()
        if len(g1) != 1:
            raise SpikeError(f"[D] expected one GUID for the peer, got {g1}")
        kill_peer(peer)
        peer = None
        if not surv.spin_until(lambda: surv.presence(1) == DEAD,
                               HEALTH_LIVELINESS_LEASE_S + 4):
            raise SpikeError("[D] killed peer never DEAD")
        peer = spawn_peer(wan, "nodeA", "router1", 1)  # same identity, new process/GUID
        if not surv.spin_until(lambda: surv.presence(1) == ALIVE, 15):
            raise SpikeError(f"[D] restarted peer never re-entered ALIVE: "
                             f"{surv.roster.get(1)}")
        g_now = router_guids()
        new_guids = g_now - g1
        if not new_guids:
            raise SpikeError(f"[D] no NEW GUID for the restarted peer (saw {g_now}, "
                             f"old {g1})")
        print(f"  [D] router_id 1 DEAD -> ALIVE across restart; participant GUID changed "
              f"(old {next(iter(g1))[:24]}..., new {next(iter(new_guids))[:24]}...); "
              f"roster joined on participant_name, not GUID  PASS")
    finally:
        if peer:
            kill_peer(peer)
        surv.close()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "peer":
        ap = argparse.ArgumentParser()
        ap.add_argument("mode")
        ap.add_argument("--domain", type=int, required=True)
        ap.add_argument("--node", required=True)
        ap.add_argument("--router", required=True)
        ap.add_argument("--router-id", type=int, required=True)
        ap.add_argument("--stale-after", type=int, default=-1)
        ap.add_argument("--lifetime", type=float, default=90.0)
        return run_peer(ap.parse_args())

    base = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BASE_DOMAIN
    print(f"presence spike on base domain {base} (UDPv4-only)")
    print(f"numbers: heartbeat {HEARTBEAT_PERIOD_S}s, deadline {HEALTH_DEADLINE_S}s, "
          f"liveliness lease {HEALTH_LIVELINESS_LEASE_S}s, participant lease "
          f"{WAN_PARTICIPANT_LEASE_S}s/{WAN_PARTICIPANT_ASSERT_S}s (D16: "
          f"{WAN_PARTICIPANT_LEASE_S} > {HEALTH_LIVELINESS_LEASE_S})\n")
    failures = []
    for part in (part_a_identity, part_b_dead_before_purge,
                 part_c_stale_not_dead, part_d_restart_guid_join):
        try:
            part(base)
        except SpikeError as e:
            failures.append(str(e))
            print(f"  FAIL: {e}")
        print()
    if failures:
        print(f"SPIKE FAILED ({len(failures)} failure(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SPIKE PASSED: RouterHealth liveliness declares DEAD before participant purge "
          "(D16 ordering observed), STALE is a deadline-driven policy flag that never "
          "escalates, the LAN mesh aggregate tracks the roster, and the D74 "
          "participant_name identity is readable at discovery, detected on role_name "
          "alone, and joins the roster across a restart/GUID change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
