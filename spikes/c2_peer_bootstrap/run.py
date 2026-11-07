#!/usr/bin/env python3
"""Own the isolated container and retain only its compact measured results."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="connext:7.7.0")
    parser.add_argument("--license-dir", type=Path,
                        default=Path("/home/dgreenberg/rti_connext_dds-7.7.0"))
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--report", type=Path,
                        default=ROOT / "debug/test_reports/c2_peer_bootstrap.json")
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    if not (args.license_dir / "rti_license.dat").is_file():
        parser.error("license directory must contain rti_license.dat")
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.image],
        text=True).strip()
    with tempfile.TemporaryDirectory(prefix="c2-peer-bootstrap-") as temporary:
        work = Path(temporary)
        staged = work / "source"
        staged.mkdir()
        for name in ("spike.py", "qos.xml"):
            shutil.copyfile(SOURCE / name, staged / name)
        results = work / "results"
        results.mkdir()
        name = work.name
        command = [
            "docker", "run", "--rm", "--name", name, "--network", "none",
            "-v", f"{staged}:/spike:ro", "-v", f"{results}:/results",
            "-v", f"{args.license_dir.resolve()}:/shared:ro", "-w", "/spike",
            "-e", "RTI_LICENSE_FILE=/shared/rti_license.dat",
            "-e", "PYTHONPYCACHEPREFIX=/tmp/pycache",
            "--entrypoint", "python3", image_id,
            "spike.py", "--output", "/results",
            "--repetitions", str(args.repetitions),
        ]
        print(f"Owned container: {name}; network=none; image={image_id}", flush=True)
        try:
            subprocess.run(command, check=True, timeout=args.repetitions * 300)
        finally:
            state = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Running}}", name],
                capture_output=True, text=True)
            if state.returncode == 0 and state.stdout.strip() == "true":
                subprocess.run(["docker", "stop", "--time", "15", name], check=True)
        measured = json.loads((results / "results.json").read_text())
        if len(measured) != args.repetitions * 2 or not all(r["passed"] for r in measured):
            raise RuntimeError("incomplete or failed protocol results")
        report = {
            "completed_at_epoch": time.time(), "image_id": image_id,
            "network": "none", "connext_version": measured[0]["connext_version"],
            "source_hashes": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in staged.iterdir()
            },
            "results": measured,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Retained report: {args.report.resolve()}", flush=True)


if __name__ == "__main__":
    main()
