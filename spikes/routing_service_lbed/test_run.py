"""Launcher ownership/locking regressions without Docker or DDS."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


RUNNER = Path(__file__).with_name("run.sh")
FAKE_SG = """#!/usr/bin/env python3
import os
from pathlib import Path
import sys
import time

command = sys.argv[3]
state = Path(os.environ["LBED_FAKE_STATE"])
mode = os.environ["LBED_FAKE_MODE"]
with (state / "calls").open("a") as stream:
    stream.write(command + "\\n")
if command.startswith("docker ps"):
    if mode == "query_failure":
        print("simulated Docker query failure", file=sys.stderr)
        sys.exit(42)
    if mode == "active":
        print("existing-container")
elif command.startswith("docker image inspect"):
    print("fake-image-id")
elif command.startswith("docker run"):
    print("fake completed run")
elif not command.startswith("docker rm"):
    sys.exit("Unexpected command: " + command)
if ((mode == "query_hold" and command.startswith("docker ps")) or
        (mode == "cleanup_hold" and command.startswith("docker rm"))):
    (state / "entered").touch()
    deadline = time.monotonic() + 10
    while not (state / "release").exists():
        if time.monotonic() >= deadline:
            sys.exit("Timed out waiting for test release")
        time.sleep(0.01)
"""


class LauncherSafetyTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="lbed-launch-test-")
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.runner = self.root / "spikes/routing_service_lbed/run.sh"
        self.runner.parent.mkdir(parents=True)
        shutil.copy2(RUNNER, self.runner)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "sg"
        fake.write_text(FAKE_SG)
        fake.chmod(0o755)
        self.artifacts = [
            self.root / "debug" / directory / ("routing_service_lbed" + suffix) / "retained"
            for directory in ("test_reports", "pcap") for suffix in ("", "_cft")
        ]
        for path in self.artifacts:
            path.parent.mkdir(parents=True)
            path.write_text("retained evidence")
        identity = self.root.stat()
        lock = Path(f"/tmp/act-routing-service-lbed-{identity.st_dev}-{identity.st_ino}.lock")
        self.addCleanup(lock.unlink, missing_ok=True)

    def environment(self, mode):
        return dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                    LBED_FAKE_STATE=str(self.root), LBED_FAKE_MODE=mode)

    def launch(self, mode="ok", args=()):
        return subprocess.run(
            ["bash", str(self.runner), *args], env=self.environment(mode),
            text=True, capture_output=True, timeout=15)

    def assert_retained(self):
        for path in self.artifacts:
            self.assertEqual(path.read_text(), "retained evidence", str(path))

    def test_query_failure_preserves_artifacts_and_stops_before_launch(self):
        result = self.launch("query_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unable to check LBED container ownership", result.stderr)
        self.assert_retained()
        calls = (self.root / "calls").read_text().splitlines()
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("docker ps"))

    def test_existing_container_preserves_artifacts(self):
        result = self.launch("active")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already owns these report paths", result.stderr)
        self.assert_retained()

    def check_concurrent_launch(self, mode, args=()):
        first = subprocess.Popen(
            ["bash", str(self.runner)], env=self.environment(mode),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 5
            while not (self.root / "entered").exists():
                self.assertIsNone(first.poll(), "First launch exited before the test barrier")
                self.assertLess(time.monotonic(), deadline, "First launch missed the test barrier")
                time.sleep(0.01)
            if mode == "cleanup_hold":
                for path in self.artifacts:
                    path.write_text("retained evidence")
            before = (self.root / "calls").read_text()
            second = self.launch(args=args)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("Unable to acquire the LBED launch lock", second.stderr)
            self.assertEqual((self.root / "calls").read_text(), before)
            self.assert_retained()
        finally:
            (self.root / "release").touch()
            output, _ = first.communicate(timeout=15)
        self.assertEqual(first.returncode, 0, output)
        subsequent = self.launch(args=args)
        self.assertEqual(subsequent.returncode, 0, subsequent.stderr)

    def test_concurrent_launch_cannot_pass_the_pre_container_window(self):
        self.check_concurrent_launch("query_hold")

    def test_cft_and_default_launches_share_the_lock(self):
        self.check_concurrent_launch("query_hold", ("--cft",))

    def test_lock_is_held_until_container_cleanup_finishes(self):
        self.check_concurrent_launch("cleanup_hold")


if __name__ == "__main__":
    unittest.main()
