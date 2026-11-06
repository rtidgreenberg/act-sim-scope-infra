"""Phase 9 Link-Metrics Capture e2e (D14/D18/D81; design: docs/cpp_router/link-health.md).

Two router_main processes (control + platform roles, config/e2e_link_stats.yaml). Each
runs a LinkStatsCollector on its WAN participant: it polls every registered WAN endpoint's
per-matched-endpoint reliable-protocol statuses (the ENABLED PlatformInitStatus route's
WAN leg + PresenceMonitor's RouterHealth bellwether pair), rolls them up per peer NAME
resolved from the middleware discovery DB (D81 — no roster join), measures passive RTT via
scheduled RouterHealth acknowledgment metadata, and publishes ActRouterLinkStats on its admin
(LAN) participant.

Asserts the D81 item-7 evidence map:

  E1  under steady route traffic the platform observer's ActRouterLinkStats for the CONTROL
      peer advances pushed_samples + heartbeats_sent, and the control observer's for the
      PLATFORM peer advances samples_received — with the peer attributed by "<node>/<router>"
      name (the discovery-DB attribution, not a roster join).
  E2  a (re)match interval is stamped rediscovery_in_interval (the first poll after a WAN
      endpoint matches a peer — TRANSIENT_LOCAL-durable so a reader joining shortly after
      startup still catches it).
  E3  rtt_count / rtt_*_us populate from health samples in both directions.
  E4  link stats never bump the router's RouterStatus.state_revision (telemetry, D5): with
      no commands in flight, state_revision holds steady across several link-stats intervals.

The "no probe endpoints or extra periodic responses" contract is checked with the
spikes/partition_retarget/ dumpcap/GuidPrefix pattern (see test_link_stats_wire_frugal).

Run from the repo root (see router/test_e2e/README.md).
"""

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import rti.connextdds as dds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import (  # noqa: E402
    render_config, set_wan_qos_env, start_router, wait_for_mutual_discovery)
from util.dds_probe import (  # noqa: E402
    Probe, reader_qos, read_status_revision)

# e2e_link_stats.yaml loads the canonical QoS XML (for the wan_status/wan_event route
# endpoint aliases); its participant profiles are env-templated, so set loopback defaults for
# the router_main subprocesses that inherit this env. (The in-process probes below use plain
# default QoS and never load the WAN lib, so this only needs to run before the routers launch.)
set_wan_qos_env()

# Default RTPS port mapping (7.7): a domain D uses UDP ports [PB + DG*D, PB + DG*D + DG).
RTPS_PORT_BASE = 7400
RTPS_DOMAIN_GAIN = 250

TYPE_NAME = "platform_init_status"
TOPIC_NAME = "PlatformInitStatus"
# The reliable event leg (production platform_events uses wan_event) — same platform_wan
# participant as the best-effort status leg, so the node carries both QoS classes (D95).
EVENT_TYPE_NAME = "contact_report"
EVENT_TOPIC_NAME = "ContactReport"
LINK_STATS_TOPIC = "ActRouterLinkStats"
THIS_NODE = "Platform_30"

# "<node>/<router>" identities (D79): platform from the config, control from the conftest
# router_pair --node-name/--name overrides.
PLATFORM = "Platform_30/e2e-link-stats"
CONTROL = "Control_20/e2e-control-role"


def link_stats_reader(probe, provider):
    # KEEP_ALL so every interval sample is accumulated (not just the latest per instance),
    # matched to the collector's RELIABLE + TRANSIENT_LOCAL writer.
    return probe.reader(
        LINK_STATS_TOPIC, "RouterLinkStats",
        qos=reader_qos("router_link_stats"),
        dtype=provider.type("RouterLinkStats"))


def _fields(data):
    return {
        "observer": str(data["observer_router"]),
        "peer": str(data["peer_router"]),
        "network": str(data["network"]),
        "pushed_samples": int(data["pushed_samples"]),
        "heartbeats_sent": int(data["heartbeats_sent"]),
        "samples_received": int(data["samples_received"]),
        "rtt_count": int(data["rtt_count"]),
        "rtt_mean_us": int(data["rtt_mean_us"]),
        "rtt_valid": bool(data["rtt_valid"]),
        "rtt_age_ms": int(data["rtt_last_sample_age_ms"]),
        "ack_age_us": int(data["rtt_last_ack_age_us"]),
        "interval_ms": int(data["interval_ms"]),
        "rediscovery": bool(data["rediscovery_in_interval"]),
        "capture_seq": int(data["capture_seq"]),
    }


def drain_into(reader, acc):
    """Append every fresh valid ActRouterLinkStats sample (take() drains the KEEP_ALL
    reader cache) into acc, a list of field dicts."""
    for s in reader.take():
        if s.info.valid:
            acc.append(_fields(s.data))


def test_link_stats(router_binary, admin_types_xml, e2e_tmp_dir, unique_domains,
                    router_pair):
    control, platform, _ = router_pair("e2e_link_stats.yaml", unique_domains)
    provider = dds.QosProvider(str(admin_types_xml))
    alive = lambda: control.is_alive() and platform.is_alive()  # noqa: E731

    platform_app = Probe(unique_domains["platform_lan"])
    control_app = Probe(unique_domains["control_lan"])
    # Link-stats readers on each router's admin (LAN) participant. Created right after the
    # routers come up so the TRANSIENT_LOCAL first (rediscovery) sample is still cached.
    plat_stats_probe = Probe(unique_domains["platform_lan"])
    ctrl_stats_probe = Probe(unique_domains["control_lan"])
    try:
        plat_stats = link_stats_reader(plat_stats_probe, provider)
        ctrl_stats = link_stats_reader(ctrl_stats_probe, provider)

        writer = platform_app.writer(TOPIC_NAME, TYPE_NAME)
        control_reader = control_app.reader(TOPIC_NAME, TYPE_NAME)
        # Reliable event leg (wan_event route): drive it too, with a control-side consumer, so
        # the platform_wan participant carries both a best-effort and a reliable WAN data leg
        # to the CONTROL peer. The consumer matters — a route writer's per-endpoint stats only
        # attribute when the full chain has a live reader draining the forwarded stream (D95).
        event_writer = platform_app.writer(EVENT_TOPIC_NAME, EVENT_TYPE_NAME)
        control_event_reader = control_app.reader(EVENT_TOPIC_NAME, EVENT_TYPE_NAME)

        plat_acc, ctrl_acc = [], []
        forwarded = False
        # Drive steady route traffic (~10 Hz) while draining both stats streams; stop early
        # once we have enough intervals on both sides + RTT + a rediscovery-stamped interval +
        # end-to-end forwarding + the platform_wan legs attributed (have_coverage). Deadline is
        # generous (30 s) because route-leg attribution can lag discovery by several intervals.
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            assert alive(), (f"a router exited early; control={control.log_path} "
                             f"platform={platform.log_path}")
            writer.write(platform_app.sample(
                TYPE_NAME, **{"source": THIS_NODE}))
            event_writer.write(platform_app.sample(
                EVENT_TYPE_NAME, **{"source": THIS_NODE}))
            for s in control_reader.take():
                if s.info.valid and str(s.data["source"]) == THIS_NODE:
                    forwarded = True
            control_event_reader.take()  # drain the reliable leg's forwarded stream (consumer)
            drain_into(plat_stats, plat_acc)
            drain_into(ctrl_stats, ctrl_acc)

            plat_cp = [f for f in plat_acc if f["observer"] == PLATFORM
                       and f["peer"] == CONTROL]
            ctrl_pp = [f for f in ctrl_acc if f["observer"] == CONTROL
                       and f["peer"] == PLATFORM]
            have_push = any(f["pushed_samples"] > 0 for f in plat_cp)
            have_heartbeats = any(f["heartbeats_sent"] > 0 for f in plat_cp)
            have_recv = any(f["samples_received"] > 0 for f in ctrl_pp)
            have_rtt = (any(f["rtt_count"] > 0 and f["rtt_valid"] for f in plat_cp)
                        and any(f["rtt_count"] > 0 and f["rtt_valid"] for f in ctrl_pp))
            have_rediscovery = any(f["rediscovery"] for f in plat_cp + ctrl_pp)
            # Don't exit until the platform_wan data legs have actually attributed to the peer
            # (E1b) — otherwise the loop can break on the first few bellwether-only intervals,
            # before the route legs' per-endpoint stats resolve (attribution lags discovery,
            # D95). The 20 s deadline still bounds a genuine regression.
            have_coverage = max((f["pushed_samples"] for f in plat_cp), default=0) >= 5
            if (forwarded and have_push and have_heartbeats and have_recv and have_rtt
                    and have_rediscovery and have_coverage and len(plat_cp) >= 3):
                break
            time.sleep(0.1)

        assert forwarded, (f"PlatformInitStatus never forwarded end-to-end; "
                           f"control={control.log_path} platform={platform.log_path}")

        plat_cp = [f for f in plat_acc if f["observer"] == PLATFORM
                   and f["peer"] == CONTROL]
        ctrl_pp = [f for f in ctrl_acc if f["observer"] == CONTROL
                   and f["peer"] == PLATFORM]

        # (E1) attribution by name + writer/reader counters advance.
        assert plat_cp, (f"platform published no ActRouterLinkStats for peer {CONTROL}; "
                         f"accumulated={plat_acc}; log {platform.log_path}")
        assert ctrl_pp, (f"control published no ActRouterLinkStats for peer {PLATFORM}; "
                         f"accumulated={ctrl_acc}; log {control.log_path}")
        assert all(f["network"] for f in plat_cp), \
            f"platform link stats missing network label: {plat_cp}"
        assert max(f["pushed_samples"] for f in plat_cp) > 0, \
            (f"platform->control pushed_samples never advanced (route + health writer to "
             f"the peer); samples={plat_cp}; log {platform.log_path}")
        assert max(f["heartbeats_sent"] for f in plat_cp) > 0, \
            f"platform->control heartbeats_sent never advanced; samples={plat_cp}"
        assert max(f["samples_received"] for f in ctrl_pp) > 0, \
            (f"control<-platform samples_received never advanced; samples={ctrl_pp}; "
             f"log {control.log_path}")

        # (E1b) platform_wan DATA-leg coverage — regression for the is_wan ->
        # {team_scoped, on_wan} decomposition (design-decisions.md 2026-07-22; task
        # docs/cpp_router/is-wan-flag-decomposition-task.md). Both route writers to CONTROL
        # (the best-effort status leg + the reliable event leg) live on platform_wan, which is
        # on_wan regardless of team_scoped (this fixture's platform_wan is NOT team_scoped —
        # see config/e2e_link_stats.yaml — but on_wan is what drives this coverage either
        # way; the two flags are orthogonal, and platform_wan IS team_scoped too on the real
        # control-platform.yaml topology post-D103). Before the decomposition, WAN-leg
        # link-stats registration keyed off the single conflated flag (team_wan-only since
        # D87, before D103 retired team_wan), so platform_wan data legs were never polled and
        # this peer's per-interval pushed_samples reflected ONLY the
        # ~1 Hz RouterHealth bellwether (router_main registers that unconditionally). Once the
        # legs are polled, the platform_wan route writers add their forwarded traffic (driven
        # above at ~10 Hz each) on top of the
        # bellwether. This assertion lives HERE (not a standalone probe) on purpose: the route
        # writer's per-matched-endpoint stats only attribute when the full chain has a live
        # consumer draining the forwarded stream — a bare high-rate publisher with no consumer
        # never attributes the route writer (its matched-subscription participant data doesn't
        # resolve). max over intervals also absorbs intermittent discovery-data attribution.
        # A ceiling of 5 sits well above any bellwether-only interval (~1) and below the route
        # rate. Fails before the decomposition (bellwether-only ~1), passes after.
        assert max(f["pushed_samples"] for f in plat_cp) >= 5, (
            f"platform_wan route-leg link stats not covered: max per-interval pushed_samples "
            f"to {CONTROL} was {max(f['pushed_samples'] for f in plat_cp)}, consistent with "
            f"the ~1 Hz RouterHealth bellwether alone — the platform_wan data leg is not "
            f"being polled (is_wan/on_wan decomposition regression); samples={plat_cp}; "
            f"log {platform.log_path}")

        # (E3) RTT from the app-ack probe.
        assert any(f["rtt_count"] > 0 for f in plat_cp + ctrl_pp), \
            (f"no RTT samples from the RouterLinkProbe app-ack on either side; "
             f"plat={plat_cp} ctrl={ctrl_pp}; logs control={control.log_path} "
             f"platform={platform.log_path}")

        # (E2) a (re)match interval is stamped rediscovery.
        assert any(f["rediscovery"] for f in plat_cp + ctrl_pp), \
            (f"no interval was stamped rediscovery_in_interval on a fresh peer match; "
             f"plat={plat_cp} ctrl={ctrl_pp}")

        # (E4) link stats do not bump RouterStatus.state_revision (telemetry, D5). With no
        # commands in flight, revision holds steady across several link-stats intervals.
        status_reader = plat_stats_probe.reader(
            "ActRouterStatus", "RouterStatus",
            qos=reader_qos("router_admin_status"),
            dtype=provider.type("RouterStatus"))
        rev0 = None
        t0 = time.monotonic()
        while time.monotonic() < t0 + 2.0 and rev0 is None:
            rev0 = read_status_revision(status_reader)
            time.sleep(0.1)
        assert rev0 is not None, f"no RouterStatus revision from platform; {platform.log_path}"
        stable_deadline = time.monotonic() + 4.0  # >= 3 link-stats ticks
        while time.monotonic() < stable_deadline:
            assert alive(), "a router exited during the revision-stability window"
            writer.write(platform_app.sample(
                TYPE_NAME, **{"source": THIS_NODE}))
            rev = read_status_revision(status_reader)
            assert rev == rev0, \
                (f"state_revision moved ({rev0} -> {rev}) with only link-stats/heartbeat "
                 f"ticks and route traffic flowing — a telemetry tick bumped revision; "
                 f"log {platform.log_path}")
            time.sleep(0.2)
    finally:
        platform_app.close()
        control_app.close()
        plat_stats_probe.close()
        ctrl_stats_probe.close()


def _wan_port_range(wan_domain):
    lo = RTPS_PORT_BASE + RTPS_DOMAIN_GAIN * wan_domain
    return lo, lo + RTPS_DOMAIN_GAIN - 1


@pytest.mark.parametrize("recovery_mode", ["graceful", "crash", "outage"])
def test_health_rtt_idle_restart(router_binary, admin_types_xml, e2e_tmp_dir,
                                 unique_domains, recovery_mode):
    """No app endpoints: metadata, bidirectional RTT, 250ms stats cadence, and
    graceful/crash restart or unchanged-writer recovery after roster pruning."""
    config = render_config("e2e_link_stats.yaml", unique_domains, e2e_tmp_dir)
    config.write_text(config.read_text().replace("link_stats_period_ms: 1000",
                                                "link_stats_period_ms: 250"))
    platform = start_router(router_binary, config, "platform", e2e_tmp_dir,
                            admin_participant="platform_lan")
    control = start_router(router_binary, config, "control", e2e_tmp_dir,
                           node_name="Control_20", name="e2e-control-role",
                           admin_participant="control_lan")
    provider = dds.QosProvider(str(admin_types_xml))
    plat_probe = Probe(unique_domains["platform_lan"])
    ctrl_probe = Probe(unique_domains["control_lan"])
    wan_probe = Probe(unique_domains["wan"], participant_profile="wan_router_participant")
    try:
        plat_reader = link_stats_reader(plat_probe, provider)
        ctrl_reader = link_stats_reader(ctrl_probe, provider)
        health_reader = wan_probe.reader("RouterHealth", "RouterHealth",
                                        qos=reader_qos("router_health"),
                                        dtype=provider.type("RouterHealth"))

        def wait_rtt(previous_session=None):
            health, plat, ctrl = {}, [], []
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                assert control.is_alive() and platform.is_alive()
                drain_into(plat_reader, plat)
                drain_into(ctrl_reader, ctrl)
                for sample in health_reader.take():
                    if sample.info.valid:
                        data = sample.data
                        health[str(data["router"])] = {
                            "session": int(data["session_id"]),
                            "acks": {str(ref["router"]): (
                                int(ref["ack_session_id"]), int(ref["ack_heartbeat_seq"]),
                                int(ref["ack_age_at_send_us"])) for ref in data["peers_seen"]},
                        }
                ready = (all(name in health for name in (CONTROL, PLATFORM))
                         and any(f["rtt_count"] and f["rtt_valid"] for f in plat)
                         and any(f["rtt_count"] and f["rtt_valid"] for f in ctrl)
                         and any(not f["rtt_count"] and f["rtt_valid"] for f in plat + ctrl))
                if ready:
                    session = health[CONTROL]["session"]
                    if previous_session is not None and session == previous_session:
                        time.sleep(0.1)
                        continue
                    if (health[PLATFORM]["acks"].get(CONTROL, (0,))[0] == session
                        and health[CONTROL]["acks"].get(PLATFORM, (0,))[0]
                            == health[PLATFORM]["session"]):
                        assert session and health[PLATFORM]["session"]
                        for observer, peer in ((CONTROL, PLATFORM), (PLATFORM, CONTROL)):
                            _, sequence, age = health[observer]["acks"][peer]
                            assert sequence > 0 and 0 <= age <= 32_000_000
                        return health, plat, ctrl
                time.sleep(0.1)
            pytest.fail(f"No mutual passive RTT/metadata after restart: {health}, {plat}, {ctrl}")

        health, plat, ctrl = wait_rtt()
        old_session = health[CONTROL]["session"]
        assert not any(p["topic"] == "RouterLinkProbe"
                       for p in wan_probe.discovered_publications())
        assert any(f["rtt_count"] == 0 and f["rtt_mean_us"] == 0
                   and f["rtt_valid"] for f in plat + ctrl)
        assert all(150 <= f["interval_ms"] <= 400 for f in plat + ctrl), (plat, ctrl)
        old_handles = set(health_reader.matched_publications)
        if recovery_mode == "outage":
            os.kill(control._popen.pid, signal.SIGSTOP)
            try:
                deadline = time.monotonic() + 75
                while time.monotonic() < deadline:
                    if "presence_peer_forgotten" in platform.log_path.read_text():
                        break
                    time.sleep(0.25)
                else:
                    pytest.fail("Suspended writer was not pruned after >60s outage")
            finally:
                os.kill(control._popen.pid, signal.SIGCONT)
        elif recovery_mode == "crash":
            control.kill()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                matched = set(health_reader.matched_publications)
                dead_handles = [handle for handle in old_handles & matched
                                if not health_reader.is_matched_publication_alive(handle)]
                if dead_handles:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("Crashed health writer never became dead while still matched")
        else:
            control.stop()
        plat_reader.take()
        ctrl_reader.take()
        health_reader.take()
        if recovery_mode != "outage":
            control = start_router(router_binary, config, "control", e2e_tmp_dir,
                                   node_name="Control_20", name="e2e-control-role",
                                   admin_participant="control_lan")
        recovered, _, _ = wait_rtt(None if recovery_mode == "outage" else old_session)
        if recovery_mode == "outage":
            assert recovered[CONTROL]["session"] == old_session
        elif recovery_mode == "crash":
            assert all(handle in health_reader.matched_publications for handle in dead_handles)
    finally:
        wan_probe.close()
        plat_probe.close()
        ctrl_probe.close()
        platform.stop()
        control.stop()


def test_link_stats_wire_frugal(router_binary, admin_types_xml, e2e_tmp_dir, unique_domains):
    """Capture startup SEDP and a separate steady window: health RTT remains
    available without application traffic, with no probe or WAN admin endpoints."""
    if not (shutil.which("dumpcap") and shutil.which("tshark")):
        pytest.skip("dumpcap/tshark not available for the wire-frugality capture")

    config_path = render_config("e2e_link_stats.yaml", unique_domains, e2e_tmp_dir)
    wan_lo, wan_hi = _wan_port_range(unique_domains["wan"])
    cap_path = e2e_tmp_dir / "wan_frugal.pcap"

    cap = subprocess.Popen(["dumpcap", "-i", "lo", "-a", "duration:40",
                            "-f", f"udp portrange {wan_lo}-{wan_hi}",
                            "-w", str(cap_path), "-q"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.6)  # let dumpcap attach before the routers announce
    platform = start_router(router_binary, config_path, "platform", e2e_tmp_dir,
                            admin_participant="platform_lan")
    control = start_router(router_binary, config_path, "control", e2e_tmp_dir,
                           node_name="Control_20", name="e2e-control-role",
                           admin_participant="control_lan")
    provider = dds.QosProvider(str(admin_types_xml))
    plat_probe = Probe(unique_domains["platform_lan"])
    ctrl_probe = Probe(unique_domains["control_lan"])
    plat_acc, ctrl_acc = [], []
    try:
        plat_stats = link_stats_reader(plat_probe, provider)
        ctrl_stats = link_stats_reader(ctrl_probe, provider)
        c_ok, p_ok = wait_for_mutual_discovery(control, platform)
        assert c_ok and p_ok, (f"routers did not discover each other; "
                               f"control={control.log_path} platform={platform.log_path}")
        steady_start = time.time()
        for _ in range(40):
            drain_into(plat_stats, plat_acc)
            drain_into(ctrl_stats, ctrl_acc)
            time.sleep(0.1)
        steady_end = time.time()
        for records, observer, peer in ((plat_acc, PLATFORM, CONTROL),
                                        (ctrl_acc, CONTROL, PLATFORM)):
            assert any(f["observer"] == observer and f["peer"] == peer
                       and f["rtt_count"] > 0 and f["rtt_valid"] for f in records), records
    finally:
        plat_probe.close()
        ctrl_probe.close()
        platform.stop()
        control.stop()
        cap.terminate()
        try:
            cap.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cap.kill()
            cap.wait()
        time.sleep(0.2)  # let the pcap flush

    # One tshark pass over every RTPS packet: port(s), size, and any SEDP topic name(s).
    # tshark can exit non-zero on the truncated final packet of a terminated live capture
    # while still emitting every complete packet — parse regardless of exit code.
    out = subprocess.run(
        ["tshark", "-r", str(cap_path), "-Y", "rtps", "-T", "fields",
         "-e", "udp.srcport", "-e", "udp.dstport", "-e", "frame.len",
         "-e", "rtps.param.topicName", "-e", "frame.time_epoch",
         "-e", "rtps.guidPrefix.src", "-E", "separator=|"],
        capture_output=True, text=True, check=True).stdout

    wan_topics, all_topics = set(), set()
    wan_bytes = 0
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) < 4:
            continue
        srcport, dstport, length, topicfield = parts[0], parts[1], parts[2], parts[3]
        in_wan = False
        for p in (srcport, dstport):
            if p.isdigit() and wan_lo <= int(p) <= wan_hi:
                in_wan = True
        topics = [t for t in topicfield.replace(",", " ").split() if t]
        for t in topics:
            all_topics.add(t)
            if in_wan:
                wan_topics.add(t)
        if (in_wan and length.isdigit() and len(parts) >= 6
                and steady_start <= float(parts[4]) <= steady_end and parts[5]):
            wan_bytes += int(length)
    bytes_per_s = wan_bytes / (steady_end - steady_start)
    print(f"[wire-frugal] WAN domain {unique_domains['wan']} ports [{wan_lo},{wan_hi}]: "
          f"topics={sorted(wan_topics)}; steady-state ~{bytes_per_s:.0f} bytes/s; "
          f"all topics on lo={sorted(all_topics)}")

    assert "RouterLinkProbe" not in all_topics, \
        f"Retired probe endpoint observed: {sorted(all_topics)}"
    assert "RouterHealth" in wan_topics, \
        f"RouterHealth (bellwether) SEDP not in the WAN port range; wan={sorted(wan_topics)}"
    # Positive health discovery and LAN RTT above make endpoint absence meaningful.
    assert "ActRouterLinkStats" not in wan_topics, \
        (f"ActRouterLinkStats crossed the WAN — it must stay LAN-only (WAN-frugal, D14); "
         f"wan_topics={sorted(wan_topics)}")
    assert "ActRouterStatus" not in wan_topics, \
        f"ActRouterStatus crossed the WAN (should be LAN-only); wan={sorted(wan_topics)}"

    # Health + discovery only, no application traffic or triggered RTT response.
    assert 0 < bytes_per_s < 50_000, \
        (f"WAN steady-state bytes/s out of the frugal range ({bytes_per_s:.0f}); a Phase 9 "
         f"regression may have added WAN traffic beyond the probe pair (or the capture was "
         f"empty)")
