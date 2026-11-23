import subprocess
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "harness_v2/scripts/run_container_baseline.sh"


class MeshLauncherTests(unittest.TestCase):
    def test_generated_gateway_slots_span_one_second(self):
        with tempfile.TemporaryDirectory(prefix="startup-compose-") as directory:
            root = Path(directory)
            scripts = root / "harness_v2/scripts"
            scripts.mkdir(parents=True)
            shutil.copyfile(RUNNER, scripts / RUNNER.name)
            (root / "router").symlink_to(ROOT / "router", target_is_directory=True)
            (root / "harness_v2/qos").symlink_to(
                ROOT / "harness_v2/qos", target_is_directory=True)
            for count in (1, 6):
                work = root / "debug/render"
                subprocess.run(
                    ["bash", str(scripts / RUNNER.name), "render", "--emane",
                     "--router-backend", "custom", "--wan-contract", "legacy",
                     "--platforms", str(count), "--workdir", str(work)],
                    env={**os.environ, "CONNEXT_SHARED_DIR": "/unused-license"},
                    check=True, capture_output=True, text=True)
                services = yaml.safe_load((work / "compose.yaml").read_text())["services"]
                delays = [float(service["environment"]["WAN_STARTUP_DELAY_SEC"])
                          for service in services.values()]
                self.assertEqual(len(delays), count + 1)
                self.assertEqual(delays[0], 0)
                self.assertEqual(delays[-1], 1)
                for slot, delay in enumerate(delays):
                    self.assertAlmostEqual(delay, slot / count, places=8)

    def test_entrypoint_delays_actual_gateway_launch(self):
        with tempfile.TemporaryDirectory(prefix="startup-entrypoint-") as directory:
            root = Path(directory)
            for subdir in ("router/build", "debug/scripts", "harness_v2/scripts", "harness_v2/qos"):
                (root / subdir).mkdir(parents=True)
            shutil.copyfile(ROOT / "harness_v2/qos/wan_policy.py",
                            root / "harness_v2/qos/wan_policy.py")
            router = root / "router/build/router_main"
            router.write_text(
                "#!/usr/bin/env python3\nimport os,time\nfrom pathlib import Path\n"
                "Path(os.environ['NODE_DEBUG_DIR'],'started').write_text(str(time.monotonic()))\n"
                "time.sleep(1.2)\n")
            router.chmod(0o755)
            (root / "debug/scripts/router_journal_subscriber.py").write_text(
                "import time; time.sleep(3)\n")
            (root / "harness_v2/scripts/start_control_sim.sh").write_text("sleep 3\n")
            entrypoint = ROOT / "docker/connext-7.7/node-entrypoint.sh"
            processes = []
            try:
                for delay in ("0", "1"):
                    debug = root / ("node-" + delay)
                    process = subprocess.Popen(
                        ["bash", str(entrypoint)],
                        env={**os.environ, "ACT_WORKSPACE": str(root), "NODE_DEBUG_DIR": str(debug),
                             "NODE_ROLE": "control", "NODE_ID": "20", "NODE_NAME": "StartupTest",
                             "ROUTER_BACKEND": "custom", "EMANE_ENABLED": "0",
                             "EMANE_TRAFFIC_MONITOR": "0", "MESH_DASHBOARD": "0",
                             "WAN_STARTUP_DELAY_SEC": delay},
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    processes.append((process, debug))
                for process, debug in processes:
                    stdout, stderr = process.communicate(timeout=8)
                    self.assertEqual(process.returncode, 0, stdout + stderr)
                started = [float((debug / "started").read_text()) for _, debug in processes]
                self.assertGreaterEqual(started[1] - started[0], 0.8)
                self.assertLess(started[1] - started[0], 1.5)
            finally:
                for process, _ in processes:
                    if process.poll() is None:
                        process.terminate()
                        process.communicate(timeout=5)

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
             "--router-backend", "routing_service", "--wan-contract", "legacy"],
            text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --wan-contract three_topic_union or six_topic_union", result.stderr)

    def test_default_render_uses_six_topic_union_and_ipv6(self):
        with tempfile.TemporaryDirectory(prefix="default-render-", dir=ROOT / "debug") as directory:
            work = Path(directory) / "render"
            subprocess.run(
                ["bash", str(RUNNER), "render", "--emane",
                 "--platforms", "2", "--workdir", str(work)],
                env={**os.environ, "CONNEXT_SHARED_DIR": "/home/dgreenberg/rti_connext_dds-7.7.0"},
                check=True, capture_output=True, text=True)
            ctrl_cfg = yaml.safe_load((work / "control-platform-20.yaml").read_text())
            self.assertEqual(ctrl_cfg["router"]["wan_contract"], "six_topic_union")
            self.assertEqual(
                ctrl_cfg["qos_profiles"]["wan_participant_emane"],
                "ACT_QOS_LIB::rs_six_topic_participant_emane_v6")
            compose = yaml.safe_load((work / "compose.yaml").read_text())["services"]
            self.assertEqual(compose["control-20"]["environment"]["ROUTER_BACKEND"], "routing_service")
            self.assertEqual(compose["control-20"]["environment"]["EMANE_IPV6"], "fd42:88::1")
            self.assertEqual(compose["control-20"]["environment"]["WAN_PEER_V6"], "fd42:88::1")
            self.assertEqual(compose["control-20"]["environment"]["WAN_PEER_V6_2"], "fd42:88::2")
            self.assertTrue((work / "control-platform-20.xml").is_file())
            self.assertTrue((work / "control-platform-30.xml").is_file())
            rendered_xml = (work / "control-platform-20.xml").read_text()
            for family in ("P1_Event", "P1_Periodic", "P2_Event", "P2_State", "P2_Periodic", "P3_Periodic"):
                self.assertIn(f'name="wan_out_{family}"', rendered_xml)
