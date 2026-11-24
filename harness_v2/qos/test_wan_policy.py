import os
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from harness_v2.qos.wan_policy import resolve_wan_policy


class WanPolicyTests(unittest.TestCase):
    def test_static_xml_defaults_match_resolver(self):
        variables = ET.parse(Path(__file__).with_name("act_qos_profiles.xml")).getroot().find(
            "configuration_variables")
        defaults = {item.findtext("name"): item.findtext("value") for item in variables}
        for key, value in resolve_wan_policy({}).items():
            self.assertEqual(defaults[key], value)

    def test_defaults_and_ceiling(self):
        self.assertEqual(resolve_wan_policy({}), {
            "WAN_TIMEOUT_SEC": "300", "WAN_HB_PERIOD_SEC": "3", "WAN_HB_RETRIES": "100"})
        self.assertEqual(resolve_wan_policy({"WAN_TIMEOUT_SEC": "301"})["WAN_HB_RETRIES"], "101")
        self.assertEqual(resolve_wan_policy({
            "WAN_TIMEOUT_SEC": "300", "WAN_HB_PERIOD_SEC": "7"})["WAN_HB_RETRIES"], "43")
        self.assertEqual(resolve_wan_policy({
            "WAN_TIMEOUT_SEC": "32", "WAN_HB_RETRIES": "11"})["WAN_HB_RETRIES"], "11")

    def test_invalid_or_conflicting_settings_fail(self):
        for name in ("WAN_TIMEOUT_SEC", "WAN_HB_PERIOD_SEC", "WAN_HB_RETRIES"):
            for value in ("", "0", "-1", "nan", "inf", "3.5", " 3", "\u0663",
                          "2147483648", "9" * 100):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    resolve_wan_policy({name: value})
        with self.assertRaisesRegex(ValueError, "derived"):
            resolve_wan_policy({"WAN_HB_RETRIES": "10"})


class NativeWanPolicyTests(unittest.TestCase):
    def test_wan_discovery_responses_span_one_second(self):
        try:
            import rti.connextdds as dds
        except ModuleNotFoundError as error:
            if error.name not in ("rti", "rti.connextdds"):
                raise
            self.skipTest("Installed Connext binding required")
        provider = dds.QosProvider(str(Path(__file__).with_name("act_qos_profiles.xml")))
        for profile in (
                "wan_router_participant", "wan_router_participant_emane",
                "wan_two_topic_participant", "wan_two_topic_participant_emane",
                "wan_three_topic_participant", "wan_three_topic_participant_emane",
                "rs_six_topic_participant_v6", "rs_six_topic_participant_emane_v6"):
            qos = provider.participant_qos_from_profile("ACT_QOS_LIB::" + profile)
            effective = ET.fromstring(qos.to_string(print_all=True))
            for name in ("publication_reader", "subscription_reader", "participant_configuration_reader"):
                with self.subTest(profile=profile, reader=name):
                    reader = effective.find("./discovery_config/" + name)
                    for field, seconds in (("min_heartbeat_response_delay", "0"),
                                           ("max_heartbeat_response_delay", "1")):
                        self.assertEqual(reader.findtext(field + "/sec"), seconds)
                        self.assertEqual(reader.findtext(field + "/nanosec"), "0")

    def test_wan_readers_inherit_preemptive_nack_suppression(self):
        try:
            import rti.connextdds as dds
        except ModuleNotFoundError as error:
            if error.name not in ("rti", "rti.connextdds"):
                raise
            self.skipTest("Installed Connext binding required")
        provider = dds.QosProvider(str(Path(__file__).with_name("act_qos_profiles.xml")))
        for profile in (
                "wan_event", "wan_three_topic_event",
                "wan_union_contacts", "rs_wan_event",
                "rs_three_topic_event", "rs_three_topic_state",
                "rs_six_topic_p1_event",
                "rs_six_topic_p2_event", "rs_six_topic_p2_state",
        ):
            with self.subTest(profile=profile):
                reader = provider.datareader_qos_from_profile("ACT_QOS_LIB::" + profile)
                self.assertEqual(
                    reader.property.get("dds.data_reader.protocol.disable_preemptive_nack"),
                    "true")
                self.assertFalse(reader.protocol.disable_positive_acks)
        for profile in ("lan_event", "lan_status", "wan_periodic", "wan_union_periodic",
                        "rs_wan_periodic", "rs_three_topic_periodic",
                        "rs_six_topic_p1_periodic", "rs_six_topic_p2_periodic",
                        "rs_six_topic_p3_periodic"):
            with self.subTest(profile=profile):
                reader = provider.datareader_qos_from_profile("ACT_QOS_LIB::" + profile)
                self.assertNotIn(
                    "dds.data_reader.protocol.disable_preemptive_nack",
                    dict(reader.property.get_all()))

    def test_periodic_profiles_remain_best_effort_without_preemptive_nacks(self):
        try:
            import rti.connextdds as dds
        except ModuleNotFoundError as error:
            if error.name not in ("rti", "rti.connextdds"):
                raise
            self.skipTest("Installed Connext binding required")
        provider = dds.QosProvider(str(Path(__file__).with_name("act_qos_profiles.xml")))
        for profile in ("wan_periodic", "wan_union_periodic", "rs_wan_periodic",
                        "rs_three_topic_periodic", "rs_six_topic_p1_periodic",
                        "rs_six_topic_p2_periodic", "rs_six_topic_p3_periodic"):
            with self.subTest(profile=profile):
                reader = provider.datareader_qos_from_profile("ACT_QOS_LIB::" + profile)
                writer = provider.datawriter_qos_from_profile("ACT_QOS_LIB::" + profile)
                self.assertEqual(reader.reliability.kind, dds.ReliabilityKind.BEST_EFFORT)
                self.assertEqual(writer.reliability.kind, dds.ReliabilityKind.BEST_EFFORT)
                self.assertFalse(reader.property.exists(
                    "dds.data_reader.protocol.disable_preemptive_nack"))

    def test_effective_xml_defaults_and_custom_policy(self):
        try:
            import rti.connextdds as dds
        except ModuleNotFoundError as error:
            if error.name not in ("rti", "rti.connextdds"):
                raise
            self.skipTest("Installed Connext binding required")
        for requested in ({}, {"WAN_TIMEOUT_SEC": "301", "WAN_HB_PERIOD_SEC": "7"}):
            policy = resolve_wan_policy(requested)
            with self.subTest(policy=policy), patch.dict(os.environ, policy):
                provider = dds.QosProvider(str(Path(__file__).with_name("act_qos_profiles.xml")))
                writer = provider.datawriter_qos_from_profile("ACT_QOS_LIB::wan_event")
                protocol = writer.protocol.rtps_reliable_writer
                self.assertEqual(protocol.max_heartbeat_retries, int(policy["WAN_HB_RETRIES"]))
                self.assertEqual(float(protocol.heartbeat_period), int(policy["WAN_HB_PERIOD_SEC"]))
                self.assertEqual(writer.liveliness.lease_duration, dds.Duration.infinite)
                self.assertEqual(writer.durability.kind, dds.DurabilityKind.VOLATILE)
                reader = provider.datareader_qos_from_profile("ACT_QOS_LIB::wan_union_periodic")
                self.assertEqual(reader.property.get("act.presence.dead_timeout_sec"),
                                 policy["WAN_TIMEOUT_SEC"])
                self.assertEqual(float(reader.deadline.period), 2)
                for profile in ("wan_two_topic_participant", "wan_two_topic_participant_emane"):
                    participant = provider.participant_qos_from_profile("ACT_QOS_LIB::" + profile)
                    self.assertEqual(
                        float(participant.discovery_config.participant_liveliness_lease_duration),
                        int(policy["WAN_TIMEOUT_SEC"]))


if __name__ == "__main__":
    unittest.main()
