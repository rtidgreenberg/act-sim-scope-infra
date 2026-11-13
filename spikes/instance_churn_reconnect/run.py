#!/usr/bin/env python3
"""Freeze inputs, own the isolated container, and retain failed as well as passed evidence."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from workload import positive_seconds
from priorities import PRIORITY_MODES, TOPIC_PRIORITY_MODES, topic_priorities


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from harness_v2.qos.wan_policy import resolve_wan_policy


def source_files(contract):
    files = [
        HERE / name for name in (
            "run.py", "spike.py", "workload.py", "qos.xml", "priorities.py", "priority_order.py", "wire.py")]
    files.extend(ROOT / name for name in (
        "harness_v2/qos/act_qos_profiles.xml", "harness_v2/qos/wan_policy.py",
        "harness_v2/datamodel/gen/ActTypes.xml"))
    if contract == "three_topic":
        files.extend(HERE.parent / "three_topic_wan" / name for name in (
            "run.py", "three_topic.py", "controls.py", "topics.py", "qos.xml", "compare.py",
            "repair_trace.py", "summarize.py"))
    return files


def main(contract="two_topic"):
    if contract not in ("two_topic", "three_topic"):
        raise ValueError(f"Unknown spike contract: {contract}")
    three_topic = contract == "three_topic"
    worker_directory = HERE.parent / "three_topic_wan" if three_topic else HERE
    worker = worker_directory / ("three_topic.py" if three_topic else "spike.py")
    default_priority = "sample_priority" if three_topic else "production"
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="connext:7.7.0")
    parser.add_argument("--license-dir", type=Path,
                        default=Path("/home/dgreenberg/rti_connext_dds-7.7.0"))
    parser.add_argument("--baseline", type=float, default=20)
    parser.add_argument("--recovery", type=float, default=20)
    parser.add_argument("--outage-seconds", type=positive_seconds, default=2)
    parser.add_argument("--drain-seconds", type=positive_seconds, default=10)
    parser.add_argument("--flow-profile", choices=("coarse", "fine", "burst"), default="coarse")
    parser.add_argument("--capture-wire", action="store_true")
    parser.add_argument("--family", choices=["Event"] if three_topic else ["Event", "Status", "both"],
                        default="Event" if three_topic else "both")
    parser.add_argument("--event-durability", choices=["production", "transient_local"],
                        default="production")
    parser.add_argument("--priority-mode", choices=("sample_priority",) if three_topic else PRIORITY_MODES,
                        default=default_priority)
    parser.add_argument("--topic-priority", action="append", default=[], metavar="TOPIC=INTEGER")
    parser.add_argument("--pattern", choices=["steady", "short", "revival"], default="steady")
    parser.add_argument("--offset", type=float, default=0)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--control-only", action="store_true")
    mode.add_argument("--outage-only", action="store_true")
    if three_topic:
        sys.path.insert(0, str(worker_directory))
        from topics import COMPARISON_CASES
        parser.add_argument("--comparison-case", choices=COMPARISON_CASES, default="baseline")
    parser.add_argument("--ordering-only", action="store_true",
                        help="assert queued command ordering with equal/reversed priorities")
    parser.add_argument("--unfiltered", action="store_true")
    args = parser.parse_args()
    try:
        topic_priorities(args.topic_priority)
    except ValueError as error:
        parser.error(str(error))
    if args.topic_priority and args.priority_mode not in TOPIC_PRIORITY_MODES:
        parser.error("--topic-priority requires a sample-priority or channel mode")
    if args.ordering_only and (args.topic_priority or args.priority_mode != default_priority):
        parser.error("--ordering-only runs its own fixed priority comparisons")
    if args.ordering_only and (args.capture_wire or args.flow_profile != "coarse"):
        parser.error("--ordering-only uses its own controller, without workload capture")
    if args.ordering_only and three_topic and args.comparison_case != "baseline":
        parser.error("--ordering-only qualifies the default layout; use the workload for comparison cases")
    if args.flow_profile != "coarse" and args.priority_mode == "production":
        parser.error("--flow-profile requires an experimental priority mode")
    if args.priority_mode == "priority_bands_batch" and (
            args.flow_profile != "coarse" or args.capture_wire):
        parser.error("batch experiment requires coarse tokens and separately qualified wire decoding")
    if args.priority_mode != "production" and (
            args.family != "Event" or args.event_durability != "production"):
        parser.error("priority experiments require --family Event and production durability")
    policy = resolve_wan_policy(os.environ)
    if not (args.license_dir / "rti_license.dat").is_file():
        parser.error("license directory must contain rti_license.dat")
    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True).strip()
    metadata = ROOT / ".spike-source.json"
    if metadata.exists():
        checkout = json.loads(metadata.read_text())
        report_root = Path(checkout["repository"])
    else:
        report_root = ROOT
        checkout = {
            "source_commit": subprocess.check_output(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
            "source_status": subprocess.check_output(
                ["git", "-C", str(ROOT), "status", "--short"], text=True),
        }
    run_id = time.strftime("%Y%m%dT%H%M%S") + f"-{time.time_ns() % 1000000}"
    report_prefix = "three_topic_wan" if three_topic else "instance_churn_reconnect"
    report = report_root / "debug/test_reports" / f"{report_prefix}_{run_id}"
    report.mkdir(parents=True, exist_ok=False)
    capture_directory = report_root / "debug/pcap" / report_prefix / run_id
    if args.capture_wire:
        capture_directory.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="instance-churn-reconnect-") as directory:
        work = Path(directory)
        staged = work / "workspace"
        files = source_files(contract)
        hashes = {}
        for source in files:
            relative = source.relative_to(ROOT)
            destination = staged / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            hashes[str(relative)] = hashlib.sha256(destination.read_bytes()).hexdigest()
        manifest = {
            "image": image, "source_commit": checkout["source_commit"],
            "source_status": checkout["source_status"],
            "source_hashes": hashes, "arguments": {
                name: str(value) if isinstance(value, Path) else value
                for name, value in vars(args).items()},
            "network": "none", "production_changes": False, "contract": contract,
            "wan_policy_environment": policy,
            "capture_directory": str(capture_directory) if args.capture_wire else None,
        }
        (report / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        name = f"act-churn-{run_id}"
        command = [
            "docker", "run", "--rm", "--name", name, "--network", "none",
            "--cap-add", "NET_ADMIN",
            "-v", f"{staged}:/workspace:ro", "-v", f"{report}:/results",
            "-v", f"{args.license_dir.resolve()}:/shared:ro",
            "-e", "RTI_LICENSE_FILE=/shared/rti_license.dat",
            "-e", "PYTHONPATH=/workspace",
            "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "ACT_INSTANCE_CHURN_ISOLATED=1",
            "--entrypoint", "python3", image,
            "/workspace/" + str(worker.relative_to(ROOT)),
            "--output", "/results", "--baseline", str(args.baseline),
            "--recovery", str(args.recovery), "--family", args.family,
            "--outage-seconds", str(args.outage_seconds),
            "--pattern", args.pattern, "--offset", str(args.offset),
            "--event-durability", args.event_durability,
            "--priority-mode", args.priority_mode,
            "--flow-profile", args.flow_profile, "--drain-seconds", str(args.drain_seconds),
        ]
        if args.capture_wire:
            command.extend(["--capture-wire", "--capture-dir", "/captures"])
            index = command.index("--entrypoint")
            command[index:index] = ["-v", f"{capture_directory}:/captures"]
        for override in args.topic_priority:
            command.extend(["--topic-priority", override])
        if three_topic:
            command.extend(["--comparison-case", args.comparison_case])
        if args.ordering_only:
            worker_index = command.index("/workspace/" + str(worker.relative_to(ROOT)))
            command[worker_index:] = [
                "/workspace/" + str((worker_directory / (
                    "controls.py" if three_topic else "priority_order.py")).relative_to(ROOT)),
                "--output", "/results/results.json"]
        for key, value in policy.items():
            index = command.index("--entrypoint")
            command[index:index] = ["-e", f"{key}={value}"]
        if args.control_only and not args.ordering_only:
            command.append("--control-only")
        if args.outage_only and not args.ordering_only:
            command.append("--outage-only")
        if args.unfiltered and not args.ordering_only:
            command.append("--unfiltered")
        print(f"Owned container {name}; retained report {report}", flush=True)
        try:
            completed = subprocess.run(
                command, timeout=600 + 4 * (
                    args.baseline + args.recovery + args.outage_seconds + args.drain_seconds),
                capture_output=True, text=True)
            (report / "console.txt").write_text(completed.stdout + completed.stderr)
            print(completed.stdout, end="", flush=True)
            if completed.stderr:
                print(completed.stderr, end="", flush=True)
        finally:
            state = subprocess.run(["docker", "container", "inspect", name],
                                   capture_output=True, text=True)
            if state.returncode == 0:
                subprocess.run(["docker", "stop", "--time", "10", name], check=True)
        if not (report / "results.json").is_file():
            raise RuntimeError(f"Worker did not produce results; see {report}")
        print(f"Report: {report}", flush=True)
        raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
