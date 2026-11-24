"""Production independent bindings; run only in an owned private network namespace."""

from contextlib import ExitStack
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import pytest
import rti.connextdds as dds
import yaml

from conftest import REPO_ROOT, RouterProcess, set_wan_qos_env
from test_routing_service import rs_build


SERVICE = REPO_ROOT / "router/routing_service"
sys.path.insert(0, str(SERVICE))
from run import write_bundle


CASES = (
    ("command", "Mission", "control_command", "command", "source", "event_high", "Event"),
    ("contact", "ContactReport", "contact_report", "contact_report", "contact_id", "state_normal", "State"),
    ("power", "PlatformPowerStatus", "platform_power_status", "power", "source", "periodic_normal", "Periodic"),
    ("late", "PlatformData", "platform_data", "data", "source", "periodic_normal", "Periodic"),
)
FAMILIES = ("Event", "State", "Periodic")


class IndependentGateway:
    def __init__(self, build, domains, work, cleanup, auto=True, finite_deadline=False,
                 event_history=False):
        self.work = work
        self.cleanup = cleanup
        self.auto = auto
        self.qos = dds.QosProvider(str(SERVICE / "fixtures/auto_qos.xml"))
        self.wan_qos = dds.QosProvider(str(REPO_ROOT / "harness_v2/qos/act_qos_profiles.xml"))
        self.types = dds.QosProvider(str(REPO_ROOT / "harness_v2/datamodel/gen/ActTypes.xml"))
        self.lan = dds.DomainParticipant(
            domains["platform_lan"], self.qos.participant_qos_from_profile("AUTOQOS_TEST::participant"))
        cleanup.callback(self.lan.close)
        self.wan = dds.DomainParticipant(
            domains["wan"], self.wan_qos.participant_qos_from_profile(
                "ACT_QOS_LIB::wan_three_topic_participant"))
        cleanup.callback(self.wan.close)
        self.lan_pubs, self.lan_subs = self.lan.publication_reader, self.lan.subscription_reader
        self.wan_pubs, self.wan_subs = self.wan.publication_reader, self.wan.subscription_reader
        self.wan_readers, self.wan_writers = {}, {}
        self.lan_topics = {}
        self.process = None
        for family in FAMILIES:
            topic = dds.DynamicData.Topic(
                self.wan, family, self.types.type("WanStatus" if family == "Periodic" else "WanEvent"))
            profile = "ACT_QOS_LIB::" + {
                "Event": "wan_three_topic_event", "State": "wan_union_contacts",
                "Periodic": "wan_union_periodic"}[family]
            reader = dds.DynamicData.DataReader(
                dds.Subscriber(self.wan), topic, self.wan_qos.datareader_qos_from_profile(profile))
            writer = dds.DynamicData.DataWriter(
                dds.Publisher(self.wan), topic, self.wan_qos.datawriter_qos_from_profile(profile))
            cleanup.callback(reader.close)
            cleanup.callback(writer.close)
            self.wan_readers[family], self.wan_writers[family] = reader, writer
        properties = {
            "act.version": "1", "act.node": "Gateway", "act.role": "platform", "act.router": "rs",
            "act.types_uri": str(REPO_ROOT / "harness_v2/datamodel/gen/ActTypes.xml"),
            "act.wan_contract": "three_topic_union", "act.receive_policy": "static",
            "act.monitoring": "false", "act.admin_participant": "lan", "act.lan_participant": "lan",
            "act.wan_participant": "wan", "act.lan_domain": str(domains["platform_lan"]),
            "act.wan_domain": str(domains["wan"]),
        }
        for identifier, topic, type_name, branch, key, channel, _ in CASES:
            properties.update({
                f"act.binding.{identifier}.{field}": value
                for field, value in {
                    "topic_name": topic, "type": type_name, "branch": branch, "instance_id": key,
                    "channel": channel, "tx.port": "lan_in_" + identifier, "tx.recipients": "global",
                    "rx.port": "lan_out_" + identifier, "rx.recipients": "global",
                }.items()
            })
        path = work / "properties.yaml"
        path.write_text(yaml.safe_dump(properties))
        generated = subprocess.run(
            [str(build / "act_rs_config"), "--properties", str(path)],
            text=True, capture_output=True, check=True, cwd=REPO_ROOT)
        model = yaml.safe_load(generated.stdout)
        model["qos_libraries"] = [
            str(REPO_ROOT / "harness_v2/qos/act_qos_profiles.xml"),
            str(SERVICE / "fixtures/auto_qos.xml"),
        ]
        for participant in model["participants"]:
            participant["profile"] = (
                "AUTOQOS_TEST::participant" if participant["name"] == "lan"
                else "ACT_QOS_LIB::wan_three_topic_participant")
            participant["partitions"] = []
        for port in model["inputs"] + model["outputs"]:
            port["profile"] = ""
        for family in model["wan"]:
            family["profile"] = "ACT_QOS_LIB::" + {
                "Event": "wan_three_topic_event", "State": "wan_union_contacts",
                "Periodic": "wan_union_periodic"}[family["name"]]
        self.path = write_bundle(model, build / "libact_gateway_processor.so", True, work / "service.xml")
        root = ET.parse(self.path)
        for identifier, *_ in CASES:
            for kind, prefix in (("input", "lan_in_"), ("output", "lan_out_")):
                port = root.find(f".//topic_route/{kind}[@name='{prefix}{identifier}']")
                assert port is not None, (kind, identifier)
                assert port.findtext("creation_mode") == "ON_DOMAIN_MATCH"
                assert port.findtext("auto_configure_qos") == "true"
                port.find("auto_configure_qos").text = str(auto).lower()
                qos_tag = "datareader_qos" if kind == "input" else "datawriter_qos"
                endpoint_qos = port.find(qos_tag)
                if endpoint_qos is not None:
                    port.remove(endpoint_qos)
                if finite_deadline and kind == "output":
                    ET.SubElement(port, qos_tag, base_name="AUTOQOS_TEST::router_deadline")
                elif event_history and identifier == "command" and kind == "output":
                    ET.SubElement(port, qos_tag, base_name="ACT_QOS_LIB::lan_event")
        root.write(self.path)

    def start(self):
        log_path = self.work / "routing_service.log"
        log = self.cleanup.enter_context(log_path.open("w"))
        self.process = RouterProcess(subprocess.Popen(
            [sys.executable, str(SERVICE / "run.py"), "--xml", str(self.path)],
            cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT), log_path)
        self.cleanup.callback(self.process.stop)
        self.until(lambda: "rs.gateway.started" in log_path.read_text())

    def until(self, condition, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is not None:
                assert self.process.is_alive(), self.process.log_path.read_text()
            value = condition()
            if value:
                return value
            time.sleep(0.05)
        pytest.fail(self.process.log_path.read_text() if self.process is not None else "discovery timeout")

    def application(self, identifier, writer, profile="application_writer_reliable"):
        _, topic, type_name, *_ = next(case for case in CASES if case[0] == identifier)
        if topic not in self.lan_topics:
            self.lan_topics[topic] = dds.DynamicData.Topic(self.lan, topic, self.types.type(type_name))
        topic = self.lan_topics[topic]
        provider = self.wan_qos if profile.startswith("ACT_QOS_LIB::") else self.qos
        profile = profile if "::" in profile else "AUTOQOS_TEST::" + profile
        if writer:
            endpoint = dds.DynamicData.DataWriter(
                dds.Publisher(self.lan), topic,
                provider.datawriter_qos_from_profile(profile))
        else:
            endpoint = dds.DynamicData.DataReader(
                dds.Subscriber(self.lan), topic,
                provider.datareader_qos_from_profile(profile))
        self.cleanup.callback(endpoint.close)
        return endpoint

    def lan_endpoints(self, identifier):
        topic = next(case[1] for case in CASES if case[0] == identifier)
        return tuple(
            [sample.data for sample in reader.read()
             if sample.info.valid and sample.data.topic_name == topic]
            for reader in (self.lan_subs, self.lan_pubs)
        )

    def inventory(self):
        own = {str(endpoint.instance_handle)
               for endpoint in (*self.wan_readers.values(), *self.wan_writers.values())}
        result = []
        for reader in (self.wan_pubs, self.wan_subs):
            endpoints = [(sample.data.topic_name, str(sample.info.instance_handle))
                         for sample in reader.read()
                         if sample.info.valid and sample.data.topic_name in FAMILIES
                         and str(sample.info.instance_handle) not in own]
            if sorted(topic for topic, _ in endpoints) != sorted(FAMILIES):
                return None
            result.append(tuple(sorted(endpoints)))
        return tuple(result)

    def sample(self, identifier, source, sequence=1):
        _, _, type_name, _, key, _, _ = next(case for case in CASES if case[0] == identifier)
        data = dds.DynamicData(self.types.type(type_name))
        data[key] = "contact-1" if identifier == "contact" else source
        data["source"] = source
        data["run_id"] = "independent-bindings"
        data["audit_sequence"] = sequence
        data["sent_at_ns"] = 1234567890123456 + sequence
        if identifier == "command":
            data["destination"] = "Gateway"
            data["payload"] = list(range(256)) * 32
        elif identifier == "power":
            data["battery_soc_pct"] = 73.5
            data["charging"] = True
        return data

    def frame(self, identifier, data):
        _, topic, _, branch, key, _, family = next(case for case in CASES if case[0] == identifier)
        frame = dds.DynamicData(self.types.type("WanStatus" if family == "Periodic" else "WanEvent"))
        frame["source_id"] = "Remote"
        frame["original_topic"] = topic
        frame["instance_id"] = data[key]
        frame["schema_version"] = 3
        frame["source_role"] = "control"
        frame["run_id"] = data["run_id"]
        frame["audit_sequence"] = data["audit_sequence"]
        frame["sent_at_ns"] = data["sent_at_ns"]
        with frame.loan_value("audience") as audience:
            audience.data["global"] = True
        with frame.loan_value("payload") as payload:
            payload.data[branch] = data
        return frame

    def outbound(self, identifier, writer, sample):
        branch, family = next((case[3], case[6]) for case in CASES if case[0] == identifier)

        def delivered():
            writer.write(sample)
            for data, info in self.wan_readers[family].take():
                if info.valid and data["source_id"] == "Gateway":
                    with data.loan_value("payload") as payload:
                        if payload.data[branch] == sample:
                            return True
            return False

        self.until(delivered)


@pytest.fixture
def independent_gateway(rs_build, unique_domains, e2e_tmp_dir, monkeypatch):
    assert os.environ.get("ACT_RS_AUTOQOS_ISOLATED") == "1", (
        "requires ACT_RS_AUTOQOS_ISOLATED=1 in an owned private network namespace")
    set_wan_qos_env()
    for index in range(1, 12):
        monkeypatch.setenv("WAN_PEER" + (str(index) if index > 1 else ""),
                           "[0-15]@builtin.udpv4://127.0.0.1")
    with ExitStack() as cleanup:
        def create(**kwargs):
            return IndependentGateway(rs_build, unique_domains, e2e_tmp_dir, cleanup, **kwargs)
        yield create


@pytest.mark.parametrize("auto", [False, True])
@pytest.mark.parametrize("timing", ["before", "after"])
def test_native_auto_qos_and_late_binding_keep_shared_wan_identities(independent_gateway, auto, timing):
    gateway = independent_gateway(auto=auto)
    if timing == "before":
        writer = gateway.application("command", True)
        reader = gateway.application("command", False)
    gateway.start()
    identities = gateway.until(gateway.inventory)
    if timing == "after":
        assert gateway.lan_endpoints("command") == ([], [])
        writer = gateway.application("command", True)
        reader = gateway.application("command", False)
    gateway.until(lambda: all(gateway.lan_endpoints("command")))
    inputs, outputs = gateway.lan_endpoints("command")
    assert len(inputs) == len(outputs) == 1
    assert inputs[0].reliability.kind == (
        dds.ReliabilityKind.RELIABLE if auto else dds.ReliabilityKind.BEST_EFFORT)
    assert inputs[0].durability.kind == (
        dds.DurabilityKind.TRANSIENT_LOCAL if auto else dds.DurabilityKind.VOLATILE)
    assert outputs[0].durability.kind == (
        dds.DurabilityKind.TRANSIENT_LOCAL if auto else dds.DurabilityKind.VOLATILE)
    assert gateway.lan_endpoints("contact") == ([], [])
    assert gateway.lan_endpoints("power") == ([], [])
    gateway.outbound("command", writer, gateway.sample("command", "Gateway", 10))
    incoming = gateway.sample("command", "Remote", 20)
    frame = gateway.frame("command", incoming)
    gateway.until(lambda: gateway.wan_writers["Event"].publication_matched_status.current_count > 0)
    matched = []

    def received():
        gateway.wan_writers["Event"].write(frame)
        matched.extend(data for data, info in reader.take() if info.valid and data == incoming)
        return bool(matched)

    if auto:
        gateway.until(received)
        time.sleep(0.2)
        assert not any(info.valid and data["source_id"] == "Gateway" and data["audit_sequence"] == 20
                       for data, info in gateway.wan_readers["Event"].take())
    else:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            assert not received()
            time.sleep(0.05)
    late = gateway.application("late", True)
    gateway.outbound("late", late, gateway.sample("late", "Gateway", 30))
    assert gateway.until(gateway.inventory) == identities
    assert gateway.lan_endpoints("contact") == ([], [])
    assert gateway.lan_endpoints("power") == ([], [])


def test_source_restart_preserves_shared_endpoints_and_unrelated_traffic(independent_gateway):
    gateway = independent_gateway()
    writer = gateway.application("command", True)
    gateway.start()
    identities = gateway.until(gateway.inventory)
    gateway.outbound("command", writer, gateway.sample("command", "Gateway", 50))
    writer.close()
    power = gateway.application("power", True)
    gateway.outbound("power", power, gateway.sample("power", "Gateway", 60))
    replacement = gateway.application("command", True)
    gateway.until(lambda: replacement.publication_matched_status.current_count > 0)
    sample = gateway.sample("command", "Gateway", 70)
    replacement.write(sample)
    seen = []

    def received():
        seen.extend(data for data, info in gateway.wan_readers["Event"].take()
                    if info.valid and data["source_id"] == "Gateway" and data["audit_sequence"] == 70)
        return bool(seen)

    gateway.until(received)
    time.sleep(0.2)
    received()
    assert len(seen) == 1
    with seen[0].loan_value("payload") as payload:
        assert payload.data["command"] == sample
    assert gateway.until(gateway.inventory) == identities


def test_required_events_wait_in_native_history_for_late_lan_consumer(independent_gateway):
    gateway = independent_gateway(event_history=True)
    gateway.start()
    identities = gateway.until(gateway.inventory)
    assert gateway.lan_endpoints("command") == ([], [])
    gateway.until(lambda: gateway.wan_writers["Event"].publication_matched_status.current_count > 0)
    expected = [gateway.sample("command", "Remote", sequence) for sequence in (100, 101, 102)]
    for sample in expected:
        gateway.wan_writers["Event"].write(gateway.frame("command", sample))
    # Unrelated application traffic must continue while the Event output is pending.
    power = gateway.application("power", True)
    gateway.outbound("power", power, gateway.sample("power", "Gateway", 500))
    assert gateway.lan_endpoints("command") == ([], [])
    reader = gateway.application("command", False, profile="ACT_QOS_LIB::lan_event")
    seen = []

    def received():
        seen.extend(data for data, info in reader.take() if info.valid and data["source"] == "Remote")
        return all(any(data == sample for data in seen) for sample in expected)

    gateway.until(received)
    time.sleep(0.2)
    received()
    assert sorted(data["audit_sequence"] for data in seen) == [100, 101, 102]
    assert gateway.until(gateway.inventory) == identities


def test_standalone_qualifier_defaults_support_independent_layout(rs_build):
    command = [
        sys.executable, str(REPO_ROOT / "router/routing_service/qualify_auto_qos.py"),
        "--build-dir", str(rs_build), "--profile", "defaults",
        "--direction", "tx", "--timing", "after", "--absent-input",
        "--application-profile", "application_writer_reliable",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90, cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    comparisons = [json.loads(line) for line in result.stdout.splitlines()]
    assert [case["auto"] for case in comparisons] == [False, True]
    assert all(case["creation_mode"] == "ON_DOMAIN_MATCH" and case["processor_started"]
               and case["matched"] and case["delivered"] for case in comparisons), comparisons
    rejected = subprocess.run(command + ["--creation-mode", "IMMEDIATE"],
                              capture_output=True, text=True, timeout=10, cwd=REPO_ROOT)
    assert rejected.returncode == 2, rejected.stdout + rejected.stderr
    assert "invalid choice" in rejected.stderr


def test_explicit_deadline_remains_effective_with_native_output_inference(independent_gateway):
    gateway = independent_gateway(finite_deadline=True)
    reader = gateway.application("command", False, profile="application")
    gateway.start()
    gateway.until(lambda: bool(gateway.lan_endpoints("command")[1]))
    publication = gateway.lan_endpoints("command")[1][0]
    assert publication.durability.kind == dds.DurabilityKind.TRANSIENT_LOCAL
    assert publication.deadline.period == dds.Duration(0, 100000000)
    sample = gateway.sample("command", "Remote", 900)
    frame = gateway.frame("command", sample)

    def received():
        gateway.wan_writers["Event"].write(frame)
        return any(info.valid and data == sample for data, info in reader.take())

    gateway.until(received)
