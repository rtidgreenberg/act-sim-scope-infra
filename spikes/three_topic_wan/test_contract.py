from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from topics import TOPIC_CLASSES, topic_class
from repair_trace import repair_requests


class ContractTests(unittest.TestCase):
    def test_acknack_trace_handles_endianness_and_excludes_empty_and_builtin_requests(self):
        def packet(endian, bits=256, writer="80004202", bitmap=None):
            if bitmap is None:
                bitmap = b"\xff" * (4 * ((bits + 31) // 32))
            body = (b"\0" * 4 + bytes.fromhex(writer)
                    + (0).to_bytes(4, endian) + (123).to_bytes(4, endian)
                    + bits.to_bytes(4, endian) + bitmap + b"\0" * 4)
            return (b"RTPS" + b"\0" * 16 + bytes((6, int(endian == "little")))
                    + len(body).to_bytes(2, endian) + body)
        for endian in ("little", "big"):
            self.assertEqual(repair_requests(packet(endian)), [("80004202", 256, 123)])
            self.assertEqual(repair_requests(packet(endian, bits=0)), [])
            self.assertEqual(repair_requests(packet(endian, writer="000003c2")), [])
            self.assertEqual(repair_requests(packet(endian, bitmap=b"\0" * 32)), [])
            self.assertEqual(repair_requests(packet(endian, bits=1, bitmap=(1).to_bytes(4, endian))), [])
            self.assertEqual(repair_requests(packet(
                endian, bits=1, bitmap=(1 << 31).to_bytes(4, endian))), [("80004202", 1, 123)])
        with self.assertRaisesRegex(ValueError, "Unexpected standard ACKNACK"):
            repair_requests(packet("little", bits=257))

    def test_suppression_candidate_changes_only_one_reader_protocol_field(self):
        xml = ET.parse(Path(__file__).with_name("qos.xml"))
        profile = xml.find("./qos_library/qos_profile[@name='contact_reader_no_suppression']")
        self.assertEqual(profile.attrib["base_name"], "ChurnSpike::event_reader")
        reader = profile.find("datareader_qos")
        self.assertEqual([x.tag for x in reader], ["protocol"])
        protocol = reader.find("protocol/rtps_reliable_reader")
        self.assertEqual([x.tag for x in protocol], ["heartbeat_suppression_duration"])
        self.assertEqual(protocol.findtext("heartbeat_suppression_duration/sec"), "0")
        self.assertEqual(protocol.findtext("heartbeat_suppression_duration/nanosec"), "0")

    def test_exact_three_class_mapping(self):
        self.assertEqual(TOPIC_CLASSES, {
            "ContactReport": "Contacts", "Mission": "Event",
            "PlatformCommandAck": "Event", "RouterHealth": "Status"})
        self.assertEqual({topic_class(topic) for topic in TOPIC_CLASSES}, {"Contacts", "Event", "Status"})
        with self.assertRaisesRegex(ValueError, "Unmapped prototype topic"):
            topic_class("TeamAssignment")

    def test_no_multichannel_and_explicit_reliable_history_split(self):
        xml = ET.parse(Path(__file__).with_name("qos.xml"))
        self.assertEqual(xml.findall(".//multi_channel"), [])
        events = xml.find("./qos_library/qos_profile[@name='events']")
        self.assertEqual(events.attrib["base_name"], "ChurnSpike::priority_event")
        self.assertEqual(events.findtext("./datawriter_qos/history/kind"), "KEEP_ALL_HISTORY_QOS")
        self.assertEqual(events.findtext(
            "./datawriter_qos/protocol/rtps_reliable_writer/max_bytes_per_nack_response"), "524288")
        contacts = xml.find("./qos_library/qos_profile[@name='contacts']")
        self.assertEqual(contacts.attrib["base_name"], "ThreeTopic::events")
        self.assertEqual([child.tag for child in contacts.find("datawriter_qos")], ["history"])
        for name, entity in (("contacts", "datawriter_qos"), ("contact_reader", "datareader_qos")):
            profile = xml.find(f"./qos_library/qos_profile[@name='{name}']")
            self.assertEqual(profile.findtext(f"./{entity}/history/kind"), "KEEP_LAST_HISTORY_QOS")
            self.assertEqual(profile.findtext(f"./{entity}/history/depth"), "1")


if __name__ == "__main__":
    unittest.main()
