"""Native WAN reader qualification; run only in an owned network namespace."""

from contextlib import ExitStack
import itertools
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET

import rti.connextdds as dds


ROOT = Path(__file__).resolve().parents[2]
DOMAINS = itertools.count(201)
NAVIGATION = "PlatformInitStatus"
MISSION = "PlatformMissionStatus"
KEYS = (
    ("Boat30", NAVIGATION, "same"),
    ("Boat30", MISSION, "same"),
    ("Boat31", NAVIGATION, "same"),
    ("Boat30", NAVIGATION, "other"),
)


def wait_until(predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Native endpoints did not match within 10 seconds")


@unittest.skipUnless(os.environ.get("ACT_RS_TBF_ISOLATED") == "1",
                     "requires an owned private network namespace")
class WanTimeFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix="act-rs-tbf-")
        cls.addClassCleanup(cls.work.cleanup)
        root = ET.parse(ROOT / "harness_v2/qos/act_qos_profiles.xml").getroot()
        fixture = ET.parse(Path(__file__).parent / "fixtures/wan_time_filter.xml").getroot()
        root.extend(fixture.findall("qos_library"))
        path = Path(cls.work.name) / "qos.xml"
        ET.ElementTree(root).write(path)
        cls.qos = dds.QosProvider(str(path))
        cls.types = dds.QosProvider(str(ROOT / "harness_v2/datamodel/gen/ActTypes.xml"))

    def setUp(self):
        self.cleanup = ExitStack()
        self.addCleanup(self.cleanup.close)
        domain = next(DOMAINS)
        self.assertLessEqual(domain, 232)
        qos = self.qos.participant_qos_from_profile("ACT_QOS_LIB::wan_two_topic_participant")
        self.assertEqual(qos.transport_builtin.mask, dds.TransportBuiltinMask.UDPv4)
        self.participants = []
        for _ in range(2):
            participant = dds.DomainParticipant(domain, qos)
            self.participants.append(participant)
            self.cleanup.callback(participant.close)
        self.topics = [dds.DynamicData.Topic(p, "Status", self.types.type("WanStatus"))
                       for p in self.participants]
        self.publisher = dds.Publisher(self.participants[0])
        self.subscriber = dds.Subscriber(self.participants[1])
        self.writer = dds.DynamicData.DataWriter(
            self.publisher, self.topics[0],
            self.qos.datawriter_qos_from_profile("ACT_QOS_LIB::wan_union_status"))
        self.cleanup.callback(self.writer.close)
        self.readers = []

    def reader(self, profile, original_topic=None):
        topic = self.topics[1]
        if original_topic is not None:
            topic = dds.DynamicData.ContentFilteredTopic(
                topic, f"scope_{len(self.readers)}",
                dds.Filter("original_topic = %0", [f"'{original_topic}'"]))
            self.cleanup.callback(topic.close)
        qos = self.qos.datareader_qos_from_profile("ACT_RS_TBF_TEST::" + profile)
        reader = dds.DynamicData.DataReader(self.subscriber, topic, qos)
        self.cleanup.callback(reader.close)
        self.readers.append(reader)
        return reader

    def exercise(self, keys, duration):
        wait_until(lambda: self.writer.publication_matched_status.current_count == len(self.readers))
        for reader in self.readers:
            wait_until(lambda: reader.subscription_matched_status.current_count == 1)
        samples = []
        for source, original_topic, instance in keys:
            sample = dds.DynamicData(self.types.type("WanStatus"))
            sample["source_id"] = source
            sample["original_topic"] = original_topic
            sample["instance_id"] = instance
            sample["schema_version"] = 3
            sample["source_role"] = "platform"
            sample["session_id"] = 1
            with sample.loan_value("payload") as payload:
                branch = "navigation" if original_topic == NAVIGATION else "mission"
                with payload.data.loan_value(branch) as value:
                    value.data["source"] = source
            samples.append(sample)
        received = [{} for _ in self.readers]
        handles = [{} for _ in self.readers]

        def drain():
            for index, reader in enumerate(self.readers):
                for data, info in reader.take():
                    if info.valid:
                        key = tuple(str(data[field]) for field in
                                    ("source_id", "original_topic", "instance_id"))
                        received[index].setdefault(key, []).append(int(data["sent_at_ns"]) / 1e9)
                        handles[index][key] = str(info.instance_handle)

        initial = int(self.writer.datawriter_protocol_status.pushed_sample_count.total)
        start = time.monotonic()
        next_publish = start
        ticks = 0
        while time.monotonic() - start < duration:
            now = time.monotonic()
            if now >= next_publish:
                next_publish = now + 0.02
                ticks += 1
                for sample in samples:
                    sample["audit_sequence"] = ticks
                    sample["sent_at_ns"] = time.time_ns()
                    self.writer.write(sample)
            drain()
            time.sleep(0.001)
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            drain()
            time.sleep(0.005)
        pushed = int(self.writer.datawriter_protocol_status.pushed_sample_count.total) - initial
        summary = {
            "test": self.id().split(".")[-1], "ticks_per_key": ticks, "writer_pushed": pushed,
            "readers": [
                {"minimum_separation_sec": reader.qos.time_based_filter.minimum_separation.sec,
                 "counts": {"/".join(key): len(values) for key, values in stream.items()}}
                for reader, stream in zip(self.readers, received)
            ],
        }
        print(json.dumps(summary, sort_keys=True), flush=True)
        for stream, instances in zip(received, handles):
            self.assertEqual(len(set(instances.values())), len(stream),
                             "Every complete key must identify a distinct DDS instance")
        return received, ticks, pushed

    def check_period(self, stream, keys, separation, minimum, maximum):
        self.assertEqual(set(stream), set(keys))
        for key, times in stream.items():
            self.assertGreaterEqual(len(times), minimum, key)
            self.assertLessEqual(len(times), maximum, key)
            for earlier, later in zip(times, times[1:]):
                self.assertGreaterEqual(later - earlier, separation - 0.03, key)

    def test_unfiltered_control_receives_high_rate(self):
        self.reader("unfiltered")
        streams, ticks, pushed = self.exercise(KEYS, 1.3)
        self.assertGreaterEqual(ticks, 40)
        self.check_period(streams[0], KEYS, 0, int(ticks * 0.95), ticks)
        self.assertEqual(pushed, ticks * len(KEYS))

    def test_one_reader_has_independent_full_key_timers_but_one_period(self):
        reader = self.reader("one_second")
        policy = reader.qos.time_based_filter
        self.assertEqual(policy.minimum_separation, dds.Duration(1))
        self.assertEqual([name for name in dir(type(policy)) if not name.startswith("_")],
                         ["minimum_separation"])
        streams, ticks, pushed = self.exercise(KEYS, 3.3)
        self.check_period(streams[0], KEYS, 1, 3, 4)
        self.assertEqual(pushed, ticks * len(KEYS),
                         "Keyed WAN TBF must not be reported as writer-side throttling")

    def test_separate_cft_readers_can_use_different_original_topic_rates(self):
        self.reader("one_second", NAVIGATION)
        self.reader("two_seconds", MISSION)
        keys = KEYS[:2]
        streams, ticks, pushed = self.exercise(keys, 4.3)
        self.check_period(streams[0], [keys[0]], 1, 4, 5)
        self.check_period(streams[1], [keys[1]], 2, 2, 3)
        self.assertEqual(pushed, ticks * len(keys))


if __name__ == "__main__":
    if os.environ.get("ACT_RS_TBF_ISOLATED") != "1":
        raise SystemExit("Run in a private network namespace with ACT_RS_TBF_ISOLATED=1")
    unittest.main(verbosity=2)
