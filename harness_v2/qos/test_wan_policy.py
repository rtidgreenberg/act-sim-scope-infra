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
                reader = provider.datareader_qos_from_profile("ACT_QOS_LIB::wan_union_status")
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
