import collections
from pathlib import Path
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

from experiment import transient_local_snapshot
from workload import (
    STATIONARY_CONTACT_ID, discrepancies, initial_identities, operations, positive_seconds,
    tick_seconds,
)


class WorkloadTests(unittest.TestCase):
    def test_outage_duration_is_finite_and_positive(self):
        self.assertEqual(positive_seconds("120"), 120)
        for value in ("0", "-2", "nan", "inf", "not-a-number"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                positive_seconds(value)

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
