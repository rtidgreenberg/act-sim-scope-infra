import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

import yaml

from run import render


class RoutingServiceConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.model = {
            "node": "Boat30", "role": "platform", "router": "rs", "teams": ["TEAM_A"],
            "types": "harness_v2/datamodel/gen/ActTypes.xml",
            "qos_libraries": ["harness_v2/qos/act_qos_profiles.xml"],
            "async_bandwidth_bps": 2000000,
            "participants": [
                {"name": "lan", "domain": 103, "profile": "ACT_QOS_LIB::lan_router_participant",
                 "partitions": []},
                {"name": "wan", "domain": 102, "profile": "ACT_QOS_LIB::wan_two_topic_participant",
                 "partitions": ["SHARED"]},
            ],
            "inputs": [{"name": "lan_in_ContactReport", "topic": "ContactReport",
                        "type": "contact_report", "participant": "lan",
                        "profile": "ACT_QOS_LIB::lan_event"}],
            "outputs": [],
            "wan": [
                {"name": family, "participant": "wan", "profile": profile,
                 "writer_profile": "ACT_QOS_LIB::rs_wan_" + family.lower(),
                 "expression": "source_id <> %0", "parameters": ["'Boat30'"]}
                for family, profile in (
                    ("Event", "ACT_QOS_LIB::wan_event"),
                    ("Status", "ACT_QOS_LIB::wan_union_status"))
            ],
        }
        self.xml = ET.fromstring(render(self.model, "/tmp/config.yaml", "/tmp/plugin.so", True))

    def test_one_route_and_exact_shared_wan_endpoints(self):
        routes = self.xml.findall(".//topic_route")
        self.assertEqual(len(routes), 1)
        route = routes[0]
        self.assertEqual([port.findtext("topic_name") for port in route.findall("input")
                          if port.attrib["participant"] == "wan"], ["Event", "Status"])
        self.assertEqual([port.findtext("topic_name") for port in route.findall("output")
                          if port.attrib["participant"] == "wan"], ["Event", "Status"])
        self.assertTrue(all(port.findtext("creation_mode") == "IMMEDIATE"
                            for port in list(route) if port.tag in ("input", "output")))

    def test_reader_retention_is_copied_from_production_xml(self):
        original = ET.parse(self.model["qos_libraries"][0]).find(
            "./qos_library/qos_profile[@name='router_lifecycle_reader']/"
            "datareader_qos/reader_data_lifecycle")
        for reader in self.xml.findall(".//topic_route/input/datareader_qos"):
            self.assertEqual(ET.tostring(reader.find("reader_data_lifecycle")),
                             ET.tostring(original))

    def test_wan_writers_share_controller_and_have_two_priority_bands(self):
        for family, threshold in (("Event", 300), ("Status", 200)):
            output = self.xml.find(f".//topic_route/output[@name='wan_out_{family}']")
            self.assertEqual(output.find("datawriter_qos").attrib["base_name"],
                             "ACT_QOS_LIB::rs_wan_" + family.lower())
            profile = self.xml.find(
                "./qos_library/qos_profile[@name='rs_wan_" + family.lower() + "']/datawriter_qos")
            self.assertEqual(profile.findtext("publish_mode/kind"),
                             "ASYNCHRONOUS_PUBLISH_MODE_QOS")
            self.assertEqual(profile.findtext("publish_mode/flow_controller_name"),
                             "dds.flow_controller.token_bucket.act_rs")
            self.assertEqual(profile.findtext("multi_channel/filter_name"), "DDSPRIFLTR")
            self.assertEqual([e.text for e in profile.findall(
                "multi_channel/channels/element/filter_expression")],
                [f"@priority >= {threshold}", f"@priority < {threshold}"])

    def test_flow_budget_is_applied_only_to_wan(self):
        for participant in self.xml.findall(".//domain_route/participant"):
            properties = participant.findall("domain_participant_qos/property/value/element")
            self.assertEqual(len(properties), 7 if participant.attrib["name"] == "wan" else 0)
        variables = {entry.findtext("name"): entry.findtext("value") for entry in
                     self.xml.findall("./configuration_variables/value/element")}
        self.assertEqual(variables["RS_FLOW_BYTES_PER_PERIOD"], "2500")

    def test_registration_and_node_local_grouping(self):
        service = self.xml.find("routing_service")
        self.assertEqual(service.attrib["group_name"], "Boat30")
        registrations = service.findall(".//register_type")
        self.assertEqual({entry.attrib["type_ref"] for entry in registrations},
                         {"contact_report", "WanEvent", "WanStatus"})
        self.assertTrue(all("type_name" not in entry.attrib for entry in registrations))

    def test_schema_sentinel_normalization_preserves_meaning(self):
        self.assertTrue(all(element.text.strip() == "LENGTH_AUTO"
                            for element in self.xml.iter("type_object_max_serialized_length")))
        for tag in ("min_send_window_size", "max_send_window_size"):
            self.assertTrue(all(element.text.strip() != "-1" for element in self.xml.iter(tag)))

    def test_data_plane_boundary_is_explicit(self):
        properties = {
            entry.findtext("name"): entry.findtext("value")
            for entry in self.xml.findall(".//processor/property/value/element")
        }
        self.assertEqual(properties["data_plane_only"], "true")
        self.assertEqual(properties["teams"], "[TEAM_A]")


@unittest.skipUnless(os.environ.get("ACT_RS_BUILD_DIR"),
                     "set ACT_RS_BUILD_DIR to test the native configuration generator")
class NativeConfigurationTests(unittest.TestCase):
    def test_lan_default_and_named_profiles_for_both_roles(self):
        root = Path(__file__).resolve().parents[2]
        binary = Path(os.environ["ACT_RS_BUILD_DIR"]) / "act_rs_config"
        self.assertTrue(binary.is_file(), binary)
        for role in ("control", "platform"):
            for alias in ("", "default", "lan_event", "undefined"):
                with self.subTest(role=role, alias=alias), tempfile.TemporaryDirectory() as directory:
                    config = yaml.safe_load((root / "router/config/control-platform.yaml").read_text())
                    config["router"]["wan_contract"] = "two_topic_union"
                    config["router"]["admin_participant"] = role + "_lan"
                    config["types"]["xml"] = str(root / "harness_v2/datamodel/gen/ActTypes.xml")
                    config["qos_libraries"] = [str(root / "harness_v2/qos/act_qos_profiles.xml")]
                    config["qos_profiles"]["wan_participant"] = "ACT_QOS_LIB::wan_two_topic_participant"
                    config["qos_profiles"]["wan_status"] = "ACT_QOS_LIB::wan_union_status"
                    config["routes"] = [route for route in config["routes"]
                                        if "destination_side" in route]
                    for route in config["routes"]:
                        route["source_side"]["input"]["reader_qos"] = alias
                        route["destination_side"]["output"]["writer_qos"] = alias
                    path = Path(directory) / "config.yaml"
                    path.write_text(yaml.safe_dump(config))
                    result = subprocess.run(
                        [str(binary), str(path), role, "Boat30", "rs"],
                        cwd=root, text=True, capture_output=True)
                    if alias == "undefined":
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("undefined", result.stderr)
                        continue
                    self.assertEqual(result.returncode, 0, result.stderr)
                    model = yaml.safe_load(result.stdout)
                    event_inputs = [port for port in model["inputs"]
                                    if port["topic"] in ("ContactReport", "ControlCommand",
                                                        "PlatformCommandAck", "ActTeamAssignment",
                                                        "ActPlatformStatusMode")]
                    self.assertTrue(event_inputs)
                    self.assertTrue(model["outputs"])
                    reader = ("ACT_QOS_LIB::lan_event" if alias == "lan_event"
                              else "ACT_QOS_LIB::router_default_reader")
                    writer = ("ACT_QOS_LIB::lan_event" if alias == "lan_event"
                              else "ACT_QOS_LIB::router_default_writer")
                    self.assertTrue(all(port["profile"] == reader for port in event_inputs))
                    self.assertTrue(all(port["profile"] == writer for port in model["outputs"]))


if __name__ == "__main__":
    unittest.main()
