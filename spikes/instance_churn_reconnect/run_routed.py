#!/usr/bin/env python3
"""Build a frozen private router and run the actual adapter churn checks."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from experiment import transient_local_snapshot
from workload import positive_seconds


ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=float, default=20)
    parser.add_argument("--recovery", type=float, default=20)
    parser.add_argument("--outage-seconds", type=positive_seconds, default=2)
    parser.add_argument("--family", choices=["Event", "Status", "both"], default="both")
    parser.add_argument("--event-durability", choices=["production", "transient_local"],
                        default="production")
    parser.add_argument("--yaml-cpp-source", type=Path)
    parser.add_argument("--license-dir", type=Path,
                        default=Path("/home/dgreenberg/rti_connext_dds-7.7.0"))
    args = parser.parse_args()
    if min(args.baseline, args.recovery) < 2:
        parser.error("baseline/recovery must be >=2s")
    if not (args.license_dir / "rti_license.dat").is_file():
        parser.error("license directory must contain rti_license.dat")
    if args.yaml_cpp_source and not (args.yaml_cpp_source / "CMakeLists.txt").is_file():
        parser.error("yaml-cpp source must contain CMakeLists.txt")
    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", "connext:7.7.0"],
        text=True).strip()
    run_id = time.strftime("%Y%m%dT%H%M%S") + f"-{time.time_ns() % 1000000}"
    report = ROOT / "debug/test_reports" / f"instance_churn_routed_{run_id}"
    report.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="instance-churn-routed-") as directory:
        work = Path(directory)
        staged = work / "workspace"
        for relative in ("router", "harness_v2/datamodel", "harness_v2/qos",
                         "spikes/instance_churn_reconnect"):
            shutil.copytree(
                ROOT / relative, staged / relative,
                ignore=shutil.ignore_patterns("build", "build-*", "__pycache__", ".pytest_cache"))
        (staged / "debug/logs/router_e2e").mkdir(parents=True)
        hashes = {str(path.relative_to(staged)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in staged.rglob("*") if path.is_file()}
        changed = [name for name, digest in hashes.items()
                   if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
        if changed:
            raise RuntimeError(f"Source changed while freezing snapshot: {changed}")
        if args.event_durability == "transient_local":
            transient_local_snapshot(staged)
        effective_qos_hash = hashlib.sha256(
            (staged / "harness_v2/qos/act_qos_profiles.xml").read_bytes()).hexdigest()
        (report / "manifest.json").write_text(json.dumps({
            "image": image, "source_hashes": hashes,
            "source_commit": subprocess.check_output(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
            "source_status": subprocess.check_output(
                ["git", "-C", str(ROOT), "status", "--short"], text=True),
            "baseline_s": args.baseline, "recovery_s": args.recovery,
            "outage_s": args.outage_seconds,
            "family": args.family, "event_durability_experiment": args.event_durability,
            "effective_private_qos_sha256": effective_qos_hash,
            "runtime_network": "none", "production_binary_overwritten": False,
        }, indent=2) + "\n")
        user = f"{os.getuid()}:{os.getgid()}"
        build_name, test_name = f"act-churn-build-{run_id}", f"act-churn-routed-{run_id}"
        build = [
            "docker", "run", "--rm", "--name", build_name, "--network", "bridge",
            "--user", user, "-v", f"{staged}:/workspace:rw",
            "-e", "HOME=/tmp", "-e", "PYTHONPYCACHEPREFIX=/tmp/pycache",
            "-e", "NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0",
        ]
        cmake_extra = ""
        if args.yaml_cpp_source:
            build.extend(["-v", f"{args.yaml_cpp_source.resolve()}:/yaml-cpp:ro"])
            cmake_extra = " -DFETCHCONTENT_SOURCE_DIR_YAML-CPP=/yaml-cpp"
        build.extend([
            "--entrypoint", "sh", image, "-ec",
            "python3 -m pip install --quiet --target /workspace/.test-deps pytest && "
            "cmake -S /workspace/router -B /workspace/router/build "
            "-DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0" + cmake_extra + " && "
            "cmake --build /workspace/router/build --target router_main -j2",
        ])
        test = [
            "docker", "run", "--rm", "--name", test_name, "--network", "none",
            "--cap-add", "NET_ADMIN",
            "-v", f"{staged}:/workspace:ro", "-v", f"{report}:/results",
            "-v", f"{args.license_dir.resolve()}:/shared:ro",
            "--tmpfs", "/workspace/debug/logs/router_e2e:rw,mode=1777",
            "-w", "/workspace",
            "-e", "RTI_LICENSE_FILE=/shared/rti_license.dat",
            "-e", "NDDSHOME=/opt/rti.com/rti_connext_dds-7.7.0",
            "-e", "PYTHONPYCACHEPREFIX=/tmp/pycache",
            "-e", "PYTHONPATH=/workspace/.test-deps:/workspace/router/test_e2e:/workspace/spikes/instance_churn_reconnect",
            "-e", "ACT_INSTANCE_CHURN_ISOLATED=1", "-e", "CHURN_RESULTS=/results",
            "-e", f"CHURN_BASELINE={args.baseline}", "-e", f"CHURN_RECOVERY={args.recovery}",
            "-e", f"CHURN_OUTAGE_SECONDS={args.outage_seconds}",
            "-e", f"CHURN_EVENT_DURABILITY={args.event_durability}",
            "--entrypoint", "python3", image, "-m", "pytest", "-p", "conftest",
            "-p", "no:cacheprovider", "-q", "--basetemp=/tmp/churn-pytest",
            "--junitxml=/results/junit.xml",
            "/workspace/spikes/instance_churn_reconnect/test_routed_churn.py",
        ]
        if args.family != "both":
            test.extend(["-k", args.family])
        print(f"Frozen private build; retained report {report}", flush=True)
        try:
            preparation = subprocess.run(build, capture_output=True, text=True, timeout=600)
            (report / "build.txt").write_text(preparation.stdout + preparation.stderr)
            if preparation.returncode:
                print(preparation.stdout + preparation.stderr, flush=True)
                raise SystemExit(preparation.returncode)
            completed = subprocess.run(
                test, capture_output=True, text=True,
                timeout=480 + 4 * (args.baseline + args.recovery + args.outage_seconds))
            (report / "console.txt").write_text(completed.stdout + completed.stderr)
            print(completed.stdout + completed.stderr, flush=True)
            print(f"Report: {report}", flush=True)
            raise SystemExit(completed.returncode)
        finally:
            for name in (build_name, test_name):
                state = subprocess.run(["docker", "container", "inspect", name],
                                       capture_output=True, text=True)
                if state.returncode == 0:
                    subprocess.run(["docker", "stop", "--time", "10", name], check=True)


if __name__ == "__main__":
    main()
