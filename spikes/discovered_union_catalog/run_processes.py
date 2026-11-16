#!/usr/bin/env python3
"""Own the three proof processes; never share application schemas with gateways."""

import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main():
    build, source, qos = map(Path, sys.argv[1:4])
    options = sys.argv[4:]
    if options not in ([], ["--discovery-only"]):
        raise ValueError(f"Unknown options: {options}")
    processes = {}
    logs = {}
    with tempfile.TemporaryDirectory(prefix="discovered-union-") as work:
        work = Path(work)
        # Only the fixture process is given an application-schema argument.
        app_types = work / "application_types.xml"
        app_types.write_bytes((source / "application_types.xml").read_bytes())
        common = [str(source / "bootstrap.xml"), str(qos), *options]

        def start(role, arguments):
            logs[role] = (work / f"{role}.log").open("w+")
            processes[role] = subprocess.Popen(
                [str(build / role), *arguments], stdout=logs[role],
                stderr=subprocess.STDOUT, cwd=work,
            )

        def healthy():
            for role, process in processes.items():
                if process.poll() not in (None, 0):
                    raise RuntimeError(f"{role} exited {process.returncode}")

        try:
            start("application", [str(app_types), *common])
            start("gateway", common)
            deadline = time.monotonic() + 40
            while True:
                healthy()
                if "UNION_PUBLISHED" in (work / "gateway.log").read_text():
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("gateway did not advertise its startup union")
                time.sleep(0.05)
            start("platform", common)
            deadline = time.monotonic() + 160
            while any(p.poll() is None for p in processes.values()):
                healthy()
                if time.monotonic() >= deadline:
                    raise TimeoutError("proof processes did not finish")
                time.sleep(0.1)
            healthy()
        finally:
            for process in processes.values():
                if process.poll() is None:
                    process.terminate()
            for process in processes.values():
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for role, log in logs.items():
                log.close()
                print(f"\n--- {role} ---", flush=True)
                print((work / f"{role}.log").read_text(), flush=True)


if __name__ == "__main__":
    main()
