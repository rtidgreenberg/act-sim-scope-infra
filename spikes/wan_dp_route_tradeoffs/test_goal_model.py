from contextlib import redirect_stderr
import io
import json
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from goal_model import (SQUAD_KINDS, datamodel, initial_membership, membership_groups,
                        reassignment_membership, swap_membership, team_kinds)
from scale import (STABLE, audit, goal_requirements, recipients, validate_metadata_scope)
from schema_profile import generate, profiles
from run_scale import main as scale_main


class GoalModelTests(unittest.TestCase):
    def test_scopes_and_affected_endpoints(self):
        for count in (3, 6):
            model = datamodel(count)
            self.assertEqual(model["squad_topics"], ["Pose", "Velocity", "Speed"])
            self.assertEqual(len(model["team_topics"]), count)
            self.assertEqual(model["team_endpoints_per_platform"], 2 * count)
            self.assertEqual(len(model["squad_topics"] + model["team_topics"] + model["c2_topics"]), 14)
            for kind in SQUAD_KINDS:
                self.assertEqual(recipients(1, kind, 32, 4, count), [0] + list(range(2, 33)))
            for kind in team_kinds(count):
                self.assertEqual(recipients(1, kind, 32, 4, count), [0, 2, 3, 4])
                self.assertEqual(recipients(1, kind, 32, 4, count, assigned=False), [0])
            self.assertEqual(recipients(1, 5, 32, 4, count), [0])

    def test_swap_and_mixing_reassignment_preserve_team_sizes(self):
        for population, size in ((6, 2), (32, 4), (32, 8)):
            before = initial_membership(population, size)
            swapped = swap_membership(before)
            self.assertEqual(sum(before[n] != swapped[n] for n in before), 2)
            final = reassignment_membership(swapped)
            self.assertEqual(set(final), set(before))
            self.assertTrue(all(final[n] != swapped[n] for n in before))
            self.assertEqual({len(g) for g in membership_groups(final).values()}, {size})
            old_sets = {frozenset(g) for g in membership_groups(swapped).values()}
            self.assertTrue(all(frozenset(g) not in old_sets
                                for g in membership_groups(final).values()))
            self.assertEqual(recipients(1, 12, population, size, 6, final),
                             [0] + [n for n in range(1, population + 1)
                                    if n != 1 and final[n] == final[1]])
        with self.assertRaisesRegex(ValueError, "three equal"):
            reassignment_membership(initial_membership(4, 2))
        with self.assertRaises(ValueError):
            team_kinds(4)

    def test_goal_schema_names_sizes_and_fields_are_explicit(self):
        with tempfile.TemporaryDirectory(prefix="goal-schema-test-") as directory:
            path = generate(Path(directory), goal=True)
            structs = ET.parse(path).getroot().findall("./types/struct")
            self.assertEqual(len(structs), 14)
            names = {struct.get("name") for struct in structs}
            self.assertIn("ScaleMissionState", names)
            self.assertIn("ScaleTaskProgress", names)
            self.assertIn("ScaleCoordinationIntent", names)
            self.assertNotIn("ScaleMissionElement", names)
            manifest = json.loads((Path(directory) / "schema_manifest.json").read_text())
            self.assertEqual(manifest["goal_model_version"], 1)
            task = next(t for t in manifest["types"] if t["type"] == "ScaleTaskProgress")
            self.assertEqual((task["min_sample_target"], task["max_sample_target"]), (80, 160))
            self.assertEqual(task["mission_fields"], ["task_id", "state", "progress"])
            self.assertEqual(profiles()[1][0], "TaskElement")
            self.assertEqual(profiles(True)[1][0], "TaskProgress")

    def test_expected_matches_include_self_but_data_recipients_do_not(self):
        membership = initial_membership(6, 2)
        snapshots = {1: {"endpoints": {"12:1": {}, "12:0": {}, "10:1": {}, "10:0": {}}}}
        self.assertEqual(goal_requirements(snapshots, 6, 3, membership, "A", True),
                         [(1, "12:1", 3), (1, "12:0", 2), (1, "10:1", 7), (1, "10:0", 6)])
        self.assertEqual(goal_requirements(snapshots, 6, 3, membership, "B", False)[:2],
                         [(1, "12:1", 1), (1, "12:0", 0)])
        self.assertEqual(goal_requirements(snapshots, 6, 3, membership, "A", False)[:2],
                         [(1, "12:1", 2), (1, "12:0", 1)])

    def test_delivery_uses_send_time_scope_and_phase_grace(self):
        now = time.monotonic()
        key = "1:session:1"
        snapshots = {node: {"sent": {}, "received": {}} for node in range(3)}
        snapshots[1]["sent"][key] = dict(
            kind=12, phase=STABLE, sent_at=now - 10, serialized_bytes=110,
            expected_receivers=[0, 2])
        for node in (0, 2):
            snapshots[node]["received"][key] = dict(received_at=now - 9)
        result = audit(snapshots, 2, 1, {STABLE}, 3)
        self.assertEqual(result["destination_receives"], 2)
        snapshots[1]["phase_ends"] = {STABLE: now - 9}
        self.assertEqual(audit(snapshots, 2, 1, {STABLE}, 3)["sends"], 0)

    def test_metadata_scope_uses_new_membership_not_original_groups(self):
        membership = swap_membership(initial_membership(6, 2))
        snapshots = {}
        for node in range(7):
            expected = [other for other in range(7) if other != node and (
                node == 0 or other == 0 or membership[node] == membership[other])]
            owners = {f"p{other}": 1 for other in expected}
            snapshots[node] = {
                "participants": {"main": {"guid": f"p{node}",
                    "publications_by_owner": owners, "subscriptions_by_owner": owners}},
                "endpoints": {"12:1": {"participant": "main"}, "12:0": {"participant": "main"}},
            }
        visible = validate_metadata_scope(snapshots, "A", 2, True, membership)
        self.assertEqual(visible[1], 4)
        with self.assertRaisesRegex(AssertionError, "metadata scope"):
            validate_metadata_scope(snapshots, "A", 2, True)

    def test_matrix_rejects_invalid_goal_or_protocol_dimensions_before_launch(self):
        base = ["run_scale.py", "--populations", "6", "--team-sizes", "2",
                "--team-topic-counts", "3", "--protocols", "SPDP2"]
        invalid = (
            ["--populations", "4"],
            ["--team-sizes", "1"],
            ["--team-topic-counts", "1,3"],
            ["--team-topic-counts", "1", "--protocols", "SPDP"],
            ["--protocols", "SPDP,SPDP"],
            ["--variants", "ABC"],
            ["--change-hold-seconds", "7"],
        )
        for options in invalid:
            with self.subTest(options=options), patch("sys.argv", base + options), \
                    patch("run_scale.subprocess.run") as launch, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    scale_main()
                self.assertEqual(error.exception.code, 2)
                launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
