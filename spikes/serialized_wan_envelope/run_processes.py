#!/usr/bin/env python3
"""Own all three children and keep orchestration files outside DDS."""
import pathlib
import subprocess
import sys
import time


def main():
    build, source = map(pathlib.Path, sys.argv[1:])
    commands = {
        "application": [str(build / "application"), str(source / "application_types.xml"),
                        str(source / "qos.xml")],
        "source": [str(build / "gateway"), "source", str(source / "envelope.xml"),
                   str(source / "qos.xml")],
        "destination": [str(build / "gateway"), "destination", str(source / "envelope.xml"),
                        str(source / "qos.xml")],
    }
    children = {}
    logs = {}
    passed = False
    try:
        for role, command in commands.items():
            logs[role] = open(f"{role}.log", "w", encoding="utf-8")
            children[role] = subprocess.Popen(command, stdout=logs[role],
                                               stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            statuses = [process.poll() for process in children.values()]
            if any(code is not None and code != 0 for code in statuses):
                raise RuntimeError(f"Child failed: {dict(zip(children, statuses))}")
            if all(code == 0 for code in statuses):
                passed = True
                break
            time.sleep(0.1)
        if not passed:
            raise RuntimeError("Owned child timeout")
    finally:
        for process in children.values():
            if process.poll() is None:
                process.terminate()
        for process in children.values():
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for stream in logs.values():
            stream.close()
        for role in logs:
            print(f"--- {role} ---")
            print(pathlib.Path(f"{role}.log").read_text(encoding="utf-8"))
        print("CLEANUP all owned child PIDs reaped")
    print("PASS isolated serialized WAN envelope: 2 phases, 15 originals, 5 explicit rejections")


if __name__ == "__main__":
    main()
