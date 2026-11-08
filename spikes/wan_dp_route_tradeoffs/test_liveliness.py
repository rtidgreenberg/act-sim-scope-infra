import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import xml.etree.ElementTree as ET

from liveliness import QOS, audit, report, liveliness_frames


class LivelinessTests(unittest.TestCase):
    def test_assertion_fanout_does_not_look_like_faster_cadence(self):
        rows = "\n".join((
            "0.0|146|abc|000200c2|0x15|1",
            "0.001|146|abc|000200c2|0x15|1",
            "10.0|146|abc|000200c2|0x15|2",
            "10.001|146|abc|000200c2|0x15|2",
        ))
        with patch("liveliness.subprocess.run", return_value=SimpleNamespace(stdout=rows)):
            result = liveliness_frames(Path("/unused"), {"abc": "Control"}, 0, 11)["abc"]
        self.assertEqual(result["frames"], 4)
        self.assertEqual(result["whole_frame_bytes"], 584)
        self.assertEqual(result["logical_assertions"], 2)
        self.assertAlmostEqual(result["median_logical_interval_s"], 10)

    def test_only_lease_changes_between_health_profiles(self):
        root = ET.parse(QOS).getroot()
        baseline = root.find(".//qos_profile[@name='lease3']")
        changed = root.find(".//qos_profile[@name='lease30']")
        self.assertEqual(changed.attrib["base_name"], "HealthTiming::lease3")
        for kind in ("datawriter_qos", "datareader_qos"):
            self.assertEqual(baseline.find(f"{kind}/liveliness/lease_duration/sec").text, "3")
            self.assertEqual(changed.find(f"{kind}/liveliness/lease_duration/sec").text, "30")
            self.assertEqual(baseline.find(f"{kind}/deadline/period/sec").text, "2")
            self.assertEqual(len(changed.find(kind)), 1)
        participant = root.find(".//qos_profile[@name='participant']/domain_participant_qos")
        self.assertEqual(participant.find("transport_builtin/mask").text, "UDPv4")
        self.assertEqual(participant.find("discovery_config/participant_liveliness_assert_period/sec").text, "30")

    def test_delivery_audit_requires_eligible_receives_without_duplicates(self):
        sender = {"sent": {1: 0, 2: 10, 3: 12, 4: 13}}
        receiver = {"received": {"2": 10.1, "3": 12.1}, "duplicates": 0}
        self.assertEqual(audit(sender, receiver, 10, 15), 2)
        with self.assertRaises(AssertionError):
            audit(sender, {"received": {"2": 10.1}, "duplicates": 0}, 10, 15)
        with self.assertRaises(AssertionError):
            audit(sender, dict(receiver, duplicates=1), 10, 15)

    def test_report_separates_liveliness_savings_from_total_traffic(self):
        runs = []
        for lease, count in ((3, 60), (30, 6)):
            discovery = count*146*2
            runs.append(dict(
                lease_s=lease, repetition=1, missing=0, duplicates=0, audited_receives=120,
                guid_map={"control": "Control", "platform": "Platform"},
                liveliness={p: dict(median_interval_s=lease/3, whole_frame_bytes=count*146)
                            for p in ("control", "platform")},
                capture=dict(accounting_version=2, windows=dict(steady=dict(
                    duration_s=60, class_bytes=dict(discovery=discovery, data=20000),
                    total_bytes=discovery+20000, per_dp_bytes={"control": discovery+20000},
                    discovery_tx_Bps=discovery/60, total_tx_Bps=(discovery+20000)/60))),
                deadline_after_last_receive_s=2, dead_after_kill_s=lease,
                dead_after_last_liveliness_s=lease,
            ))
        with tempfile.TemporaryDirectory(prefix="health-report-test-") as directory:
            output = Path(directory)/"report"
            result = report(dict(repetitions=1, seconds=60), runs, output)
            self.assertAlmostEqual(result["liveliness_byte_reduction_percent"], 90)
            self.assertEqual(result["audited_receives"], 240)
            self.assertIn("not the percentage saving in all discovery", output.with_suffix(".md").read_text())
            self.assertLess(result["aggregate"]["30"]["total_kbps"]["median"],
                            result["aggregate"]["3"]["total_kbps"]["median"])
        with self.assertRaisesRegex(AssertionError, "incomplete"):
            report(dict(repetitions=1, seconds=60), runs[:1], Path("/unused"))
