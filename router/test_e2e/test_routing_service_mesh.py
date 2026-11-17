"""Service-owned monitoring and team-control acceptance on isolated DDS domains."""

import os
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
from contextlib import ExitStack
import copy
import xml.etree.ElementTree as ET

import pytest
import rti.connextdds as dds
import yaml

from harness_v2.receive_policy import ReceivePolicyHistory
from harness_v2.scripts.delivery_audit_report import expected_receivers
from test_routing_service import rs_build, rs_contract, rs_mesh, configure_contract
from conftest import REPO_ROOT, RouterProcess, render_config, set_wan_qos_env
from util.dds_probe import Probe, reader_qos, writer_qos, write_until_seen


RS_WAN_CONTRACT = "three_topic_union"
RS_MONITORING = True
RS_RECEIVE_POLICY = "mission"
RS_QOS_PROFILES = {"periodic_1s": "ACT_QOS_LIB::lan_status_1s"}


def wait_sample(reader, predicate, timeout=20, send=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if send:
            send()
        for sample in reader.take():
            if sample.info.valid:
                result = predicate(sample.data)
                if result:
                    return result
        time.sleep(0.05)
    raise AssertionError("Native processor monitoring did not reach expected state")


def mission_writer(probe, node, phase, sequence=1, sent=None):
    writer = probe.writer("PlatformMissionStatus", "platform_mission_status", writer_qos("lan_event"))
    deadline = time.monotonic() + 15
    while writer.publication_matched_status.current_count == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert writer.publication_matched_status.current_count > 0
    sample = probe.sample("platform_mission_status", source=node, source_node=node,
                          mission_phase=phase, run_id="mission-policy", audit_sequence=sequence,
                          sent_at_ns=time.time_ns() if sent is None else sent)
    return writer, sample


def applied(reader, node, phase, error=None, send=None, after_revision=None):
    def predicate(data):
        for peer in data["peers"]:
            health = peer["health"]
            if (health["router"] == node + "/rs" and health["receive_state"] == phase
                    and str(peer["presence"]) == "PRESENCE_ALIVE"
                    and (after_revision is None or health["state_revision"] > after_revision)):
                if error is None or error in health["receive_filter_error"]:
                    return health
    return wait_sample(reader, predicate, send=send)


def absent(reader, sequence, seconds=0.8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        assert not any(info.valid and data["audit_sequence"] == sequence
                       for data, info in reader.take())
        time.sleep(0.04)


def test_internal_diagnostics_forward_without_an_external_lan_producer(rs_mesh):
    nodes, observer = rs_mesh
    local = nodes["Boat30"][1].reader(
        "ActRouterDiagnostics", "RouterDiagnostics", reader_qos("lan_status"))
    snapshot = wait_sample(local, lambda data: data)
    local_route = next(route for route in snapshot["routes"]
                       if route["route_name"] == "ActRouterDiagnostics.tx")
    assert str(local_route["state"]) == "ROUTE_ENABLED", str(local_route)
    reader = observer.reader("Periodic", "WanStatus", reader_qos("wan_union_status"))
    frame = wait_sample(reader, lambda data: data
                        if data["source_id"] == "Boat30"
                        and data["original_topic"] == "ActRouterDiagnostics" else None)
    assert frame["payload.diagnostics.router"] == "Boat30/rs"
    assert frame["payload.diagnostics.snapshot_seq"] > 0
    route = next(route for route in frame["payload.diagnostics.routes"]
                 if route["route_name"] == "ActRouterDiagnostics.tx")
    assert str(route["state"]) == "ROUTE_ENABLED"
    assert route["topic_status"][0]["input_matched"] == 0
    assert "Internal diagnostics producer" in route["topic_status"][0]["match_reason"]
    assert "rs.gateway.lan_active direction=tx topic=ActRouterDiagnostics" not in (
        nodes["Boat30"][0].log_path.read_text())


@pytest.mark.parametrize("rs_contract", ["two_topic_union", "three_topic_union"])
def test_internal_diagnostics_failure_preserves_monitoring_cadence(
        rs_build, rs_contract, unique_domains, e2e_tmp_dir):
    set_wan_qos_env()
    for index in range(1, 12):
        os.environ["WAN_PEER" + (str(index) if index > 1 else "")] = "[0-15]@builtin.udpv4://127.0.0.1"
    path = render_config("control-platform.yaml", unique_domains, e2e_tmp_dir)
    config = yaml.safe_load(path.read_text())
    configure_contract(config, rs_contract)
    config["router"]["admin_participant"] = "platform_lan"
    config["routing_service"] = {"receive_policy": "static"}
    path.write_text(yaml.safe_dump(config))
    prepared = e2e_tmp_dir / "diagnostics_failure.xml"
    subprocess.run([sys.executable, str(REPO_ROOT / "router/routing_service/run.py"),
                    "--config", str(path), "--role", "platform", "--node-name", "Boat30",
                    "--name", "rs", "--build-dir", str(rs_build), "--render", str(prepared)],
                   check=True, capture_output=True, text=True)
    root = ET.parse(prepared)
    family = "Periodic" if rs_contract == "three_topic_union" else "Status"
    qos = root.find(f".//output[@name='wan_out_{family}']/datawriter_qos")
    # Health occupies the only WAN instance; diagnostics must fail on every tick.
    limits = ET.SubElement(qos, "resource_limits")
    ET.SubElement(limits, "max_instances").text = "1"
    ET.SubElement(limits, "initial_instances").text = "1"
    root.write(prepared)
    with ExitStack() as cleanup:
        observer = Probe(unique_domains["wan"], participant_name="Control_20/observer",
                         role_name="act.router", participant_partition=["SHARED"],
                         participant_profile=("wan_three_topic_participant"
                                              if rs_contract == "three_topic_union"
                                              else "wan_two_topic_participant"))
        cleanup.callback(observer.close)
        health = observer.reader(family, "WanStatus", reader_qos("wan_union_status"))
        local = Probe(unique_domains["platform_lan"])
        cleanup.callback(local.close)
        diagnostics = local.reader("ActRouterDiagnostics", "RouterDiagnostics", reader_qos("lan_status"))
        links = local.reader("ActRouterLinkStats", "RouterLinkStats", reader_qos("router_link_stats"))
        log_path = e2e_tmp_dir / "diagnostics_failure.log"
        with log_path.open("w") as log:
            process = RouterProcess(subprocess.Popen(
                [sys.executable, str(REPO_ROOT / "router/routing_service/run.py"),
                 "--xml", str(prepared)], cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT), log_path)
        cleanup.callback(process.stop)

        def diagnostics_failed(data):
            return any(route["route_name"] == "ActRouterDiagnostics.tx" and route["last_error"]
                       for route in data["routes"])

        wait_sample(diagnostics, diagnostics_failed)
        health.take()
        links.take()
        sequences, captures = set(), set()
        deadline = time.monotonic() + 3.2
        while time.monotonic() < deadline:
            assert process.is_alive(), log_path.read_text()
            for data, info in health.take():
                if info.valid and data["original_topic"] == "RouterHealth":
                    sequences.add(data["payload.health.heartbeat_seq"])
            for data, info in links.take():
                if info.valid and data["peer_router"] == "Control_20/observer":
                    captures.add(data["capture_seq"])
            time.sleep(0.02)
        assert len(sequences) >= 2, sequences
        assert max(sequences) - min(sequences) <= 5, sequences
        assert len(captures) >= 2, captures
        assert "event=rs.diagnostics.error" in log_path.read_text()


@pytest.mark.parametrize("kind,wrong_domain", [
    ("input", False), ("output", False), ("input", True), ("output", True),
    ("participant", True),
])
def test_native_startup_rejects_lan_endpoint_ownership(
        rs_build, unique_domains, e2e_tmp_dir, kind, wrong_domain):
    """Bypass Python preflight: the native processor must reject the actual endpoint."""
    set_wan_qos_env()
    for index in range(1, 12):
        os.environ["WAN_PEER" + (str(index) if index > 1 else "")] = "[0-15]@builtin.udpv4://127.0.0.1"
    path = render_config("control-platform.yaml", unique_domains, e2e_tmp_dir)
    config = yaml.safe_load(path.read_text())
    configure_contract(config, "three_topic_union")
    config["router"]["admin_participant"] = "platform_lan"
    config["routing_service"] = {"receive_policy": "static"}
    path.write_text(yaml.safe_dump(config))
    prepared = e2e_tmp_dir / "prepared.xml"
    subprocess.run([sys.executable, str(REPO_ROOT / "router/routing_service/run.py"),
                    "--config", str(path), "--role", "platform", "--node-name", "Boat30",
                    "--name", "rs", "--build-dir", str(rs_build), "--data-plane-only",
                    "--render", str(prepared)], check=True, capture_output=True, text=True)
    root = ET.parse(prepared)
    domain = root.find(".//domain_route")
    lan = domain.find("participant[@name='platform_lan']")
    direction = "output" if kind == "output" else "input"
    topic = "Mission" if direction == "output" else "ContactReport"
    port = next(port for port in root.findall(".//topic_route/" + direction)
                if port.get("participant") == "platform_lan" and port.findtext("topic_name") == topic)
    actual_domain = unique_domains["platform_lan"]
    if kind == "participant":
        lan.find("domain_id").text = str(unique_domains["control_lan"])
        actual_domain = unique_domains["control_lan"]
    else:
        other = copy.deepcopy(lan)
        other.set("name", "wrong_lan")
        other.find("domain_participant_qos/participant_name/name").text = "Boat30/rs/wrong_lan"
        if wrong_domain:
            other.find("domain_id").text = str(unique_domains["control_lan"])
            actual_domain = unique_domains["control_lan"]
        domain.insert(0, other)
        port.set("participant", "wrong_lan")
    root.write(prepared)
    home = Path(os.environ["NDDSHOME"])
    executable, = (home / "resource/app/bin").glob("*/rtiroutingserviceapp")
    environment = dict(os.environ, LD_LIBRARY_PATH=os.pathsep.join([
        str(home / "resource/app/lib" / executable.parent.name),
        str(home / "lib" / executable.parent.name), os.environ.get("LD_LIBRARY_PATH", "")]))
    log_path = e2e_tmp_dir / "native.log"
    with log_path.open("w") as log:
        process = RouterProcess(subprocess.Popen(
            [str(executable), "-cfgFile", str(prepared), "-cfgName", "ACTGateway",
             "-verbosity", "WARN"], env=environment, stdout=log, stderr=subprocess.STDOUT), log_path)
    with ExitStack() as cleanup:
        cleanup.callback(process.stop)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            assert process.is_alive(), log_path.read_text()
            if "rs.gateway.started" in log_path.read_text():
                break
            time.sleep(0.05)
        assert "rs.gateway.started" in log_path.read_text(), log_path.read_text()
        # Discovery now activates only the deliberately misbound child. The hub
        # is allowed to run, but no rejected native handle may enter its registry.
        application = Probe(actual_domain)
        cleanup.callback(application.close)
        observer = Probe(unique_domains["wan"], participant_profile="wan_three_topic_participant",
                         participant_partition=["SHARED"])
        cleanup.callback(observer.close)
        marker = "ownership-rejection"
        if direction == "input":
            writer = application.writer(topic, "contact_report", writer_qos("lan_event"))
            sample = application.sample("contact_report", source="Boat30", source_node="Boat30",
                                        contact_id=marker)
            received = observer.reader("State", "WanEvent", reader_qos("wan_union_contacts"))
        else:
            received = application.reader(topic, "control_command", reader_qos("lan_event"))
            writer = observer.writer("Event", "WanEvent", writer_qos("wan_event"))
            payload = observer.sample("control_command", source=marker, destination="Boat30",
                                      command_id=marker)
            sample = observer.sample("WanEvent", schema_version=3, source_id="OwnershipPeer",
                                     source_role="control", original_topic=topic, instance_id=marker)
            sample["audience.destination_ids"] = ["Boat30"]
            sample["payload.command"] = payload
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            writer.write(sample)
            if "LAN participant/domain mismatch" in log_path.read_text() or not process.is_alive():
                break
            time.sleep(0.05)
        text = log_path.read_text()
        assert "LAN participant/domain mismatch" in text, text
        assert f"event=rs.gateway.lan_active direction={'tx' if direction == 'input' else 'rx'} topic={topic}" not in text
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            writer.write(sample)
            for data, info in received.take():
                if info.valid:
                    assert data["instance_id" if direction == "input" else "command_id"] != marker
            time.sleep(0.03)


def test_rejected_oversized_team_diagnostic_keeps_ack_and_heartbeat_serializable(rs_mesh):
    nodes, _ = rs_mesh
    boat = nodes["Boat30"][1]
    control = nodes["Control_20"][1]
    mesh = control.reader("ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))
    heartbeat = applied(mesh, "Boat30", "Init")["heartbeat_seq"]
    ack = boat.reader("ActRouterCommandAck", "RouterCommandAck", reader_qos("router_admin_command_ack"))
    writer = boat.writer("ActRouterCommand", "RouterCommand", writer_qos("router_admin_command"))
    command = boat.sample("RouterCommand", target_node="Boat30", target_router="rs",
                          command_id="oversized-rejection", kind=3, participant_name="platform_wan",
                          partition_name="x" * 254 + ";")
    rejected = wait_sample(ack, lambda data: data if data["command_id"] == "oversized-rejection" else None,
                           send=lambda: writer.write(command))
    assert not rejected["accepted"]
    assert len(rejected["message"].encode()) <= 255
    assert "invalid or duplicate team" in rejected["message"]
    health = applied(mesh, "Boat30", "Init", error="invalid or duplicate team")
    assert health["heartbeat_seq"] > heartbeat
    assert len(health["receive_filter_error"].encode()) <= 255
    assert "x" * 254 + ";" in nodes["Boat30"][0].log_path.read_text()


@pytest.mark.parametrize("rs_contract", ["two_topic_union", "three_topic_union"])
@pytest.mark.parametrize("output_ready", [False, True])
def test_invalid_status_never_revives_peer(rs_mesh, rs_contract, output_ready):
    nodes, observer = rs_mesh
    process, control = nodes["Control_20"]
    mesh = control.reader("ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))
    family = "Periodic" if rs_contract == "three_topic_union" else "Status"
    frames = observer.reader(family, "WanStatus", reader_qos("wan_union_status"))
    health = wait_sample(frames, lambda data: dds.DynamicData(data)
                         if data["original_topic"] == "RouterHealth" else None)
    health["source_id"] = "InvalidStatus"
    health["instance_id"] = "InvalidStatus/rs"
    health["payload.health.router"] = "InvalidStatus/rs"
    writer = observer.writer(family, "WanStatus", writer_qos("wan_union_status"))
    navigation = (control.reader("PlatformStatus", "platform_init_status", reader_qos("lan_status"))
                  if output_ready else None)

    def presence(data, expected):
        return any(peer["health"]["router"] == "InvalidStatus/rs"
                   and str(peer["presence"]) == expected for peer in data["peers"])

    wait_sample(mesh, lambda data: presence(data, "PRESENCE_ALIVE"),
                send=lambda: writer.write(health))
    writer.unregister_instance(writer.register_instance(health))
    wait_sample(mesh, lambda data: presence(data, "PRESENCE_STALE"), timeout=6)
    invalid = dds.DynamicData(health)
    invalid["schema_version"] = 999
    invalid["original_topic"] = "PlatformStatus"
    invalid["instance_id"] = "InvalidStatus"
    invalid["audience.control"] = True
    invalid["audience.global"] = True
    invalid["payload.navigation"] = observer.sample(
        "platform_init_status", source="InvalidStatus", audit_sequence=98765)
    observed = 0
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        writer.write(invalid)
        for sample in mesh.take():
            if sample.info.valid:
                assert not presence(sample.data, "PRESENCE_ALIVE")
                observed += presence(sample.data, "PRESENCE_STALE")
        if navigation is not None:
            assert not any(info.valid and data["audit_sequence"] == 98765
                           for data, info in navigation.take())
        time.sleep(0.03)
    assert observed > 0
    assert "unsupported schema version" in process.log_path.read_text()
    invalid["schema_version"] = 3
    wait_sample(mesh, lambda data: presence(data, "PRESENCE_ALIVE"),
                send=lambda: writer.write(invalid))


@pytest.mark.parametrize("rs_contract", ["two_topic_union", "three_topic_union"])
def test_status_burst_observes_every_consumed_instance(rs_mesh, rs_contract):
    nodes, observer = rs_mesh
    process, control = nodes["Control_20"]
    mesh = control.reader("ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))
    navigation = control.reader("PlatformStatus", "platform_init_status", reader_qos("lan_status"))
    family = "Periodic" if rs_contract == "three_topic_union" else "Status"
    frames = observer.reader(family, "WanStatus", reader_qos("wan_union_status"))
    template = wait_sample(frames, lambda data: dds.DynamicData(data)
                           if data["original_topic"] == "RouterHealth" else None)
    writer = observer.writer(family, "WanStatus", writer_qos("wan_union_status"))
    peers = {f"Burst{index}/rs" for index in range(40)}

    def present(data, expected):
        return {peer["health"]["router"] for peer in data["peers"]
                if str(peer["presence"]) == expected} & peers

    health = []
    for index in range(40):
        frame = dds.DynamicData(template)
        frame["source_id"] = f"Burst{index}"
        frame["instance_id"] = f"Burst{index}/rs"
        frame["payload.health.router"] = f"Burst{index}/rs"
        wait_sample(mesh, lambda data: frame["instance_id"] in present(data, "PRESENCE_ALIVE"),
                    send=lambda: writer.write(frame))
        health.append(frame)
    for frame in health:
        writer.unregister_instance(writer.register_instance(frame))
    wait_sample(mesh, lambda data: present(data, "PRESENCE_STALE") == peers, timeout=8)
    wait_sample(mesh, lambda data: navigation.subscription_matched_status.current_count > 0)
    children = Path(f"/proc/{process._popen.pid}/task/{process._popen.pid}/children").read_text().split()
    assert len(children) == 1, children
    service_pid = int(children[0])
    os.kill(service_pid, signal.SIGSTOP)
    try:
        for index, frame in enumerate(health):
            frame["original_topic"] = "PlatformStatus"
            frame["instance_id"] = f"Burst{index}"
            frame["audience.control"] = True
            frame["audience.global"] = True
            frame["payload.navigation"] = observer.sample(
                "platform_init_status", source=f"Burst{index}", audit_sequence=98000 + index)
            writer.write(frame)
        time.sleep(0.3)
    finally:
        os.kill(service_pid, signal.SIGCONT)
    wait_sample(mesh, lambda data: present(data, "PRESENCE_ALIVE") == peers, timeout=3)
    received = set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and len(received) < 40:
        received.update((data["source"], data["audit_sequence"]) for data, info in navigation.take()
                        if info.valid and 98000 <= data["audit_sequence"] < 98040)
        time.sleep(0.03)
    assert received == {(f"Burst{index}", 98000 + index) for index in range(40)}


@pytest.mark.skipif(os.environ.get("ROUTER_CONFIG_LOSS_TEST") != "1",
                    reason="requires an explicitly owned isolated network with NET_ADMIN")
@pytest.mark.parametrize("rs_contract,family", [
    ("two_topic_union", "Status"), ("three_topic_union", "Periodic"),
    ("three_topic_union", "State"),
])
def test_replaceable_packet_loss_does_not_poison_router_health(rs_mesh, rs_contract, family):
    nodes, observer = rs_mesh
    process, control = nodes["Control_20"]
    remote = nodes["Boat30"][1].reader(
        "ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))
    state = family == "State"
    type_name, profile = (("WanEvent", "wan_union_contacts") if state
                          else ("WanStatus", "wan_union_status"))
    frames = observer.reader(family, type_name, reader_qos(profile))
    if state:
        frame = observer.sample("WanEvent", schema_version=3, source_id="PacketLossPeer",
                                source_role="platform", original_topic="ContactReport",
                                instance_id="latest")
        frame["audience.control"] = True
        frame["payload.contact_report"] = observer.sample(
            "contact_report", source="PacketLossPeer", contact_id="latest")
        contacts = control.reader("ContactReport", "contact_report", reader_qos("lan_event"))
    else:
        frame = wait_sample(frames, lambda data: dds.DynamicData(data)
                            if data["original_topic"] == "RouterHealth" else None)
        frame["source_id"] = "PacketLossPeer"
        frame["instance_id"] = "PacketLossPeer/rs"
        frame["payload.health.router"] = "PacketLossPeer/rs"
    writer = observer.writer(family, type_name, writer_qos(profile))
    event = "rs.gateway.latest_state_loss" if state else "rs.gateway.best_effort_loss"

    def control_health(data):
        return next((peer["health"] for peer in data["peers"]
                     if peer["health"]["router"] == "Control_20/rs"), None)

    initial = wait_sample(remote, control_health)
    assert str(initial["overall_state"]) != "ROUTER_ERROR"
    wait_sample(frames, lambda data: data["source_id"] == "PacketLossPeer",
                send=lambda: writer.write(frame))
    subprocess.run(["tc", "qdisc", "add", "dev", "lo", "root", "netem", "loss", "100%"],
                   check=True, capture_output=True, text=True)
    try:
        for sequence in range(1, 31):
            if state:
                frame["payload.contact_report.audit_sequence"] = sequence
            writer.write(frame)
            time.sleep(0.02)
        counters = subprocess.run(["tc", "-s", "qdisc", "show", "dev", "lo"],
                                  check=True, capture_output=True, text=True).stdout
        assert "loss 100%" in counters
    finally:
        subprocess.run(["tc", "qdisc", "del", "dev", "lo", "root"],
                       check=True, capture_output=True, text=True)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        writer.write(frame)
        if event in process.log_path.read_text():
            break
        time.sleep(0.05)
    text = process.log_path.read_text()
    assert event in text, text
    assert "rs.gateway.history_error" not in text, text
    recovered = wait_sample(remote, lambda data: (
        health if (health := control_health(data))
        and health["heartbeat_seq"] > initial["heartbeat_seq"] else None))
    assert recovered["overall_state"] == initial["overall_state"]
    if state:
        wait_sample(contacts, lambda data: data["source"] == "PacketLossPeer"
                    and data["contact_id"] == "latest" and data["audit_sequence"] == 30)


def test_periodic_lifecycle_never_revives_peer_and_health_loss_uses_countdown(rs_mesh):
    nodes, observer = rs_mesh
    mesh = nodes["Control_20"][1].reader(
        "ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))
    frames = observer.reader("Periodic", "WanStatus", reader_qos("wan_union_status"))
    template = wait_sample(frames, lambda data: dds.DynamicData(data)
                           if data["original_topic"] == "RouterHealth" else None)
    template["source_id"] = "Lifecycle"
    template["instance_id"] = "Lifecycle/rs"
    template["payload.health.router"] = "Lifecycle/rs"
    writer = observer.writer("Periodic", "WanStatus", writer_qos("wan_union_status"))

    def peer(data):
        return next((peer for peer in data["peers"]
                     if peer["health"]["router"] == "Lifecycle/rs"), None)

    def state(data, expected):
        found = peer(data)
        return found if found and str(found["presence"]) == expected else None

    def send_health():
        template["audit_sequence"] += 1
        template["payload.health.heartbeat_seq"] = template["audit_sequence"]
        template["sent_at_ns"] = time.time_ns()
        template["payload.health.send_timestamp"] = template["sent_at_ns"]
        writer.write(template)

    navigation = dds.DynamicData(template)
    navigation["original_topic"] = "PlatformStatus"
    navigation["instance_id"] = "Lifecycle"
    navigation["audience.control"] = True
    navigation["audience.global"] = True
    navigation["payload.navigation"] = observer.sample("platform_init_status", source="Lifecycle")
    for action in ("unregister", "dispose"):
        wait_sample(mesh, lambda data: state(data, "PRESENCE_ALIVE"), send=send_health)
        # Confirm actual Status reception before changing health lifecycle. With
        # no LAN consumer, submission does not prove that the hub observed it:
        # a later valid Status must legitimately recover a missing heartbeat.
        wait_sample(mesh, lambda data: found if
                    (found := state(data, "PRESENCE_ALIVE")) and found["health_age_ms"] >= 100 else None)
        writer.write(navigation)
        wait_sample(mesh, lambda data: found if
                    (found := state(data, "PRESENCE_ALIVE")) and
                    found["health_age_ms"] - found["last_seen_delta_ms"] >= 50 else None, timeout=2)
        handle = writer.register_instance(template)
        if action == "unregister":
            writer.unregister_instance(handle)
        else:
            writer.dispose_instance(handle)
        wait_sample(mesh, lambda data: state(data, "PRESENCE_STALE"), timeout=6)
        mesh.take()
        handle = writer.register_instance(navigation)
        if action == "unregister":
            writer.unregister_instance(handle)
        else:
            writer.dispose_instance(handle)
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            for sample in mesh.take():
                if sample.info.valid and peer(sample.data):
                    assert str(peer(sample.data)["presence"]) == "PRESENCE_STALE"
            time.sleep(0.03)
        wait_sample(mesh, lambda data: state(data, "PRESENCE_ALIVE"), send=send_health)
        mesh.take()
        handle = writer.register_instance(template)
        if action == "unregister":
            writer.unregister_instance(handle)
        else:
            writer.dispose_instance(handle)
        wait_sample(mesh, lambda data: state(data, "PRESENCE_STALE"), timeout=6)
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            for sample in mesh.take():
                if sample.info.valid and peer(sample.data):
                    assert str(peer(sample.data)["presence"]) == "PRESENCE_STALE"
            time.sleep(0.03)
        # Any valid Status (not only a heartbeat) restores reachability.
        wait_sample(mesh, lambda data: state(data, "PRESENCE_ALIVE"),
                    send=lambda: writer.write(navigation))
    for process, _ in nodes.values():
        assert "rs.lifecycle.error" not in process.log_path.read_text()
        assert "event=rs.gateway.lan_active direction=rx topic=PlatformStatus" not in process.log_path.read_text()


@pytest.fixture
def audited_rs_mesh(request, monkeypatch, e2e_tmp_dir):
    monkeypatch.setenv("NODE_DEBUG_DIR", str(e2e_tmp_dir))
    monkeypatch.delenv("ACT_RUN_ID", raising=False)
    (e2e_tmp_dir / "run_id").write_text("policy-audit-test\n")
    if hasattr(request, "param"):
        monkeypatch.setattr(request.module, "RS_RECEIVE_POLICY", request.param)
    mesh = request.getfixturevalue("rs_mesh")
    return mesh, e2e_tmp_dir / "events.jsonl"


def test_receive_policy_audit_records_only_successful_commits_and_restart(audited_rs_mesh):
    (nodes, _), path = audited_rs_mesh
    boat = nodes["Boat30"][1]
    mesh = nodes["Control_20"][1].reader(
        "ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))

    def records():
        return [row for line in path.read_text().splitlines()
                if (row := json.loads(line))["event"] == "receive_policy"]

    def local_records():
        return [row for row in records() if row["node"] == "Boat30"]

    assert local_records()[0]["receive_profile"] == "Init"
    assert local_records()[0]["teams"] == ["TEAM_A"]
    assert all(row["node"] != "Control_20" for row in records())
    policy_writer, policy_sample = mission_writer(boat, "Boat30", "ORBIT")
    applied(mesh, "Boat30", "ORBIT", send=lambda: policy_writer.write(policy_sample))
    assert [row["receive_profile"] for row in local_records()] == ["Init", "ActiveMission"]
    ack = boat.reader("ActRouterCommandAck", "RouterCommandAck", reader_qos("router_admin_command_ack"))
    writer = boat.writer("ActRouterCommand", "RouterCommand", writer_qos("router_admin_command"))
    for identifier, team, accepted in (("bad-policy-team", "x" * 254 + ";", False),
                                        ("remove-policy-team", "TEAM_A", True)):
        command = boat.sample("RouterCommand", target_node="Boat30", target_router="rs",
                              command_id=identifier, kind=4 if accepted else 3,
                              participant_name="platform_wan", partition_name=team)
        result = wait_sample(ack, lambda data: data if data["command_id"] == identifier else None,
                             send=lambda: writer.write(command))
        assert result["accepted"] == accepted
        assert len(local_records()) == (3 if accepted else 2)
    assert local_records()[-1]["teams"] == []
    previous = nodes["Boat30"][0]
    previous.stop()
    restart_log = previous.log_path.with_name("policy_restart.log")
    with restart_log.open("w") as log:
        restarted = RouterProcess(subprocess.Popen(
            previous._popen.args, cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT), restart_log)
    try:
        deadline = time.monotonic() + 20
        while len(local_records()) < 4 and time.monotonic() < deadline:
            assert restarted.is_alive(), restart_log.read_text()
            time.sleep(0.05)
        assert local_records()[-1]["receive_profile"] == "Init"
        assert [row["sequence"] for row in local_records()] == [1, 2, 3, 1]
        for row in records():
            assert row["run_id"] == "policy-audit-test"
            assert row["topic"] == "PlatformStatus"
            assert row["source_node"] == row["node"]
            assert row["sent_at_ns"] == row["recorded_at_ns"] > 0
        history = ReceivePolicyHistory()
        for row in records():
            history.add(row)
        expectation = {"enabled": True,
                       "recipient_rule": {"kind": "mission_navigation", "nodes": ["Boat30"]}}
        for row, profile, teams in zip(
                local_records(), ("Init", "ActiveMission", "ActiveMission", "Init"),
                (["TEAM_A"], ["TEAM_A"], [], ["TEAM_A"])):
            assert history.state_at(row["run_id"], "Boat30", row["sent_at_ns"]) == {
                "receive_profile": profile, "teams": tuple(teams)}
            sent = {"run_id": row["run_id"], "source_node": "Boat31",
                    "sent_at_ns": row["sent_at_ns"]}
            assert expected_receivers(sent, expectation, history) == (
                ["Boat30"] if profile == "ActiveMission" else [])
    finally:
        restarted.stop()


@pytest.mark.parametrize("audited_rs_mesh", ["static"], indirect=True)
def test_static_receive_policy_emits_no_policy_audit(audited_rs_mesh):
    (nodes, _), path = audited_rs_mesh
    reader = nodes["Control_20"][1].reader(
        "ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))
    wait_sample(reader, lambda data: len(data["peers"]) == 2)
    assert not any(json.loads(line)["event"] == "receive_policy"
                   for line in path.read_text().splitlines())


def test_receive_policy_audit_append_failure_is_explicit(request, monkeypatch, e2e_tmp_dir):
    monkeypatch.setenv("NODE_DEBUG_DIR", str(e2e_tmp_dir / "missing"))
    monkeypatch.setenv("ACT_RUN_ID", "failed-audit-test")
    nodes, _ = request.getfixturevalue("rs_mesh")
    for node in ("Boat30", "Boat31"):
        assert "rs.receive_policy.audit_failed" in nodes[node][0].log_path.read_text()
        assert nodes[node][0].is_alive()
    assert "rs.receive_policy.audit_failed" not in nodes["Control_20"][0].log_path.read_text()


def test_service_owned_health_protocol_stats_and_team_updates(rs_mesh):
    nodes, observer = rs_mesh
    control = nodes["Control_20"][1]
    boat = nodes["Boat30"][1]
    mesh = control.reader("ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))
    links = control.reader("ActRouterLinkStats", "RouterLinkStats", reader_qos("router_link_stats"))
    status = boat.reader("ActRouterStatus", "RouterStatus", reader_qos("router_admin_status"))
    ack = boat.reader("ActRouterCommandAck", "RouterCommandAck", reader_qos("router_admin_command_ack"))
    command = boat.writer("ActRouterCommand", "RouterCommand", writer_qos("router_admin_command"))

    def healthy(data):
        peers = data["peers"]
        return len(peers) == 2 and all(
            str(peer["presence"]) == "PRESENCE_ALIVE"
            and peer["health"]["router_backend"] == "routing_service"
            and peer["health"]["reporting_policy"] == "always_on"
            for peer in peers)

    wait_sample(mesh, healthy)
    wait_sample(links, lambda data: data["rtt_valid"] and data["samples_received"] > 0)
    wait_sample(status, lambda data: any(
        topic["wan_family"] == "Periodic" and topic["publication_priority"] > 0
        and topic["output_write_timeouts"] == 0
        for route in data["routes"] for topic in route["topic_status"]))
    policy_writer, policy_sample = mission_writer(nodes["Boat31"][1], "Boat31", "ORBIT")
    policy_writer.write(policy_sample)
    applied(mesh, "Boat31", "ORBIT", send=lambda: policy_writer.write(policy_sample))
    team_writer = boat.writer("PlatformMissionStatus", "platform_mission_status", writer_qos("lan_event"))
    team_reader = nodes["Boat31"][1].reader(
        "PlatformMissionStatus", "platform_mission_status", reader_qos("lan_status"))
    team_sample = boat.sample("platform_mission_status", source="Boat30", source_node="Boat30",
                             mission_phase="IDLE", sent_at_ns=time.time_ns(), audit_sequence=1)
    assert write_until_seen(lambda: team_writer.write(team_sample), team_reader,
                            lambda data: data["audit_sequence"], 1).get(1)
    data = boat.sample("RouterCommand", target_node="Boat30", target_router="rs",
                       command_id="remove-team", kind=4, participant_name="platform_wan",
                       partition_name="TEAM_A")
    deadline = time.monotonic() + 15
    accepted = False
    while time.monotonic() < deadline and not accepted:
        command.write(data)
        for sample in ack.take():
            if sample.info.valid and sample.data["command_id"] == "remove-team":
                accepted = accepted or sample.data["accepted"]
        time.sleep(0.1)
    assert accepted
    wait_sample(status, lambda data: any(
        participant["name"] == "platform_wan" and "TEAM_A" not in participant["team_partition"]
        for participant in data["participants"]))
    wait_sample(mesh, lambda data: any(
        peer["health"]["router"] == "Boat30/rs"
        and "TEAM_A" not in peer["health"]["team_partition"] for peer in data["peers"]))
    team_reader.take()
    team_sample["audit_sequence"] = 2
    deadline = time.monotonic() + 1.2
    while time.monotonic() < deadline:
        team_writer.write(team_sample)
        assert not any(info.valid and data["audit_sequence"] == 2
                       for data, info in team_reader.take())
        time.sleep(0.05)
    publications = observer.discovered_publications()
    gateways = {}
    for publication in publications:
        gateways.setdefault(publication["participant_key"], []).append(publication["topic"])
    gateways = [topics for topics in gateways.values() if "Event" in topics]
    assert len(gateways) == 3
    assert all(sorted(topics) == ["Event", "Periodic", "State"] for topics in gateways)
    assert all(process.is_alive() for process, _ in nodes.values())


def test_mission_profiles_native_transitions_local_authority_and_filter_evidence(rs_mesh):
    from test_wan_filter_observations import observations

    nodes, observer = rs_mesh
    control, boat, peer = (nodes[node][1] for node in ("Control_20", "Boat30", "Boat31"))
    mesh = control.reader("ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))
    initial = applied(mesh, "Boat30", "Init")
    assert initial["receive_profile"] == "Init"
    previous_revision = initial["state_revision"]
    writer, local = mission_writer(boat, "Boat30", "TRANSIT")
    remote_writer, remote = mission_writer(peer, "Boat31", "CANCELLED")
    remote_reader = boat.reader("PlatformMissionStatus", "platform_mission_status", reader_qos("lan_status"))
    contact_writer = peer.writer("ContactReport", "contact_report", writer_qos("lan_event"))
    contact_reader = boat.reader("ContactReport", "contact_report", reader_qos("lan_event"))
    c2_report = control.reader("PlatformMissionStatus", "platform_mission_status", reader_qos("lan_status"))
    c2_contact = control.reader("ContactReport", "contact_report", reader_qos("lan_event"))
    command_writer = control.writer("Mission", "control_command", writer_qos("lan_event"))
    command_reader = boat.reader("Mission", "control_command", reader_qos("lan_event"))
    unintended_command_reader = peer.reader("Mission", "control_command", reader_qos("lan_event"))
    team_writer = control.writer("Team", "TeamAssignment", writer_qos("lan_event"))
    team_reader = boat.reader("Team", "TeamAssignment", reader_qos("lan_event"))
    navigation_writer = peer.writer("PlatformStatus", "platform_init_status", writer_qos("lan_event"))
    navigation_reader = boat.reader("PlatformStatus", "platform_init_status", reader_qos("lan_status"))
    navigation = peer.sample("platform_init_status", source="Boat31", audit_sequence=900)
    forbidden = [
        (peer.writer("PlatformData", "platform_data", writer_qos("lan_event")),
         boat.reader("PlatformData", "platform_data", reader_qos("lan_status")),
         peer.sample("platform_data", source="Boat31", audit_sequence=900)),
        (control.writer("ActPlatformStatusMode", "PlatformStatusMode", writer_qos("lan_event")),
         boat.reader("ActPlatformStatusMode", "PlatformStatusMode", reader_qos("lan_event")),
         control.sample("PlatformStatusMode", platform_node="Boat30", resolution_mode=2,
                        request_id="excluded")),
    ]
    contact = peer.sample("contact_report", source="Boat31", source_node="Boat31",
                          contact_id="mission-peer", audit_sequence=100)
    remote["audit_sequence"] = 100
    for _ in range(10):
        remote_writer.write(remote)
        contact_writer.write(contact)
        time.sleep(0.05)
    assert write_until_seen(lambda: contact_writer.write(contact), c2_contact,
                            lambda data: data["audit_sequence"], 100).get(100)
    absent(remote_reader, 100)
    absent(contact_reader, 100)
    native_reader_id = None

    for sequence, phase in enumerate(("TRANSIT", "ORBIT", "RTB", "HOLDING",
                                      "IDLE", "TRANSIT", "COMPLETE", "ORBIT", "CANCELLED"), 2):
        local["mission_phase"] = phase
        local["sent_at_ns"] = time.time_ns()
        local["audit_sequence"] = sequence
        writer.write(local)
        health = applied(mesh, "Boat30", phase, send=lambda: writer.write(local),
                         after_revision=previous_revision)
        previous_revision = health["state_revision"]
        active = phase in ("TRANSIT", "ORBIT", "RTB", "HOLDING")
        assert health["receive_profile"] == ("ActiveMission" if active else "Init")
        assert health["reporting_policy"] == "always_on"
        assert write_until_seen(lambda: writer.write(local), c2_report,
                                lambda data: data["audit_sequence"], sequence).get(sequence)
        command = control.sample("control_command", source="Control_20", destination="Boat30",
                                 audit_sequence=sequence, command_id=f"phase-{sequence}")
        assert write_until_seen(lambda: command_writer.write(command), command_reader,
                                lambda data: data["audit_sequence"], sequence).get(sequence)
        absent(unintended_command_reader, sequence, seconds=0.2)
        assignment = control.sample("TeamAssignment", platform_node="Boat30",
                                    team_name=f"phase-{sequence}")
        assert write_until_seen(lambda: team_writer.write(assignment), team_reader,
                                lambda data: data["team_name"], f"phase-{sequence}").get(
                                    f"phase-{sequence}")
        remote_reader.take()
        contact_reader.take()
        navigation_reader.take()
        remote["audit_sequence"] = contact["audit_sequence"] = 100 + sequence
        navigation["audit_sequence"] = 200 + sequence
        if active:
            assert write_until_seen(lambda: remote_writer.write(remote), remote_reader,
                                    lambda data: data["audit_sequence"], 100 + sequence).get(100 + sequence)
            assert write_until_seen(lambda: contact_writer.write(contact), contact_reader,
                                    lambda data: data["audit_sequence"], 100 + sequence).get(100 + sequence)
            assert write_until_seen(lambda: navigation_writer.write(navigation), navigation_reader,
                                    lambda data: data["audit_sequence"], 200 + sequence).get(200 + sequence)
            # Peer CANCELLED reports delivered on the LAN never change the local phase.
            applied(mesh, "Boat30", phase)
            if native_reader_id is None:
                def active_filters(data):
                    for peer_info in data["peers"]:
                        if peer_info["health"]["router"] == "Boat31/rs":
                            filters = observations(peer_info["health"])
                            periodic = filters.get("Periodic", {}).get("readers", {}).get("Boat30/rs")
                            state = filters.get("State", {}).get("readers", {}).get("Boat30/rs")
                            if (periodic and state and periodic["has_filter"] and state["has_filter"]
                                    and "'PlatformMissionStatus'" in periodic["expression"]
                                    and "'PlatformStatus'" in periodic["expression"]
                                    and "'ContactReport'" in state["expression"]
                                    and "'TEAM_A'" in periodic["parameters"]
                                    and "'TEAM_A'" in state["parameters"]):
                                return periodic["reader_id"]
                native_reader_id = wait_sample(mesh, active_filters, timeout=40)
        else:
            for _ in range(8):
                remote_writer.write(remote)
                contact_writer.write(contact)
                navigation_writer.write(navigation)
                time.sleep(0.05)
            absent(remote_reader, 100 + sequence)
            absent(contact_reader, 100 + sequence)
            absent(navigation_reader, 200 + sequence)
        for excluded_writer, excluded_reader, excluded_sample in forbidden:
            excluded_writer.write(excluded_sample)
            time.sleep(0.1)
            assert not any(info.valid for _, info in excluded_reader.take())

    # The sender's native discovery API observes actual remote reader CFTs.
    def init_filters(data):
        for peer_info in data["peers"]:
            if peer_info["health"]["router"] != "Boat31/rs":
                continue
            filters = observations(peer_info["health"])
            periodic = filters.get("Periodic", {}).get("readers", {}).get("Boat30/rs")
            if periodic and periodic["has_filter"] and "'PlatformMissionStatus'" not in periodic["expression"]:
                return periodic
    native = wait_sample(mesh, init_filters, timeout=40)
    assert native["reader_id"] == native_reader_id, "Policy transition recreated a WAN reader"
    assert "'PlatformMissionStatus'" not in native["expression"]
    assert "'PlatformStatus'" not in native["expression"]
    assert "'PlatformData'" not in native["expression"]
    assert "local mission source does not match" not in nodes["Boat30"][0].log_path.read_text()
    assert all(process.is_alive() for process, _ in nodes.values())


def test_mission_invalid_stale_source_and_unassigned_team_updates(rs_mesh):
    nodes, _ = rs_mesh
    control, boat, peer = (nodes[node][1] for node in ("Control_20", "Boat30", "Boat31"))
    mesh = control.reader("ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))
    writer, local = mission_writer(boat, "Boat30", "HOLDING", 1)
    writer.write(local)
    applied(mesh, "Boat30", "HOLDING", send=lambda: writer.write(local))
    sent = local["sent_at_ns"]
    for changes, error in (({"mission_phase": "UNKNOWN", "sent_at_ns": sent + 1}, "invalid local mission"),
                           ({"mission_phase": "IDLE", "sent_at_ns": sent, "audit_sequence": 500}, "stale"),
                           ({"source": "NotBoat30", "sent_at_ns": sent + 2}, "source"),
                           ({"source_node": "NotBoat30", "sent_at_ns": sent + 3}, "source"),
                           ({"sent_at_ns": 0, "timestamp": 0}, "invalid source timestamp")):
        invalid = boat.sample("platform_mission_status", source="Boat30", source_node="Boat30",
                              mission_phase="IDLE", audit_sequence=2, sent_at_ns=sent + 2)
        for field, value in changes.items():
            invalid[field] = value
        writer.write(invalid)
        assert applied(mesh, "Boat30", "HOLDING", error)["receive_profile"] == "ActiveMission"
    reports = boat.writer("PlatformStatus", "platform_init_status", writer_qos("lan_event"))
    report_reader = control.reader("PlatformStatus", "platform_init_status", reader_qos("lan_status"))
    report = boat.sample("platform_init_status", source="Boat30", audit_sequence=77)
    assert write_until_seen(lambda: reports.write(report), report_reader,
                            lambda data: data["audit_sequence"], 77).get(77)
    command = boat.writer("ActRouterCommand", "RouterCommand", writer_qos("router_admin_command"))
    ack = boat.reader("ActRouterCommandAck", "RouterCommandAck", reader_qos("router_admin_command_ack"))
    update = boat.sample("RouterCommand", target_node="Boat30", target_router="rs",
                         command_id="unassign", kind=4, participant_name="platform_wan",
                         partition_name="TEAM_A")
    assert write_until_seen(lambda: command.write(update), ack,
                            lambda data: data["command_id"], "unassign").get("unassign")[0]["accepted"]
    applied(mesh, "Boat30", "HOLDING")
    peer_writer, sample = mission_writer(peer, "Boat31", "ORBIT", 88)
    reader = boat.reader("PlatformMissionStatus", "platform_mission_status", reader_qos("lan_status"))
    for _ in range(8):
        peer_writer.write(sample)
        time.sleep(0.1)
    absent(reader, 88)
    # Even a sender alias equal to our protected identity is not a same-team match.
    peer_command = peer.writer("ActRouterCommand", "RouterCommand", writer_qos("router_admin_command"))
    peer_ack = peer.reader("ActRouterCommandAck", "RouterCommandAck", reader_qos("router_admin_command_ack"))
    alias = peer.sample("RouterCommand", target_node="Boat31", target_router="rs",
                       command_id="identity-alias", kind=3, participant_name="platform_wan",
                       partition_name="Boat30")
    assert write_until_seen(lambda: peer_command.write(alias), peer_ack,
                            lambda data: data["command_id"], "identity-alias").get("identity-alias")[0]["accepted"]
    sample["audit_sequence"] = 89
    for _ in range(8):
        peer_writer.write(sample)
        time.sleep(0.1)
    absent(reader, 89)
    update["kind"] = 3
    update["command_id"] = "reassign"
    assert write_until_seen(lambda: command.write(update), ack,
                            lambda data: data["command_id"], "reassign").get("reassign")[0]["accepted"]
    sample["audit_sequence"] = 90
    assert write_until_seen(lambda: peer_writer.write(sample), reader,
                            lambda data: data["audit_sequence"], 90).get(90)


def test_active_peer_lifecycle_and_local_mission_dispose_do_not_change_policy(rs_mesh):
    nodes, _ = rs_mesh
    control, boat, peer = (nodes[node][1] for node in ("Control_20", "Boat30", "Boat31"))
    mesh = control.reader("ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))
    local_writer, local = mission_writer(boat, "Boat30", "ORBIT")
    local_writer.write(local)
    applied(mesh, "Boat30", "ORBIT", send=lambda: local_writer.write(local))
    writer = peer.writer("ContactReport", "contact_report", writer_qos("lan_event"))
    reader = boat.reader("ContactReport", "contact_report", reader_qos("lan_event"))
    sample = peer.sample("contact_report", source="Boat31", source_node="Boat31",
                         contact_id="active-lifecycle", audit_sequence=501)
    assert write_until_seen(lambda: writer.write(sample), reader,
                            lambda data: data["audit_sequence"], 501).get(501)
    handle = writer.register_instance(sample)
    writer.dispose_instance(handle)
    deadline = time.monotonic() + 10
    disposed = False
    while time.monotonic() < deadline and not disposed:
        disposed = any(info.state.instance_state == dds.InstanceState.NOT_ALIVE_DISPOSED
                       for _, info in reader.take())
        time.sleep(0.05)
    assert disposed
    sample["audit_sequence"] = 502
    assert write_until_seen(lambda: writer.write(sample), reader,
                            lambda data: data["audit_sequence"], 502).get(502)
    local_writer.dispose_instance(local_writer.register_instance(local))
    assert applied(mesh, "Boat30", "ORBIT")["receive_profile"] == "ActiveMission"


def test_active_navigation_is_fleet_global_across_teams_and_unassigned(rs_mesh):
    nodes, _ = rs_mesh
    control, boat, peer = (nodes[node][1] for node in ("Control_20", "Boat30", "Boat31"))
    mesh = control.reader("ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))
    navigation_writer = peer.writer("PlatformStatus", "platform_init_status", writer_qos("lan_event"))
    navigation_reader = boat.reader("PlatformStatus", "platform_init_status", reader_qos("lan_status"))
    c2_reader = control.reader("PlatformStatus", "platform_init_status", reader_qos("lan_status"))
    navigation = peer.sample("platform_init_status", source="Boat31", audit_sequence=700)
    contact_writer = peer.writer("ContactReport", "contact_report", writer_qos("lan_event"))
    contact_reader = boat.reader("ContactReport", "contact_report", reader_qos("lan_event"))
    contact = peer.sample("contact_report", source="Boat31", source_node="Boat31",
                          contact_id="cross-team-navigation", audit_sequence=700)
    command = boat.writer("ActRouterCommand", "RouterCommand", writer_qos("router_admin_command"))
    ack = boat.reader("ActRouterCommandAck", "RouterCommandAck", reader_qos("router_admin_command_ack"))

    def membership(team, kind, identifier):
        update = boat.sample("RouterCommand", target_node="Boat30", target_router="rs",
                             command_id=identifier, kind=kind, participant_name="platform_wan",
                             partition_name=team)
        seen = write_until_seen(lambda: command.write(update), ack,
                                lambda data: data["command_id"], identifier)
        assert seen[identifier][0]["accepted"]

    assert write_until_seen(lambda: navigation_writer.write(navigation), c2_reader,
                            lambda data: data["audit_sequence"], 700).get(700)
    absent(navigation_reader, 700)
    local_writer, local = mission_writer(boat, "Boat30", "TRANSIT")
    applied(mesh, "Boat30", "TRANSIT", send=lambda: local_writer.write(local))
    membership("TEAM_A", 4, "navigation-unassign")
    for sequence, assign_other_team in ((701, False), (702, True)):
        if assign_other_team:
            membership("TEAM_B", 3, "navigation-other-team")
        navigation["audit_sequence"] = sequence
        assert write_until_seen(lambda: navigation_writer.write(navigation), navigation_reader,
                                lambda data: data["audit_sequence"], sequence).get(sequence)
        contact["audit_sequence"] = sequence
        for _ in range(8):
            contact_writer.write(contact)
            time.sleep(0.05)
        absent(contact_reader, sequence)
    local["mission_phase"] = "COMPLETE"
    local["sent_at_ns"] = time.time_ns()
    local["audit_sequence"] = 2
    applied(mesh, "Boat30", "COMPLETE", send=lambda: local_writer.write(local))
    navigation_reader.take()
    navigation["audit_sequence"] = 703
    for _ in range(8):
        navigation_writer.write(navigation)
        time.sleep(0.05)
    absent(navigation_reader, 703)
    assert write_until_seen(lambda: navigation_writer.write(navigation), c2_reader,
                            lambda data: data["audit_sequence"], 703).get(703)


def test_policy_model_replacement_keeps_native_source_pump_and_heartbeats_valid(rs_mesh):
    nodes, _ = rs_mesh
    control, boat = (nodes[node][1] for node in ("Control_20", "Boat30"))
    mesh = control.reader("ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))
    mission_reader = control.reader(
        "PlatformMissionStatus", "platform_mission_status", reader_qos("lan_status"))
    navigation_reader = control.reader("PlatformStatus", "platform_init_status", reader_qos("lan_status"))
    navigation_writer = boat.writer("PlatformStatus", "platform_init_status", writer_qos("lan_event"))
    local_writer, local = mission_writer(boat, "Boat30", "IDLE")
    navigation = boat.sample("platform_init_status", source="Boat30", source_node="Boat30")
    heartbeat_before = applied(mesh, "Boat30", "Init")["heartbeat_seq"]
    # Each change replaces the complete model during a live LAN input loan.
    # Reports after the mission input in the source map must still be processed.
    phases = ("TRANSIT", "ORBIT", "CANCELLED", "RTB", "COMPLETE", "HOLDING", "IDLE", "CANCELLED") * 3
    for sequence, phase in enumerate(phases, 1):
        local["mission_phase"] = phase
        local["sent_at_ns"] = time.time_ns()
        local["audit_sequence"] = sequence
        assert write_until_seen(lambda: local_writer.write(local), mission_reader,
                                lambda data: data["audit_sequence"], sequence).get(sequence)
        navigation["audit_sequence"] = sequence
        assert write_until_seen(lambda: navigation_writer.write(navigation), navigation_reader,
                                lambda data: data["audit_sequence"], sequence).get(sequence)
        assert all(process.is_alive() for process, _ in nodes.values())
    health = applied(mesh, "Boat30", "CANCELLED")
    assert health["receive_profile"] == "Init"
    assert health["heartbeat_seq"] > heartbeat_before
    for process, _ in nodes.values():
        log = process.log_path.read_text()
        assert "rs.sample.error" not in log, log[-12000:]
        assert "rs.lifecycle.error" not in log, log[-12000:]
        assert "ROUTERProcessor_onRouteEvent:FAILURE" not in log, log[-12000:]


@pytest.fixture
def rs_platform_stacks(rs_mesh, unique_domains, extra_domains, e2e_tmp_dir):
    """Own both complete platform application stacks, plus target router replacement."""
    nodes, observer = rs_mesh
    applications = {}
    generation = {}
    with ExitStack() as cleanup:
        def start(node, restart_router=False):
            generation[node] = generation.get(node, 0) + 1
            work = e2e_tmp_dir / f"{node}-stack-{generation[node]}"
            work.mkdir()
            if restart_router:
                previous, probe = nodes[node]
                log_path = work / "routing_service.log"
                with log_path.open("w") as log:
                    router = RouterProcess(subprocess.Popen(previous._popen.args, cwd=REPO_ROOT,
                        stdout=log, stderr=subprocess.STDOUT), log_path)
                cleanup.callback(router.stop)
                nodes[node] = (router, probe)
            domain = (unique_domains if node == "Boat30" else extra_domains)["platform_lan"]
            processes = []
            for name, script, arguments, role in (
                ("simulator", "harness_v2/sims/platform_sim.py",
                 ["--source", node, "--destination", "Control_20", "--domain_id", str(domain),
                  "--qos_profile", "ACT_QOS_LIB::lan_platform_participant",
                  "--run-id", f"stack-{generation[node]}-{node}"], "act.platform_sim"),
                ("controller", "harness_v2/scripts/platform_mesh_control.py",
                 ["--domain", str(domain), "--node", node, "--router-name", "rs",
                  "--always-on-reports"], "act.platform_mesh_control"),
            ):
                environment = dict(os.environ,
                    NDDS_QOS_PROFILES=";".join(str(REPO_ROOT / path) for path in (
                        "harness_v2/qos/act_qos_profiles.xml", "harness_v2/datamodel/gen/ActTypes.xml")),
                    ACT_PARTICIPANT_NAME=f"{node}/{name}", ACT_PARTICIPANT_ROLE=role,
                    NODE_DEBUG_DIR=str(work), PYTHONUNBUFFERED="1")
                log_path = work / f"{name}.log"
                with log_path.open("w") as log:
                    process = RouterProcess(subprocess.Popen(
                        [sys.executable, str(REPO_ROOT / script), *arguments],
                        cwd=REPO_ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT), log_path)
                cleanup.callback(process.stop)
                processes.append(process)
            applications[node] = processes
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not (work / "simulator_ready").exists():
                assert all(process.is_alive() for process in processes), [
                    process.log_path.read_text() for process in processes]
                time.sleep(0.1)
            assert (work / "simulator_ready").exists(), processes[0].log_path.read_text()
            return processes

        def stop(node):
            for process in reversed(applications[node]):
                process.stop()
            nodes[node][0].stop()
            assert not nodes[node][0].is_alive()
            assert all(not process.is_alive() for process in applications[node])

        start("Boat30")
        start("Boat31")
        yield nodes, observer, applications, stop, start


def test_whole_platform_restart_starts_init_without_mission_replay(rs_platform_stacks):
    nodes, _, applications, stop, start = rs_platform_stacks
    control = nodes["Control_20"][1]
    mesh = control.reader("ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))
    applied(mesh, "Boat30", "IDLE")
    applied(mesh, "Boat31", "IDLE")
    command_writer = control.writer("Mission", "control_command", writer_qos("lan_event"))
    ack = control.reader("PlatformCommandAck", "control_command_ack", reader_qos("lan_event"))

    def route(identifier):
        sample = control.sample("control_command", source="Control_20", source_node="Control_20",
            destination="Boat30", command_id=identifier, command_type="MISSION",
            payload=list(json.dumps({"v": 1, "op": "route", "w": [[33.5, -118.5]]}).encode()),
            timestamp=int(time.time() * 1_000_000), sent_at_ns=time.time_ns())
        seen = write_until_seen(lambda: command_writer.write(sample), ack,
                               lambda data: data["command_id"] if data["source"] == "Boat30" else "",
                               identifier)
        assert seen.get(identifier)[0]["accepted"], seen.get(identifier)[0].to_json()
        return applied(mesh, "Boat30", "TRANSIT")

    initial = route("before-stack-restart")
    assert initial["receive_profile"] == "ActiveMission"
    old_session = initial["session_id"]
    retained_router = nodes["Boat31"][0]
    retained_applications = list(applications["Boat31"])
    retained_control = nodes["Control_20"][0]
    stop("Boat30")
    start("Boat30", restart_router=True)
    recovered = applied(mesh, "Boat30", "IDLE")
    assert recovered["receive_profile"] == "Init"
    assert recovered["session_id"] != old_session
    assert all(process.is_alive() for process in retained_applications)
    assert retained_router is nodes["Boat31"][0] and retained_router.is_alive()
    assert retained_control is nodes["Control_20"][0] and retained_control.is_alive()
    # Let several autonomous producer ticks pass; no old command/state is replayed.
    time.sleep(2.2)
    assert applied(mesh, "Boat30", "IDLE")["receive_profile"] == "Init"
    assert route("after-stack-restart")["receive_profile"] == "ActiveMission"


@pytest.mark.parametrize("rs_mesh,filtered", [
    ({}, False),
    ({"periodic_input_profiles": {"PlatformStatus": "periodic_1s"}}, True),
], indirect=["rs_mesh"])
def test_periodic_input_filter_is_explicit_and_per_instance(rs_mesh, filtered):
    nodes, _ = rs_mesh
    source = nodes["Boat30"][1]
    control = nodes["Control_20"][1]
    writer = source.writer("PlatformStatus", "platform_init_status", writer_qos("lan_event"))
    reader = control.reader("PlatformStatus", "platform_init_status", reader_qos("lan_status"))
    other_writer = source.writer("PlatformDebugStatus", "platform_debug_status", writer_qos("lan_event"))
    other_reader = control.reader("PlatformDebugStatus", "platform_debug_status", reader_qos("lan_status"))
    deadline = time.monotonic() + 10
    while writer.publication_matched_status.current_count < 1 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert writer.publication_matched_status.current_count > 0
    # Ensure the complete native WAN/LAN path is ready before timing a subsecond burst.
    warmup = source.sample("platform_init_status", source="warmup", audit_sequence=999)
    assert write_until_seen(lambda: writer.write(warmup), reader,
                            lambda data: data["audit_sequence"], 999).get(999)
    time.sleep(1.1)
    time.sleep(0.5)
    reader.take()
    counts = {"first": set(), "second": set()}
    other = set()
    start = time.monotonic()
    sequence = 0
    while time.monotonic() - start < 0.8:
        sequence += 1
        for identity in counts:
            sample = source.sample("platform_init_status", source=identity, audit_sequence=sequence)
            writer.write(sample)
        other_writer.write(source.sample(
            "platform_debug_status", source="debug", audit_sequence=sequence))
        time.sleep(0.04)
        for sample in reader.take():
            if sample.info.valid:
                counts[sample.data["source"]].add(sample.data["audit_sequence"])
        for sample in other_reader.take():
            if sample.info.valid:
                other.add(sample.data["audit_sequence"])
    assert len(other) >= 5, "An input filter must not throttle another report topic"
    if filtered:
        assert all(len(sequences) == 1 for sequences in counts.values()), counts
    else:
        assert all(len(sequences) >= 5 for sequences in counts.values()), counts


def test_service_health_deadline_and_status_recovery(rs_mesh):
    nodes, observer = rs_mesh
    process, _ = nodes["Boat30"]
    control = nodes["Control_20"][1]
    reader = control.reader("ActRouterMeshStatus", "RouterMeshStatus", reader_qos("router_mesh_status"))

    def presence(data, state):
        return any(peer["health"]["router"] == "Boat30/rs"
                   and str(peer["presence"]) == state for peer in data["peers"])

    wait_sample(reader, lambda data: presence(data, "PRESENCE_ALIVE"))
    frames = observer.reader("Periodic", "WanStatus", reader_qos("wan_union_status"))
    navigation = wait_sample(frames, lambda data: dds.DynamicData(data)
                             if data["source_id"] == "Boat30"
                             and data["original_topic"] == "RouterHealth" else None)
    navigation["original_topic"] = "PlatformStatus"
    navigation["instance_id"] = "Boat30"
    navigation["audience.control"] = True
    navigation["audience.global"] = True
    navigation["payload.navigation"] = observer.sample(
        "platform_init_status", source="Boat30", audit_sequence=97531)
    writer = observer.writer("Periodic", "WanStatus", writer_qos("wan_union_status"))
    navigation_reader = control.reader("PlatformStatus", "platform_init_status", reader_qos("lan_status"))
    assert write_until_seen(lambda: writer.write(navigation), navigation_reader,
                            lambda data: data["audit_sequence"], 97531).get(97531)
    children = Path(f"/proc/{process._popen.pid}/task/{process._popen.pid}/children").read_text().split()
    assert len(children) == 1, children
    service_pid = int(children[0])
    os.kill(service_pid, signal.SIGSTOP)
    try:
        wait_sample(reader, lambda data: presence(data, "PRESENCE_STALE"), timeout=6)
        reader.take()
        writer.unregister_instance(writer.register_instance(navigation))
        observed = 0
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            for sample in reader.take():
                if sample.info.valid:
                    assert not presence(sample.data, "PRESENCE_ALIVE")
                    assert not presence(sample.data, "PRESENCE_DEAD")
                    if presence(sample.data, "PRESENCE_STALE"):
                        observed += 1
            time.sleep(0.03)
        assert observed > 0
    finally:
        os.kill(service_pid, signal.SIGCONT)
    wait_sample(reader, lambda data: presence(data, "PRESENCE_ALIVE"), timeout=8)
    assert all(process.is_alive() for process, _ in nodes.values())
