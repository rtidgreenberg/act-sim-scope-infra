#!/usr/bin/env python3
"""Focused unit tests for immutable delivery-audit expectation snapshots."""

import importlib.util
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("delivery_audit_report",
                                              SCRIPT_DIR / "delivery_audit_report.py")
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


MANIFEST = {
    "nodes": ["control_20", "platform_30"],
    "topics": {
        "ControlCommand": {"minimum_sent": 1, "completion_threshold_percent": 100},
        "PlatformInitStatus": {"minimum_sent": 1, "completion_threshold_percent": 100},
    },
}
EXPECTATIONS = {
    "ControlCommand": {
        "enabled": True, "source_nodes": ["control_20"],
        "recipient_rule": {"kind": "sample_destination", "nodes": ["platform_30"]},
    },
    "PlatformInitStatus": {
        "enabled": True, "source_nodes": ["platform_30"],
        "recipient_rule": {"kind": "fixed_nodes", "nodes": ["control_20"]},
    },
}


def event(kind, topic, source, sequence, node, **extra):
    return {"event": kind, "topic": topic, "source_node": source,
            "sequence": sequence, "node": node, **extra}


class DeliveryAuditExpectationTests(unittest.TestCase):
    def analyze(self, events, expectations=EXPECTATIONS):
        return REPORT.analyze(events, [Path("events.jsonl")], [], MANIFEST, expectations, "r1")

    def test_targeted_command_uses_snapshot_destination_rule(self):
        report = self.analyze([
            event("sent", "ControlCommand", "control_20", 1, "control_20",
                  destination="platform_30"),
            event("received", "ControlCommand", "control_20", 1, "platform_30"),
            event("sent", "PlatformInitStatus", "platform_30", 1, "platform_30"),
            event("received", "PlatformInitStatus", "platform_30", 1, "control_20"),
        ])
        self.assertEqual("pass", report["verdict"])

    def test_unknown_destination_is_not_counted_as_loss(self):
        expectations = {**EXPECTATIONS, "ControlCommand": {**EXPECTATIONS["ControlCommand"],
                        "enabled": False}}
        report = self.analyze([
            event("sent", "ControlCommand", "control_20", 1, "control_20",
                  destination="platform_99"),
            event("sent", "PlatformInitStatus", "platform_30", 1, "platform_30"),
            event("received", "PlatformInitStatus", "platform_30", 1, "control_20"),
        ], expectations)
        self.assertEqual(1, len(report["not_expected"]))
        self.assertEqual("pass", report["verdict"])

    def test_receive_outside_snapshot_is_unexpected(self):
        report = self.analyze([
            event("sent", "ControlCommand", "control_20", 1, "control_20",
                  destination="platform_30"),
            event("received", "ControlCommand", "control_20", 1, "control_20"),
            event("sent", "PlatformInitStatus", "platform_30", 1, "platform_30"),
            event("received", "PlatformInitStatus", "platform_30", 1, "control_20"),
        ])
        self.assertEqual("fail", report["verdict"])
        self.assertEqual(1, len(report["unexpected"]))


if __name__ == "__main__":
    unittest.main()