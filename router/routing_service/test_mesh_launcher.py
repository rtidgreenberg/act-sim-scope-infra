import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "harness_v2/scripts/run_container_baseline.sh"


class MeshLauncherTests(unittest.TestCase):
    def test_backend_validation_is_independent_of_argument_order(self):
        for options in (
            ["--router-backend", "routing_service", "--wan-contract", "three_topic_union"],
            ["--wan-contract", "three_topic_union", "--router-backend", "routing_service"],
        ):
            with self.subTest(options=options):
                result = subprocess.run(
                    ["bash", str(RUNNER), "render", "--emane", *options,
                     "--workdir", "/tmp/act-rs-parser-check"],
                    text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--workdir must be beneath", result.stderr)

    def test_processor_rejects_legacy_contract(self):
        result = subprocess.run(
            ["bash", str(RUNNER), "render", "--emane",
             "--router-backend", "routing_service"],
            text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --wan-contract three_topic_union", result.stderr)
