"""Prepared Routing Service configuration snapshots; no DDS entities are created."""

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from gui.mesh_dashboard.server.delivery_config import load_delivery_config, load_router_config
from gui.mesh_dashboard.server.system_config import load_system_config
from gui.mesh_dashboard.tests.test_system_config import fake_provider
from router.routing_service.run import write_bundle


ROOT = Path(__file__).resolve().parents[3]
WORKDIR = os.environ.get("ACT_RS_TEST_WORKDIR", ".")


def prepared_model():
    properties = {
        "act.version": "1", "act.types_uri": str(ROOT / "harness_v2/datamodel/gen/ActTypes.xml"),
        "act.node": "Boat30", "act.role": "platform", "act.router": "rs",
        "act.wan_contract": "three_topic_union", "act.monitoring": "true", "act.teams": "TEAM_A",
        "act.admin_participant": "lan", "act.lan_participant": "lan",
        "act.wan_participant": "wan", "act.lan_domain": "30", "act.wan_domain": "200",
        "act.async_bandwidth_bps": "4000000",
    }
    inputs, outputs = [], []
    for topic, type_name, branch, identity, channel, tx, rx in (
            ("ContactReport", "contact_report", "contact_report", "contact_id",
             "state_normal", "control", "teams"),
            ("PlatformData", "platform_data", "data", "source", "periodic_normal", "teams", None),
            ("PlatformStatus", "platform_init_status", "navigation", "source",
             "periodic_normal", "global", None),
            ("PlatformCommandAck", "control_command_ack", "command_ack", "source",
             "event_high", "control", None),
            ("Mission", "control_command", "command", "source",
             "event_high", None, "addressed")):
        prefix = f"act.binding.{topic}."
        properties.update({prefix + "topic_name": topic, prefix + "type": type_name,
                           prefix + "branch": branch, prefix + "instance_id": identity,
                           prefix + "channel": channel})
        for direction, recipients, ports in (("tx", tx, inputs), ("rx", rx, outputs)):
            if recipients is None:
                continue
            name = ("lan_in_" if direction == "tx" else "lan_out_") + topic
            properties[prefix + direction + ".port"] = name
            properties[prefix + direction + ".recipients"] = recipients
            profile = ("lan_status_1s" if topic == "PlatformStatus" and direction == "tx" else
                       "lan_status" if channel.startswith("periodic_") else "lan_event")
            ports.append({"name": name, "topic": topic, "type": type_name, "participant": "lan",
                          "profile": "ACT_QOS_LIB::" + profile})
    return {
        "node": "Boat30", "role": "platform", "router": "rs", "teams": ["TEAM_A"],
        "admin_participant": "lan", "async_bandwidth_bps": 4000000,
        "types": str(ROOT / "harness_v2/datamodel/gen/ActTypes.xml"),
        "qos_libraries": [str(ROOT / "harness_v2/qos/act_qos_profiles.xml")],
        "properties": properties, "inputs": inputs, "outputs": outputs,
        "participants": [
            {"name": "lan", "domain": 30, "partitions": [], "profile": "ACT_QOS_LIB::lan_router_participant"},
            {"name": "wan", "domain": 200, "partitions": [],
             "profile": "ACT_QOS_LIB::wan_three_topic_participant"}],
        "wan": [
            {"name": family, "type": type_name, "participant": "wan",
             "profile": "ACT_QOS_LIB::" + profile,
             "writer_profile": "ACT_QOS_LIB::rs_three_topic_" + family.lower(),
             "expression": "source_id <> %0", "parameters": ["'Boat30'"]}
            for family, type_name, profile in (
                ("Event", "WanEvent", "wan_three_topic_event"),
                ("State", "WanEvent", "wan_union_contacts"),
                ("Periodic", "WanStatus", "wan_union_status"))],
    }


def snapshot_provider():
    provider = fake_provider()
    original = provider.datareader_qos_from_profile.side_effect

    def reader(profile):
        qos = original(profile)
        qos.time_based_filter = SimpleNamespace(minimum_separation=0)
        return qos

    provider.datareader_qos_from_profile.side_effect = reader
    return provider


class NativeXmlConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(dir=WORKDIR)
        self.addCleanup(self.work.cleanup)
        self.path = write_bundle(prepared_model(), "plugin.so", False,
                                 Path(self.work.name) / "node-config.xml")

    def test_prepared_xml_loads_embedded_qos_and_ports_without_yaml_or_type_file_reads(self):
        self.path.with_name("node-config.types.xml").unlink()
        documents = []

        def factory(source):
            document = source if isinstance(source, str) else source.string_profile[0]
            documents.append(ET.fromstring(document))
            return snapshot_provider()

        with patch("yaml.safe_load", side_effect=AssertionError("no YAML after preparation")):
            path, config, libraries, _provider = load_router_config(self.path, factory)
            delivery = load_delivery_config(self.path, factory, float("inf"))
            system = load_system_config(self.path, factory, float("inf"))
        self.assertEqual(path, self.path)
        self.assertEqual(libraries, [self.path])
        self.assertEqual(config["native"]["admin_participant"], "lan")
        self.assertEqual(config["native"]["receive_policy"], "mission")
        self.assertEqual(set(config["participants"]), {"lan", "wan"})
        self.assertEqual(config["participants"]["wan"]["domain"], 200)
        self.assertTrue(config["routes"])
        self.assertTrue({"Event", "State", "Periodic"}.issubset(delivery["topics"]))
        self.assertEqual(delivery["async_bandwidth_bps"], 4000000)
        contact = delivery["topics"]["ContactReport"]
        self.assertEqual({entry["endpoint"] for entry in contact}, {"reader", "writer"})
        self.assertTrue(all(entry["family"] == "State" for entry in contact))
        self.assertEqual(contact[0]["publication_priority"], 200)
        self.assertEqual(contact[0]["lan"]["profile"], "ACT_QOS_LIB::lan_event")
        self.assertTrue(all(document.find("routing_service") is None for document in documents))
        self.assertTrue(all("$(" not in ET.tostring(document, encoding="unicode") for document in documents))
        sections = {section["id"]: section for section in system["sections"]}
        self.assertEqual({row[0] for row in sections["wan-qos"]["rows"]},
                         {"Event", "State", "Periodic"})
        self.assertIn("lan-qos", sections)
        self.assertTrue(any(row[0] == "Platform receive policy"
                            and row[1] == "Mission-state CFT selection"
                            for row in sections["system"]["rows"]))
        self.assertEqual(len(sections["priorities"]["rows"]), 8)
        self.assertNotIn("Telemetry", str(system))
        self.assertNotIn("router YAML", str(system))

    def test_duplicate_properties_missing_ports_and_unresolved_variables_fail_explicitly(self):
        original = self.path.read_text()
        for mutation, message in (("duplicate", "duplicate processor"),
                                  ("port", "invalid tx port"), ("variable", "unresolved XML")):
            with self.subTest(mutation=mutation):
                root = ET.fromstring(original)
                if mutation == "duplicate":
                    properties = root.find(".//processor/property/value")
                    properties.append(ET.fromstring(ET.tostring(properties[0])))
                elif mutation == "port":
                    route = next(route for route in root.findall(".//topic_route")
                                 if route.find("input[@name='lan_in_ContactReport']") is not None)
                    route.remove(route.find("input[@name='lan_in_ContactReport']"))
                else:
                    root.find(".//qos_library/qos_profile").attrib["name"] = "$(ACT_UNDEFINED_SNAPSHOT_TEST)"
                self.path.write_text(ET.tostring(root, encoding="unicode"))
                with self.assertRaisesRegex(ValueError, message):
                    load_delivery_config(self.path, lambda _: snapshot_provider(), float("inf"))
        self.path.write_text(original)

    def test_independent_lan_defaults_are_displayed_as_configured_not_inferred_qos(self):
        root = ET.parse(self.path)
        port = root.find(".//output[@name='lan_out_Mission']")
        port.remove(port.find("datawriter_qos"))
        port.find("auto_configure_qos").text = "1"
        root.write(self.path)
        _, config, _, _ = load_router_config(self.path, lambda _: snapshot_provider())
        self.assertEqual(config["native"]["runtime_mode"], "independent")
        lane = config["native"]["ports"]["lan_out_Mission"]
        self.assertTrue(lane["auto_configure_qos"])
        self.assertEqual(lane["creation_mode"], "ON_DOMAIN_MATCH")
        delivery = load_delivery_config(self.path, lambda _: snapshot_provider(), float("inf"))
        self.assertTrue(delivery["topics"]["Mission"][0]["lan"]["auto_configure_qos"])
        system = load_system_config(self.path, lambda _: snapshot_provider(), float("inf"))
        section = next(section for section in system["sections"] if section["id"] == "lan-qos")
        self.assertIn("not live inferred", section["description"])
        self.assertTrue(any(row[0] == "Mission" and "native auto-QoS" in row[4]
                            for row in section["rows"]))

    def test_independent_configuration_rejects_mismatched_routes_and_sessions(self):
        original = self.path.read_text()
        for mutation, message in (("role", "inconsistent native route registration"),
                                  ("properties", "inconsistent native gateway"),
                                  ("hub", "one native gateway hub"),
                                  ("auto-qos", "invalid auto_configure_qos"),
                                  ("session", "one gateway session")):
            with self.subTest(mutation=mutation):
                root = ET.fromstring(original)
                session = root.find(".//session")
                lane = next(route for route in session.findall("topic_route")
                            if route.find("input[@name='lan_in_ContactReport']") is not None)
                if mutation == "hub":
                    duplicate = ET.fromstring(ET.tostring(session.find("topic_route")))
                    duplicate.attrib["name"] += "_duplicate"
                    session.append(duplicate)
                elif mutation == "auto-qos":
                    lane.find("./input/auto_configure_qos").text = "not-a-boolean"
                elif mutation == "session":
                    session.remove(lane)
                    other = ET.SubElement(root.find(".//domain_route"), "session", name="other")
                    other.append(lane)
                else:
                    name = "act.route_topic" if mutation == "role" else "act.node"
                    entry = next(entry for entry in lane.findall("./processor/property/value/element")
                                 if entry.findtext("name") == name)
                    entry.find("value").text = "Wrong"
                self.path.write_text(ET.tostring(root, encoding="unicode"))
                with self.assertRaisesRegex(ValueError, message):
                    load_delivery_config(self.path, lambda _: snapshot_provider(), float("inf"))

    def test_existing_combined_gateway_xml_remains_readable(self):
        root = ET.parse(self.path)
        session = root.find(".//session")
        hub = session.find("topic_route")
        properties = hub.find("./processor/property/value")
        for entry in list(properties):
            if entry.findtext("name") in ("act.runtime_mode", "act.route_role", "act.route_topic"):
                properties.remove(entry)
        for lane in list(session.findall("topic_route"))[1:]:
            for port in lane:
                if port.tag in ("input", "output"):
                    hub.append(port)
            session.remove(lane)
        root.write(self.path)
        _, config, _, _ = load_router_config(self.path, lambda _: snapshot_provider())
        self.assertEqual(config["native"]["runtime_mode"], "combined")
        self.assertEqual(set(config["native"]["wan"]), {"Event", "State", "Periodic"})
        self.assertEqual(len(config["native"]["bindings"]), 6)


class NativeXmlProviderTests(unittest.TestCase):
    def test_exact_emane_participant_composition_preserves_base_qos_and_property_budget(self):
        try:
            import rti.connextdds as dds
        except ModuleNotFoundError as error:
            if error.name not in ("rti", "rti.connextdds"):
                raise
            self.skipTest("installed Connext binding required; no DDS entities created")
        for role in ("control", "platform"):
            with self.subTest(role=role), tempfile.TemporaryDirectory(dir=WORKDIR) as directory:
                model = prepared_model()
                model["role"] = model["properties"]["act.role"] = role
                base_profile = "ACT_QOS_LIB::wan_three_topic_participant_emane"
                model["participants"][1]["profile"] = base_profile
                path = write_bundle(model, "plugin.so", False, Path(directory) / "node.xml")
                _path, config, _libraries, provider = load_router_config(path, dds.QosProvider)
                base = provider.participant_qos_from_profile(base_profile)
                composed = provider.participant_qos_from_profile(config["participants"]["wan"]["qos"])
                self.assertGreaterEqual(composed.resource_limits.participant_property_list_max_length,
                                        composed.property.size())
                self.assertEqual(composed.transport_builtin, base.transport_builtin)
                self.assertEqual(composed.discovery, base.discovery)
                self.assertEqual(composed.discovery_config, base.discovery_config)
                self.assertEqual(composed.resource_limits.contentfilter_property_max_length,
                                 base.resource_limits.contentfilter_property_max_length)
                for name, value in base.property.get_all().items():
                    self.assertEqual(composed.property.get(name), value, name)
                self.assertEqual(composed.property.get(
                    "dds.flow_controller.token_bucket.act_rs.scheduling_policy"),
                    "DDS_HPF_FLOW_CONTROLLER_SCHED_POLICY")

    def test_actual_generated_bundle_resolves_xml_only_qos_filters_priorities_and_budget(self):
        try:
            import rti.connextdds as dds
        except ModuleNotFoundError as error:
            if error.name not in ("rti", "rti.connextdds"):
                raise
            self.skipTest("installed Connext binding required; no DDS entities created")
        with tempfile.TemporaryDirectory(dir=WORKDIR) as directory:
            path = write_bundle(prepared_model(), "plugin.so", False, Path(directory) / "node.xml")
            path.with_name("node.types.xml").unlink()
            with patch("yaml.safe_load", side_effect=AssertionError("no YAML after preparation")):
                delivery = load_delivery_config(path, dds.QosProvider, dds.Duration.infinite)
                system = load_system_config(path, dds.QosProvider, dds.Duration.infinite)
                _path, config, _libraries, provider = load_router_config(path, dds.QosProvider)
            filtered = delivery["topics"]["PlatformStatus"][0]["lan"]
            unfiltered = delivery["topics"]["PlatformData"][0]["lan"]
            self.assertEqual(filtered["settings"]["minimum_separation_seconds"], 1)
            self.assertEqual(unfiltered["settings"]["minimum_separation_seconds"], 0)
            event = next(value for value in delivery["topics"]["Event"] if value["endpoint"] == "writer")
            self.assertEqual(event["profile"], "ACT_QOS_LIB::rs_three_topic_event")
            self.assertEqual(event["settings"]["reliability"], "RELIABLE")
            self.assertEqual(delivery["topics"]["Mission"][0]["publication_priority"], 600)
            self.assertEqual(delivery["topics"]["RouterHealth"][0]["family"], "Periodic")
            self.assertEqual(delivery["topics"]["RouterHealth"][0]["publication_priority"], 500)
            wan = config["participants"]["wan"]
            qos = provider.participant_qos_from_profile(wan["qos"])
            self.assertEqual(qos.property.get("dds.flow_controller.token_bucket.act_rs.token_bucket.bytes_per_token"), "5000")
            sections = {section["id"]: section for section in system["sections"]}
            self.assertEqual(len(sections["wan-qos"]["rows"]), 6)
            self.assertTrue(any(row[0] == "PlatformStatus" and row[-1] == "1 s"
                                for row in sections["lan-qos"]["rows"]))
            presence = {row[0]: row[1] for row in sections["presence"]["rows"]}
            self.assertEqual(presence["Recovery to ALIVE"], "Any valid received Periodic")
            root = ET.parse(path).getroot()
            reader = root.find(".//input[@name='lan_in_PlatformData']/datareader_qos")
            separation = ET.SubElement(ET.SubElement(reader, "time_based_filter"), "minimum_separation")
            ET.SubElement(separation, "sec").text = "0"
            ET.SubElement(separation, "nanosec").text = "250000000"
            path.write_text(ET.tostring(root, encoding="unicode"))
            overridden = load_delivery_config(path, dds.QosProvider, dds.Duration.infinite)
            lan = overridden["topics"]["PlatformData"][0]["lan"]
            self.assertEqual(lan["profile"], "ACT_QOS_LIB::lan_status")
            self.assertEqual(lan["settings"]["minimum_separation_seconds"], 0.25)


if __name__ == "__main__":
    unittest.main()
