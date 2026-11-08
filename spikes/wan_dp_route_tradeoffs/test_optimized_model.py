import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from goal_model import (active_kinds, datamodel, endpoint_inventory, team_kinds,
                        OPTIMIZED_FIELDS, OPTIMIZED_SAMPLE_VALUES)
from schema_profile import generate
from scale import recipients, goal_requirements, audit, STABLE
import scale_report
from test_goal_report import manifest, result


class OptimizedModelTests(unittest.TestCase):
    def test_merged_fields_and_feedback_tags_are_audited(self):
        for kind in active_kinds(True):
            self.assertEqual(set(OPTIMIZED_SAMPLE_VALUES[kind]),
                             {field for field, _ in OPTIMIZED_FIELDS[kind]})
        now = time.monotonic()
        snapshots = {node: {"sent": {}, "received": {}, "goal_model_version": 2}
                     for node in (0, 1, 2)}
        sample = dict(kind=3, phase=STABLE, sent_at=now-10, serialized_bytes=112,
                      expected_receivers=[0, 2], content=OPTIMIZED_SAMPLE_VALUES[3])
        snapshots[1]["sent"]["key"] = sample
        for node in (0, 2):
            snapshots[node]["received"]["key"] = dict(received_at=now-9,
                                                       content=sample["content"].copy())
        self.assertEqual(audit(snapshots, 2, 2, {STABLE}, 3)["destination_receives"], 2)
        snapshots[2]["received"]["key"]["content"]["task_progress"] = 0
        with self.assertRaisesRegex(AssertionError, "payload mismatch"):
            audit(snapshots, 2, 2, {STABLE}, 3)

    def test_seven_real_topics_preserve_all_logical_families(self):
        model = datamodel(3, True)
        self.assertEqual(model["squad_topics"], ["NavigationState"])
        self.assertEqual(len(model["team_topics"]), 3)
        self.assertEqual(model["c2_topics"], ["ControlCommand", "ControlFeedback"])
        self.assertEqual(model["dds_topic_count"], 7)
        self.assertEqual(model["team_endpoints_per_platform"], 6)
        self.assertEqual(endpoint_inventory(6, 3, True), 80)
        self.assertEqual(endpoint_inventory(32, 3, True), 392)
        with self.assertRaises(ValueError):
            datamodel(6, True)
        with tempfile.TemporaryDirectory(prefix="optimized-schema-") as directory:
            xml = generate(Path(directory), goal=True, optimized=True)
            structs = {s.get("name"): {m.get("name") for m in s.findall("member")}
                       for s in ET.parse(xml).getroot().findall("./types/struct")}
            self.assertEqual(len(structs), 7)
            self.assertFalse({"ScalePose", "ScaleSpeed", "ScaleVelocity",
                              "ScaleTaskProgress", "ScaleContactReport"} & structs.keys())
            self.assertTrue({"latitude", "longitude", "heading_deg", "north_mps",
                             "east_mps", "down_mps", "metres_per_second"}
                            <= structs["ScaleNavigationState"])
            self.assertTrue({"mission_id", "task_id", "task_progress"}
                            <= structs["ScaleMissionState"])
            self.assertTrue({"track_count", "track_id", "classification", "latitude"}
                            <= structs["ScaleSituationalAwareness"])
            self.assertTrue({"coordination_id", "intent_id", "valid_for_s"}
                            <= structs["ScaleCoordinationState"])
            self.assertTrue({"feedback_kind", "command_id", "reason"}
                            <= structs["ScaleControlFeedback"])
            captured = json.loads((Path(directory) / "schema_manifest.json").read_text())
            self.assertEqual((captured["goal_model_version"], captured["type_count"],
                              captured["application_type_count"]), (2, 7, 6))

    def test_consolidated_routes_and_matches(self):
        self.assertEqual(active_kinds(True), (0, 3, 4, 5, 9, 12))
        for kind in team_kinds(3, True):
            self.assertEqual(recipients(1, kind, 6, 2, 3, optimized=True), [0, 2])
            self.assertEqual(recipients(1, kind, 6, 2, 3, assigned=False, optimized=True), [0])
        self.assertEqual(recipients(1, 9, 6, 2, 3, optimized=True), [0, 2, 3, 4, 5, 6])
        self.assertEqual(recipients(1, 5, 6, 2, 3, optimized=True), [0])
        snapshots = {1: {"endpoints": {"4:1": {}, "4:0": {}, "9:1": {}, "9:0": {}}}}
        wanted = goal_requirements(snapshots, 6, 3, {n: (n-1)//2 for n in range(1, 7)},
                                   "B", True, True)
        self.assertEqual(wanted, [(1, "4:1", 3), (1, "4:0", 2), (1, "9:1", 7), (1, "9:0", 6)])

    def test_report_accepts_only_exact_optimized_inventory_and_version(self):
        spec = manifest(repetitions=1)
        spec.update(goal_model_version=2, team_topic_counts=[3])
        rows = []
        native_rows = []
        for protocol in spec["protocols"]:
            for variant in spec["variants"]:
                native = result(protocol=protocol, variant=variant)
                native.update(goal_model_version=2, application_type_count=6,
                              active_endpoints=80, goal_datamodel=datamodel(3, True))
                native["checkpoints"]["before_assignment"]["0"]["endpoint_count"] = 80
                native["checkpoints"]["after_assignment"]["0"]["control_wire_learned_types"] = 4
                native["assignment_validation"]["pre_assignment_team_receivers"] = {
                    str(kind): [0] for kind in team_kinds(3, True)}
                native_rows.append(native)
                rows.append(scale_report.row(native))
        with patch.object(scale_report, "validate_provenance"), \
                patch.object(scale_report, "validate_capture_integrity"):
            scale_report.validate_rows(rows, spec)
            for field, value in (("goal_model_version", 1), ("application_type_count", 14),
                                 ("active_endpoints", 148), ("control_wire_learned_types", 12)):
                bad = copy.deepcopy(rows)
                bad[0][field] = value
                with self.subTest(field=field), self.assertRaises(ValueError):
                    scale_report.validate_rows(bad, spec)
        with tempfile.TemporaryDirectory(prefix="optimized-report-") as directory:
            output = Path(directory) / "report.md"
            scale_report.write_goal_report(rows, spec, scale_report.aggregates(rows), output)
            text = output.read_text()
            self.assertIn("NavigationState", text)
            self.assertIn("ControlFeedback", text)
            self.assertIn("total 12N+8", text)
            self.assertIn("Goal model v2", text)
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps(spec))
            (root / "results.json").write_text(json.dumps(native_rows))
            with patch.object(scale_report, "validate_provenance"), \
                    patch.object(scale_report, "validate_capture_integrity"):
                retained = scale_report.write(root, root / "public-report.md")
            self.assertEqual(len(retained["runs"]), 4)
            self.assertIn("NavigationState", (root / "public-report.md").read_text())


if __name__ == "__main__":
    unittest.main()
