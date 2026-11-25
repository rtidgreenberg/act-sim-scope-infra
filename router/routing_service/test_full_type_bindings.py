"""Prepared non-ACT bindings; native traffic requires a private network namespace."""

from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET

import yaml

from run import preflight, write_bundle


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures/full_type_bindings.xml"
ACT_TYPES = ROOT / "harness_v2/datamodel/gen/ActTypes.xml"
CUSTOM_CASES = (
    ("command", "DeviceCommands", "DeviceCommand", "device_command", "request_id", "event_high"),
    ("status", "DeviceUpdates", "DeviceStatus", "device_status", "device_id", "periodic_normal"))
ACT_CASES = (
    ("command", "Mission", "control_command", "command", "source", "event_high"),
    ("status", "PlatformPowerStatus", "platform_power_status", "power", "source", "periodic_normal"))


def properties(node, domain, act=False):
    result = {
        "act.version": "1", "act.types_uri": str(ACT_TYPES if act else FIXTURE),
        "act.node": node, "act.role": "platform", "act.router": "rs",
        "act.wan_contract": "three_topic_union", "act.receive_policy": "static",
        "act.monitoring": "false", "act.admin_participant": "lan",
        "act.lan_participant": "lan", "act.lan_domain": str(domain),
        "act.wan_participant": "wan", "act.wan_domain": "102",
    }
    for identifier, topic, type_name, branch, key, channel in ACT_CASES if act else CUSTOM_CASES:
        prefix = f"act.binding.{identifier}."
        result.update({prefix + field: value for field, value in {
            "topic_name": topic, "type": type_name, "branch": branch, "instance_id": key,
            "channel": channel, "tx.port": "input_" + identifier, "tx.recipients": "global",
            "rx.port": "output_" + identifier, "rx.recipients": "global",
        }.items()})
    return result


@unittest.skipUnless(os.environ.get("ACT_RS_BUILD_DIR"), "requires a fresh native processor build")
class FullTypeConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.build = Path(os.environ["ACT_RS_BUILD_DIR"]).resolve()
        work = tempfile.TemporaryDirectory(dir=os.environ.get("ACT_RS_TEST_WORKDIR", "."))
        self.addCleanup(work.cleanup)
        self.work = Path(work.name).resolve()

    def model(self, node="DeviceA", domain=103, act=False, dotted=False,
              contract="three_topic_union"):
        path = self.work / (node + ".yaml")
        config = properties(node, domain, act)
        config["act.wan_contract"] = contract
        if dotted:
            config["act.binding.command.topic_name"] = "devices.commands"
            config["act.binding.status.topic_name"] = "devices.updates"
        path.write_text(yaml.safe_dump(config))
        generated = subprocess.run(
            [str(self.build / "act_rs_config"), "--properties", str(path)],
            text=True, capture_output=True, check=True, cwd=ROOT)
        model = yaml.safe_load(generated.stdout)
        model["qos_libraries"] = [str(ROOT / "harness_v2/qos/act_qos_profiles.xml")]
        for participant in model["participants"]:
            participant["profile"] = "ACT_QOS_LIB::" + (
                "router_journal_participant" if participant["name"] == "lan"
                else "wan_three_topic_participant")
            participant["partitions"] = [] if participant["name"] == "lan" else ["SHARED"]
        for port in model["inputs"] + model["outputs"]:
            port["profile"] = "ACT_QOS_LIB::lan_event"
        for family in model["wan"]:
            family["profile"] = (family["writer_profile"] if contract == "six_topic_union"
                                 else "ACT_QOS_LIB::" + {
                "Event": "wan_three_topic_event" if contract == "three_topic_union" else "wan_event",
                "State": "wan_union_contacts", "Status": "wan_union_periodic",
                "Periodic": "wan_union_periodic"}[family["name"]])
        return model

    def test_native_model_render_and_frozen_bundle(self):
        model = self.model()
        path = write_bundle(model, self.build / "libact_gateway_processor.so",
                            True, self.work / "gateway.xml")
        prepared = preflight(path)
        self.assertEqual(prepared["act.binding.command.type"], "DeviceCommand")
        self.assertEqual(prepared["act.binding.status.instance_id"], "device_id")
        self.assertEqual(Path(prepared["act.types_uri"]).read_bytes(), FIXTURE.read_bytes())
        self.assertEqual({family["name"] for family in model["wan"]}, {"Event", "State", "Periodic"})

    def test_native_dotted_topic_bundle_preserves_binding_ids(self):
        model = self.model(dotted=True)
        path = write_bundle(model, self.build / "libact_gateway_processor.so",
                            True, self.work / "dotted.xml")
        prepared = preflight(path)
        self.assertEqual(prepared["act.binding.command.topic_name"], "devices.commands")
        self.assertEqual(prepared["act.binding.status.topic_name"], "devices.updates")
        again = self.work / "again.yaml"
        again.write_text(yaml.safe_dump(prepared))
        generated = subprocess.run(
            [str(self.build / "act_rs_config"), "--properties", str(again)],
            text=True, capture_output=True, check=True, cwd=ROOT)
        self.assertEqual(yaml.safe_load(generated.stdout)["properties"], prepared)
        self.assertEqual({port["topic"] for port in model["inputs"]},
                         {"devices.commands", "devices.updates"})

    def test_native_registry_rejects_insufficient_capacities(self):
        cases = [
            (envelope, field, minimum - 1, field)
            for envelope in ("WanEvent", "WanStatus")
            for field, minimum in (("source_id", 255), ("original_topic", 255),
                                   ("instance_id", 512), ("source_role", 255), ("run_id", 255))
        ] + [
            ("WanAudience", "destination_ids", 15, "sequence"),
            ("WanAudience", "destination_ids", 254, "string"),
            ("DeviceCommand", "request_id", 513, "key"),
        ]
        for structure, field, bound, kind in cases:
            with self.subTest(structure=structure, field=field, bound=bound, kind=kind):
                root = ET.parse(FIXTURE)
                member = root.find(f"./types/struct[@name='{structure}']/member[@name='{field}']")
                member.set("sequenceMaxLength" if kind == "sequence" else "stringMaxLength",
                           str(bound))
                xml = self.work / "capacity.xml"
                root.write(xml)
                config = properties("DeviceGateway", 103)
                config["act.types_uri"] = str(xml)
                path = self.work / "capacity.yaml"
                path.write_text(yaml.safe_dump(config))
                result = subprocess.run(
                    [str(self.build / "act_rs_config"), "--properties", str(path)],
                    text=True, capture_output=True, cwd=ROOT)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("capacity", result.stderr)

    def test_native_registry_accepts_exact_capacity_key(self):
        root = ET.parse(FIXTURE)
        root.find("./types/struct[@name='DeviceCommand']/member[@name='request_id']").set(
            "stringMaxLength", "512")
        xml = self.work / "boundary.xml"
        root.write(xml)
        result = subprocess.run(
            [str(self.build / "test_full_type_bindings"), str(xml)],
            text=True, capture_output=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_registry_rejects_malformed_prepared_schemas(self):
        for name in ("missing_key", "integer_key", "extra_key", "missing_metadata",
                     "wrong_metadata", "duplicate_member_id", "duplicate_label", "multiple_labels",
                     "default_label", "negative_label"):
            with self.subTest(schema=name):
                root = ET.parse(FIXTURE).getroot()
                command = root.find("./types/struct[@name='DeviceCommand']")
                envelope = root.find("./types/struct[@name='WanEvent']")
                union = root.find("./types/union[@name='DeviceEvents']")
                if name == "missing_key":
                    command.find("member[@name='request_id']").attrib.pop("key")
                elif name == "integer_key":
                    field = command.find("member[@name='request_id']")
                    field.set("type", "int32")
                    field.attrib.pop("stringMaxLength")
                elif name == "extra_key":
                    command.find("member[@name='opcode']").set("key", "true")
                elif name == "missing_metadata":
                    envelope.remove(envelope.find("member[@name='sent_at_ns']"))
                elif name == "wrong_metadata":
                    envelope.find("member[@name='schema_version']").set("type", "uint64")
                elif name == "multiple_labels":
                    union.find("case").insert(1, ET.Element("caseDiscriminator", value="42"))
                elif name in ("default_label", "negative_label"):
                    union.find("case/caseDiscriminator").set(
                        "value", "default" if name == "default_label" else "-1")
                else:
                    case = ET.SubElement(union, "case")
                    ET.SubElement(case, "caseDiscriminator", value="41" if name == "duplicate_label" else "42")
                    ET.SubElement(case, "member", name="other", id="17" if name == "duplicate_member_id" else "18",
                                  type="nonBasic", nonBasicTypeName="DeviceCommand")
                xml = self.work / (name + ".xml")
                ET.ElementTree(root).write(xml)
                config = properties("DeviceA", 103)
                config["act.types_uri"] = str(xml)
                path = self.work / (name + ".yaml")
                path.write_text(yaml.safe_dump(config))
                result = subprocess.run(
                    [str(self.build / "act_rs_config"), "--properties", str(path)],
                    text=True, capture_output=True, cwd=ROOT)
                self.assertNotEqual(result.returncode, 0, name)
                self.assertTrue(result.stderr.strip(), name)

    @unittest.skipUnless(os.environ.get("ACT_RS_SCHEMA_ISOLATED") == "1",
                         "requires an owned private network namespace")
    def test_native_processor_command_and_nested_status_round_trip(self):
        self.native_round_trip()

    @unittest.skipUnless(os.environ.get("ACT_RS_SCHEMA_ISOLATED") == "1",
                         "requires an owned private network namespace")
    def test_native_processor_existing_act_command_and_status_round_trip(self):
        self.native_round_trip(act=True)

    @unittest.skipUnless(os.environ.get("ACT_RS_SCHEMA_ISOLATED") == "1",
                         "requires an owned private network namespace")
    def test_native_processor_dotted_topics_round_trip(self):
        self.native_round_trip(dotted=True)

    @unittest.skipUnless(os.environ.get("ACT_RS_SCHEMA_ISOLATED") == "1",
                         "requires an owned private network namespace")
    def test_native_six_topic_common_type_custom_round_trip(self):
        self.native_round_trip(contract="six_topic_union")

    @unittest.skipUnless(os.environ.get("ACT_RS_SCHEMA_ISOLATED") == "1",
                         "requires an owned private network namespace")
    def test_native_six_topic_common_type_act_round_trip(self):
        self.native_round_trip(act=True, contract="six_topic_union")

    @unittest.skipUnless(os.environ.get("ACT_RS_SCHEMA_ISOLATED") == "1",
                         "requires an owned private network namespace")
    def test_native_two_topic_act_round_trip(self):
        self.native_round_trip(act=True, contract="two_topic_union")

    def native_round_trip(self, act=False, dotted=False, contract="three_topic_union"):
        import rti.connextdds as dds

        with ExitStack() as cleanup:
            env = dict(os.environ)
            for index in range(1, 12):
                env["WAN_PEER" + (str(index) if index > 1 else "")] = "[0-15]@builtin.udpv4://127.0.0.1"
            processes, logs, participants = [], [], []
            types = dds.QosProvider(str(ACT_TYPES if act else FIXTURE))
            qos = dds.QosProvider(str(ROOT / "harness_v2/qos/act_qos_profiles.xml"))
            for node, domain in (("DeviceA", 103), ("DeviceB", 105)):
                model = self.model(node, domain, act, dotted, contract)
                path = write_bundle(model, self.build / "libact_gateway_processor.so",
                                    True, self.work / (node + ".xml"))
                preflight(path)
                log_path = self.work / (node + ".log")
                log = cleanup.enter_context(log_path.open("w"))
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / "router/routing_service/run.py"),
                     "--xml", str(path)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                def stop(process=process):
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=20)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                cleanup.callback(stop)
                processes.append(process)
                logs.append(log_path)
                participant = dds.DomainParticipant(
                    domain, qos.participant_qos_from_profile("ACT_QOS_LIB::router_journal_participant"))
                cleanup.callback(participant.close)
                participants.append(participant)

            def wait(predicate):
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    self.assertTrue(all(p.poll() is None for p in processes),
                                    [path.read_text() for path in logs])
                    if predicate():
                        return
                    time.sleep(0.05)
                self.fail("\n".join(path.read_text() for path in logs))

            wait(lambda: all("rs.gateway.started" in path.read_text() for path in logs))
            for identifier, topic, type_name, _, key, _ in ACT_CASES if act else CUSTOM_CASES:
                if dotted:
                    topic = "devices.commands" if identifier == "command" else "devices.updates"
                topics = [dds.DynamicData.Topic(p, topic, types.type(type_name)) for p in participants]
                writer = dds.DynamicData.DataWriter(
                    dds.Publisher(participants[0]), topics[0],
                    qos.datawriter_qos_from_profile("ACT_QOS_LIB::lan_event"))
                cleanup.callback(writer.close)
                reader = dds.DynamicData.DataReader(
                    dds.Subscriber(participants[1]), topics[1],
                    qos.datareader_qos_from_profile("ACT_QOS_LIB::lan_event"))
                cleanup.callback(reader.close)
                sample = dds.DynamicData(types.type(type_name))
                sample[key] = "original-instance"
                if type_name == "DeviceCommand":
                    sample["opcode"] = -731
                    sample["bytes"] = [0, 1, 127, 255]
                elif type_name == "DeviceStatus":
                    sample["serial"] = (1 << 60) + 71
                    reading = dds.DynamicData(types.type("SensorReading"))
                    reading["label"] = "temperature"
                    reading["values"] = [-0.125, 123456.75]
                    sample["readings"] = [reading]
                else:
                    sample["run_id"] = "unchanged-act"
                    sample["audit_sequence"] = (1 << 40) + 71
                    sample["sent_at_ns"] = 1234567890123456
                    if type_name == "control_command":
                        sample["destination"] = "DeviceB"
                        sample["payload"] = list(range(256)) * 32
                    else:
                        sample["battery_soc_pct"] = 81.5
                        sample["charging"] = True
                seen = []

                def delivered():
                    writer.write(sample)
                    seen.extend(data for data, info in reader.take() if info.valid)
                    return any(data == sample for data in seen)

                wait(delivered)
                self.assertTrue(any(data == sample for data in seen), topic)


if __name__ == "__main__":
    unittest.main()
