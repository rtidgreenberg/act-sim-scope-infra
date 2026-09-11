#!/usr/bin/env python3
"""Create a compact delivery-audit verdict from node-owned JSONL event logs."""

import argparse
import html
import json
import os
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile


AUDITED_RECIPIENTS = {
    "ControlCommand": lambda event: [event["destination"]],
    "PlatformInitStatus": lambda event: ["Control_20"],
}


def load_events(debug_root, run_id, nodes):
    events = []
    errors = []
    event_paths = [Path(debug_root) / f"{node}_debug/events.jsonl" for node in nodes]
    for path in event_paths:
        if not path.exists():
            errors.append(f"missing event log {path}")
            continue
        with path.open(encoding="utf-8") as event_file:
            for line_number, line in enumerate(event_file, 1):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    errors.append(f"invalid JSON in {path}:{line_number}: {error.msg}")
                    continue
                if event.get("run_id") == run_id:
                    required = {"event", "node", "topic", "source_node", "sequence"}
                    missing = sorted(required - event.keys())
                    if missing:
                        errors.append(f"missing {', '.join(missing)} in {path}:{line_number}")
                    else:
                        events.append(event)
    return events, [path for path in event_paths if path.exists()], errors


def load_manifest(path, run_id):
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {}, [f"invalid manifest {path}: {error}"]
    if manifest.get("run_id") != run_id:
        return {}, [f"manifest run_id does not match {run_id}"]
    topics = manifest.get("topics")
    nodes = manifest.get("nodes")
    if not isinstance(nodes, list) or not nodes or not all(isinstance(node, str) for node in nodes):
        return {}, ["manifest has no node list"]
    if not isinstance(topics, dict) or not topics:
        return {}, ["manifest has no topic requirements"]
    for topic, requirement in topics.items():
        if not isinstance(requirement, dict):
            return {}, [f"manifest topic {topic} is not an object"]
        if not isinstance(requirement.get("minimum_sent"), int):
            return {}, [f"manifest topic {topic} has no integer minimum_sent"]
        if not isinstance(requirement.get("completion_threshold_percent"), (int, float)):
            return {}, [f"manifest topic {topic} has no completion threshold"]
    return {"nodes": nodes, "topics": topics}, []


def analyze(events, event_paths, evidence_errors, manifest, run_id):
    requirements = manifest.get("topics", {})
    sends = [event for event in events if event.get("event") == "sent"
             and event.get("topic") in AUDITED_RECIPIENTS]
    receives = [event for event in events if event.get("event") == "received"
                and event.get("topic") in AUDITED_RECIPIENTS]
    received_keys = Counter(
        (event["topic"], event["source_node"], event["sequence"], event["node"])
        for event in receives)
    expected = []
    for sent in sends:
        for receiver in AUDITED_RECIPIENTS[sent["topic"]](sent):
            expected.append((sent, receiver))

    missing = []
    duplicates = []
    for sent, receiver in expected:
        key = (sent["topic"], sent["source_node"], sent["sequence"], receiver)
        count = received_keys.pop(key, 0)
        if count == 0:
            missing.append({"topic": sent["topic"], "source_node": sent["source_node"],
                            "sequence": sent["sequence"], "receiver_node": receiver})
        elif count > 1:
            duplicates.append({"topic": sent["topic"], "source_node": sent["source_node"],
                               "sequence": sent["sequence"], "receiver_node": receiver,
                               "count": count})
    unexpected = [
        {"topic": topic, "source_node": source, "sequence": sequence,
         "receiver_node": receiver, "count": count}
        for (topic, source, sequence, receiver), count in sorted(received_keys.items())
    ]
    topic_names = sorted(requirements)
    topics = {}
    for topic in topic_names:
        requirement = requirements[topic]
        topic_sends = [event for event in sends if event["topic"] == topic]
        topic_expected = [item for item in expected if item[0]["topic"] == topic]
        topic_missing = [item for item in missing if item["topic"] == topic]
        topic_duplicates = [item for item in duplicates if item["topic"] == topic]
        topic_unexpected = [item for item in unexpected if item["topic"] == topic]
        received_expected = len(topic_expected) - len(topic_missing)
        completion_percent = (100 * received_expected / len(topic_expected)) \
            if topic_expected else 0
        topic_verdict = "pass"
        if evidence_errors or len(topic_sends) < requirement["minimum_sent"]:
            topic_verdict = "inconclusive"
        elif topic_missing or topic_duplicates or topic_unexpected \
                or completion_percent < requirement["completion_threshold_percent"]:
            topic_verdict = "fail"
        topics[topic] = {
            "verdict": topic_verdict,
            "priority": requirement.get("priority", "unspecified"),
            "minimum_sent": requirement["minimum_sent"],
            "completion_threshold_percent": requirement["completion_threshold_percent"],
            "sent": len(topic_sends),
            "expected_deliveries": len(topic_expected),
            "received_expected_deliveries": received_expected,
            "missing": len(topic_missing),
            "duplicates": len(topic_duplicates),
            "unexpected": len(topic_unexpected),
            "completion_percent": completion_percent,
        }
    if not event_paths or evidence_errors:
        verdict = "inconclusive"
    elif any(topic["verdict"] == "fail" for topic in topics.values()):
        verdict = "fail"
    elif any(topic["verdict"] == "inconclusive" for topic in topics.values()):
        verdict = "inconclusive"
    else:
        verdict = "pass"
    return {
        "run_id": run_id,
        "verdict": verdict,
        "event_log_paths": [str(path) for path in event_paths],
        "evidence_errors": evidence_errors,
        "sent": len(sends),
        "expected_deliveries": len(expected),
        "received_expected_deliveries": len(expected) - len(missing),
        "missing": missing,
        "duplicates": duplicates,
        "unexpected": unexpected,
        "topics": topics,
        "completion_percent": (100 * (len(expected) - len(missing)) / len(expected))
        if expected else 0,
    }


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
        output.write(content)
        temporary_name = output.name
    os.replace(temporary_name, path)


def render_html(report):
    rows = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{html.escape(str(value))}</td></tr>"
        for name, value in (
            ("Verdict", report["verdict"]),
            ("Sent", report["sent"]),
            ("Expected deliveries", report["expected_deliveries"]),
            ("Received expected deliveries", report["received_expected_deliveries"]),
            ("Completion", f'{report["completion_percent"]:.1f}%'),
            ("Missing", len(report["missing"])),
            ("Unexpected", len(report["unexpected"])),
        ))
    detail = html.escape(json.dumps(report, indent=2, sort_keys=True))
    topic_rows = "".join(
        "<tr>" + "".join(
            f"<td>{html.escape(str(value))}</td>"
            for value in (topic, summary["verdict"], summary["sent"],
                          summary["expected_deliveries"],
                          summary["received_expected_deliveries"],
                          summary["priority"], f'{summary["completion_percent"]:.1f}%')) + "</tr>"
        for topic, summary in report["topics"].items())
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><title>Delivery audit</title>"
            "</head><body><h1>Delivery audit</h1><table>" + rows +
            "</table><h2>Topic Completion</h2><table><tr><th>Topic</th><th>Verdict</th>"
            "<th>Sent</th><th>Expected</th><th>Received</th><th>Priority</th><th>Completion</th></tr>" +
            topic_rows + "</table><h2>Details</h2><pre>" + detail + "</pre></body></html>\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-root", default="debug")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--test-id", required=True)
    parser.add_argument("--manifest", required=True)
    arguments = parser.parse_args()
    manifest, manifest_errors = load_manifest(arguments.manifest, arguments.run_id)
    events, paths, evidence_errors = load_events(arguments.debug_root, arguments.run_id,
                                                 manifest.get("nodes", []))
    report = analyze(events, paths, evidence_errors + manifest_errors, manifest,
                     arguments.run_id)
    report["test_id"] = arguments.test_id
    report_root = Path(arguments.debug_root) / "test_reports"
    atomic_write(report_root / f"{arguments.test_id}.json",
                 json.dumps(report, indent=2, sort_keys=True) + "\n")
    atomic_write(report_root / f"{arguments.test_id}.html", render_html(report))
    print(f"delivery audit: {report['verdict']} ({report['received_expected_deliveries']}/"
          f"{report['expected_deliveries']} expected deliveries)")
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())