#!/usr/bin/env python3
"""Repeat the recovery matrix sequentially from one immutable input snapshot."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "instance_churn_reconnect"))
from run import source_files
from topics import COMPARISON_CASES, TRADEOFF_CASES
from summarize import summary, write_report
from workload import positive_seconds
sys.path.insert(0, str(ROOT))
from harness_v2.qos.wan_policy import resolve_wan_policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", choices=COMPARISON_CASES,
                        default=["mixed_last", "bands_all", "bands_last", "baseline", "none_all"])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--image", default="connext:7.7.0")
    parser.add_argument("--baseline", type=positive_seconds, default=20)
    parser.add_argument("--recovery", type=positive_seconds, default=40)
    parser.add_argument("--outage-seconds", type=positive_seconds, default=120)
    parser.add_argument("--capture-wire", action="store_true")
    parser.add_argument("--tradeoffs", action="store_true",
                        help="run the lossless topology/retention/suppression matrix")
    parser.add_argument("--include-healthy", action="store_true")
    parser.add_argument("--healthy-seconds", type=positive_seconds, default=20)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if min(args.baseline, args.recovery) < 2:
        parser.error("--baseline and --recovery must be at least 2 seconds")
    if args.healthy_seconds < 4:
        parser.error("--healthy-seconds must be at least 4 seconds")
    if args.tradeoffs:
        args.cases = list(TRADEOFF_CASES)
    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True).strip()
    policy = resolve_wan_policy(os.environ)
    report = ROOT / "debug/test_reports" / (
        "three_topic_comparison_" + time.strftime("%Y%m%dT%H%M%S") + f"-{time.time_ns() % 1000000}")
    report.mkdir(parents=True, exist_ok=False)
    source = {
        "repository": str(ROOT), "source_commit": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "source_status": subprocess.check_output(
            ["git", "-C", str(ROOT), "status", "--short"], text=True),
    }
    results = []
    with tempfile.TemporaryDirectory(prefix="three-topic-comparison-") as directory:
        snapshot = Path(directory) / "workspace"
        hashes = {}
        for path in source_files("three_topic"):
            relative = path.relative_to(ROOT)
            data = path.read_bytes()
            destination = snapshot / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            hashes[str(relative)] = hashlib.sha256(data).hexdigest()
        (snapshot / ".spike-source.json").write_text(json.dumps(source, indent=2) + "\n")
        with tarfile.open(report / "inputs.tar.gz", "w:gz") as archive:
            for name in (*hashes, ".spike-source.json"):
                archive.add(snapshot / name, arcname=name)
        batch_manifest = {
            **source, "image": image, "source_hashes": hashes, "policy": policy,
            "arguments": vars(args), "execution": "sequential; reverse order on alternate repeats",
            "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host_cpu_count": os.cpu_count(), "host_load_average_start": list(os.getloadavg()),
            "host_scope": "shared VM; private networks, not dedicated CPU or memory",
        }
        (report / "manifest.json").write_text(json.dumps(batch_manifest, indent=2) + "\n")
        print(f"Frozen comparison: {report}", flush=True)
        for repeat in range(args.repeats):
            cases = args.cases if repeat % 2 == 0 else list(reversed(args.cases))
            for case in cases:
                for outage in ((False, True) if args.include_healthy else (True,)):
                    baseline = args.baseline if outage else args.healthy_seconds / 2
                    recovery = args.recovery if outage else args.healthy_seconds / 2
                    scenario = "outage" if outage else "healthy"
                    command = [
                        sys.executable, str(snapshot / "spikes/three_topic_wan/run.py"),
                        "--image", image, "--comparison-case", case,
                        "--outage-only" if outage else "--control-only",
                        "--baseline", str(baseline), "--recovery", str(recovery),
                        "--outage-seconds", str(args.outage_seconds), "--drain-seconds", "30",
                    ]
                    if args.capture_wire:
                        command.append("--capture-wire")
                    trial_started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    load_started = list(os.getloadavg())
                    completed = subprocess.run(
                        command, capture_output=True, text=True, env={**os.environ, **policy},
                        timeout=600 + 4 * (baseline + recovery + args.outage_seconds + 30))
                    (report / f"{repeat + 1}-{case}-{scenario}.console.txt").write_text(
                        completed.stdout + completed.stderr)
                    lines = [line.removeprefix("Report: ") for line in completed.stdout.splitlines()
                             if line.startswith("Report: ")]
                    if completed.returncode not in (0, 1) or len(lines) != 1:
                        raise RuntimeError(f"Comparison worker failed to report: {case}; see {report}")
                    run_report = Path(lines[0])
                    manifest = json.loads((run_report / "manifest.json").read_text())
                    if manifest["source_hashes"] != hashes or manifest["image"] != image:
                        raise RuntimeError(f"Frozen input mismatch: {run_report}")
                    raw = json.loads((run_report / "results.json").read_text())
                    if len(raw) != 1 or "effective_qos" not in raw[0]:
                        raise RuntimeError(f"Incomplete comparison result: {run_report}")
                    entry = {**summary(raw[0]), "repeat": repeat + 1, "report": str(run_report),
                             "started_at_utc": trial_started,
                             "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             "host_load_average_start": load_started,
                             "host_load_average_end": list(os.getloadavg())}
                    results.append(entry)
                    (report / "comparison.json").write_text(json.dumps(results, indent=2) + "\n")
                    print(json.dumps({name: entry[name] for name in (
                        "case", "scenario", "repeat", "passed", "recovery_s", "fresh_rtt_max_s",
                        "correctness_passed", "errors")}), flush=True)
    batch_manifest["completed_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (report / "manifest.json").write_text(json.dumps(batch_manifest, indent=2) + "\n")
    write_report(report)
    print(f"Comparison: {report}", flush=True)


if __name__ == "__main__":
    main()
