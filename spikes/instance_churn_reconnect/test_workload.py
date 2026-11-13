import collections
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from experiment import transient_local_snapshot
from priorities import PRIORITY_MODES, TOPIC_PRIORITY_MODES, topic_priorities
from wire import event_submessages
from workload import (
    STATIONARY_CONTACT_ID, discrepancies, initial_identities, missing_write_sequences, operations, positive_seconds,
    reception_order, tick_seconds,
    routed_presence_errors,
)


class WorkloadTests(unittest.TestCase):
    def test_cross_reader_order_uses_reception_times_not_polling_order(self):
        for first in ("Event", "Contacts"):
            second = "Contacts" if first == "Event" else "Event"
            observations = [(4, second, 2), (1, first, 1),
                            (3, second, 1), (2, first, 2)]
            expected = [(first, 1), (first, 2), (second, 1), (second, 2)]
            for polled in (observations, list(reversed(observations))):
                with self.subTest(first=first, polled=polled):
                    self.assertEqual(reception_order(polled), expected)

    def test_cross_reader_timestamp_ties_are_not_broken_by_polling_order(self):
        observations = [(1, "Event", 1), (1, "Contacts", 2)]
        for polled in (observations, list(reversed(observations))):
            with self.subTest(polled=polled), self.assertRaisesRegex(
                    AssertionError, "ambiguous: tied timestamps"):
                reception_order(polled)
        self.assertEqual(reception_order([(1, "Event", 2), (1, "Event", 1)]),
                         [("Event", 2), ("Event", 1)])

    def test_runner_accepts_overrides_for_all_sample_priority_modes(self):
        from spikes.instance_churn_reconnect import run as runner

        class ParsedArguments(Exception):
            pass

        self.assertEqual(set(TOPIC_PRIORITY_MODES),
                         set(PRIORITY_MODES) - {"production", "paced_fifo"})
        for mode in TOPIC_PRIORITY_MODES:
            with self.subTest(mode=mode), mock.patch.object(
                    sys, "argv", ["run.py", "--family", "Event", "--priority-mode", mode,
                                  "--topic-priority", "ControlCommand=200"]), mock.patch.object(
                    runner, "resolve_wan_policy", side_effect=ParsedArguments):
                with self.assertRaises(ParsedArguments):
                    runner.main()
        for mode in ("production", "paced_fifo"):
            error = io.StringIO()
            with self.subTest(mode=mode), redirect_stderr(error), mock.patch.object(
                    sys, "argv", ["run.py", "--family", "Event", "--priority-mode", mode,
                                  "--topic-priority", "ControlCommand=200"]), mock.patch.object(
                    runner, "resolve_wan_policy") as policy:
                with self.assertRaises(SystemExit) as stopped:
                    runner.main()
                self.assertEqual(stopped.exception.code, 2)
                self.assertIn("--topic-priority requires", error.getvalue())
                policy.assert_not_called()

    def test_worker_accepts_and_propagates_priority_overrides(self):
        try:
            from spikes.instance_churn_reconnect import spike
        except ModuleNotFoundError as error:
            if error.name not in ("rti", "rti.connextdds"):
                raise
            self.skipTest("Connext binding required to import worker; no DDS entities created")
        for mode in PRIORITY_MODES:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(
                    prefix="churn-worker-cli-") as work:
                output, error = io.StringIO(), io.StringIO()
                endpoint = mock.Mock()
                result = {"run_id": "cli-priority", "passed": True, "errors": []}
                with redirect_stdout(output), redirect_stderr(error), mock.patch.object(
                        sys, "argv", ["spike.py", "--output", work, "--control-only",
                                      "--family", "Event", "--priority-mode", mode,
                                      "--topic-priority", "ControlCommand=200"]), mock.patch.object(
                        spike, "run_case", return_value=result):
                    with self.assertRaises(SystemExit) as stopped:
                        spike.main(endpoint_class=endpoint)
                if mode in TOPIC_PRIORITY_MODES:
                    self.assertEqual(stopped.exception.code, 0, error.getvalue())
                    endpoint.assert_called_once_with(
                        "Event", True, "production", mode,
                        {"ControlCommand": 200, "PlatformCommandAck": 100, "ContactReport": 10},
                        "coarse")
                    endpoint.return_value.close.assert_called_once_with()
                    self.assertTrue((Path(work) / "results.json").is_file())
                    self.assertTrue((Path(work) / "junit.xml").is_file())
                else:
                    self.assertEqual(stopped.exception.code, 2)
                    self.assertIn("--topic-priority requires", error.getvalue())
                    endpoint.assert_not_called()

    def test_routed_presence_retention_carries_early_dead_or_removal(self):
        for state in ("DEAD", None):
            trace = [
                {"at": 10, "peers": {"boat": "ALIVE"}},
                {"at": 12, "peers": {"boat": "STALE"}},
                {"at": 14, "peers": {} if state is None else {"boat": state}},
                {"at": 131, "peers": {"boat": "ALIVE"}},
            ]
            with self.subTest(state=state):
                self.assertIn("Peer was not retained STALE inside the system timeout",
                              routed_presence_errors(trace, 10, 130, 300,
                                                     "boat", "STALE", "ALIVE"))

    def test_routed_presence_stale_must_occur_inside_outage(self):
        trace = [
            {"at": 1, "peers": {"boat": "STALE"}},
            {"at": 9, "peers": {"boat": "ALIVE"}},
            {"at": 131, "peers": {"boat": "STALE"}},
            {"at": 132, "peers": {"boat": "ALIVE"}},
        ]
        self.assertIn("Two-topic health did not report STALE during outage",
                      routed_presence_errors(trace, 10, 130, 300,
                                             "boat", "STALE", "ALIVE"))

    def test_routed_presence_valid_interval_and_recovery_boundary(self):
        trace = [
            {"at": 9, "peers": {"boat": "ALIVE"}},
            {"at": 12, "peers": {"boat": "STALE"}},
            {"at": 130, "peers": {"boat": "ALIVE"}},
        ]
        self.assertEqual(routed_presence_errors(trace, 10, 130, 300,
                                               "boat", "STALE", "ALIVE"), [])

    def test_routed_presence_requires_known_state_at_retention_boundary(self):
        trace = [
            {"at": 18, "peers": {"boat": "STALE"}},
            {"at": 131, "peers": {"boat": "ALIVE"}},
        ]
        self.assertIn("Peer was not retained STALE inside the system timeout",
                      routed_presence_errors(trace, 10, 130, 300,
                                             "boat", "STALE", "ALIVE"))
        self.assertEqual(routed_presence_errors([], None, None, 300,
                                               "boat", "STALE", "ALIVE"),
                         ["No completed outage for presence verification"])

    def test_event_history_requires_each_valid_payload_not_just_disposed_final_state(self):
        observations = [
            {"sequence": 1, "valid": True}, {"sequence": 1, "valid": True},
            {"sequence": 2, "valid": False}, {"sequence": 4, "valid": True},
        ]
        self.assertEqual(missing_write_sequences({1, 2, 3}, observations), [2, 3])
        self.assertEqual(missing_write_sequences({1, 4}, observations), [])

    def test_wire_dispose_packing_and_data_session_sequence_identity(self):
        def submessage(kind, sequence, disposed=False, writer="80000102", endian="little"):
            inline_offset = 24 if kind == 0x14 else 16
            number = b"\0" * 4 + sequence.to_bytes(4, endian)
            body = (b"\0\0" + inline_offset.to_bytes(2, endian) + b"\0" * 4
                    + bytes.fromhex(writer) + number)
            if kind == 0x14:
                body += number
            flags = int(endian == "little")
            if disposed:
                flags |= 2
                body += ((0x71).to_bytes(2, endian) + (4).to_bytes(2, endian)
                         + b"\0\0\0\1" + (1).to_bytes(2, endian) + b"\0\0")
            return bytes((kind, flags)) + len(body).to_bytes(2, endian) + body

        packet = (b"RTPS" + b"\0" * 16 + submessage(0x14, 1, True)
                  + submessage(0x14, 2, True)
                  + submessage(0x15, 3, writer="80002102"))
        decoded = event_submessages(packet, "80002102")
        self.assertEqual(len(decoded), 2)
        self.assertTrue(all(disposed for _, _, disposed in decoded))
        self.assertEqual(len({sequence for sequence, _, _ in decoded}), 2)
        big_endian = b"RTPS" + b"\0" * 16 + submessage(0x15, 4, True, endian="big")
        self.assertTrue(event_submessages(big_endian, "80002102")[0][2])
        self.assertEqual(event_submessages(b"not RTPS", "80002102"), [])

    def test_fine_and_burst_profiles_keep_the_same_average_token_budget(self):
        xml = ET.parse(Path(__file__).with_name("qos.xml"))
        fine = xml.find("./qos_library/qos_profile[@name='priority_participant_fine']")
        properties = {
            entry.findtext("name").split(".")[-1]: int(entry.findtext("value"))
            for entry in fine.findall("./domain_participant_qos/property/value/element")
        }
        self.assertEqual(properties["bytes_per_token"], 1024)
        self.assertEqual(properties["tokens_added_per_period"], 5)
        rate = properties["bytes_per_token"] * properties["tokens_added_per_period"]
        self.assertEqual(rate * 1_000_000_000 / properties["nanosec"], 250_000)
        burst = xml.find("./qos_library/qos_profile[@name='priority_participant_burst']")
        self.assertEqual(burst.attrib["base_name"], "ChurnSpike::priority_participant_fine")
        entries = burst.findall("./domain_participant_qos/property/value/element")
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].findtext("name").endswith(".max_tokens"))
        self.assertEqual(entries[0].findtext("value"), "50")

    def test_logical_topic_priority_mapping(self):
        priorities = topic_priorities(["ControlCommand=500", "ContactReport=5"])
        self.assertEqual(priorities, {
            "ControlCommand": 500, "PlatformCommandAck": 100, "ContactReport": 5})
        for value in ("Unknown=10", "ContactReport=0", "ContactReport=-1",
                      "ContactReport=nan", "ContactReport=2147483648", "ControlCommand",
                      "ContactReport=١", "ContactReport=1.5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                topic_priorities([value])
        self.assertEqual(topic_priorities([])["ControlCommand"], 100)
        self.assertEqual(topic_priorities(["ContactReport=2147483647"])["ContactReport"],
                         2_147_483_647)

    def test_larger_repair_profile_changes_only_response_limit(self):
        xml = ET.parse(Path(__file__).with_name("qos.xml"))
        profile = xml.find("./qos_library/qos_profile[@name='priority_bands_repair']")
        self.assertEqual(profile.attrib["base_name"], "ChurnSpike::priority_bands")
        writer = profile.find("datawriter_qos")
        self.assertEqual([child.tag for child in writer], ["protocol"])
        protocol = writer.find("protocol")
        self.assertEqual([child.tag for child in protocol], ["rtps_reliable_writer"])
        repair = protocol.find("rtps_reliable_writer")
        self.assertEqual([child.tag for child in repair], ["max_bytes_per_nack_response"])
        self.assertEqual(repair.findtext("max_bytes_per_nack_response"), "524288")

    def test_keep_last_one_profile_changes_only_writer_history(self):
        xml = ET.parse(Path(__file__).with_name("qos.xml"))
        profile = xml.find("./qos_library/qos_profile[@name='priority_bands_last1']")
        self.assertEqual(profile.attrib["base_name"], "ChurnSpike::priority_bands_repair")
        writer = profile.find("datawriter_qos")
        self.assertEqual([child.tag for child in writer], ["history"])
        self.assertEqual(writer.findtext("history/kind"), "KEEP_LAST_HISTORY_QOS")
        self.assertEqual(writer.findtext("history/depth"), "1")

    def test_batch_profile_preserves_priority_bands_and_timestamps(self):
        xml = ET.parse(Path(__file__).with_name("qos.xml"))
        profile = xml.find("./qos_library/qos_profile[@name='priority_bands_batch']")
        self.assertEqual(profile.attrib["base_name"], "ChurnSpike::priority_bands")
        batch = profile.find("./datawriter_qos/batch")
        self.assertEqual(batch.findtext("enable"), "true")
        self.assertEqual(batch.findtext("max_samples"), "4")
        self.assertEqual(batch.findtext("max_data_bytes"), "2048")
        self.assertEqual(batch.findtext("max_flush_delay/nanosec"), "10000000")
        self.assertEqual(batch.findtext("source_timestamp_resolution/sec"), "0")
        self.assertEqual(batch.findtext("source_timestamp_resolution/nanosec"), "0")
        self.assertEqual([child.tag for child in profile.find("datawriter_qos")], ["batch"])

    def test_priority_profiles_keep_one_event_writer_and_all_logical_topics(self):
        xml = ET.parse(Path(__file__).with_name("qos.xml"))
        profile = xml.find("./qos_library/qos_profile[@name='priority_event']")
        self.assertEqual(profile.attrib["base_name"], "ACT_QOS_LIB::wan_event")
        self.assertEqual(profile.findtext("./datawriter_qos/publish_mode/kind"),
                         "ASYNCHRONOUS_PUBLISH_MODE_QOS")
        self.assertEqual(profile.findtext("./datawriter_qos/publish_mode/priority"), "-1")
        channels = xml.findall(
            "./qos_library/qos_profile[@name='priority_channels']"
            "/datawriter_qos/multi_channel/channels/element")
        self.assertEqual(len(channels), 3)
        self.assertTrue(all(channel.findtext("priority") == "-1" for channel in channels))
        expressions = " ".join(channel.findtext("filter_expression") for channel in channels)
        for topic in topic_priorities([]):
            self.assertEqual(expressions.count(f"'{topic}'"), 1)

    def test_outage_duration_is_finite_and_positive(self):
        self.assertEqual(positive_seconds("120"), 120)
        for value in ("0", "-2", "nan", "inf", "not-a-number"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                positive_seconds(value)

    def test_priority_bands_cover_all_valid_priorities_without_topic_names(self):
        xml = ET.parse(Path(__file__).with_name("qos.xml"))
        profile = xml.find("./qos_library/qos_profile[@name='priority_bands']")
        self.assertEqual(profile.attrib["base_name"], "ChurnSpike::priority_event")
        policy = profile.find("./datawriter_qos/multi_channel")
        self.assertEqual(policy.findtext("filter_name"), "DDSPRIFLTR")
        channels = policy.findall("./channels/element")
        self.assertEqual([channel.findtext("filter_expression") for channel in channels],
                         ["@priority >= 100", "@priority < 100"])
        self.assertTrue(all(channel.findtext("priority") == "-1" for channel in channels))
        self.assertNotIn("original_topic", ET.tostring(policy, encoding="unicode"))

    def test_two_minute_churn_does_not_touch_stationary_contact(self):
        self.assertNotIn(STATIONARY_CONTACT_ID, initial_identities("steady"))
        counts = collections.Counter()
        for tick in range(2400):
            for item in operations(tick):
                self.assertNotEqual(item.identity, STATIONARY_CONTACT_ID)
                counts[item.kind] += 1
        self.assertEqual(counts, {"create": 2400, "update": 7200, "dispose": 2400})

    def test_transient_local_changes_only_private_event_durability(self):
        root = Path(__file__).resolve().parents[2]
        relative = Path("harness_v2/qos/act_qos_profiles.xml")
        original_bytes = (root / relative).read_bytes()
        with tempfile.TemporaryDirectory(prefix="churn-qos-unit-") as directory:
            snapshot = Path(directory)
            for name in (relative, Path("spikes/instance_churn_reconnect/qos.xml")):
                target = snapshot / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / name, target)
            before = ET.parse(snapshot / relative)
            transient_local_snapshot(snapshot)
            after = ET.parse(snapshot / relative)
            for old_profile in before.findall("./qos_library/qos_profile"):
                new_profile = after.find(
                    f"./qos_library/qos_profile[@name='{old_profile.attrib['name']}']")
                if old_profile.attrib["name"] != "wan_event":
                    self.assertEqual(ET.tostring(old_profile), ET.tostring(new_profile))
                    continue
                for endpoint in ("datawriter_qos", "datareader_qos"):
                    self.assertEqual(new_profile.findtext(f"{endpoint}/durability/kind"),
                                     "TRANSIENT_LOCAL_DURABILITY_QOS")
                    old = old_profile.find(endpoint)
                    new = new_profile.find(endpoint)
                    self.assertEqual(
                        [ET.tostring(child) for child in old if child.tag != "durability"],
                        [ET.tostring(child) for child in new if child.tag != "durability"])
            self.assertEqual((root / relative).read_bytes(), original_bytes)

    def test_exact_main_rate_and_two_second_outage_counts(self):
        counts = collections.Counter(
            item.kind for tick in range(40) for item in operations(tick))
        self.assertEqual(counts, {"create": 40, "update": 120, "dispose": 40})
        self.assertEqual(40 * tick_seconds("steady"), 2)

    def test_lifetime_population_and_update_cadence(self):
        live = set(initial_identities("steady"))
        writes = collections.defaultdict(list)
        for tick in range(400):
            for item in operations(tick):
                if item.kind == "dispose":
                    self.assertIn(item.identity, live)
                    live.remove(item.identity)
                    if item.identity >= 0:
                        self.assertEqual(tick - item.identity, 160)
                elif item.kind == "create":
                    self.assertNotIn(item.identity, live)
                    live.add(item.identity)
                    writes[item.identity].append(tick)
                else:
                    self.assertIn(item.identity, live)
                    if item.identity >= 0:
                        writes[item.identity].append(tick)
            self.assertEqual(len(live), 160)
        self.assertEqual(writes[0], [0, 40, 80, 120])

    def test_short_lived_rate_and_lifetime(self):
        live = set(initial_identities("short"))
        counts = collections.Counter()
        for tick in range(20):
            for item in operations(tick, "short"):
                counts[item.kind] += 1
                if item.kind == "dispose":
                    live.remove(item.identity)
                    self.assertEqual(tick * 5 - (item.identity - item.identity % 5), 50)
                else:
                    live.add(item.identity)
            self.assertEqual(len(live), 50)
        self.assertEqual(counts, {"create": 100, "dispose": 100})
        self.assertEqual(20 * tick_seconds("short"), 2)

    def test_revival_reuses_only_disposed_keys(self):
        live = set(initial_identities("revival"))
        disposed = set()
        revivals = 0
        for tick in range(700):
            for item in operations(tick, "revival"):
                if item.kind == "dispose":
                    live.remove(item.identity)
                    disposed.add(item.identity)
                elif item.kind == "create":
                    self.assertNotIn(item.identity, live)
                    revivals += item.identity in disposed
                    live.add(item.identity)
                else:
                    self.assertIn(item.identity, live)
        self.assertGreater(revivals, 0)

    def test_absent_never_seen_disposed_instance_is_not_missing(self):
        self.assertEqual(discrepancies(
            {"x": {"state": "disposed", "sequence": 1}}, {}),
            {"ghosts": [], "missing": [], "stale": [], "unexpected": []})

    def test_distinguishes_ghost_missing_and_stale(self):
        expected = {
            "ghost": {"state": "disposed", "sequence": 1},
            "missing": {"state": "alive", "sequence": 2},
            "stale": {"state": "alive", "sequence": 3},
        }
        actual = {
            "ghost": {"state": "alive", "sequence": 1},
            "stale": {"state": "alive", "sequence": 2},
        }
        self.assertEqual(discrepancies(expected, actual),
                         {"ghosts": ["ghost"], "missing": ["missing"],
                          "stale": ["stale"], "unexpected": []})


if __name__ == "__main__":
    unittest.main()
