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


def load_events(debug_root, run_id):
    events = []
    event_paths = sorted(Path(debug_root).glob("*_debug/events.jsonl"))
    for path in event_paths:
        with path.open(encoding="utf-8") as event_file:
            for line_number, line in enumerate(event_file, 1):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"invalid JSON in {path}:{line_number}") from error
                if event.get("run_id") == run_id:
                    events.append(event)
    return events, event_paths


def analyze(events, event_paths, run_id):
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
    if not event_paths or not sends:
        verdict = "inconclusive"
    elif missing or duplicates or unexpected:
        verdict = "fail"
    else:
        verdict = "pass"
    return {
        "run_id": run_id,
        "verdict": verdict,
        "event_log_paths": [str(path) for path in event_paths],
        "sent": len(sends),
        "expected_deliveries": len(expected),
        "received_expected_deliveries": len(expected) - len(missing),
        "missing": missing,
        "duplicates": duplicates,
        "unexpected": unexpected,
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
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><title>Delivery audit</title>"
            "</head><body><h1>Delivery audit</h1><table>" + rows +
            "</table><h2>Details</h2><pre>" + detail + "</pre></body></html>\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-root", default="debug")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--test-id", required=True)
    arguments = parser.parse_args()
    events, paths = load_events(arguments.debug_root, arguments.run_id)
    report = analyze(events, paths, arguments.run_id)
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