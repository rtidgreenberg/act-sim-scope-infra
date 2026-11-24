"""Native, isolated acceptance checks for the parallel Routing Service processor.

Run explicitly with ACT_RS_BUILD_DIR pointing to the variant's build directory.
The existing controller/health tests remain the full migration gate.
"""

from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import pytest
import rti.connextdds as dds
import yaml

from conftest import REPO_ROOT, RouterProcess, render_config, set_wan_qos_env
from test_two_topic_controller import CASES
from util.dds_probe import Probe, reader_qos, writer_qos, write_until_seen


@pytest.fixture(scope="module")
def rs_build():
    path = os.environ.get("ACT_RS_BUILD_DIR")
    if not path:
        pytest.skip("set ACT_RS_BUILD_DIR to exercise the parallel Routing Service variant")
    build = Path(path)
    assert (build / "libact_gateway_processor.so").is_file(), build
    assert (build / "act_rs_config").is_file(), build
    return build


@pytest.fixture
def rs_contract(request):
    return getattr(request.module, "RS_WAN_CONTRACT", "two_topic_union")


def configure_contract(config, contract):
    config["router"]["wan_contract"] = contract
    three_topic = contract == "three_topic_union"
    config["qos_profiles"]["wan_participant"] = (
        "ACT_QOS_LIB::wan_three_topic_participant" if three_topic
        else "ACT_QOS_LIB::wan_two_topic_participant")
    config["qos_profiles"]["wan_event"] = (
        "ACT_QOS_LIB::wan_three_topic_event" if three_topic else "ACT_QOS_LIB::wan_event")
    config["qos_profiles"]["wan_contacts"] = "ACT_QOS_LIB::wan_union_contacts"
    config["qos_profiles"]["wan_periodic"] = "ACT_QOS_LIB::wan_union_periodic"


@pytest.fixture
def rs_mesh(rs_build, rs_contract, unique_domains, extra_domains, e2e_tmp_dir,
            monkeypatch, request):
    set_wan_qos_env()
    for index in range(1, 12):
        monkeypatch.setenv("WAN_PEER" + (str(index) if index > 1 else ""),
                           "[0-15]@builtin.udpv4://127.0.0.1")
    with ExitStack() as cleanup:
        observer = Probe(unique_domains["wan"], participant_profile=(
            "wan_three_topic_participant" if rs_contract == "three_topic_union"
            else "wan_two_topic_participant"),
                         participant_partition=["SHARED"])
        cleanup.callback(observer.close)
        # Start builtin readers before the gateways so discovery evidence is retained.
        publication_reader = observer.participant.publication_reader
        subscription_reader = observer.participant.subscription_reader
        nodes = {}
        for node, role, domains, participant in (
            ("Control_20", "control", unique_domains, "control_lan"),
            ("Boat30", "platform", unique_domains, "platform_lan"),
            ("Boat31", "platform", dict(unique_domains, platform_lan=extra_domains["platform_lan"]),
             "platform_lan"),
        ):
            work = e2e_tmp_dir / rs_contract / node
            work.mkdir(parents=True)
            path = render_config("control-platform.yaml", domains, work)
            config = yaml.safe_load(path.read_text())
            configure_contract(config, rs_contract)
            config["router"]["admin_participant"] = participant
            config["routing_service"] = dict(getattr(request, "param", {}))
            config["routing_service"].setdefault(
                "receive_policy", getattr(request.module, "RS_RECEIVE_POLICY", "static"))
            config["qos_profiles"].update(getattr(request.module, "RS_QOS_PROFILES", {}))
            enabled_topics = {"Mission", "Team", "ActPlatformStatusMode",
                              "PlatformCommandAck", "ContactReport"}
            if getattr(request.module, "RS_MONITORING", False):
                enabled_topics.add("ActRouterDiagnostics")
            for route in config["routes"]:
                route["enabled"] = True
                if route.get("topics") and all(
                        topic["name"] not in enabled_topics for topic in route["topics"]):
                    route["enabled"] = False
            path.write_text(yaml.safe_dump(config))
            log_path = work / "routing_service.log"
            with log_path.open("w") as log:
                process = RouterProcess(subprocess.Popen(
                    [sys.executable, str(REPO_ROOT / "router/routing_service/run.py"),
                     "--config", str(path), "--role", role, "--node-name", node,
                     "--name", "rs", "--build-dir", str(rs_build),
                     *([] if getattr(request.module, "RS_MONITORING", False)
                       else ["--data-plane-only"]),
                     *(["--team", "TEAM_A"] if role == "platform" else [])],
                    cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT), log_path)
            cleanup.callback(process.stop)
            probe = Probe(domains[participant])
            cleanup.callback(probe.close)
            nodes[node] = (process, probe)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            assert all(process.is_alive() for process, _ in nodes.values()), [
                process.log_path.read_text() for process, _ in nodes.values()]
            if all("rs.gateway.started" in process.log_path.read_text()
                   for process, _ in nodes.values()):
                break
            time.sleep(0.1)
        assert all("rs.gateway.started" in process.log_path.read_text()
                   for process, _ in nodes.values()), [p.log_path.read_text() for p, _ in nodes.values()]
        yield nodes, observer


def test_rs_all_existing_application_cases_and_shared_endpoint_counts(rs_mesh, rs_contract):
    nodes, observer = rs_mesh
    control = nodes["Control_20"][1]
    boat = nodes["Boat30"][1]
    other = nodes["Boat31"][1]
    for topic, type_name, key, role in CASES:
        source = control if role == "control" else boat
        destination = other if topic == "PlatformData" else boat if role == "control" else control
        sample = source.sample(type_name, **{key: "Boat30" if key == "platform_node" else topic})
        if key == "source" or topic == "ContactReport":
            sample["run_id"] = "rs-acceptance"
            sample["audit_sequence"] = 42
            sample["sent_at_ns"] = 123456789
        if topic == "ContactReport":
            sample["source"] = "Boat30"
            sample["source_node"] = "Boat30"
        if topic == "Mission":
            sample["destination"] = "Boat30"
            sample["payload"] = list(range(256)) * 32
        writer = source.writer(topic, type_name, writer_qos("lan_event"))
        reader = destination.reader(topic, type_name)
        seen = write_until_seen(lambda: writer.write(sample), reader, lambda data: data[key],
                                sample[key],
                                check_alive=lambda: all(p.is_alive() for p, _ in nodes.values()))
        assert seen.get(sample[key]), (topic, [p.log_path.read_text() for p, _ in nodes.values()])
        assert seen[sample[key]][0] == sample, topic

    publications = observer.discovered_publications()
    counts = {}
    for publication in publications:
        counts.setdefault(publication["participant_key"], []).append(publication["topic"])
    expected = ["Event", "Periodic", "State"] if rs_contract == "three_topic_union" else ["Event", "Status"]
    gateways = [topics for topics in counts.values() if "Event" in topics]
    assert len(gateways) == 3, counts
    assert all(sorted(topics) == expected for topics in gateways), counts
    subscriptions = {}
    for data, info in observer.participant.subscription_reader.read():
        if info.valid:
            subscriptions.setdefault(str(data.participant_key.value), []).append(data.topic_name)
    gateways = [topics for topics in subscriptions.values() if "Event" in topics]
    assert len(gateways) == 3, subscriptions
    assert all(sorted(topics) == expected for topics in gateways), subscriptions


@pytest.mark.parametrize("operation", ["dispose", "unregister"])
@pytest.mark.parametrize("topic,type_name,key", [
    ("ContactReport", "contact_report", "contact_id"),
    ("PlatformStatus", "platform_init_status", "source"),
    ("PlatformCommandAck", "control_command_ack", "source"),
])
def test_rs_native_lifecycle(rs_mesh, operation, topic, type_name, key):
    nodes, _ = rs_mesh
    source, destination = nodes["Boat30"][1], nodes["Control_20"][1]
    writer = source.writer(topic, type_name, writer_qos("lan_event"))
    reader = destination.reader(topic, type_name, reader_qos("lan_event"))
    sample = source.sample(type_name, **{key: "lifecycle", "run_id": "rs-lifecycle"})
    sample["audit_sequence"] = 99
    if topic == "ContactReport":
        sample["source"] = "Boat30"
    seen = write_until_seen(lambda: writer.write(sample), reader,
                            lambda data: data[key], sample[key],
                            check_alive=lambda: all(p.is_alive() for p, _ in nodes.values()))
    assert seen.get(sample[key])
    handle = writer.register_instance(sample)
    if operation == "dispose":
        writer.dispose_instance(handle)
        expected = dds.InstanceState.NOT_ALIVE_DISPOSED
    else:
        assert not writer.qos.writer_data_lifecycle.autodispose_unregistered_instances
        writer.unregister_instance(handle)
        expected = dds.InstanceState.NOT_ALIVE_NO_WRITERS
    deadline = time.monotonic() + 10
    states = []
    while time.monotonic() < deadline:
        for data, info in reader.take():
            states.append(info.state.instance_state)
            if info.state.instance_state == expected:
                return
        time.sleep(0.05)
    pytest.fail(f"{topic}/{operation}: {states}; {[p.log_path.read_text() for p, _ in nodes.values()]}")


def test_rs_addressed_filtering_and_malformed_frame_recovery(rs_mesh):
    nodes, observer = rs_mesh
    control, boat, other = (nodes[node][1] for node in ("Control_20", "Boat30", "Boat31"))
    writer = control.writer("Mission", "control_command", writer_qos("lan_event"))
    intended = boat.reader("Mission", "control_command")
    forbidden = other.reader("Mission", "control_command")
    sample = control.sample("control_command", source="control", destination="Boat30",
                            audit_sequence=123, command_id="addressed")
    seen = write_until_seen(lambda: writer.write(sample), intended,
                            lambda data: data["command_id"], "addressed",
                            check_alive=lambda: all(p.is_alive() for p, _ in nodes.values()))
    assert seen.get("addressed")
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        assert not any(info.valid for _, info in forbidden.take())
        time.sleep(0.05)

    # Use the actual production-packed frame, then invalidate its schema version.
    frames = observer.reader("Event", "WanEvent", reader_qos("wan_event"))
    seen = write_until_seen(lambda: writer.write(sample), frames,
                            lambda data: data["instance_id"], "control",
                            check_alive=lambda: all(p.is_alive() for p, _ in nodes.values()))
    frame = seen["control"][0]
    injected = observer.writer("Event", "WanEvent", writer_qos("wan_event"))
    deadline = time.monotonic() + 10
    while injected.publication_matched_status.current_count < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert injected.publication_matched_status.current_count >= 2
    intended.take()
    frame["source_id"] = "Injected"
    frame["schema_version"] = 2
    injected.write(frame)
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        assert not any(info.valid for _, info in intended.take())
        time.sleep(0.05)
    frame["schema_version"] = 3
    seen = write_until_seen(lambda: injected.write(frame), intended,
                            lambda data: data["command_id"], "addressed",
                            check_alive=lambda: all(p.is_alive() for p, _ in nodes.values()))
    assert seen.get("addressed")
    assert "rs.sample.error" in nodes["Boat30"][0].log_path.read_text()


@pytest.fixture
def priority_config(rs_build, rs_contract, e2e_tmp_dir):
    # These configuration-only checks create no DDS entities or domain reservations.
    domains = {"control_lan": 101, "wan": 102, "platform_lan": 103}
    path = render_config("control-platform.yaml", domains, e2e_tmp_dir)
    config = yaml.safe_load(path.read_text())
    configure_contract(config, rs_contract)
    config["router"]["admin_participant"] = "platform_lan"
    path.write_text(yaml.safe_dump(config))

    def normalized(settings=None):
        candidate = dict(config)
        if settings is not None:
            candidate["routing_service"] = settings
        path.write_text(yaml.safe_dump(candidate))
        return subprocess.run(
            [str(rs_build / "act_rs_config"), str(path), "platform", "Boat30", "rs", "TEAM_A"],
            cwd=REPO_ROOT, text=True, capture_output=True)

    return normalized


def test_rs_priority_defaults_and_overrides(priority_config, rs_contract):
    result = priority_config()
    assert result.returncode == 0, result.stderr
    model = yaml.safe_load(result.stdout)
    assert model["async_bandwidth_bps"] == 2000000
    three_topic = rs_contract == "three_topic_union"
    assert model["publication_priorities"]["Mission"] == (600 if three_topic else 400)
    assert model["publication_priorities"]["RouterHealth"] == (500 if three_topic else 300)
    assert model["publication_priorities"]["ContactReport"] == 200
    assert model["publication_priorities"]["PlatformStatus"] == 100
    qos = dds.QosProvider(str(REPO_ROOT / "harness_v2/qos/act_qos_profiles.xml"))
    assert all(qos.datareader_qos_from_profile(port["profile"])
               .time_based_filter.minimum_separation == dds.Duration.zero
               for port in model["inputs"] if port["type"].startswith("platform_"))
    result = priority_config({"topic_priorities": {"ContactReport": "high",
                                                  "RouterHealth": "normal"}})
    assert result.returncode == 0, result.stderr
    priorities = yaml.safe_load(result.stdout)["publication_priorities"]
    assert priorities["ContactReport"] == 400
    assert priorities["RouterHealth"] == 100


@pytest.mark.parametrize("settings,error", [
    ({"topic_priorities": {"ContactReport": "urgent"}}, "must be high or normal"),
    ({"topic_priorities": {"UnknownTopic": "high"}}, "UnknownTopic"),
    ({"topic_priorities": ["ContactReport"]}, "must be a mapping"),
    ({"async_bandwidth_bps": 0}, "async_bandwidth_bps"),
    ({"unknown_setting": True}, "unknown routing_service setting"),
])
def test_rs_rejects_invalid_priority_configuration(priority_config, settings, error):
    result = priority_config(settings)
    assert result.returncode != 0
    assert error in result.stderr


def test_rs_four_classes_overtake_pending_lower_classes(priority_config, unique_domains,
                                                       e2e_tmp_dir):
    result = priority_config()
    assert result.returncode == 0, result.stderr
    priorities = yaml.safe_load(result.stdout)["publication_priorities"]
    root = ET.parse(REPO_ROOT / "harness_v2/qos/act_qos_profiles.xml").getroot()
    fixture = ET.parse(REPO_ROOT / "router/routing_service/fixtures/wan_priority.xml").getroot()
    root.extend(fixture.findall("qos_library"))
    path = e2e_tmp_dir / "priorities.xml"
    ET.ElementTree(root).write(path)
    provider = dds.QosProvider(str(path))
    types = dds.QosProvider(str(REPO_ROOT / "harness_v2/datamodel/gen/ActTypes.xml"))
    with ExitStack() as cleanup:
        participant = dds.DomainParticipant(
            unique_domains["wan"],
            provider.participant_qos_from_profile("ACT_RS_PRIORITY_TEST::on_demand"))
        cleanup.callback(participant.close)
        publisher = dds.Publisher(participant)
        subscriber = dds.Subscriber(participant)
        controller = participant.find_flow_controller("dds.flow_controller.token_bucket.act_rs")
        cleanup.callback(controller.close)
        endpoints = {}
        for family in ("Event", "Status"):
            topic = dds.DynamicData.Topic(participant, family, types.type("Wan" + family))
            reader = dds.DynamicData.DataReader(
                subscriber, topic, provider.datareader_qos_from_profile(
                    "ACT_QOS_LIB::wan_event" if family == "Event"
                    else "ACT_QOS_LIB::wan_union_periodic"))
            writer = dds.DynamicData.DataWriter(
                publisher, topic, provider.datawriter_qos_from_profile(
                    "ACT_QOS_LIB::rs_wan_event" if family == "Event"
                    else "ACT_QOS_LIB::rs_wan_periodic"))
            cleanup.callback(reader.close)
            cleanup.callback(writer.close)
            endpoints[family] = (writer, reader)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not all(
                writer.publication_matched_status.current_count == 1
                for writer, _ in endpoints.values()):
            time.sleep(0.01)
        assert all(w.publication_matched_status.current_count == 1 for w, _ in endpoints.values())
        # Queue low classes first, with repeated keys kept separate to avoid cache replacement.
        queued = [
            ("Status", "PlatformStatus", "navigation", "source"),
            ("Event", "ContactReport", "contact_report", "contact_id"),
            ("Status", "RouterHealth", "health", "router"),
            ("Event", "Mission", "command", "source"),
        ]
        for family, original, branch, key in queued:
            for sequence in (1, 2):
                frame = dds.DynamicData(types.type("Wan" + family))
                frame["source_id"] = "Boat30"
                frame["original_topic"] = original
                frame["instance_id"] = f"priority-{sequence}"
                frame["schema_version"] = 3
                frame["audit_sequence"] = sequence
                frame["run_id"] = "priority-order"
                with frame.loan_value("payload") as payload:
                    with payload.data.loan_value(branch) as value:
                        value.data[key] = f"priority-{sequence}"
                        if original == "Mission":
                            value.data["destination"] = "Control_20"
                params = dds.WriteParams()
                params.priority = priorities[original]
                endpoints[family][0].write(frame, params)
        assert all(not reader.take() for _, reader in endpoints.values()), \
            "On-demand flow controller leaked queued samples"
        received = []
        deadline = time.monotonic() + 10
        while len(received) < 8 and time.monotonic() < deadline:
            controller.trigger_flow()
            time.sleep(0.03)
            for _, reader in endpoints.values():
                received.extend(
                    (info.reception_timestamp.sec * 1000000000 + info.reception_timestamp.nanosec,
                     str(data["original_topic"]), int(data["audit_sequence"]))
                    for data, info in reader.take() if info.valid)
        assert [(topic, sequence) for _, topic, sequence in sorted(received)] == [
            (topic, sequence) for topic in
            ("Mission", "RouterHealth", "ContactReport", "PlatformStatus")
            for sequence in (1, 2)]


@pytest.mark.parametrize("rs_mesh,high", [
    ({"topic_priorities": {"PlatformCommandAck": "high"}}, True),
    ({"topic_priorities": {"PlatformCommandAck": "normal"}}, False),
], indirect=["rs_mesh"], ids=["high-overtakes", "normal-fifo"])
def test_rs_actual_processor_ack_priority_under_contact_backlog(rs_mesh, high):
    nodes, observer = rs_mesh
    source = nodes["Boat30"][1]
    reader = observer.reader("Event", "WanEvent", reader_qos("wan_event"))
    contacts = source.writer("ContactReport", "contact_report", writer_qos("lan_event"))
    acks = source.writer("PlatformCommandAck", "control_command_ack", writer_qos("lan_event"))
    deadline = time.monotonic() + 10
    while reader.subscription_matched_status.current_count < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert reader.subscription_matched_status.current_count == 3
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not all(
            writer.publication_matched_status.current_count > 0 for writer in (contacts, acks)):
        time.sleep(0.01)
    assert all(w.publication_matched_status.current_count > 0 for w in (contacts, acks))
    sample = source.sample("contact_report", source="Boat30", run_id="rs-priority-backlog")
    total = 200
    for sequence in range(total):
        sample["contact_id"] = f"contact-{sequence}"
        sample["audit_sequence"] = sequence
        contacts.write(sample)
    received = []

    def collect():
        received.extend((str(data["original_topic"]), int(data["audit_sequence"]))
                        for data, info in reader.take()
                        if info.valid and data["run_id"] == "rs-priority-backlog")

    deadline = time.monotonic() + 10
    while len(received) < 20 and time.monotonic() < deadline:
        collect()
        time.sleep(0.005)
    assert 20 <= len(received) < total, "The test needs an actual pending contact backlog"
    ack = source.sample("control_command_ack", source="Boat30", run_id="rs-priority-backlog",
                        audit_sequence=999, command_id="urgent")
    acks.write(ack)
    deadline = time.monotonic() + 15
    while len(received) < total + 1 and time.monotonic() < deadline:
        collect()
        time.sleep(0.005)
    assert len(received) == total + 1, received
    assert [sequence for topic, sequence in received if topic == "ContactReport"] == list(range(total))
    position = received.index(("PlatformCommandAck", 999))
    print(f"ACK priority={'high' if high else 'normal'} receive_position={position} "
          f"contact_backlog={total}; all contact sequences received in order")
    if high:
        assert position < total - 20, position
    else:
        assert position == total, position


@pytest.mark.parametrize("rs_mesh", [
    {"periodic_input_profiles": {"PlatformStatus": "lan_status_1hz"}},
], indirect=True)
def test_rs_periodic_lan_input_is_one_hz_per_instance(rs_mesh, rs_contract):
    nodes, observer = rs_mesh
    source = nodes["Boat30"][1]
    writer = source.writer("PlatformStatus", "platform_init_status", writer_qos("lan_event"))
    reader = observer.reader("Periodic" if rs_contract == "three_topic_union" else "Status",
                             "WanStatus", reader_qos("wan_union_periodic"))
    sample = source.sample("platform_init_status", source="rate-test", run_id="rs-rate")
    deadline = time.monotonic() + 10
    while writer.publication_matched_status.current_count == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert writer.publication_matched_status.current_count > 0
    sent = 0
    received = []
    start = time.monotonic()
    while time.monotonic() - start < 3.3:
        sent += 1
        sample["audit_sequence"] = sent
        sample["sent_at_ns"] = time.time_ns()
        writer.write(sample)
        received.extend(data["sent_at_ns"] for data, info in reader.take()
                        if info.valid and data["run_id"] == "rs-rate")
        time.sleep(0.01)
    deadline = time.monotonic() + 0.1
    while time.monotonic() < deadline:
        received.extend(data["sent_at_ns"] for data, info in reader.take()
                        if info.valid and data["run_id"] == "rs-rate")
        time.sleep(0.005)
    assert sent >= 100
    assert 3 <= len(received) <= 4, received
    assert all(later - earlier >= 970000000 for earlier, later in zip(received, received[1:]))
