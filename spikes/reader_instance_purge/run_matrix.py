#!/usr/bin/env python3
"""Run native acceptance and retention probes; no third-party test dependencies."""

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=pathlib.Path)
    parser.add_argument("qos", type=pathlib.Path)
    parser.add_argument("report", type=pathlib.Path)
    args = parser.parse_args()
    cases = []
    for mode in ("take", "read_then_take"):
        for profile in ("base", "infinite"):
            for operation in ("unregister", "dispose"):
                cases.append((profile, operation, mode))
        cases.append(("disposed_0", "dispose", mode))
        for delay in (2, 5):
            cases.append((f"nowriter_{delay}", "unregister", mode))
            cases.append((f"disposed_{delay}", "dispose", mode))
    cases.append(("samples_2", "qos_only", "take"))
    cases.extend([
        ("base", "unregister", "hold_take"),
        ("disposed_0", "dispose", "hold_take"),
    ])
    report = {
        "connext_version": "7.7.0",
        "network": "Docker --network none; UDPv4 loopback; domains 210 and 211",
        "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
        "qos_sha256": hashlib.sha256(args.qos.read_bytes()).hexdigest(),
        "cases": [],
        "failures": [],
    }
    for profile, operation, mode in cases:
        command = [str(args.binary), str(args.qos), profile, operation, mode]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=40)
        records = [line.removeprefix("RESULT ") for line in completed.stdout.splitlines()
                   if line.startswith("RESULT ")]
        if len(records) != 1:
            raise RuntimeError(f"Missing native result: {command}\n"
                               f"{completed.stdout}\n{completed.stderr}")
        result = json.loads(records[0])
        if result["status"] == "rejected":
            result["native_diagnostic"] = completed.stderr.strip()
        report["cases"].append(result)
        print(f"{profile}/{operation}/{mode}: {result['status']} "
              f"at {result['phase']}; relayed={result['relayed']}", flush=True)
        failures = report["failures"]
        label = f"{profile}/{operation}/{mode}"
        if completed.returncode or result["status"] == "failed":
            failures.append(f"{label}: {result['error']}\n{completed.stderr}")
        elif result["status"] == "rejected":
            if profile not in ("nowriter_2", "nowriter_5", "disposed_2", "disposed_5"):
                failures.append(f"{label}: unexpected rejection: {result['error']}")
            else:
                field = ("autopurge_nowriter_instances_delay" if operation == "unregister"
                         else "autopurge_disposed_instances_delay")
                if field not in completed.stderr:
                    failures.append(f"{label}: rejection not attributed to {field}: "
                                    f"{completed.stderr}")
        elif operation != "qos_only":
            probes = {item["phase"]: item for item in result["probes"]}
            if mode == "read_then_take" and not probes["read_loan"]["ok"]:
                failures.append(f"{label}: key unavailable before take")
            retained = profile == "infinite" or (profile == "base" and operation == "dispose")
            purged = profile == "disposed_0" or (profile == "base" and operation == "unregister")
            if purged:
                if not probes["take_loan"]["ok"] or probes["after_return_loan"]["ok"]:
                    failures.append(f"{label}: zero-delay loan/purge boundary changed")
                if mode == "hold_take" and not probes["take_loan_after_2100ms"]["ok"]:
                    failures.append(f"{label}: key lost during 2.1-second outstanding loan")
            if retained and not all(item["ok"] for item in result["probes"]):
                failures.append(f"{label}: infinite retention lost key")
            if retained or purged or mode == "read_then_take":
                if not result["relayed"] or not result["destination_state"]:
                    failures.append(f"{label}: key-only native relay not observed")
            if not result["revived"]:
                failures.append(f"{label}: revival failed")
            if profile.endswith(("_2", "_5")):
                delay = int(profile[-1])
                if not probes["after_1000ms"]["ok"]:
                    failures.append(f"{label}: finite delay did not retain key for 1 second")
                if probes["after_5500ms"]["ok"]:
                    failures.append(f"{label}: key still present after finite delay")
                if delay == 5 and not probes["after_2500ms"]["ok"]:
                    failures.append(f"{label}: 5-second delay expired early")
                if delay == 2 and probes["after_2500ms"]["ok"]:
                    failures.append(f"{label}: 2-second delay did not expire")
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{len(cases)} cases; {len(report['failures'])} validation failures", flush=True)
    for failure in report["failures"]:
        print(failure, file=sys.stderr)
    return bool(report["failures"])


if __name__ == "__main__":
    sys.exit(main())
