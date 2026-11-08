#!/usr/bin/env python3
"""Run the DDS spike in owned, network-isolated, disposable Connext containers."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import tempfile
import time
from spike import (STAGE_SOURCE_FILES, snapshot_sources, source_hashes,
                   validate_capture_integrity, validate_provenance)


ROOT = Path(__file__).resolve().parents[2]


def run_repetition(args, output, repetition):
    manifest = json.loads((output / "manifest.json").read_text())
    orders = ("ABCD", "DCBA", "BADC")
    name = f"wan-dp-spike-{output.name}-{repetition}"
    target = output / f"rep-{repetition}"
    target.mkdir()
    command = [
        "docker", "run", "--rm", "--name", name, "--network", "none",
        "--cap-add", "NET_RAW", "--cap-add", "NET_ADMIN",
        "-v", f"{output / 'source'}:/workspace:ro",
        "-v", f"{args.license_dir.resolve()}:/shared:ro",
        "-v", f"{target}:/results", "-w", "/workspace",
        "-e", "RTI_LICENSE_FILE=/shared/rti_license.dat",
        "-e", "PYTHONPYCACHEPREFIX=/tmp/pycache",
        "-v", f"{output / 'manifest.json'}:/run-manifest.json:ro",
        "-e", f"WAN_DP_IMAGE_ID={manifest['image_id']}",
        "--entrypoint", "python3", manifest["image_id"],
        "spikes/wan_dp_route_tradeoffs/spike.py", "--output", "/results",
        "--manifest", "/run-manifest.json",
        "--idle-seconds", str(args.idle_seconds),
        "--variants", args.variants or orders[(repetition - 1) % len(orders)],
        "--repetition", str(repetition),
    ]
    print(f"Starting repetition {repetition}: {name}", flush=True)
    with (target / "container.log").open("w") as log:
        try:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True,
                           timeout=4 * (args.idle_seconds + 180) + 30)
        finally:
            state = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", name],
                                   capture_output=True, text=True)
            if state.returncode == 0 and state.stdout.strip() == "true":
                subprocess.run(["docker", "stop", "--time", "15", name], check=True,
                               stdout=subprocess.DEVNULL)
    print(f"Completed repetition {repetition}", flush=True)
    results = json.loads((target / "summary.json").read_text())
    for result in results:
        validate_provenance(result.get("execution_provenance"), manifest)
        validate_capture_integrity(result["capture"].get("integrity"), result["capture"])
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="connext:7.7.0")
    parser.add_argument("--license-dir", type=Path,
                        default=Path("/home/dgreenberg/rti_connext_dds-7.7.0"))
    parser.add_argument("--idle-seconds", type=int, default=150)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--variants", default="")
    args = parser.parse_args()
    if args.repetitions < 1 or args.parallel < 1:
        parser.error("repetitions and parallelism must be positive")
    if not (args.license_dir / "rti_license.dat").is_file():
        parser.error("license directory must contain rti_license.dat")
    subprocess.run(["docker", "image", "inspect", args.image], check=True,
                   stdout=subprocess.DEVNULL)
    output = Path(tempfile.mkdtemp(prefix="wan-dp-spike-"))
    print(f"Raw artifacts: {output}", flush=True)
    snapshot = snapshot_sources(output, STAGE_SOURCE_FILES)
    manifest = {
        "started_at": time.time(), "image": args.image,
        "image_id": subprocess.check_output(
            ["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True).strip(),
        "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                                text=True).strip(),
        "worktree_status": subprocess.check_output(["git", "status", "--short"], cwd=ROOT,
                                                   text=True),
        "execution_provenance_version": 1, "capture_integrity_version": 1,
        "source_hashes": source_hashes(STAGE_SOURCE_FILES, snapshot / "spikes/wan_dp_route_tradeoffs"),
        "input_hashes": {},
        "settings": dict(idle_seconds=args.idle_seconds, repetitions=args.repetitions,
                         concurrent_repetitions=args.parallel, network="none",
                         variants=args.variants or "balanced ABCD/DCBA/BADC"),
        "limitations": [
            "Stage 1 only; no real-router or EMANE runs",
            "Synthetic common TradeoffSample type and 64-byte payload on every topic",
            "Loopback unicast initial-peer probe range 0-31, not the live mesh RF topology",
            "Discovery categories are not assertion-only or correlated responses",
            "Concurrent repetitions share host CPU when parallelism is greater than one",
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = [executor.submit(run_repetition, args, output, repetition)
                   for repetition in range(1, args.repetitions + 1)]
        results = [result for future in futures for result in future.result()]
    (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"RESULTS={output / 'results.json'}", flush=True)


if __name__ == "__main__":
    main()
