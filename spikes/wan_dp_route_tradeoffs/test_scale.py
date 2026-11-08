import unittest
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

from scale import HEALTH, recipients, validate_metadata_scope
from scale_report import aggregates, group_key, validate_rows
from html_report import build
from schema_profile import POSE, PROFILES, generate, size_payload


class WorkloadTests(unittest.TestCase):
    def test_fourteen_type_inventory(self):
        self.assertEqual(len(PROFILES), 14)
        self.assertEqual({PROFILES[i][0] for i in (9, 10, 13)}, {"Pose", "Velocity", "Speed"})
        self.assertTrue(all(p[1] <= p[2] for p in PROFILES))

    def test_mission_schemas_do_not_pad_type_objects(self):
        with tempfile.TemporaryDirectory(prefix="mission-schema-test-") as directory:
            path = generate(Path(directory))
            structs = ET.parse(path).getroot().findall("./types/struct")
            self.assertEqual(len(structs), 14)
            self.assertEqual({s.attrib["name"] for s in structs},
                             {f"Scale{p[0]}" for p in PROFILES})
            for struct in structs:
                members = struct.findall("member")
                self.assertLessEqual(len(members), 10)
                self.assertFalse(any(m.attrib["name"].startswith("detail_") for m in members))
                self.assertEqual({m.attrib["name"] for m in members if m.attrib.get("key") == "true"},
                                 {"source", "instance"})
            manifest = json.loads((Path(directory) / "schema_manifest.json").read_text())
            self.assertEqual(manifest["type_count"], 14)
            self.assertNotIn("target_total_bytes", manifest)
            self.assertEqual([t["topic"] for t in manifest["types"]], [s.attrib["name"] for s in structs])

    def test_sample_alignment_stays_inside_minimum_and_maximum(self):
        class AlignedSample(dict):
            def to_cdr_buffer(self):
                length = 52 + len(self["payload"])
                return bytes((length + 3) // 4 * 4)

        for target, expected in ((117, 120), (1521, 1520)):
            sample = AlignedSample()
            self.assertEqual(size_payload(sample, target, 117, 1521), expected)
        with self.assertRaisesRegex(ValueError, "outside"):
            size_payload(AlignedSample(), 116, 117, 1521)

    def test_pose_reaches_control_and_whole_squad(self):
        self.assertEqual(recipients(1, POSE, 7, 3), [0, 2, 3, 4, 5, 6, 7])

    def test_only_contacts_are_shared_with_team(self):
        self.assertEqual(recipients(1, 12, 7, 3), [0, 2, 3])
        self.assertEqual(recipients(7, 12, 7, 3), [0])
        self.assertEqual(recipients(1, 10, 7, 3), [0])

    def test_health_and_commands_keep_control_reachable(self):
        self.assertEqual(recipients(0, HEALTH, 2, 3), [1, 2])
        self.assertEqual(recipients(2, HEALTH, 2, 3), [0])
        self.assertEqual(recipients(0, 0, 2, 3), [1])

    def test_squad32_team_boundaries(self):
        self.assertEqual(recipients(1, 12, 32, 4), [0, 2, 3, 4])
        self.assertEqual(recipients(32, 12, 32, 8), [0, 25, 26, 27, 28, 29, 30, 31])
        self.assertEqual(len(recipients(1, POSE, 32, 4)), 32)
        self.assertEqual(recipients(0, HEALTH, 32, 8), list(range(1, 33)))


class ScaleReportTests(unittest.TestCase):
    def setUp(self):
        self.manifest = dict(populations=[2], variants="ABC", repetitions=2, seconds=150)
        self.rows = [
            dict(platforms=2, variant=variant, repetition=repetition,
                 wan_dps=5 if variant == "A" else 3, active_endpoints=52,
                 preassignment_endpoints=14 if variant == "C" else 52,
                 control_wire_learned_types=12, missing=0, unintended=0,
                 application_type_count=14, steady_window_seconds=150,
                 enable_command_max_latency_ms=1.0,
                 convergence_discovery_bytes=100 * repetition)
            for variant in "ABC" for repetition in (1, 2)
        ]

    def test_complete_matrix_and_observed_ranges(self):
        validate_rows(self.rows, self.manifest)
        grouped = aggregates(self.rows)
        self.assertEqual(grouped["2:A"]["repetitions"], 2)
        self.assertEqual(grouped["2:A"]["metrics"]["convergence_discovery_bytes"],
                         dict(median=150, min=100, max=200))

    def test_duplicate_case_cannot_replace_missing_case(self):
        self.rows[-1] = self.rows[0].copy()
        with self.assertRaisesRegex(ValueError, "matrix"):
            validate_rows(self.rows, self.manifest)

    def test_new_manifests_require_execution_and_capture_proofs(self):
        self.manifest.update(
            execution_provenance_version=1, source_hashes={"qos.xml": "a" * 64},
            input_hashes={}, image_id="sha256:" + "b" * 64)
        with self.assertRaisesRegex(ValueError, "provenance"):
            validate_rows(self.rows, self.manifest)
        proof = {key: self.manifest[key] for key in ("source_hashes", "input_hashes", "image_id")}
        for r in self.rows:
            r["execution_provenance"] = proof
        validate_rows(self.rows, self.manifest)
        self.manifest["capture_integrity_version"] = 1
        for r in self.rows:
            r["capture"] = {}
        with self.assertRaisesRegex(ValueError, "capture integrity"):
            validate_rows(self.rows, self.manifest)
        self.manifest["execution_provenance_version"] = 99
        with self.assertRaisesRegex(ValueError, "unsupported"):
            validate_rows(self.rows, self.manifest)

    def test_capture_accounting_versions_are_validated_and_not_pooled(self):
        self.manifest["capture_accounting_version"] = 2
        for r in self.rows:
            r["capture_accounting_version"] = 2
        validate_rows(self.rows, self.manifest)
        self.assertNotIn("capture_accounting_version", aggregates(self.rows)["2:A"]["metrics"])
        for value in (1, None):
            with self.subTest(version=value):
                changed = [r.copy() for r in self.rows]
                changed[0]["capture_accounting_version"] = value
                with self.assertRaisesRegex(ValueError, "accounting version"):
                    validate_rows(changed, self.manifest)
                with self.assertRaisesRegex(ValueError, "accounting versions"):
                    aggregates(changed)

    def test_health_and_assignment_models_are_not_pooled(self):
        changed = [dict(r, health_profile="scale_periodic", assignment_model_version=1)
                   for r in self.rows]
        changed[0]["health_profile"] = "scale_health30"
        with self.assertRaisesRegex(ValueError, "health profiles"):
            aggregates(changed)
        changed[0]["health_profile"] = "scale_periodic"
        changed[0]["assignment_model_version"] = 2
        with self.assertRaisesRegex(ValueError, "assignment models"):
            aggregates(changed)

    def test_inventory_and_discovery_gates(self):
        for field, value in (
                ("wan_dps", 4), ("active_endpoints", 51), ("preassignment_endpoints", 51),
                ("control_wire_learned_types", 11),
                ("application_type_count", 13), ("steady_window_seconds", 149)):
            with self.subTest(field=field):
                changed = [r.copy() for r in self.rows]
                changed[0][field] = value
                with self.assertRaises(ValueError):
                    validate_rows(changed, self.manifest)


    def test_delivery_and_activation_gates(self):
        for field, value in (
                ("missing", 1), ("unintended", 1), ("enable_command_max_latency_ms", None)):
            with self.subTest(field=field):
                changed = [r.copy() for r in self.rows]
                changed[0][field] = value
                with self.assertRaises(ValueError):
                    validate_rows(changed, self.manifest)

    def test_team_dimensions_are_not_combined(self):
        manifest = dict(populations=[32], team_sizes=[4, 8], variants="ABC",
                        repetitions=2, seconds=150)
        rows = [
            dict(r, platforms=32, team_size=t, wan_dps=65 if r["variant"] == "A" else 33,
                 active_endpoints=592, preassignment_endpoints=164 if r["variant"] == "C" else 592,
                 convergence_discovery_bytes=t*100*r["repetition"])
            for t in (4, 8) for r in self.rows
        ]
        validate_rows(rows, manifest)
        grouped = aggregates(rows)
        self.assertEqual(len(grouped), 6)
        self.assertEqual(grouped[group_key(32, "A", 4)]["metrics"]["convergence_discovery_bytes"]["median"], 600)
        self.assertEqual(grouped[group_key(32, "A", 8)]["metrics"]["convergence_discovery_bytes"]["median"], 1200)
        rows[-1] = rows[0].copy()
        with self.assertRaisesRegex(ValueError, "matrix"):
            validate_rows(rows, manifest)


class ParticipantScopeTests(unittest.TestCase):
    def test_two_dp_scope_changes_only_on_team_assignment(self):
        snapshots = {
            0: dict(participants=dict(main=dict(guid="c")),
                    endpoints={"0:1": dict(participant="main"), "0:0": dict(participant="main")}),
            1: dict(participants=dict(main=dict(guid="m1"), position=dict(guid="p1")),
                    endpoints={"0:1": dict(participant="main"), "0:0": dict(participant="main"),
                               "1:1": dict(participant="position"), "1:0": dict(participant="position")}),
            2: dict(participants=dict(main=dict(guid="m2"), position=dict(guid="p2")),
                    endpoints={"0:1": dict(participant="main"), "0:0": dict(participant="main"),
                               "1:1": dict(participant="position"), "1:0": dict(participant="position")}),
        }
        plan = {"c": ["m1", "p1", "m2", "p2"], "m1": ["c"], "m2": ["c"],
                "p1": ["c", "p2"], "p2": ["c", "p1"]}
        for snapshot in snapshots.values():
            for p in snapshot["participants"].values():
                for kind in ("publications", "subscriptions"):
                    p[kind+"_by_owner"] = dict.fromkeys(plan[p["guid"]], 1)
        self.assertEqual(validate_metadata_scope(snapshots, "A", 2, False), {1: 6, 2: 6})
        for node, peer in ((1, "m2"), (2, "m1")):
            for kind in ("publications", "subscriptions"):
                snapshots[node]["participants"]["main"][kind+"_by_owner"][peer] = 1
        self.assertEqual(validate_metadata_scope(snapshots, "A", 2, True), {1: 8, 2: 8})
        with self.assertRaisesRegex(AssertionError, "metadata scope"):
            validate_metadata_scope(snapshots, "A", 2, False)
        with self.assertRaisesRegex(AssertionError, "metadata scope"):
            validate_metadata_scope(snapshots, "A", 1, True)

    def test_one_dp_retains_all_peer_metadata(self):
        snapshots = {
            node: dict(participants=dict(main=dict(
                guid=f"p{node}",
                publications_by_owner={f"p{other}": 1 for other in range(3) if other != node},
                subscriptions_by_owner={f"p{other}": 1 for other in range(3) if other != node})),
                endpoints={"0:1": dict(participant="main"), "0:0": dict(participant="main")})
            for node in range(3)
        }
        self.assertEqual(validate_metadata_scope(snapshots, "B", 1, False), {1: 4, 2: 4})
        self.assertEqual(validate_metadata_scope(snapshots, "B", 1, True), {1: 4, 2: 4})
        snapshots[1]["participants"]["main"]["publications_by_owner"]["p2"] = 2
        with self.assertRaisesRegex(AssertionError, "metadata scope"):
            validate_metadata_scope(snapshots, "B", 1, True)

    def test_health30_profile_retains_presence_settings(self):
        root = ET.parse(Path(__file__).with_name("qos.xml")).getroot()
        p = root.find(".//qos_profile[@name='scale_health30']")
        self.assertEqual(p.attrib["base_name"], "Tradeoffs::scale_periodic")
        for kind in ("datawriter_qos", "datareader_qos"):
            self.assertEqual(p.find(f"{kind}/liveliness/lease_duration/sec").text, "30")
            self.assertEqual(p.find(f"{kind}/deadline/period/sec").text, "2")
            self.assertEqual(p.find(f"{kind}/history/depth").text, "1")


class HtmlReportTests(unittest.TestCase):
    def setUp(self):
        self.current = dict(
            full_windows=False,
            manifest=dict(populations=[2], variants="ABC", repetitions=1, seconds=3,
                          started_at=0, source_hashes={"qos.xml": "verified-hash"},
                          schema_manifest=dict(sizing="Sample sizes only",
                                               types=[dict(type=f"Scale{p[0]}", rate_hz=p[3])
                                                      for p in PROFILES])),
            totals=dict(audited_sends=631, destination_receives=841, missing=0, unintended=0),
            type_discovery_verification=dict(
                qos_source_sha256="verified-hash", type_lookup_enabled=True,
                type_object_max_serialized_length="LENGTH_AUTO",
                type_code_max_serialized_length=0, request_types_filter="*"),
            sdk_sample_validation=[
                dict(type=f"Scale{p[0]}", minimum_target=p[1], maximum_target=p[2],
                     measured_minimum=p[1], measured_maximum=p[2]) for p in PROFILES],
            runs=[
                dict(platforms=2, variant=v, repetition=1, wan_dps=5 if v == "A" else 3,
                     active_endpoints=52, preassignment_endpoints=14 if v == "C" else 52,
                     control_wire_learned_types=12, application_type_count=14,
                     steady_window_seconds=3, missing=0, unintended=0,
                     enable_command_max_latency_ms=10, enable_to_all_control_matches_ms=20,
                     convergence_discovery_bytes=total, startup_discovery_bytes=total-1024,
                     activation_discovery_bytes=1024)
                for v, total in zip("ABC", (102400, 51200, 61440))])

    def test_report_numbers_and_scope(self):
        html = build(self.current)
        self.assertIn("50.0%", html)
        self.assertIn("Smoke evidence, not a scale verdict", html)
        self.assertIn("TypeLookup", html)
        self.assertIn("Measured min / max", html)
        self.assertNotIn('src="https:', html)

    def test_winner_and_direction_follow_measured_totals(self):
        for totals, winners, label in (
                ((51200, 102400, 61440), {"A"}, "more setup discovery"),
                ((102400, 61440, 51200), {"C"}, "less setup discovery"),
                ((51200, 51200, 61440), {"A", "B"}, "equal setup discovery"),
                ((0, 0, 0), {"A", "B", "C"}, "percentage comparison is undefined")):
            with self.subTest(totals=totals):
                for row, total in zip(self.current["runs"], totals):
                    row.update(convergence_discovery_bytes=total,
                               startup_discovery_bytes=total, activation_discovery_bytes=0)
                html = build(self.current)
                self.assertIn(label, html)
                for variant in "ABC":
                    self.assertEqual(
                        f'<article class="card best">\n          <div class="eyebrow">Design {variant}' in html,
                        variant in winners)
                if len(winners) > 1:
                    self.assertIn("tied for lowest observed setup traffic", html)
                if not any(totals):
                    self.assertIn(">N/A</div>", html)

    def test_inconsistent_setup_totals_are_rejected(self):
        self.current["runs"][0]["activation_discovery_bytes"] += 1
        with self.assertRaisesRegex(ValueError, "discovery totals"):
            build(self.current)

    def test_type_verification_must_match_measurement_qos(self):
        self.current["type_discovery_verification"]["qos_source_sha256"] = "different"
        with self.assertRaisesRegex(ValueError, "configuration"):
            build(self.current)

    def test_sample_size_validation_cannot_be_missing(self):
        self.current["sdk_sample_validation"].pop()
        with self.assertRaisesRegex(ValueError, "fourteen"):
            build(self.current)

if __name__ == "__main__":
    unittest.main()
