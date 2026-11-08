"""DDS-free acceptance tests for goal reports and historical grouping."""

import copy
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

import scale_report


def manifest(populations=(6,), team_sizes=(2,), repetitions=2):
    return dict(
        populations=list(populations), team_sizes=list(team_sizes),
        team_topic_counts=[3, 6], protocols=["SPDP", "SPDP2"], variants=["A", "B"],
        repetitions=repetitions, seconds=150, concurrent_cases=1,
        health_profile="scale_health30", goal_model_version=1, assignment_model_version=3,
        required_changes=["swap", "reassignment"], change_hold_seconds=10,
        capture_integrity_version=1, execution_provenance_version=1,
        capture_accounting_version=2, limitations=["Fixture only"],
        schema_manifest={"RendezvousState": {"minimum_bytes": 96, "maximum_bytes": 160}},
    )


def result(n=6, t=2, k=3, protocol="SPDP", variant="A", repetition=1):
    factor = (1 if variant == "A" else 2) * (1 if protocol == "SPDP" else 0.5)

    def window(seconds):
        return dict(duration_s=seconds, total_bytes=1000 * factor,
                    class_bytes={"discovery": 100 * factor},
                    total_tx_Bps=1000 * factor / seconds,
                    discovery_tx_Bps=100 * factor / seconds,
                    discovery_peak_1s_Bps=50 * factor,
                    sedp_writer_frames_bytes=20 * factor)

    names = ["startup", "activation", "stable_position", "mission_burst", "operational",
             "swap_change", "swap_settled", "reassignment_change", "reassignment_settled"]
    windows = {name: window(150 if name == "stable_position" else 10) for name in names}
    metadata = {str(node): 10 for node in range(1, n + 1)}
    before = {str(node): (node - 1) // t for node in range(1, n + 1)}
    swapped = dict(before)
    swapped["1"], swapped[str(t + 1)] = swapped[str(t + 1)], swapped["1"]
    reassigned = {}
    for team in range(n // t):
        nodes = [node for node in swapped if swapped[node] == team]
        for index, node in enumerate(nodes):
            reassigned[node] = (team + 1 + index % (n // t - 1)) % (n // t)
    changes = []
    for name, old, new in (("swap", before, swapped), ("reassignment", swapped, reassigned)):
        changed = [node for node in old if old[node] != new[node]]
        changes.append(dict(
            name=name, window=f"{name}_change", settled_window=f"{name}_settled",
            membership_before=old, membership_after=new, changed_nodes=changed,
            participant_partition_updates=len(changed) if variant == "A" else 0,
            endpoint_parent_updates=0 if variant == "A" else 2 * k * len(changed),
            convergence_ms=25 * factor, guids_preserved=True, endpoints_preserved=True,
            probes_sent=10, probe_missing=0, probe_unintended=0,
            before_metadata_records=metadata, after_metadata_records=metadata,
            settled_hold_seconds=10,
            strict_audit=dict(sends=10, destination_receives=20, missing=0, unintended=0),
        ))
    kinds = (1, 3, 12) if k == 3 else (1, 2, 3, 4, 11, 12)
    inventory = (19 + k) * n + 16
    return dict(
        platforms=n, team_size=t, team_topic_count=k, protocol=protocol,
        variant=variant, repetition=repetition,
        goal_model_version=1, assignment_model_version=3, protocol_verified=True,
        goal_datamodel={"application_type_count": 14, "team_topic_count": k},
        total_wan_dps=2 * n + 1 if variant == "A" else n + 1,
        application_type_count=14, active_endpoints=inventory,
        health_profile="scale_health30", execution_provenance={"fixture": True},
        assignment_validation=dict(
            pre_assignment_contact_receivers=[0],
            pre_assignment_team_receivers={str(kind): [0] for kind in kinds},
            before_metadata_records=metadata, after_metadata_records=metadata),
        checkpoints=dict(before_assignment={"0": {"endpoint_count": inventory}},
                         after_assignment={"0": {"control_wire_learned_types": 12}}),
        capture={"windows": windows, "accounting_version": 2, "integrity": {"fixture": True}},
        changes=changes, enable_to_all_control_matches_ms=20,
        delivery=dict(enable_command_max_latency_ms=5, pose_max_latency_ms=3, sends=100,
                      destination_receives=200, missing=0, unintended=0, serialized_sizes={}),
        cpu_seconds_during_stable=1, worker_peak_rss_kib_sum=1024, guid_map={},
    )


def matrix(spec):
    return [result(n, t, k, protocol, variant, repetition)
            for n in spec["populations"] for t in spec["team_sizes"]
            for k in spec["team_topic_counts"] for protocol in spec["protocols"]
            for variant in spec["variants"] for repetition in range(1, spec["repetitions"] + 1)]


class GoalReportTests(unittest.TestCase):
    def setUp(self):
        self.spec = manifest()
        self.rows = [scale_report.row(item) for item in matrix(self.spec)]
        self.provenance = patch.object(scale_report, "validate_provenance").start()
        self.capture = patch.object(scale_report, "validate_capture_integrity").start()
        self.addCleanup(patch.stopall)

    def rejects(self, mutate):
        rows, spec = copy.deepcopy(self.rows), copy.deepcopy(self.spec)
        mutate(rows, spec)
        with self.assertRaises(ValueError):
            scale_report.validate_rows(rows, spec)

    def test_confirmed_32_case_cartesian_matrix(self):
        spec = manifest((32,), (4, 8))
        rows = [scale_report.row(item) for item in matrix(spec)]
        scale_report.validate_rows(rows, spec)
        self.assertEqual(len(rows), 32)
        self.assertEqual(len(scale_report.aggregates(rows)), 16)
        self.assertEqual(self.provenance.call_count, 32)
        self.assertEqual(self.capture.call_count, 32)

    def test_legacy_group_keys_unchanged(self):
        self.assertEqual(scale_report.group_key(32, "A", None), "32:A")
        self.assertEqual(scale_report.group_key(32, "A", 3), "32:A")
        self.assertEqual(scale_report.group_key(32, "A", 4), "32:t4:A")
        self.assertEqual(scale_report.group_key(32, "A", 3, 6, "SPDP2"),
                         "32:t3:k6:SPDP2:A")
        with self.assertRaises(ValueError):
            scale_report.group_key(32, "A", 4, 3)

    def test_grouping_never_pools_protocol_or_k(self):
        grouped = scale_report.aggregates(self.rows)
        self.assertEqual(len(grouped), 8)
        self.assertEqual({g["repetitions"] for g in grouped.values()}, {2})
        self.assertEqual({g["protocol"] for g in grouped.values()}, {"SPDP", "SPDP2"})
        self.assertEqual({g["team_topic_count"] for g in grouped.values()}, {3, 6})
        self.assertNotIn("protocol_verified", next(iter(grouped.values()))["metrics"])

    def test_change_metrics_and_proofs_retained(self):
        r = self.rows[0]
        self.assertEqual(r["swap_discovery_bytes"], 100)
        self.assertEqual(r["swap_sedp_writer_frames_bytes"], 20)
        self.assertEqual(r["swap_total_bytes"], 1000)
        self.assertEqual(r["swap_participant_partition_updates"], 2)
        self.assertEqual(r["reassignment_participant_partition_updates"], 6)
        self.assertEqual(r["swap_settled_hold_seconds"], 10)
        b = next(r for r in self.rows if r["variant"] == "B" and r["team_topic_count"] == 6)
        self.assertEqual(b["swap_endpoint_parent_updates"], 24)
        self.assertEqual(b["reassignment_endpoint_parent_updates"], 72)
        self.assertIn("strict_audit", r["changes"][0])

    def test_incomplete_duplicate_or_dimension_mismatch_rejected(self):
        for mutation in (
            lambda rows, spec: rows.pop(),
            lambda rows, spec: rows.__setitem__(1, copy.deepcopy(rows[0])),
            lambda rows, spec: rows[0].update(protocol="SPDP3"),
            lambda rows, spec: rows[0].update(team_topic_count=4),
        ):
            with self.subTest(mutation=mutation):
                self.rejects(mutation)

    def test_mandatory_versions_and_native_protocol_proof(self):
        for field in ("capture_integrity_version", "execution_provenance_version",
                      "capture_accounting_version", "goal_model_version", "assignment_model_version"):
            self.rejects(lambda rows, spec, field=field: spec.pop(field))
        for value in (None, False, 1):
            self.rejects(lambda rows, spec, value=value: rows[0].update(protocol_verified=value))
        self.rejects(lambda rows, spec: rows[0].update(goal_datamodel={}))
        self.rejects(lambda rows, spec: rows[0].update(assignment_model_version=2))
        self.rejects(lambda rows, spec: spec.update(health_profile="scale_periodic"))

    def test_source_and_capture_guards_really_called(self):
        scale_report.validate_rows(self.rows, self.spec)
        self.provenance.assert_any_call(self.rows[0]["execution_provenance"], self.spec)
        self.capture.assert_any_call(self.rows[0]["capture"]["integrity"], self.rows[0]["capture"])
        for guard in (self.provenance, self.capture):
            guard.side_effect = ValueError("invalid retained measurement proof")
            with self.assertRaisesRegex(ValueError, "invalid retained"):
                scale_report.validate_rows(self.rows, self.spec)
            guard.side_effect = None

    def test_inventory_and_eager_endpoint_guards(self):
        for field in ("active_endpoints", "preassignment_endpoints", "wan_dps",
                      "application_type_count", "steady_window_seconds"):
            self.rejects(lambda rows, spec, field=field: rows[0].update({field: 1}))
        self.rejects(lambda rows, spec:
                     rows[0].update(steady_total_kbps=float("nan")))
        for count in (0, 11, 13):
            self.rejects(lambda rows, spec, count=count:
                         rows[0].update(control_wire_learned_types=count))

    def test_inconsistent_datamodel_cannot_be_hidden_by_grouping(self):
        self.rejects(lambda rows, spec:
                     rows[0]["goal_datamodel"].update(application_type_count=13))

    def test_every_selected_team_family_closed_before_assignment(self):
        for value in ({}, {"1": [0]}, {"1": [0], "3": [0], "12": [0, 1]}):
            self.rejects(lambda rows, spec, value=value:
                         rows[0]["assignment_validation"].update(pre_assignment_team_receivers=value))
        self.rejects(lambda rows, spec:
                     rows[0]["assignment_validation"].update(pre_assignment_contact_receivers=[0, 1]))

    def test_required_changes_and_memberships(self):
        self.rejects(lambda rows, spec: rows[0]["changes"].pop())
        self.rejects(lambda rows, spec: spec.update(required_changes=["swap"]))
        self.rejects(lambda rows, spec: rows[0]["changes"][0].update(changed_nodes=["1"]))
        self.rejects(lambda rows, spec: rows[0]["changes"][0]["membership_after"].pop("1"))
        self.rejects(lambda rows, spec:
                     rows[0]["changes"][0]["membership_after"].update({"1": 99}))
        self.rejects(lambda rows, spec:
                     rows[0]["changes"][1].update(membership_after=dict(
                         rows[0]["changes"][1]["membership_before"])))
        rows = [scale_report.row(item) for item in matrix(manifest((4,), (2,)))]
        with self.assertRaisesRegex(ValueError, "three complete teams"):
            scale_report.validate_rows(rows, manifest((4,), (2,)))

    def test_identity_update_hold_and_audit_guards(self):
        invalid = dict(
            participant_partition_updates=0, endpoint_parent_updates=10,
            guids_preserved=False, endpoints_preserved=False,
            convergence_ms=float("nan"), before_metadata_records={},
            after_metadata_records={"1": -1}, settled_hold_seconds=9, probes_sent=0,
            probe_missing=1, probe_unintended=1, window="activation",
            settled_window="operational")
        for field, value in invalid.items():
            with self.subTest(field=field):
                self.rejects(lambda rows, spec, field=field, value=value:
                             rows[0]["changes"][0].update({field: value}))
        for field, value in dict(sends=0, destination_receives=0, missing=1, unintended=1).items():
            self.rejects(lambda rows, spec, field=field, value=value:
                         rows[0]["changes"][0]["strict_audit"].update({field: value}))
        self.rejects(lambda rows, spec:
                     rows[0]["capture"]["windows"]["swap_settled"].update(duration_s=9))

    def test_legacy_and_goal_rows_cannot_be_pooled(self):
        rows = copy.deepcopy(self.rows)
        rows[0].pop("goal_model_version")
        with self.assertRaisesRegex(ValueError, "historical and goal"):
            scale_report.aggregates(rows)

    def test_goal_writer_data_driven_conclusions_and_complete_retention(self):
        source = Path("spikes/wan_dp_route_tradeoffs") / (".goal-report-test-" + uuid.uuid4().hex)
        source.mkdir()
        self.addCleanup(shutil.rmtree, source)
        (source / "manifest.json").write_text(json.dumps(self.spec))
        (source / "results.json").write_text(json.dumps(matrix(self.spec)))
        retained = scale_report.write(source, source / "report")
        text = (source / "report.md").read_text()
        saved = json.loads((source / "report.json").read_text())
        self.assertEqual(len(retained["comparisons"]), 8)
        architecture = [r for r in saved["comparisons"] if r["kind"] == "architecture"]
        protocol = [r for r in saved["comparisons"] if r["kind"] == "protocol"]
        self.assertTrue(all(r["setup_winners"] == ["A"] for r in architecture))
        self.assertTrue(all(r["savings_percent"]["steady_discovery_kbps"] == -100 for r in architecture))
        self.assertTrue(all(r["savings_percent"]["steady_discovery_kbps"] == 50 for r in protocol))
        self.assertIn("lowest setup discovery: A", text)
        self.assertIn("steady discovery saving -100.00%", text)
        self.assertIn("SPDP2 versus SPDP", text)
        self.assertIn("Transitional team samples are not strict-audited", text)
        self.assertIn("not real-router, production-schema, or RF forecasts", text)
        self.assertIn("TaskProgress, MissionState, ContactReport", text)
        self.assertIn("squad-wide Pose, Velocity and Speed on position DP", text)
        self.assertIn("| Pose, Velocity, Speed | Squad-wide | Squad-wide |", text)
        self.assertNotIn("GlobalPose", text)
        self.assertIn('"minimum_bytes": 96', text)
        self.assertNotIn("| RendezvousState | 80", text)
        self.assertEqual(saved["runs"][0]["changes"], self.rows[0]["changes"])
        self.assertEqual(saved["runs"][0]["capture"], self.rows[0]["capture"])
        self.assertEqual(saved["runs"][0]["execution_provenance"], {"fixture": True})

    def test_markdown_schema_sizes_follow_manifest(self):
        self.spec["schema_manifest"]["RendezvousState"]["minimum_bytes"] = 112
        source = Path("spikes/wan_dp_route_tradeoffs") / (".goal-report-test-" + uuid.uuid4().hex)
        source.mkdir()
        self.addCleanup(shutil.rmtree, source)
        scale_report.write_goal_report(
            self.rows, self.spec, scale_report.aggregates(self.rows), source / "report")
        text = (source / "report.md").read_text()
        self.assertIn('"minimum_bytes": 112', text)
        self.assertNotIn('"minimum_bytes": 96', text)

    def test_zero_baseline_and_ties_do_not_assume_a_saving(self):
        grouped = scale_report.aggregates(self.rows)
        for group in grouped.values():
            for field in ("startup_discovery_bytes", "activation_discovery_bytes",
                          "convergence_discovery_bytes"):
                group["metrics"][field] = {"median": 0, "min": 0, "max": 0}
        source = Path("spikes/wan_dp_route_tradeoffs") / (".goal-report-test-" + uuid.uuid4().hex)
        source.mkdir()
        self.addCleanup(shutil.rmtree, source)
        saved = scale_report.write_goal_report(self.rows, self.spec, grouped, source / "report")
        for comparison in saved["comparisons"]:
            self.assertIsNone(comparison["savings_percent"]["convergence_discovery_bytes"])
            if comparison["kind"] == "architecture":
                self.assertEqual(comparison["setup_winners"], ["A", "B"])
        self.assertIn("undefined (zero baseline)", (source / "report.md").read_text())


if __name__ == "__main__":
    unittest.main()
