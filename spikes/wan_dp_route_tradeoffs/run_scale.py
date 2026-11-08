#!/usr/bin/env python3
"""Run fourteen mission-type population comparisons in isolated containers."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import random
import subprocess
import tempfile
import time
from schema_profile import generate
from goal_model import datamodel
from scale import CAPTURE_BUFFER_MIB
from spike import (SCALE_SOURCE_FILES, snapshot_sources, source_hashes,
                   validate_capture_integrity, validate_provenance)


ROOT = Path(__file__).resolve().parents[2]


def mounts(args, output):
    return ["-v", f"{output / 'source'}:/workspace:ro",
            "-v", f"{args.license_dir.resolve()}:/shared:ro",
            "-v", f"{output}:/results", "-w", "/workspace",
            "-v", f"{output / 'source'}:/results/source:ro",
            "-v", f"{output / 'mission'}:/results/mission:ro",
            "-e", "RTI_LICENSE_FILE=/shared/rti_license.dat",
            "-e", "PYTHONPYCACHEPREFIX=/tmp/pycache"]


def case(args, output, population, team_size, topic_count, protocol, variant, repetition,
         model_version=1):
    manifest = json.loads((output / "manifest.json").read_text())
    case_id = f"n{population}-t{team_size}-k{topic_count}-{protocol}-{variant}-r{repetition}"
    if topic_count > 1 and model_version == 2:
        case_id += "-m2"
    name = f"wan-dp-scale-{output.name}-{case_id}"
    command = [
        "docker", "run", "--rm", "--name", name, "--network", "none",
        "--cap-add", "NET_RAW", "--cap-add", "NET_ADMIN", *mounts(args, output),
        "-v", f"{output / 'manifest.json'}:/run-manifest.json:ro",
        "-e", f"WAN_DP_IMAGE_ID={manifest['image_id']}",
        "--entrypoint", "python3", manifest["image_id"], "spikes/wan_dp_route_tradeoffs/scale.py",
        "--manifest", "/run-manifest.json",
        "--output", f"/results/{case_id}", "--schemas", "/results/mission/schemas.xml",
        "--platforms", str(population), "--variant", variant,
        "--team-size", str(team_size),
        "--seconds", str(args.seconds), "--repetition", str(repetition),
        "--health-profile", args.health_profile,
        "--team-topic-count", str(topic_count), "--protocol", protocol,
        "--goal-model-version", str(model_version),
        "--change-hold-seconds", str(args.change_hold_seconds),
    ]
    print(f"Starting {case_id}", flush=True)
    with (output / f"{case_id}.log").open("w") as log:
        try:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True,
                           timeout=args.seconds + 900)
        finally:
            state = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", name],
                                   text=True, capture_output=True)
            if state.returncode == 0 and state.stdout.strip() == "true":
                subprocess.run(["docker", "stop", "--time", "15", name], check=True,
                               stdout=subprocess.DEVNULL)
    result = json.loads((output / case_id / "result.json").read_text())
    validate_provenance(result.get("execution_provenance"), manifest)
    validate_capture_integrity(result["capture"].get("integrity"), result["capture"])
    print(f"Completed {case_id}", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--populations", default="32")
    parser.add_argument("--variants", default="AB")
    parser.add_argument("--team-sizes", default="4,8")
    parser.add_argument("--goal-model-version", type=int, choices=(1, 2), default=2)
    parser.add_argument("--team-topic-counts")
    parser.add_argument("--protocols", default="SPDP,SPDP2")
    parser.add_argument("--change-hold-seconds", type=int, default=10)
    parser.add_argument("--seconds", type=int, default=150)
    parser.add_argument("--health-profile", choices=("scale_periodic", "scale_health30"),
                        default="scale_health30")
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--image", default="connext:7.7.0")
    parser.add_argument("--license-dir", type=Path,
                        default=Path("/home/dgreenberg/rti_connext_dds-7.7.0"))
    args = parser.parse_args()
    populations = [int(value) for value in args.populations.split(",")]
    team_sizes = [int(value) for value in args.team_sizes.split(",")]
    topic_counts = [int(value) for value in (
        args.team_topic_counts or ("3" if args.goal_model_version == 2 else "3,6")).split(",")]
    protocols = args.protocols.split(",")
    if (not populations or any(not 2 <= p <= 32 for p in populations)
            or len(set(populations)) != len(populations)
            or not team_sizes or any(not 1 <= t <= 32 for t in team_sizes)
            or len(set(team_sizes)) != len(team_sizes)
            or any(v not in "ABC" for v in args.variants)
            or len(set(args.variants)) != len(args.variants) or not args.variants
            or not topic_counts or any(k not in (1, 3, 6) for k in topic_counts)
            or len(set(topic_counts)) != len(topic_counts)
            or not protocols or any(p not in ("SPDP", "SPDP2") for p in protocols)
            or len(set(protocols)) != len(protocols)
            or min(args.seconds, args.repetitions, args.parallel) < 1):
        parser.error("unique populations 2-32, team sizes 1-32, A/B/C variants, and positive counts required")
    goal = topic_counts != [1]
    optimized = goal and args.goal_model_version == 2
    if optimized and topic_counts != [3]:
        parser.error("optimized model has three consolidated team topics; use model version 1 for the expanded 3/6 sweep")
    if goal and (1 in topic_counts or "C" in args.variants or args.change_hold_seconds < 8
                 or any(t < 2 or p % t or p // t < 3 for p in populations for t in team_sizes)):
        parser.error("goal matrix requires counts 3/6, A/B, >=3 complete teams of size >=2, and holds >=8 s")
    if not goal and protocols != ["SPDP2"]:
        parser.error("historical mode requires --protocols SPDP2")
    if not (args.license_dir / "rti_license.dat").is_file():
        parser.error("readable license directory required")
    output = Path(tempfile.mkdtemp(prefix="wan-dp-scale-"))
    output.chmod(0o755)
    print(f"RAW_ARTIFACTS={output}", flush=True)
    snapshot = snapshot_sources(output, SCALE_SOURCE_FILES)
    generate(output / "mission", goal=goal, optimized=optimized)
    for name in ("schemas.xml", "schema_manifest.json"):
        (output / "mission" / name).chmod(0o444)
    jobs = [(p, t, k, protocol, v, r) for p in populations for t in team_sizes
            for k in topic_counts for protocol in protocols for v in args.variants
            for r in range(1, args.repetitions + 1)]
    random.Random(20261106).shuffle(jobs)
    manifest = dict(
        started_at=time.time(), populations=populations, team_sizes=team_sizes, variants=args.variants,
        repetitions=args.repetitions, concurrent_cases=args.parallel, seconds=args.seconds,
        capture_accounting_version=2,
        capture_buffer_mib=CAPTURE_BUFFER_MIB,
        health_profile=args.health_profile, assignment_model_version=3 if goal else 2,
        scenario=("Optimized goal: one navigation, three team, command/feedback and separate health; "
                  "initial assignment, member swap and mixing reassignment" if optimized else
                  "Expanded goal: squad Pose/Velocity/Speed, 3/6 team state topics, C2 reports; "
                  "initial assignment, member swap and mixing reassignment"
                  if goal else "Historical contact-only model with squad-wide pose"),
        schema_manifest=json.loads((output / "mission/schema_manifest.json").read_text()),
        execution_provenance_version=1, capture_integrity_version=1,
        source_hashes=source_hashes(SCALE_SOURCE_FILES, snapshot / "spikes/wan_dp_route_tradeoffs"),
        input_hashes={name: hashlib.sha256((output / "mission" / name).read_bytes()).hexdigest()
                      for name in ("schemas.xml", "schema_manifest.json")},
        git_revision=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        image_id=subprocess.check_output(
            ["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True).strip(),
        jobs=jobs,
        limitations=[
            "Synthetic mission fields and bounded payloads sized by serialized samples",
            "No TypeObject-size calibration, UMAA definitions, or exact schema-fidelity claim",
            "Shared loopback WAN with unicast peer probes 0-95; no multi-hop forwarding or radio mesh",
            "Concurrent cases share host CPU, so latency/peak/RSS comparisons need caution",
            "Topic scopes and rates are proposed synthetic extensions, not an exact reference configuration",
            "Discovery frame categories, not assertion/response attribution",
            "No local 100-150-topic or 40-participant population and no EMANE impairments",
            "Eager endpoints in A/B; A gates team discovery via participant partitions, "
            "B gates team data via publisher/subscriber partitions after assignment",
        ])
    if goal:
        manifest.update(
            goal_model_version=args.goal_model_version, team_topic_counts=topic_counts, protocols=protocols,
            goal_datamodels={str(k): datamodel(k, optimized) for k in topic_counts},
            required_changes=["swap", "reassignment"], change_hold_seconds=args.change_hold_seconds)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = [executor.submit(case, args, output, *job, model_version=args.goal_model_version)
                   for job in jobs]
        results = [future.result() for future in futures]
    (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"RESULTS={output / 'results.json'}", flush=True)


if __name__ == "__main__":
    main()
