import io
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch

from run import SHUTDOWN_TIMEOUT, _signal_process_group, main, wait_for_service


class RoutingServiceLauncherTests(unittest.TestCase):
    def setUp(self):
        self.handlers = {signum: signal.getsignal(signum)
                         for signum in (signal.SIGINT, signal.SIGTERM)}

    def tearDown(self):
        for signum, handler in self.handlers.items():
            self.assertEqual(signal.getsignal(signum), handler)

    def start_child(self, code):
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", code], start_new_session=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(self.cleanup_child, process)
        self.assertTrue(select.select([process.stdout], [], [], 5)[0],
                        "child did not become ready")
        self.assertEqual(process.stdout.readline(), "ready\n")
        return process

    @staticmethod
    def cleanup_child(process):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()

    def wait_with_signal(self, process, signum):
        original_wait = process.wait
        requested = False

        def wait(timeout=None):
            nonlocal requested
            if not requested:
                requested = True
                os.kill(os.getpid(), signum)
            return original_wait(timeout=timeout)

        with patch.object(process, "wait", side_effect=wait):
            return wait_for_service(process)

    def test_normal_exit_preserves_return_code(self):
        process = self.start_child("print('ready', flush=True)\nraise SystemExit(23)")
        with patch("run.os.killpg", wraps=os.killpg) as killpg:
            self.assertEqual(wait_for_service(process), 23)
        killpg.assert_not_called()

    def test_graceful_stop_for_both_signals(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signum=signum):
                process = self.start_child(
                    "import signal, sys, time\n"
                    "def stop(*args): sys.exit(0)\n"
                    "signal.signal(signal.SIGINT, stop)\n"
                    "signal.signal(signal.SIGTERM, stop)\n"
                    "print('ready', flush=True)\n"
                    "while True: time.sleep(1)\n")
                with patch("run.os.killpg", wraps=os.killpg) as killpg:
                    self.assertEqual(self.wait_with_signal(process, signum), 0)
                killpg.assert_called_once_with(process.pid, signum)

    def test_stuck_child_is_killed_after_ten_seconds(self):
        process = self.start_child(
            "import signal, time\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "while True: time.sleep(1)\n")
        started = time.monotonic()
        with patch("run.os.killpg", wraps=os.killpg) as killpg, \
                patch("run.sys.stderr", new_callable=io.StringIO) as errors:
            self.assertEqual(self.wait_with_signal(process, signal.SIGTERM), -signal.SIGKILL)
        elapsed = time.monotonic() - started
        self.assertEqual(SHUTDOWN_TIMEOUT, 10)
        self.assertGreaterEqual(elapsed, SHUTDOWN_TIMEOUT - 0.05)
        self.assertLess(elapsed, SHUTDOWN_TIMEOUT + 5)
        self.assertEqual([call.args for call in killpg.call_args_list],
                         [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)])
        self.assertIn("forcing termination", errors.getvalue())

    def test_repeated_signals_do_not_extend_deadline(self):
        process = Mock(pid=123456789, returncode=None)
        process.poll.side_effect = lambda: process.returncode
        handlers = {}
        now = 100
        timeouts = []

        def install(signum, handler):
            previous = handlers.get(signum, signal.SIG_DFL)
            handlers[signum] = handler
            return previous

        def killpg(_pid, signum):
            if signum == signal.SIGKILL:
                process.returncode = -signal.SIGKILL

        def wait(timeout=None):
            nonlocal now
            timeouts.append(timeout)
            if len(timeouts) == 1:
                handlers[signal.SIGTERM](signal.SIGTERM, None)
                now = 105
            elif len(timeouts) == 2:
                handlers[signal.SIGINT](signal.SIGINT, None)
                now = 110
            elif process.returncode is not None:
                return process.returncode
            raise subprocess.TimeoutExpired("fake-service", timeout)

        process.wait.side_effect = wait
        with patch("run.signal.signal", side_effect=install), \
                patch("run.time.monotonic", side_effect=lambda: now), \
                patch("run.os.killpg", side_effect=killpg), \
                patch("run.sys.stderr", new_callable=io.StringIO):
            self.assertEqual(wait_for_service(process), -signal.SIGKILL)
        self.assertEqual(timeouts, [0.2, 5, 0, None])

    def test_wait_failure_still_stops_child(self):
        process = self.start_child(
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))\n"
            "print('ready', flush=True)\n"
            "while True: time.sleep(1)\n")
        original_wait = process.wait
        with patch.object(process, "wait",
                          side_effect=self.fail_first_wait(original_wait)):
            with self.assertRaisesRegex(RuntimeError, "wait failed"):
                wait_for_service(process)
        self.assertEqual(process.returncode, 0)

    @staticmethod
    def fail_first_wait(original_wait):
        failed = False

        def wait(timeout=None):
            nonlocal failed
            if not failed:
                failed = True
                raise RuntimeError("wait failed")
            return original_wait(timeout=timeout)

        return wait

    def test_exit_race_during_signal_is_not_an_error(self):
        process = Mock(pid=123456789)
        process.poll.side_effect = [None, 0]
        with patch("run.os.killpg", side_effect=ProcessLookupError):
            _signal_process_group(process, signal.SIGTERM)

    def test_missing_group_for_running_child_is_an_error(self):
        process = Mock(pid=123456789)
        process.poll.return_value = None
        with patch("run.os.killpg", side_effect=ProcessLookupError):
            with self.assertRaises(ProcessLookupError):
                _signal_process_group(process, signal.SIGTERM)

    def test_prepared_xml_validation_has_no_yaml_helper_or_sdk_dependency(self):
        with patch.object(sys, "argv", ["run.py", "--xml", "gateway.xml", "--validate-only"]), \
                patch.dict(sys.modules, {"yaml": None}), \
                patch("run.preflight", return_value={"act.monitoring": "true"}) as check, \
                patch("run.subprocess.run") as helper, \
                patch("run.subprocess.Popen") as launch:
            self.assertEqual(main(), 0)
        helper.assert_not_called()
        launch.assert_not_called()
        check.assert_called_once_with(Path("gateway.xml").resolve(), "ACTGateway")

    def test_prepared_xml_launches_directly_and_preserves_owned_lifecycle(self):
        executable = Path("sdk/resource/app/bin/arch/rtiroutingserviceapp")
        with patch.object(sys, "argv", ["run.py", "--xml", "gateway.xml",
                                       "--service-name", "PreparedGateway"]), \
                patch.dict(sys.modules, {"yaml": None}), \
                patch.dict(os.environ, {"NDDSHOME": "sdk"}), \
                patch("run.preflight", return_value={"act.monitoring": "true"}), \
                patch("run.Path.glob", return_value=[executable]), \
                patch("run.subprocess.run") as helper, \
                patch("run.subprocess.Popen") as launch, \
                patch("run.wait_for_service", return_value=17) as wait:
            self.assertEqual(main(), 17)
        helper.assert_not_called()
        args, kwargs = launch.call_args
        self.assertEqual(args[0], [str(executable), "-cfgFile", str(Path("gateway.xml").resolve()),
                                  "-cfgName", "PreparedGateway", "-verbosity", "WARN"])
        self.assertTrue(kwargs["start_new_session"])
        wait.assert_called_once_with(launch.return_value)

    def test_prepared_xml_cannot_silently_change_monitoring_mode(self):
        with patch.object(sys, "argv", ["run.py", "--xml", "gateway.xml", "--data-plane-only"]), \
                patch("run.preflight", return_value={"act.monitoring": "true"}), \
                patch("run.subprocess.Popen") as launch, \
                patch("run.sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 2)
        launch.assert_not_called()

    def test_development_launch_owns_temporary_xml_and_types_outside_checkout(self):
        from test_config import RoutingServiceConfigurationTests
        fixture = RoutingServiceConfigurationTests()
        fixture.setUp()
        model = dict(fixture.model, timeout=300, heartbeat=3, retries=100)
        paths = []

        def launch(path, service_name):
            import xml.etree.ElementTree as ET
            self.assertEqual(service_name, "ACTGateway")
            self.assertTrue(path.is_file())
            properties = {entry.findtext("name"): entry.findtext("value") for entry in
                          ET.parse(path).findall(".//processor/property/value/element")}
            types = Path(properties["act.types_uri"])
            self.assertEqual(types.parent, path.parent)
            self.assertTrue(types.is_file())
            self.assertNotEqual(path.parent, Path("debug/routing_service").resolve())
            paths.extend((path, types))
            return 21

        original_is_file = Path.is_file
        with patch.object(sys, "argv", ["run.py", "--config", "removed-after-preparation.yaml",
                                       "--data-plane-only"]), \
                patch.dict(os.environ), \
                patch("run.subprocess.run", return_value=Mock(stdout=json.dumps(model))), \
                patch("run.Path.is_file", autospec=True,
                      side_effect=lambda path: path.name == "libact_gateway_processor.so"
                      or original_is_file(path)), \
                patch("run.launch_native", side_effect=launch):
            self.assertEqual(main(), 21)
        self.assertTrue(paths)
        self.assertTrue(all(not path.exists() for path in paths))


if __name__ == "__main__":
    unittest.main()
