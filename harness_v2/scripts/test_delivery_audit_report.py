#!/usr/bin/env python3
"""Focused unit tests for immutable delivery-audit expectation snapshots."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("delivery_audit_report",
                                              SCRIPT_DIR / "delivery_audit_report.py")
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)
COMPILER_SPEC = importlib.util.spec_from_file_location(
    "compile_delivery_expectations", SCRIPT_DIR / "compile_delivery_expectations.py")
COMPILER = importlib.util.module_from_spec(COMPILER_SPEC)
COMPILER_SPEC.loader.exec_module(COMPILER)


MANIFEST = {
    "nodes": ["control_20", "platform_30"],
    "topics": {
        "Mission": {"minimum_sent": 1, "completion_threshold_percent": 100},
        "PlatformStatus": {"minimum_sent": 1, "completion_threshold_percent": 100},
    },
}
EXPECTATIONS = {
    "Mission": {
        "enabled": True, "source_nodes": ["Control_20"],
        "recipient_rule": {"kind": "sample_destination", "nodes": ["Platform_30"]},
    },
    "PlatformStatus": {
        "enabled": True, "source_nodes": ["Platform_30"],
        "recipient_rule": {"kind": "fixed_nodes", "nodes": ["Control_20"]},
    },
}


def event(kind, topic, source, sequence, node, **extra):
    return {"event": kind, "topic": topic, "source_node": source,
            "sequence": sequence, "node": node, **extra}


class DeliveryAuditExpectationTests(unittest.TestCase):
    def mission_navigation(self, events):
        manifest = {**MANIFEST, "run_id": "r1", "platform_receive_policy": "mission",
                    "nodes": ["control_20", "platform_30", "platform_31"]}
        expectations = COMPILER.compile_expectations(manifest)
        return REPORT.analyze(events, [Path("events.jsonl")], [], manifest,
                              expectations["topics"], "r1")

    @staticmethod
    def policy(timestamp, profile, node="Platform_31", teams=None):
        return event("receive_policy", "PlatformStatus", node, timestamp, node,
                     run_id="r1", sent_at_ns=timestamp, receive_profile=profile,
                     teams=teams or [])

    @staticmethod
    def navigation(timestamp, sequence, peer=False):
        common = {"run_id": "r1", "sent_at_ns": timestamp}
        records = [
            event("sent", "PlatformStatus", "Platform_30", sequence, "Platform_30", **common),
            event("received", "PlatformStatus", "Platform_30", sequence, "Control_20", **common),
        ]
        if peer:
            records.append(event("received", "PlatformStatus", "Platform_30", sequence,
                                 "Platform_31", **common))
        return records

    @staticmethod
    def mission():
        return [
            event("sent", "Mission", "Control_20", 1, "Control_20", destination="Platform_30"),
            event("received", "Mission", "Control_20", 1, "Platform_30"),
        ]

    def test_navigation_uses_applied_selection_at_send_not_final_profile(self):
        records = [
            self.policy(1, "Init"), self.policy(100, "ActiveMission"),
            self.policy(200, "Init"), self.policy(300, "ActiveMission"),
        ]
        for sequence, timestamp in enumerate((50, 150, 250, 350), 1):
            records.extend(self.navigation(timestamp, sequence, peer=timestamp in (150, 350)))
        # Node files need not arrive in chronological order.
        report = self.mission_navigation(list(reversed(records)) + self.mission())
        self.assertEqual("pass", report["verdict"], report)
        self.assertEqual(7, report["expected_deliveries"])
        self.assertEqual([], report["unexpected"])

    def test_navigation_still_counts_active_peer_loss(self):
        report = self.mission_navigation([
            self.policy(1, "Init"), self.policy(100, "ActiveMission"),
            *self.navigation(150, 1), *self.mission(),
        ])
        self.assertEqual("fail", report["verdict"])
        self.assertEqual(["Platform_31"], [row["receiver_node"] for row in report["missing"]])

    def test_init_navigation_receive_remains_unexpected(self):
        report = self.mission_navigation([
            self.policy(1, "Init"), *self.navigation(50, 1, peer=True), *self.mission(),
        ])
        self.assertEqual("fail", report["verdict"])
        self.assertEqual(["Platform_31"], [row["receiver_node"] for row in report["unexpected"]])

    def test_missing_policy_evidence_is_inconclusive_not_a_false_pass(self):
        for policies in ([], [self.policy(100, "ActiveMission")]):
            with self.subTest(policies=policies):
                report = self.mission_navigation([*policies, *self.navigation(150, 1),
                                                 *self.mission()])
                self.assertEqual("inconclusive", report["verdict"])
                self.assertIn("missing initial receive-policy", report["evidence_errors"][0])

    def test_initial_selection_before_startup_and_at_transition_boundaries(self):
        report = self.mission_navigation([
            self.policy(100, "Init"), self.policy(200, "ActiveMission"),
            self.policy(300, "Init"),
            *self.navigation(50, 1), *self.navigation(200, 2, peer=True),
            *self.navigation(300, 3), *self.mission(),
        ])
        self.assertEqual("pass", report["verdict"], report)

    def test_navigation_compiler_rejects_conflicting_or_unknown_policy(self):
        for overrides in ({"platform_receive_policy": "other"},
                          {"platform_receive_policy": "mission", "shared_platform_locations": True}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    COMPILER.compile_expectations({**MANIFEST, "run_id": "r1", **overrides})

    def test_standalone_report_reads_node_owned_policy_history(self):
        manifest = {**MANIFEST, "run_id": "r1", "platform_receive_policy": "mission",
                    "nodes": ["control_20", "platform_30", "platform_31"]}
        records = [self.policy(1, "Init"), self.policy(100, "ActiveMission"),
                   self.policy(200, "Init"), *self.navigation(150, 1, peer=True),
                   *self.mission()]
        for record in records:
            record.setdefault("run_id", "r1")
        with tempfile.TemporaryDirectory(prefix="receive-policy-audit-") as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps(manifest))
            (root / "expectations.json").write_text(json.dumps(COMPILER.compile_expectations(manifest)))
            for node in manifest["nodes"]:
                path = root / f"{node}_debug/events.jsonl"
                path.parent.mkdir()
                path.write_text("".join(
                    json.dumps(record) + "\n" for record in records
                    if record["node"].lower() == node))
            command = [
                sys.executable, "-B", str(SCRIPT_DIR / "delivery_audit_report.py"),
                "--debug-root", directory, "--run-id", "r1", "--test-id", "history",
                "--manifest", str(root / "manifest.json"),
                "--expectations", str(root / "expectations.json"),
            ]
            result = subprocess.run(command, cwd=directory, text=True, capture_output=True)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            report = json.loads((root / "test_reports/history.json").read_text())
            self.assertEqual(3, report["expected_deliveries"])
            self.assertEqual([], report["unexpected"])
            self.assertEqual([], report["evidence_errors"])
            (root / "platform_31_debug/events.jsonl").write_text("".join(
                json.dumps(record) + "\n" for record in records
                if record["node"] == "Platform_31" and record["event"] != "receive_policy"))
            result = subprocess.run(command, cwd=directory, text=True, capture_output=True)
            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            report = json.loads((root / "test_reports/history.json").read_text())
            self.assertEqual("inconclusive", report["verdict"])

    def test_restart_init_and_other_run_do_not_restore_old_interests(self):
        old = {**self.policy(400, "ActiveMission"), "run_id": "other-run"}
        report = self.mission_navigation([
            self.policy(1, "Init"), self.policy(100, "ActiveMission"),
            self.policy(200, "Init"), old, *self.navigation(450, 1), *self.mission(),
        ])
        self.assertEqual("pass", report["verdict"])

    def test_invalid_or_conflicting_policy_records_are_reported(self):
        for invalid in (
            {**self.policy(100, "Init"), "sent_at_ns": "100"},
            {**self.policy(100, "Init"), "receive_profile": "Unknown"},
            {**self.policy(100, "Init"), "teams": "TEAM_A"},
            self.policy(1, "ActiveMission"),
        ):
            with self.subTest(invalid=invalid):
                report = self.mission_navigation([
                    self.policy(1, "Init"), invalid, *self.navigation(150, 1), *self.mission(),
                ])
                self.assertEqual("inconclusive", report["verdict"])
                self.assertTrue(report["evidence_errors"])

    def test_policy_history_pruning_preserves_startup_and_window_predecessor(self):
        history = REPORT.ReceivePolicyHistory()
        for timestamp, profile in ((1, "Init"), (100, "ActiveMission"), (200, "Init"),
                                   (300, "ActiveMission"), (400, "Init")):
            history.add(self.policy(timestamp, profile))
        history.add({**self.policy(1, "Init"), "run_id": "old"})
        history.prune(350, "r1")
        self.assertEqual("ActiveMission", history.state_at("r1", "Platform_31", 350)["receive_profile"])
        self.assertEqual("Init", history.state_at("r1", "Platform_31", 450)["receive_profile"])
        self.assertFalse(history.has_node("old", "Platform_31"))
        self.assertEqual(3, len(history.records[("r1", "Platform_31")]))

    def test_shared_location_snapshot_includes_peers_not_source(self):
        manifest = {**MANIFEST, "run_id": "r1", "shared_platform_locations": True,
                    "nodes": ["control_20", "platform_30", "platform_31"]}
        expectation = COMPILER.compile_expectations(manifest)["topics"]["PlatformStatus"]
        self.assertEqual(["Control_20", "Platform_31"],
                         REPORT.expected_receivers({"source_node": "Platform_30"}, expectation))
        legacy = COMPILER.compile_expectations({**manifest, "shared_platform_locations": False})
        self.assertEqual(["Control_20"],
                         REPORT.expected_receivers({"source_node": "Platform_30"},
                                                   legacy["topics"]["PlatformStatus"]))

    def analyze(self, events, expectations=EXPECTATIONS):
        return REPORT.analyze(events, [Path("events.jsonl")], [], MANIFEST, expectations, "r1")

    def test_targeted_command_uses_snapshot_destination_rule(self):
        report = self.analyze([
            event("sent", "Mission", "Control_20", 1, "Control_20",
                destination="Platform_30"),
            event("received", "Mission", "Control_20", 1, "Platform_30"),
            event("sent", "PlatformStatus", "Platform_30", 1, "Platform_30"),
            event("received", "PlatformStatus", "Platform_30", 1, "Control_20"),
        ])
        self.assertEqual("pass", report["verdict"])

    def test_unknown_destination_is_not_counted_as_loss(self):
        expectations = {**EXPECTATIONS, "Mission": {**EXPECTATIONS["Mission"],
                        "enabled": False}}
        report = self.analyze([
            event("sent", "Mission", "Control_20", 1, "Control_20",
                destination="Platform_99"),
            event("sent", "PlatformStatus", "Platform_30", 1, "Platform_30"),
            event("received", "PlatformStatus", "Platform_30", 1, "Control_20"),
        ], expectations)
        self.assertEqual(1, len(report["not_expected"]))
        self.assertEqual("pass", report["verdict"])

    def test_receive_outside_snapshot_is_unexpected(self):
        report = self.analyze([
            event("sent", "Mission", "Control_20", 1, "Control_20",
                destination="Platform_30"),
            event("received", "Mission", "Control_20", 1, "Control_20"),
            event("sent", "PlatformStatus", "Platform_30", 1, "Platform_30"),
            event("received", "PlatformStatus", "Platform_30", 1, "Control_20"),
        ])
        self.assertEqual("fail", report["verdict"])
        self.assertEqual(1, len(report["unexpected"]))


if __name__ == "__main__":
    unittest.main()