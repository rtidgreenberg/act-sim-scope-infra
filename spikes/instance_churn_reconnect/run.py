#!/usr/bin/env python3
"""Freeze inputs, own the isolated container, and retain failed as well as passed evidence."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from workload import positive_seconds


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="connext:7.7.0")
    parser.add_argument("--license-dir", type=Path,
                        default=Path("/home/dgreenberg/rti_connext_dds-7.7.0"))
    parser.add_argument("--baseline", type=float, default=20)
    parser.add_argument("--recovery", type=float, default=20)
    parser.add_argument("--outage-seconds", type=positive_seconds, default=2)
    parser.add_argument("--family", choices=["Event", "Status", "both"], default="both")
    parser.add_argument("--event-durability", choices=["production", "transient_local"],
                        default="production")
    parser.add_argument("--pattern", choices=["steady", "short", "revival"], default="steady")
    parser.add_argument("--offset", type=float, default=0)
    parser.add_argument("--control-only", action="store_true")
    parser.add_argument("--unfiltered", action="store_true")
    args = parser.parse_args()
    if not (args.license_dir / "rti_license.dat").is_file():
        parser.error("license directory must contain rti_license.dat")
    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True).strip()
    run_id = time.strftime("%Y%m%dT%H%M%S") + f"-{time.time_ns() % 1000000}"
    report = ROOT / "debug/test_reports" / f"instance_churn_reconnect_{run_id}"
    report.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="instance-churn-reconnect-") as directory:
        work = Path(directory)
        staged = work / "workspace"
        files = [
            HERE / "spike.py", HERE / "workload.py", HERE / "qos.xml",
            ROOT / "harness_v2/qos/act_qos_profiles.xml",
            ROOT / "harness_v2/datamodel/gen/ActTypes.xml",
        ]
        hashes = {}
        for source in files:
            relative = source.relative_to(ROOT)
            destination = staged / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            hashes[str(relative)] = hashlib.sha256(destination.read_bytes()).hexdigest()
        manifest = {
            "image": image, "source_commit": subprocess.check_output(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
            "source_status": subprocess.check_output(
                ["git", "-C", str(ROOT), "status", "--short"], text=True),
            "source_hashes": hashes, "arguments": {
                name: str(value) if isinstance(value, Path) else value
                for name, value in vars(args).items()},
            "network": "none", "production_changes": False,
        }
        (report / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        name = f"act-churn-{run_id}"
        command = [
            "docker", "run", "--rm", "--name", name, "--network", "none",
            "--cap-add", "NET_ADMIN",
            "-v", f"{staged}:/workspace:ro", "-v", f"{report}:/results",
            "-v", f"{args.license_dir.resolve()}:/shared:ro",
            "-e", "RTI_LICENSE_FILE=/shared/rti_license.dat",
            "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "ACT_INSTANCE_CHURN_ISOLATED=1",
            "--entrypoint", "python3", image,
            "/workspace/spikes/instance_churn_reconnect/spike.py",
            "--output", "/results", "--baseline", str(args.baseline),
            "--recovery", str(args.recovery), "--family", args.family,
            "--outage-seconds", str(args.outage_seconds),
            "--pattern", args.pattern, "--offset", str(args.offset),
            "--event-durability", args.event_durability,
        ]
        if args.control_only:
            command.append("--control-only")
        if args.unfiltered:
            command.append("--unfiltered")
        print(f"Owned container {name}; retained report {report}", flush=True)
        try:
            completed = subprocess.run(
                command, timeout=600 + 4 * (args.baseline + args.recovery + args.outage_seconds),
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
