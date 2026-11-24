import os
import copy
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

import yaml

from run import lan_participant_identity, preflight, render, write_bundle


TEST_WORKDIR = os.environ.get("ACT_RS_TEST_WORKDIR", ".")


def explicit_properties(contract="three_topic_union"):
    return {
        "act.version": "1", "act.types_uri": "harness_v2/datamodel/gen/ActTypes.xml",
        "act.node": "Boat30", "act.role": "platform", "act.router": "rs",
        "act.wan_contract": contract, "act.monitoring": "false", "act.teams": "TEAM_A",
        "act.receive_policy": "static",
        "act.admin_participant": "lan", "act.lan_participant": "lan",
        "act.wan_participant": "wan", "act.lan_domain": "103", "act.wan_domain": "102",
        "act.binding.contact.topic_name": "ContactReport",
        "act.binding.contact.type": "contact_report",
        "act.binding.contact.channel": "state_normal" if contract == "three_topic_union" else "event_normal",
        "act.binding.contact.branch": "contact_report",
        "act.binding.contact.instance_id": "contact_id",
        "act.binding.contact.tx.port": "lan_in_ContactReport",
        "act.binding.contact.tx.recipients": "control",
    }


def mission_properties():
    properties = explicit_properties()
    properties["act.receive_policy"] = "mission"
    properties["act.binding.contact.rx.port"] = "lan_out_ContactReport"
    properties["act.binding.contact.rx.recipients"] = "teams"
    for identifier, topic, type_name, branch, key, channel, recipient in (
            ("command", "Mission", "control_command", "command", "source", "event_high", "addressed"),
            ("team", "Team", "TeamAssignment", "team_assignment", "platform_node", "event_high", "global"),
            ("mission", "PlatformMissionStatus", "platform_mission_status", "mission", "source",
             "periodic_normal", "teams"),
            ("navigation", "PlatformStatus", "platform_init_status", "navigation", "source",
             "periodic_normal", "global")):
        prefix = f"act.binding.{identifier}."
        properties.update({prefix + "topic_name": topic, prefix + "type": type_name,
                           prefix + "branch": branch, prefix + "instance_id": key,
                           prefix + "channel": channel, prefix + "rx.port": "lan_out_" + topic,
                           prefix + "rx.recipients": recipient})
        if identifier == "mission":
            properties[prefix + "tx.port"] = "lan_in_" + topic
            properties[prefix + "tx.recipients"] = "control;teams"
    return properties


class RoutingServiceConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.model = {
            "node": "Boat30", "role": "platform", "router": "rs", "teams": ["TEAM_A"],
            "types": "harness_v2/datamodel/gen/ActTypes.xml",
            "qos_libraries": ["harness_v2/qos/act_qos_profiles.xml"],
            "async_bandwidth_bps": 4000000,
            "properties": explicit_properties("two_topic_union"),
            "admin_participant": "lan",
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
                 "type": "Wan" + family,
                 "writer_profile": "ACT_QOS_LIB::rs_wan_" +
                                   ("event" if family == "Event" else "periodic"),
                 "expression": "source_id <> %0", "parameters": ["'Boat30'"]}
                for family, profile in (
                    ("Event", "ACT_QOS_LIB::wan_event"),
                    ("Status", "ACT_QOS_LIB::wan_union_periodic"))
            ],
        }
        self.xml = ET.fromstring(render(self.model, "unused.yaml", "plugin.so", True))

    def test_independent_lan_routes_and_exact_shared_wan_endpoints(self):
        routes = self.xml.findall(".//topic_route")
        self.assertEqual(len(routes), 2)
        route = routes[0]
        self.assertEqual([port.findtext("topic_name") for port in route.findall("input")
                          if port.attrib["participant"] == "wan"], ["Event", "Status"])
        self.assertEqual([port.findtext("topic_name") for port in route.findall("output")
                          if port.attrib["participant"] == "wan"], ["Event", "Status"])
        self.assertTrue(all(port.findtext("creation_mode") == "IMMEDIATE"
                            for port in list(route) if port.tag in ("input", "output")))
        lan = routes[1].find("input")
        self.assertEqual(lan.findtext("creation_mode"), "ON_DOMAIN_MATCH")
        self.assertEqual(lan.findtext("auto_configure_qos"), "true")

    def test_reader_retention_is_copied_from_production_xml(self):
        original = ET.parse(self.model["qos_libraries"][0]).find(
            "./qos_library/qos_profile[@name='router_lifecycle_reader']/"
            "datareader_qos/reader_data_lifecycle")
        for reader in self.xml.findall(".//topic_route/input/datareader_qos"):
            self.assertEqual(ET.tostring(reader.find("reader_data_lifecycle")),
                             ET.tostring(original))

    def test_wan_writers_share_controller_and_have_two_priority_bands(self):
        for family, threshold in (("Event", 300), ("Status", 200)):
            profile_name = "rs_wan_event" if family == "Event" else "rs_wan_periodic"
            output = self.xml.find(f".//topic_route/output[@name='wan_out_{family}']")
            self.assertEqual(output.find("datawriter_qos").attrib["base_name"],
                             "ACT_QOS_LIB::" + profile_name)
            profile = self.xml.find(
                f"./qos_library/qos_profile[@name='{profile_name}']/datawriter_qos")
            self.assertEqual(profile.findtext("publish_mode/kind"),
                             "ASYNCHRONOUS_PUBLISH_MODE_QOS")
            self.assertEqual(profile.findtext("publish_mode/flow_controller_name"),
                             "dds.flow_controller.token_bucket.act_rs")
            self.assertEqual(profile.findtext("multi_channel/filter_name"), "DDSPRIFLTR")
            self.assertEqual([e.text for e in profile.findall(
                "multi_channel/channels/element/filter_expression")],
                [f"@priority >= {threshold}", f"@priority < {threshold}"])

    def test_flow_budget_is_applied_only_to_wan(self):
        configured_limit = self.xml.find(
            "./qos_library/qos_profile[@name='rs_priority_participant']/"
            "domain_participant_qos/resource_limits/participant_property_list_max_length")
        for participant in self.xml.findall(".//domain_route/participant"):
            properties = participant.findall("domain_participant_qos/property/value/element")
            self.assertEqual(len(properties), 7 if participant.attrib["name"] == "wan" else 0)
            limits = participant.find("domain_participant_qos/resource_limits")
            if participant.attrib["name"] == "wan":
                self.assertEqual([entry.tag for entry in limits],
                                 ["participant_property_list_max_length"])
                self.assertEqual(limits.findtext("participant_property_list_max_length"),
                                 configured_limit.text)
            else:
                self.assertIsNone(limits)
        variables = {entry.findtext("name"): entry.findtext("value") for entry in
                     self.xml.findall("./configuration_variables/value/element")}
        self.assertEqual(variables["RS_FLOW_BYTES_PER_PERIOD"], "5000")

    def test_embedded_channels_include_schema_required_multicast_settings(self):
        channels = self.xml.findall(
            "./qos_library/qos_profile/datawriter_qos/multi_channel/channels/element")
        self.assertTrue(channels)
        for channel in channels:
            with self.subTest(filter=channel.findtext("filter_expression")):
                self.assertIsNotNone(channel.find("multicast_settings"))

    def test_three_topic_types_and_independent_writer_profiles(self):
        model = dict(self.model)
        model["wan"] = [
            {"name": family, "type": type_name, "participant": "wan",
             "profile": "ACT_QOS_LIB::" + profile,
             "writer_profile": "ACT_QOS_LIB::rs_three_topic_" + family.lower(),
             "expression": "source_id <> %0", "parameters": ["'Boat30'"]}
            for family, type_name, profile in (
                ("Event", "WanEvent", "wan_three_topic_event"),
                ("State", "WanEvent", "wan_union_contacts"),
                ("Periodic", "WanStatus", "wan_union_periodic"))
        ]
        model["properties"] = explicit_properties()
        root = ET.fromstring(render(model, "unused.yaml", "plugin.so", True))
        route = root.find(".//topic_route")
        for kind in ("input", "output"):
            ports = [port for port in route.findall(kind) if port.attrib["participant"] == "wan"]
            self.assertEqual([(port.findtext("topic_name"), port.findtext("registered_type_name"))
                              for port in ports],
                             [("Event", "WanEvent"), ("State", "WanEvent"),
                              ("Periodic", "WanStatus")])
        for family, threshold in (("Event", 500), ("State", 300), ("Periodic", 400)):
            profile = root.find("./qos_library/qos_profile[@name='rs_three_topic_" +
                                family.lower() + "']/datawriter_qos")
            self.assertEqual(profile.findtext("publish_mode/flow_controller_name"),
                             "dds.flow_controller.token_bucket.act_rs")
            self.assertEqual([entry.text for entry in profile.findall(
                "multi_channel/channels/element/filter_expression")],
                [f"@priority >= {threshold}", f"@priority < {threshold}"])
        self.assertEqual({entry.attrib["type_ref"] for entry in root.findall(".//register_type")},
                         {"contact_report", "WanEvent", "WanStatus"})

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
        self.assertEqual(properties["act.monitoring"], "false")
        self.assertEqual(properties["act.teams"], "TEAM_A")
        self.assertNotIn("config", properties)
        self.assertNotIn("teams", properties)
        self.assertEqual(properties["act.binding.contact.branch"], "contact_report")

    def test_monitoring_uses_service_owned_lan_ports_and_registered_types(self):
        root = ET.fromstring(render(self.model, "unused.yaml", "plugin.so", False))
        route = root.find(".//topic_route")
        self.assertEqual([port.attrib["name"] for port in route.findall("input")
                          if port.attrib["name"].startswith("monitor_")], ["monitor_command"])
        expected = {"monitor_ack", "monitor_status", "monitor_mesh", "monitor_links",
                    "monitor_diagnostics"}
        self.assertEqual({port.attrib["name"] for port in route.findall("output")
                          if port.attrib["name"].startswith("monitor_")}, expected)
        for port in list(route):
            if port.tag not in ("input", "output") or not port.attrib["name"].startswith("monitor_"):
                continue
            self.assertEqual(port.attrib["participant"], "lan")
            type_name = port.findtext("registered_type_name")
            self.assertIsNotNone(root.find(
                f".//participant[@name='lan']/register_type[@name='{type_name}']"))
        self.assertEqual(len([port for port in route.findall("output")
                              if port.attrib["participant"] == "wan"]), 2)
        properties = {entry.findtext("name"): entry.findtext("value") for entry in
                      root.findall(".//processor/property/value/element")}
        self.assertEqual(properties["act.monitoring"], "true")
        for family, internal in (("Event", "RouterConfig"), ("Status", "RouterHealth")):
            expression = route.findtext(
                f"input[@name='wan_in_{family}']/content_filter/expression")
            self.assertIn(f"source_id <> %0 AND original_topic = '{internal}'", expression)

    def check_xml(self, root):
        with tempfile.TemporaryDirectory(dir=TEST_WORKDIR) as directory:
            path = Path(directory) / "gateway.xml"
            path.write_text(ET.tostring(root, encoding="unicode"))
            return preflight(path)

    def test_preflight_validates_explicit_xml(self):
        self.assertEqual(self.check_xml(self.xml)["act.version"], "1")

    def test_preflight_preserves_combined_v1_xml(self):
        root = copy.deepcopy(self.xml)
        session = root.find(".//session")
        routes = session.findall("topic_route")
        combined = routes[0]
        for route in routes[1:]:
            for endpoint in route.findall("input") + route.findall("output"):
                endpoint.find("creation_mode").text = "IMMEDIATE"
                endpoint.find("auto_configure_qos").text = "false"
                combined.append(endpoint)
            session.remove(route)
        entries = combined.find("./processor/property/value")
        for entry in list(entries):
            if entry.findtext("name") in ("act.runtime_mode", "act.route_role", "act.route_topic"):
                entries.remove(entry)
        self.assertEqual(self.check_xml(root)["act.version"], "1")

    def test_preflight_rejects_independent_topology_and_model_mismatches(self):
        for change, message in (
                ("mode", "runtime_mode"), ("role", "role/topic"), ("session", "one native session"),
                ("activation", "ON_DOMAIN_MATCH"), ("model", "identical gateway model"),
                ("pair", "exactly one native pair"), ("qos", "explicit XML QoS")):
            with self.subTest(change=change):
                root = copy.deepcopy(self.xml)
                hub = root.find(".//topic_route")
                lan = root.findall(".//topic_route")[1]
                if change in ("mode", "role", "model"):
                    key = {"mode": "act.runtime_mode", "role": "act.route_role",
                           "model": "act.node"}[change]
                    entry = next(entry for entry in lan.findall("./processor/property/value/element")
                                 if entry.findtext("name") == key)
                    entry.find("value").text = "unsupported"
                elif change == "session":
                    ET.SubElement(root.find(".//domain_route"), "session", name="other")
                elif change == "activation":
                    lan.find("input/creation_mode").text = "IMMEDIATE"
                elif change == "pair":
                    hub.remove(hub.find("output[@name='wan_out_Event']"))
                else:
                    hub.find("input/datareader_qos").attrib.clear()
                with self.assertRaisesRegex(ValueError, message):
                    self.check_xml(root)

    def test_lan_identity_preserves_near_bound_node_router_names(self):
        for node in ("Boat30", "n" * 248, "n" * 252, "é" * 126):
            with self.subTest(node_bytes=len(node.encode())):
                model = copy.deepcopy(self.model)
                model["node"] = model["properties"]["act.node"] = node
                identity = lan_participant_identity(model["properties"])
                self.assertLessEqual(len(identity.encode()), 255)
                readable = node + "/rs/lan"
                if len(readable.encode()) <= 255:
                    self.assertEqual(identity, readable)
                else:
                    self.assertTrue(identity.startswith("act.lan.sha256/"))
                root = ET.fromstring(render(model, "unused.yaml", "plugin.so", True))
                self.assertEqual(root.findtext(
                    ".//participant[@name='lan']/domain_participant_qos/participant_name/name"), identity)
                self.assertEqual(self.check_xml(root)["act.node"], node)
        first = {"act.node": "n" * 250 + "/r", "act.router": "s", "act.lan_participant": "lan"}
        second = {"act.node": "n" * 250, "act.router": "r/s", "act.lan_participant": "lan"}
        self.assertNotEqual(lan_participant_identity(first), lan_participant_identity(second))

    def test_preflight_rejects_lan_port_owned_by_another_participant(self):
        for kind, direction in (("input", "tx"), ("output", "rx")):
            with self.subTest(kind=kind):
                root = ET.fromstring(ET.tostring(self.xml))
                port = root.find(".//topic_route/input[@name='lan_in_ContactReport']")
                if kind == "output":
                    port.tag = "output"
                    for entry in root.findall(".//processor/property/value/element"):
                        if entry.findtext("name") == "act.binding.contact.tx.port":
                            entry.find("name").text = "act.binding.contact.rx.port"
                        elif entry.findtext("name") == "act.binding.contact.tx.recipients":
                            entry.find("name").text = "act.binding.contact.rx.recipients"
                        elif entry.findtext("name") == "act.route_role" and entry.findtext("value") == "tx":
                            entry.find("value").text = "rx"
                port.set("participant", "wan")
                with self.assertRaisesRegex(ValueError, "port participant"):
                    self.check_xml(root)

    def test_preflight_rejects_wrong_lan_domain(self):
        self.xml.find(".//participant[@name='lan']/domain_id").text = "104"
        with self.assertRaisesRegex(ValueError, "LAN participant/domain mismatch"):
            self.check_xml(self.xml)

    def test_preflight_rejects_ambiguous_lan_identity(self):
        self.xml.find(".//participant[@name='lan']/domain_participant_qos/participant_name/name").text = "Boat30/rs"
        with self.assertRaisesRegex(ValueError, "LAN participant identity mismatch"):
            self.check_xml(self.xml)

    def test_persistent_bundle_keeps_type_registry_next_to_explicit_xml(self):
        with tempfile.TemporaryDirectory(dir=TEST_WORKDIR) as directory:
            path = write_bundle(self.model, "plugin.so", True, Path(directory) / "prepared.xml")
            types = path.with_name("prepared.types.xml")
            self.assertEqual(types.read_bytes(), Path(self.model["types"]).read_bytes())
            self.assertEqual(preflight(path)["act.types_uri"], str(types))
            self.assertEqual(self.model["properties"]["act.types_uri"],
                             "harness_v2/datamodel/gen/ActTypes.xml")

    def test_preflight_rejects_duplicate_unknown_missing_and_unresolved_properties(self):
        for name, value, message in (
                ("act.node", "Other", "duplicate"),
                ("act.custom", "ignored", "unknown"),
                ("act.binding.contact.tx.copy", "x", "unknown"),
                ("act.binding.contact.tx.port", "${PORT}", "unresolved")):
            with self.subTest(name=name):
                root = ET.fromstring(ET.tostring(self.xml))
                for entries in root.findall(".//processor/property/value"):
                    if message == "unresolved":
                        next(entry for entry in entries if entry.findtext("name") == name).find("value").text = value
                    else:
                        entry = ET.SubElement(entries, "element")
                        ET.SubElement(entry, "name").text = name
                        ET.SubElement(entry, "value").text = value
                with self.assertRaisesRegex(ValueError, message):
                    self.check_xml(root)
        root = ET.fromstring(ET.tostring(self.xml))
        for entries in root.findall(".//processor/property/value"):
            entries.remove(next(entry for entry in entries if entry.findtext("name") == "act.types_uri"))
        with self.assertRaisesRegex(ValueError, "missing processor property"):
            self.check_xml(root)

    def test_preflight_rejects_port_type_and_event_state_filter_mismatches(self):
        for mutation, message in (("type", "topic/type"), ("filter", "filtering")):
            with self.subTest(mutation=mutation):
                root = ET.fromstring(ET.tostring(self.xml))
                port = root.find(".//input[@name='lan_in_ContactReport']")
                if mutation == "type":
                    port.find("registered_type_name").text = "OtherType"
                else:
                    port.find("datareader_qos").attrib["base_name"] = "ACT_QOS_LIB::lan_status_1s"
                with self.assertRaisesRegex(ValueError, message):
                    self.check_xml(root)

    def test_preflight_accepts_zero_filter_overrides(self):
        for location in ("port", "profile", "reader_profile"):
            with self.subTest(location=location):
                root = ET.fromstring(ET.tostring(self.xml))
                qos = root.find(".//input[@name='lan_in_ContactReport']/datareader_qos")
                qos.set("base_name", "ACT_QOS_LIB::lan_status_1s")
                if location != "port":
                    profile = ET.SubElement(root.find("qos_library"), "qos_profile",
                                            name="unfiltered")
                    qos.set("base_name", "ACT_QOS_LIB::unfiltered")
                    if location == "profile":
                        profile.set("base_name", "ACT_QOS_LIB::lan_status_1s")
                        qos = ET.SubElement(profile, "datareader_qos")
                    else:
                        qos = ET.SubElement(profile, "datareader_qos",
                                            base_name="ACT_QOS_LIB::lan_status_1s")
                separation = ET.SubElement(ET.SubElement(qos, "time_based_filter"),
                                           "minimum_separation")
                ET.SubElement(separation, "sec").text = "0"
                ET.SubElement(separation, "nanosec").text = "0"
                self.assertEqual(self.check_xml(root)["act.node"], "Boat30")

    def test_preflight_preserves_inherited_filter_duration_components(self):
        for nanoseconds in ("0", "500000000"):
            with self.subTest(nanoseconds=nanoseconds):
                root = ET.fromstring(ET.tostring(self.xml))
                profile = ET.SubElement(root.find("qos_library"), "qos_profile",
                                        name="partial_filter")
                reader = ET.SubElement(profile, "datareader_qos")
                separation = ET.SubElement(ET.SubElement(reader, "time_based_filter"),
                                           "minimum_separation")
                ET.SubElement(separation, "sec").text = "1"
                ET.SubElement(separation, "nanosec").text = nanoseconds
                qos = root.find(".//input[@name='lan_in_ContactReport']/datareader_qos")
                qos.set("base_name", "ACT_QOS_LIB::partial_filter")
                override = ET.SubElement(ET.SubElement(qos, "time_based_filter"),
                                        "minimum_separation")
                ET.SubElement(override, "sec").text = "0"
                if nanoseconds == "0":
                    self.check_xml(root)
                else:
                    with self.assertRaisesRegex(ValueError, "filtering"):
                        self.check_xml(root)


@unittest.skipUnless(os.environ.get("ACT_RS_BUILD_DIR"),
                     "set ACT_RS_BUILD_DIR to test the native configuration generator")
class NativeConfigurationTests(unittest.TestCase):
    def test_three_topic_catalog_priorities_and_empty_state_ports(self):
        root = Path(__file__).resolve().parents[2]
        binary = Path(os.environ["ACT_RS_BUILD_DIR"]) / "act_rs_config"
        for role in ("control", "platform"):
            with self.subTest(role=role), tempfile.TemporaryDirectory(dir=TEST_WORKDIR) as directory:
                config = yaml.safe_load((root / "router/config/control-platform.yaml").read_text())
                config["router"]["wan_contract"] = "three_topic_union"
                config["router"]["admin_participant"] = role + "_lan"
                config["types"]["xml"] = str(root / "harness_v2/datamodel/gen/ActTypes.xml")
                config["qos_libraries"] = [str(root / "harness_v2/qos/act_qos_profiles.xml")]
                config["qos_profiles"].update({
                    "wan_participant": "ACT_QOS_LIB::wan_three_topic_participant",
                    "wan_event": "ACT_QOS_LIB::wan_three_topic_event",
                    "wan_contacts": "ACT_QOS_LIB::wan_union_contacts",
                    "wan_periodic": "ACT_QOS_LIB::wan_union_periodic",
                })
                path = Path(directory) / "config.yaml"
                for empty in (False, True):
                    if empty:
                        config["routes"] = []
                        config["routing_service"] = {"receive_policy": "static"}
                    path.write_text(yaml.safe_dump(config))
                    result = subprocess.run(
                        [str(binary), str(path), role, "Boat30", "rs"], cwd=root,
                        text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    model = yaml.safe_load(result.stdout)
                    self.assertTrue(all(isinstance(value, str)
                                        for value in model["properties"].values()))
                    self.assertEqual([(port["name"], port["type"]) for port in model["wan"]],
                                     [("Event", "WanEvent"), ("State", "WanEvent"),
                                      ("Periodic", "WanStatus")])
                    self.assertEqual(model["topic_families"]["ContactReport"], "State")
                    self.assertEqual(model["topic_families"]["RouterConfig"], "Event")
                    self.assertEqual(model["topic_families"]["RouterHealth"], "Periodic")
                    self.assertEqual(model["publication_priorities"]["Mission"], 600)
                    self.assertEqual(model["publication_priorities"]["RouterHealth"], 500)
                    self.assertEqual(model["publication_priorities"]["ContactReport"], 200)
                    self.assertEqual(model["publication_priorities"]["PlatformStatus"], 100)
                    if empty:
                        self.assertFalse(model.get("inputs"))
                        self.assertFalse(model.get("outputs"))

    def test_lan_default_and_named_profiles_for_both_roles(self):
        root = Path(__file__).resolve().parents[2]
        binary = Path(os.environ["ACT_RS_BUILD_DIR"]) / "act_rs_config"
        self.assertTrue(binary.is_file(), binary)
        for role in ("control", "platform"):
            for alias in ("", "default", "lan_event", "undefined"):
                with self.subTest(role=role, alias=alias), tempfile.TemporaryDirectory(dir=TEST_WORKDIR) as directory:
                    config = yaml.safe_load((root / "router/config/control-platform.yaml").read_text())
                    config["router"]["wan_contract"] = "two_topic_union"
                    config["router"]["admin_participant"] = role + "_lan"
                    config["routing_service"] = {"receive_policy": "static"}
                    config["types"]["xml"] = str(root / "harness_v2/datamodel/gen/ActTypes.xml")
                    config["qos_libraries"] = [str(root / "harness_v2/qos/act_qos_profiles.xml")]
                    config["qos_profiles"]["wan_participant"] = "ACT_QOS_LIB::wan_two_topic_participant"
                    config["qos_profiles"]["wan_periodic"] = "ACT_QOS_LIB::wan_union_periodic"
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
                                    if port["topic"] in ("ContactReport", "Mission",
                                                        "PlatformCommandAck", "Team",
                                                        "ActPlatformStatusMode")]
                    self.assertTrue(event_inputs)
                    self.assertTrue(model["outputs"])
                    reader = ("ACT_QOS_LIB::lan_event" if alias == "lan_event"
                              else "ACT_QOS_LIB::router_default_reader")
                    writer = ("ACT_QOS_LIB::lan_event" if alias == "lan_event"
                              else "ACT_QOS_LIB::router_default_writer")
                    self.assertTrue(all(port["profile"] == reader for port in event_inputs))
                    self.assertTrue(all(port["profile"] == writer for port in model["outputs"]))

    def test_periodic_input_filtering_is_opt_in_and_rejects_event_state(self):
        root = Path(__file__).resolve().parents[2]
        binary = Path(os.environ["ACT_RS_BUILD_DIR"]) / "act_rs_config"
        config = yaml.safe_load((root / "router/config/control-platform.yaml").read_text())
        config["router"]["wan_contract"] = "three_topic_union"
        config["router"]["admin_participant"] = "platform_lan"
        config["types"]["xml"] = str(root / "harness_v2/datamodel/gen/ActTypes.xml")
        config["qos_libraries"] = [str(root / "harness_v2/qos/act_qos_profiles.xml")]
        config["qos_profiles"].update({
            "wan_participant": "ACT_QOS_LIB::wan_three_topic_participant",
            "wan_event": "ACT_QOS_LIB::wan_three_topic_event",
            "wan_contacts": "ACT_QOS_LIB::wan_union_contacts",
            "wan_periodic": "ACT_QOS_LIB::wan_union_periodic",
            "filtered_periodic": "ACT_QOS_LIB::lan_status_1s",
        })
        with tempfile.TemporaryDirectory(dir=TEST_WORKDIR) as directory:
            path = Path(directory) / "config.yaml"
            for topic in (None, "PlatformStatus", "ContactReport", "Mission"):
                with self.subTest(topic=topic):
                    config["routing_service"] = {}
                    if topic:
                        config["routing_service"]["periodic_input_profiles"] = {
                            topic: "filtered_periodic"}
                    path.write_text(yaml.safe_dump(config))
                    result = subprocess.run(
                        [str(binary), str(path), "platform", "Boat30", "rs"], cwd=root,
                        text=True, capture_output=True)
                    if topic in ("ContactReport", "Mission"):
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("only supported for Periodic", result.stderr)
                        continue
                    self.assertEqual(result.returncode, 0, result.stderr)
                    model = yaml.safe_load(result.stdout)
                    inputs = model["inputs"]
                    port = next(port for port in inputs if port["topic"] == "PlatformStatus")
                    self.assertEqual(port["profile"], "ACT_QOS_LIB::lan_status_1s" if topic
                                     else "ACT_QOS_LIB::lan_status")
                    try:
                        import rti.connextdds as dds
                    except ModuleNotFoundError as error:
                        if error.name not in ("rti", "rti.connextdds"):
                            raise
                    else:
                        from gui.mesh_dashboard.server.delivery_config import load_router_config
                        xml = write_bundle(model, "plugin.so", True, Path(directory) / "node.xml")
                        _path, snapshot, _libraries, provider = load_router_config(xml, dds.QosProvider)
                        lan_input = snapshot["native"]["ports"]["lan_in_PlatformStatus"]
                        effective = provider.datareader_qos_from_profile(lan_input["resolved_profile"])
                        self.assertEqual(float(effective.time_based_filter.minimum_separation),
                                         1 if topic else 0)

    def native_properties(self, properties, teams=()):
        root = Path(__file__).resolve().parents[2]
        binary = Path(os.environ["ACT_RS_BUILD_DIR"]) / "act_rs_config"
        with tempfile.TemporaryDirectory(dir=TEST_WORKDIR) as directory:
            path = Path(directory) / "properties.yaml"
            path.write_text(yaml.safe_dump(properties))
            return subprocess.run([str(binary), "--properties", str(path.resolve()), *teams],
                                  cwd=root, text=True, capture_output=True)

    def test_explicit_constructor_and_configured_ports(self):
        properties = explicit_properties()
        properties.update({
            "act.binding.contact.tx.port": "my_contact_input",
            "act.binding.contact.rx.port": "my_contact_output",
            "act.binding.contact.rx.recipients": "teams",
        })
        result = self.native_properties(properties, ["TEAM_B"])
        self.assertEqual(result.returncode, 0, result.stderr)
        model = yaml.safe_load(result.stdout)
        self.assertEqual(model["inputs"][0]["name"], "my_contact_input")
        self.assertEqual(model["outputs"][0]["name"], "my_contact_output")
        state = next(port for port in model["wan"] if port["name"] == "State")
        self.assertIn("'TEAM_B'", state["parameters"])
        self.assertNotIn("'TEAM_A'", state["parameters"])
        self.assertEqual(model["publication_priorities"]["ContactReport"], 200)

    def test_mission_init_defaults_required_bindings_and_control_invariance(self):
        properties = mission_properties()
        result = self.native_properties(properties)
        self.assertEqual(result.returncode, 0, result.stderr)
        model = yaml.safe_load(result.stdout)
        self.assertEqual(model["receive_profile"], "Init")
        self.assertEqual(model["receive_state"], "Init")
        wan = {port["name"]: port for port in model["wan"]}
        self.assertIn("'Mission'", wan["Event"]["expression"])
        self.assertIn("'Team'", wan["Event"]["expression"])
        for port in model["wan"]:
            for topic in ("PlatformMissionStatus", "ContactReport", "PlatformStatus",
                          "ActPlatformStatusMode", "PlatformData"):
                self.assertNotIn("'" + topic + "'", port["expression"])
        for key in ("act.binding.team.rx.port", "act.binding.mission.tx.port",
                    "act.binding.navigation.rx.port"):
            missing = dict(properties)
            prefix = key.rsplit(".", 1)[0]
            for field in list(missing):
                if field.startswith(prefix + "."):
                    del missing[field]
            result = self.native_properties(missing)
            self.assertNotEqual(result.returncode, 0)
            self.assertRegex(result.stderr, "mission receive policy requires|neither tx.port nor rx.port")
        control = dict(properties, **{"act.role": "control"})
        control["act.receive_policy"] = "mission"
        mission_result = self.native_properties(control)
        control["act.receive_policy"] = "static"
        static_result = self.native_properties(control)
        self.assertEqual(mission_result.returncode, 0, mission_result.stderr)
        self.assertEqual(static_result.returncode, 0, static_result.stderr)
        self.assertEqual(yaml.safe_load(mission_result.stdout)["wan"],
                         yaml.safe_load(static_result.stdout)["wan"])

    def test_explicit_unassigned_platform_protects_identity_and_counts_it_in_team_bound(self):
        for teams in (None, "", ";".join(f"T{i}" for i in range(15)),
                      ";".join(["Boat30", *(f"T{i}" for i in range(15))]),
                      ";".join(f"T{i}" for i in range(16))):
            with self.subTest(teams=teams):
                properties = explicit_properties()
                if teams is None:
                    del properties["act.teams"]
                else:
                    properties["act.teams"] = teams
                result = self.native_properties(properties)
                if teams and len(teams.split(";")) == 16 and "Boat30" not in teams.split(";"):
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("team set exceeds 16 names", result.stderr)
                    continue
                self.assertEqual(result.returncode, 0, result.stderr)
                model = yaml.safe_load(result.stdout)
                wan = next(participant for participant in model["participants"]
                           if participant["name"] == "wan")
                optional = [] if not teams else [team for team in teams.split(";") if team != "Boat30"]
                self.assertEqual(wan["team_partition"], ["Boat30", *optional])
                self.assertEqual(model["properties"]["act.teams"], ";".join(optional))
        properties = explicit_properties()
        properties["act.role"] = "control"
        del properties["act.teams"]
        result = self.native_properties(properties)
        self.assertEqual(result.returncode, 0, result.stderr)
        wan = next(participant for participant in yaml.safe_load(result.stdout)["participants"]
                   if participant["name"] == "wan")
        self.assertEqual(wan["team_partition"], [])

    def test_development_preparation_retains_identity_through_explicit_round_trip(self):
        root = Path(__file__).resolve().parents[2]
        binary = Path(os.environ["ACT_RS_BUILD_DIR"]) / "act_rs_config"
        config = yaml.safe_load((root / "router/config/control-platform.yaml").read_text())
        config["router"]["wan_contract"] = "three_topic_union"
        config["router"]["admin_participant"] = "platform_lan"
        config["types"]["xml"] = str(root / "harness_v2/datamodel/gen/ActTypes.xml")
        config["qos_libraries"] = [str(root / "harness_v2/qos/act_qos_profiles.xml")]
        config["qos_profiles"].update({
            "wan_participant": "ACT_QOS_LIB::wan_three_topic_participant",
            "wan_event": "ACT_QOS_LIB::wan_three_topic_event",
            "wan_contacts": "ACT_QOS_LIB::wan_union_contacts",
            "wan_periodic": "ACT_QOS_LIB::wan_union_periodic",
        })
        with tempfile.TemporaryDirectory(dir=TEST_WORKDIR) as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump(config))
            for teams in ([], [f"T{i}" for i in range(15)],
                          ["Boat30", *(f"T{i}" for i in range(15))],
                          [f"T{i}" for i in range(16)]):
                with self.subTest(teams=teams):
                    result = subprocess.run(
                        [str(binary), str(path), "platform", "Boat30", "rs", *teams],
                        cwd=root, text=True, capture_output=True)
                    if len(teams) == 16 and "Boat30" not in teams:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertRegex(result.stderr, "exceeds? 16 names")
                        continue
                    self.assertEqual(result.returncode, 0, result.stderr)
                    model = yaml.safe_load(result.stdout)
                    expected = ["Boat30", *(team for team in teams if team != "Boat30")]
                    explicit = self.native_properties(model["properties"])
                    self.assertEqual(explicit.returncode, 0, explicit.stderr)
                    for snapshot in (model, yaml.safe_load(explicit.stdout)):
                        wan = next(participant for participant in snapshot["participants"]
                                   if participant["name"] == "platform_wan")
                        self.assertEqual(wan["team_partition"], expected)

    def test_six_topic_union_with_p3_opt_in_and_ipv6_profiles(self):
        root = Path(__file__).resolve().parents[2]
        binary = Path(os.environ["ACT_RS_BUILD_DIR"]) / "act_rs_config"
        config = yaml.safe_load((root / "router/config/control-platform.yaml").read_text())
        config["router"]["wan_contract"] = "six_topic_union"
        config["router"]["admin_participant"] = "platform_lan"
        config["types"]["xml"] = str(root / "harness_v2/datamodel/gen/ActTypes.xml")
        config["qos_libraries"] = [str(root / "harness_v2/qos/act_qos_profiles.xml")]
        config["qos_profiles"].update({
            "wan_participant": "ACT_QOS_LIB::rs_six_topic_participant_v6",
            "wan_event": "ACT_QOS_LIB::wan_three_topic_event",
            "wan_contacts": "ACT_QOS_LIB::wan_union_contacts",
            "wan_periodic": "ACT_QOS_LIB::wan_union_periodic",
        })
        config["routing_service"] = {"topic_priorities": {"PlatformDetailStatus": "low"}}
        with tempfile.TemporaryDirectory(dir=TEST_WORKDIR) as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump(config))
            result = subprocess.run(
                [str(binary), str(path), "platform", "Boat30", "rs", "TEAM_A"],
                cwd=root, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            model = yaml.safe_load(result.stdout)
            self.assertEqual(
                [entry["name"] for entry in model["wan"]],
                ["P1_Event", "P1_Periodic", "P2_Event", "P2_State", "P2_Periodic", "P3_Periodic"])
            self.assertEqual(model["topic_families"]["RouterConfig"], "P1_Event")
            self.assertEqual(model["topic_families"]["RouterHealth"], "P1_Periodic")
            self.assertEqual(model["topic_families"]["Mission"], "P2_Event")
            self.assertEqual(model["topic_families"]["ContactReport"], "P2_State")
            self.assertEqual(model["topic_families"]["PlatformStatus"], "P2_Periodic")
            self.assertEqual(model["topic_families"]["PlatformDetailStatus"], "P3_Periodic")
            self.assertEqual(model["publication_priorities"]["RouterConfig"], 600)
            self.assertEqual(model["publication_priorities"]["RouterHealth"], 600)
            self.assertEqual(model["publication_priorities"]["Mission"], 400)
            self.assertEqual(model["publication_priorities"]["ContactReport"], 250)
            self.assertEqual(model["publication_priorities"]["PlatformStatus"], 200)
            self.assertEqual(model["publication_priorities"]["PlatformDetailStatus"], 100)
            rendered = render(model, str(path), "plugin.so", False)
            preflight(ET.ElementTree(ET.fromstring(rendered)))
            xml_root = ET.fromstring(rendered)
            p1_in = xml_root.find(".//input[@name='wan_in_P1_Event']/content_filter/expression")
            p1_h_in = xml_root.find(".//input[@name='wan_in_P1_Periodic']/content_filter/expression")
            self.assertIn("RouterConfig", p1_in.text)
            self.assertIn("RouterHealth", p1_h_in.text)

            for bad_topic in ("Mission", "ContactReport", "RouterHealth"):
                config["routing_service"] = {"topic_priorities": {bad_topic: "low"}}
                path.write_text(yaml.safe_dump(config))
                bad = subprocess.run(
                    [str(binary), str(path), "platform", "Boat30", "rs", "TEAM_A"],
                    cwd=root, text=True, capture_output=True)
                self.assertNotEqual(bad.returncode, 0)

    def test_explicit_rejects_missing_conflicting_catalog_keys_and_wrong_family(self):
        cases = [
            ("act.types_uri", None, "missing"),
            ("act.version", "2", "version"),
            ("act.binding.contact.type", "OtherType", "catalog"),
            ("act.binding.contact.branch", "other_branch", "catalog"),
            ("act.binding.contact.instance_id", "source", "catalog"),
            ("act.binding.contact.topic_name", "CustomReport", "unexpected DDS keys"),
            ("act.binding.contact.channel", "event_normal", "channel"),
            ("act.binding.contact.tx.copy", "source", "unknown"),
            ("act.monitoring", "yes", "true or false"),
            ("act.receive_policy", "all", "mission or static"),
            ("act.teams", "TEAM_A;bad'name", "apostrophes"),
            ("act.lan_domain", "233", "out-of-range"),
            ("act.binding.contact.tx.recipients", "broadcast", "unknown recipients"),
            ("act.binding.contact.tx.recipients", "addressed", "unsupported"),
        ]
        for key, value, message in cases:
            with self.subTest(key=key):
                properties = explicit_properties()
                if value is None:
                    del properties[key]
                else:
                    properties[key] = value
                result = self.native_properties(properties)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(message, result.stderr)

    def test_monitoring_internal_filters_and_team_updates(self):
        properties = explicit_properties()
        properties["act.monitoring"] = "true"
        properties["act.binding.contact.rx.port"] = "lan_out_ContactReport"
        properties["act.binding.contact.rx.recipients"] = "teams"
        result = self.native_properties(properties, ["TEAM_B"])
        self.assertEqual(result.returncode, 0, result.stderr)
        model = yaml.safe_load(result.stdout)
        wan = {port["name"]: port for port in model["wan"]}
        self.assertIn("RouterConfig", wan["Event"]["expression"])
        self.assertIn("RouterHealth", wan["Periodic"]["expression"])
        self.assertNotIn("RouterHealth", wan["State"]["expression"])
        self.assertIn("'TEAM_B'", wan["State"]["parameters"])

    def test_native_team_update_is_transactional_and_rebuilds_both_directions(self):
        build = Path(os.environ["ACT_RS_BUILD_DIR"]).resolve()
        flags = build / "CMakeFiles/act_rs_config.dir/flags.make"
        link = build / "CMakeFiles/act_rs_config.dir/link.txt"
        if not flags.is_file() or not link.is_file():
            self.skipTest("native API check needs the CMake build metadata")
        properties = explicit_properties()
        properties.update({"act.binding.contact.tx.recipients": "teams",
                           "act.binding.contact.rx.recipients": "teams",
                           "act.binding.contact.rx.port": "lan_out_ContactReport"})
        entries = ",\n".join("{" + json.dumps(key) + "," + json.dumps(value) + "}"
                            for key, value in properties.items())
        mission_entries = ",\n".join("{" + json.dumps(key) + "," + json.dumps(value) + "}"
                                    for key, value in mission_properties().items())
        source = """
#include "GatewayModel.hpp"
#include <algorithm>
int main() {
    act_rs::GatewayModel model(std::map<std::string,std::string>{ENTRIES});
    const auto before = model.properties();
    const auto filter = model.receive_filter("State").expression;
    try { model.update_teams({"GOOD", "bad'name"}); return 1; }
    catch (const std::exception&) {}
    if (model.properties() != before || model.receive_filter("State").expression != filter) return 2;
    try {
        std::vector<std::string> many;
        for (int i = 0; i != 17; ++i) many.push_back("T" + std::to_string(i));
        model.update_teams(many); return 3;
    } catch (const std::exception&) {}
    if (model.properties() != before) return 4;
    model.update_teams({"TEAM_B"});
    for (bool source : {true,false}) {
        const auto& ids = (source ? model.sources : model.destinations).at("ContactReport").front().interest.destination_ids;
        if (std::find(ids.begin(),ids.end(),"TEAM_B") == ids.end()) return 5;
        if (std::find(ids.begin(),ids.end(),"TEAM_A") != ids.end()) return 6;
    }
    if (model.wan.team_partition != std::vector<std::string>{"Boat30","TEAM_B"}) return 7;
    model.update_teams({});
    if (model.wan.team_partition != std::vector<std::string>{"Boat30"}) return 8;
    for (const auto& participant : model.config.participants) {
        if (participant.name == model.wan.name &&
            participant.team_partition != model.wan.team_partition) return 9;
    }
    std::vector<std::string> full;
    for (int i = 0; i != 15; ++i) full.push_back("T" + std::to_string(i));
    model.update_teams(full);
    if (model.wan.team_partition.size() != 16 || model.wan.team_partition.front() != "Boat30") return 10;
    const auto full_properties = model.properties();
    full.push_back("T15");
    try { model.update_teams(full); return 11; }
    catch (const std::exception&) {}
    if (model.properties() != full_properties) return 12;
    auto next = model;
    next.update_teams({"TEAM_NEW"});
    std::map<std::string,router::WanReceiveFilter> applied;
    for (const auto& family : model.families()) applied[family] = model.receive_filter(family);
    const auto old = applied;
    bool failed = false;
    try {
        model.apply_filters(next, [&](const std::string& family, const router::WanReceiveFilter& value) {
            applied[family] = value;
            if (family == "State" && !failed) {
                failed = true;
                throw std::runtime_error("injected update failure after mutation");
            }
        });
        return 13;
    } catch (const std::exception&) {}
    if (model.properties() != full_properties || model.receive_filter_error.empty()) return 14;
    for (const auto& family : model.families()) {
        if (applied[family].expression != old.at(family).expression ||
            applied[family].parameters != old.at(family).parameters) return 15;
    }
    failed = false;
    try {
        model.apply_filters(next, [&](const std::string& family, const router::WanReceiveFilter& value) {
            if (family == "State") {
                if (!failed) { failed = true; throw std::runtime_error("update failed"); }
                throw std::runtime_error("rollback failed");
            }
            applied[family] = value;
        });
        return 16;
    } catch (const std::exception&) {}
    if (model.properties() != full_properties ||
        model.receive_filter_error.find("ROLLBACK FAILED State") == std::string::npos) return 17;
    model.apply_filters(next, [&](const std::string& family, const router::WanReceiveFilter& value) {
        applied[family] = value;
    });
    if (!model.receive_filter_error.empty() ||
        model.wan.team_partition != std::vector<std::string>{"Boat30","TEAM_NEW"}) return 18;
    act_rs::GatewayModel mission(std::map<std::string,std::string>{MISSION_ENTRIES});
    if (mission.receive_profile() != "Init") return 19;
    for (const auto& state : {"TRANSIT","ORBIT","RTB","HOLDING"}) {
        mission.update_receive_state(state);
        if (mission.receive_profile() != "ActiveMission" ||
            mission.receive_filter("Periodic").expression.find("'PlatformMissionStatus'") == std::string::npos ||
            mission.receive_filter("State").expression.find("'ContactReport'") == std::string::npos) return 20;
    }
    for (const auto& state : {"IDLE","COMPLETE","CANCELLED"}) {
        mission.update_receive_state(state);
        if (mission.receive_profile() != "Init" ||
            mission.receive_filter("Periodic").expression.find("'PlatformMissionStatus'") != std::string::npos) return 21;
    }
    const auto state = mission.receive_state;
    try { mission.update_receive_state("UNKNOWN"); return 22; }
    catch (const std::exception&) {}
    if (mission.receive_state != state) return 23;
    mission.update_receive_state("ORBIT");
    mission.update_teams({});
    if (!mission.destinations.at("PlatformStatus").front().interest.global ||
        mission.receive_filter("Periodic").expression.find("'PlatformStatus'") == std::string::npos) return 26;
    for (const auto& topic : {"PlatformMissionStatus","ContactReport"}) {
        if (!mission.destinations.at(topic).front().interest.destination_ids.empty()) return 24;
    }
    act_rs::GatewayModel restarted(mission.properties());
    if (restarted.receive_state != "Init" || restarted.receive_profile() != "Init") return 25;
    const std::string oversized = std::string(254, 'x') + ";";
    try { restarted.update_teams({oversized}); return 27; }
    catch (const std::exception& error) {
        RouterCommandAck ack;
        ack.message = act_rs::diagnostic_message(error.what());
        if (ack.message.size() > 255 || ack.message.find("invalid or duplicate team") != 0) return 28;
        rti::core::xtypes::convert(ack);
        RouterHealth health;
        health.receive_filter_error = ack.message;
        rti::core::xtypes::convert(health);
    }
    try {
        restarted.apply_filters(restarted, [&](const std::string&, const router::WanReceiveFilter&) {
            throw std::runtime_error(std::string(400, 'e'));
        });
        return 29;
    } catch (const std::exception& error) {
        if (std::string(error.what()).size() <= 255) return 30;
    }
    if (restarted.receive_filter_error.size() > 255 ||
        restarted.receive_filter_error.find("ROLLBACK FAILED") != 0) return 31;
    RouterHealth health;
    health.receive_filter_error = restarted.receive_filter_error;
    rti::core::xtypes::convert(health);
    const auto utf8 = act_rs::diagnostic_message(std::string(251, 'x') + "\\xc3\\xa9" + std::string(30, 'y'));
    if (utf8.size() != 254 || utf8.substr(251) != "...") return 32;
    if (act_rs::lan_participant_identity("Boat30", "rs", "lan") != "Boat30/rs/lan") return 33;
    if (act_rs::lan_participant_identity(std::string(252, 'n'), "rs", "lan") != LONG_LAN_IDENTITY) return 34;
    return 0;
}
""".replace("MISSION_ENTRIES", mission_entries).replace("ENTRIES", entries).replace(
            "LONG_LAN_IDENTITY", json.dumps(lan_participant_identity({
                "act.node": "n" * 252, "act.router": "rs", "act.lan_participant": "lan"})))
        compile_flags = []
        for line in flags.read_text().splitlines():
            if line.startswith(("CXX_DEFINES =", "CXX_INCLUDES =", "CXX_FLAGS =")):
                compile_flags.extend(shlex.split(line.split("=", 1)[1]))
        command = shlex.split(link.read_text())
        with tempfile.TemporaryDirectory(dir=TEST_WORKDIR) as directory:
            directory = Path(directory).resolve()
            obj, executable = directory / "check.o", directory / "check"
            environment = dict(os.environ, TMPDIR=str(directory))
            compiled = subprocess.run([command[0], *compile_flags, "-x", "c++", "-c", "-",
                                       "-o", str(obj)], input=source, text=True,
                                      capture_output=True, env=environment)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            command = [str(obj) if item.endswith("ConfigMain.cxx.o") else item for item in command]
            command[command.index("-o") + 1] = str(executable)
            linked = subprocess.run(command, cwd=build, text=True, capture_output=True, env=environment)
            self.assertEqual(linked.returncode, 0, linked.stderr)
            checked = subprocess.run([str(executable)], text=True, capture_output=True, env=environment)
            self.assertEqual(checked.returncode, 0, checked.stderr)


if __name__ == "__main__":
    unittest.main()
