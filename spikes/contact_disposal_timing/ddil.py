#!/usr/bin/env python3
"""Long-disconnection, peer loss, receiver restart and same-key lifecycle tests."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback

from delayed import configure_link, prepare, rows, tc, wait
from run import BUILD, EXPECTED, ROOT, SPIKE, digest, run

WORK = Path(tempfile.mkdtemp(prefix="act-instance-ddil-"))
RESULTS = Path("/results")
REPORT = {"status": "FAIL", "writer_cleanup_seconds": 10, "one_way_delay_seconds": 3,
          "trials": [], "scratch": str(WORK)}


def stop(process):
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def exercise(build, scenario, outage, timeout):
    directory = WORK / scenario
    directory.mkdir()
    result = {"scenario": scenario, "outage_seconds": outage, "wan_timeout_seconds": timeout}
    REPORT["trials"].append(result)
    os.environ.update(WAN_TIMEOUT_SEC=str(timeout), WAN_HB_PERIOD_SEC="3",
                      WAN_HB_RETRIES=str((timeout + 2) // 3))
    processes, logs = [], []
    stop_monitor = directory / "stop_monitor"
    cache_path = directory / "wan_writer_cache.jsonl"
    link_configured = False
    try:
        probe_qos, configs = prepare(directory, 10, 3 if timeout < 30 else None)
        if timeout < 30:
            result["participant_assert_seconds"] = 3
        sdk = Path(os.environ["NDDSHOME"])
        executable = next((sdk / "resource/app/bin").glob("*/rtiroutingserviceapp"))
        environment = os.environ.copy()
        environment["LD_LIBRARY_PATH"] = os.pathsep.join([
            str(sdk / "resource/app/lib" / executable.parent.name),
            str(sdk / "lib" / executable.parent.name)])

        def launch(node, suffix=""):
            env = environment.copy()
            if node == "Platform_30":
                env.update(LD_PRELOAD=str(build / "libcontact_cache_monitor.so"),
                           CONTACT_CACHE_LOG=str(cache_path), CONTACT_CACHE_STOP=str(stop_monitor),
                           CONTACT_CACHE_START=str(directory / "start_monitor"))
            log = (directory / (node + suffix + ".log")).open("w")
            logs.append(log)
            process = subprocess.Popen(
                [str(executable), "-cfgFile", str(configs[node]), "-cfgName", "ACTGateway",
                 "-verbosity", "WARN"], env=env, stdout=log, stderr=subprocess.STDOUT)
            processes.append(process)
            return process

        control = launch("Control_20")
        launch("Platform_30")
        probe_output = directory / "probe.jsonl"
        log = probe_output.open("w")
        logs.append(log)
        observation_seconds = outage + (30 if scenario.startswith("revive") else 45)
        probe_scenario = "dispose" if scenario == "long_outage" else scenario
        probe = subprocess.Popen(
            [str(build / "ddil_probe"), str(probe_qos), str(directory),
             probe_scenario, str(observation_seconds)], env=environment,
            stdout=log, stderr=subprocess.STDOUT)
        processes.append(probe)

        def ready():
            assert all(p.poll() is None for p in processes), "gateway/probe exited before readiness"
            return (directory / "ready").exists()

        wait(ready, "initial contact materialized", 30)
        (directory / "start_monitor").touch()
        wait(lambda: any(r.get("alive") == 1 for r in rows(cache_path)), "native WAN writer monitor")
        result["link"] = configure_link()
        link_configured = True
        tc("qdisc", "change", "dev", "lo", "parent", "1:3", "handle", "30:",
           "netem", "delay", "3000ms", "loss", "100%", "limit", "10000")
        onset = time.monotonic_ns()
        (directory / "go").touch()
        if scenario == "restart":
            time.sleep(5)
            stop(control)
            result["old_c2_exit"] = control.returncode
            result["c2_stop_since_outage_seconds"] = (time.monotonic_ns() - onset) / 1e9
            (directory / "restart_reader").touch()
            time.sleep(10)
            control = launch("Control_20", "_restarted")
            result["c2_relaunch_since_outage_seconds"] = (time.monotonic_ns() - onset) / 1e9
        time.sleep(max(0, outage - (time.monotonic_ns() - onset) / 1e9))
        tc("qdisc", "change", "dev", "lo", "parent", "1:3", "handle", "30:",
           "netem", "delay", "3000ms", "loss", "0%", "limit", "10000")
        restored = time.monotonic_ns()
        (directory / "link_restored").touch()
        result["blackout"] = {"onset_ns": onset, "restored_ns": restored}
        probe.wait(timeout=observation_seconds - outage + 20)
        assert probe.returncode == 0, probe_output.read_text()[-7000:]
        events = rows(probe_output)
        outcome = next(e for e in events if e["event"] == "result")
        disposed_ns = next(e["monotonic_ns"] for e in events if e["event"] == "dispose")
        assert onset <= disposed_ns < restored, "dispose not inside actual blackout"
        assert outcome["status"] == "PASS", outcome
        assert outcome["canary_arrival_ms"] >= outage * 1000
        result["native_result"] = outcome
        observations = [r for r in rows(cache_path) if "monotonic_ns" in r]
        assert not any("error" in r for r in rows(cache_path)), "native observer error"
        during = [r for r in observations if disposed_ns <= r["monotonic_ns"] <= restored]
        assert any(r["disposed"] == 1 and r["unacknowledged"] > 0 for r in during)
        result["wan_cache_during_outage"] = {
            "minimum_active_readers": min(r["active_readers"] for r in during),
            "minimum_matched_readers": min(r["matched_readers"] for r in during),
            "max_disposed": max(r["disposed"] for r in during)}
        first_disposed = next(r for r in during if r["disposed"] == 1)
        cleared = [r for r in observations
                   if r["monotonic_ns"] > first_disposed["monotonic_ns"]
                   and r["disposed"] == 0]
        if cleared and scenario not in ("revive", "revive_dispose"):
            result["wan_first_empty_since_dispose_ms"] = (
                cleared[0]["monotonic_ns"] - disposed_ns) / 1e6
            result["wan_emptied_during_outage"] = cleared[0]["monotonic_ns"] < restored
        if scenario == "long_outage":
            assert result["wan_cache_during_outage"]["minimum_active_readers"] >= 1
            assert not result.get("wan_emptied_during_outage", False)
            assert outcome["final_state"] == "DISPOSED" and outcome["saw_disposed"]
        if scenario in ("peer_loss", "restart"):
            assert result["wan_cache_during_outage"]["minimum_active_readers"] == 0, (
                "reliable-reader inactivity boundary not exercised", result["wan_cache_during_outage"])
            result["boundary_exercised"] = "RELIABLE_READER_INACTIVE"
            result["participant_endpoint_removal_observed"] = (
                result["wan_cache_during_outage"]["minimum_matched_readers"] == 0)
            if scenario == "restart":
                assert result["old_c2_exit"] == 0 and outcome["reader_restarted"]
            result["disposal_recovery"] = (
                "RECOVERED" if outcome["saw_disposed"] else "NOT_REPLAYED_AFTER_PEER_LOSS")
        result["qualification"] = (
            "LIMITATION_REPRODUCED" if result.get("disposal_recovery") == "NOT_REPLAYED_AFTER_PEER_LOSS"
            else "PASS")
        result["status"] = "COMPLETE"
        result["qdisc_after"] = json.loads(tc("-s", "-j", "qdisc", "show", "dev", "lo"))
        print(json.dumps({key: value for key, value in result.items()
                          if key not in ("link", "qdisc_after")}), flush=True)
    finally:
        stop_monitor.touch()
        if cache_path.exists():
            wait(lambda: any(r.get("event") == "monitor_stopped" for r in rows(cache_path)),
                 "cache monitor shutdown", 5)
        for process in reversed(processes):
            stop(process)
        for log in logs:
            log.close()
        if link_configured:
            tc("qdisc", "del", "dev", "lo", "root")
        result["process_returncodes"] = [p.returncode for p in processes]
        (RESULTS / (scenario + ".json")).write_text(json.dumps(result, indent=2) + "\n")


def main():
    try:
        assert {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}
        REPORT["binary_hashes"] = {name: digest(BUILD / name) for name in EXPECTED}
        assert REPORT["binary_hashes"] == EXPECTED
        REPORT["source_hashes"] = {p.name: digest(p) for p in SPIKE.iterdir() if p.is_file()}
        for key in ("CONTROL_LAN_PEER1", "CONTROL_LAN_PEER2", "CONTROL_LAN_PEER3",
                    "PLATFORM_LAN_PEER1", "PLATFORM_LAN_PEER2", "PLATFORM_LAN_PEER3"):
            os.environ[key] = "127.0.0.1"
        for i in range(1, 12):
            os.environ["WAN_PEER" + (str(i) if i > 1 else "")] = "[0-15]@builtin.udpv4://127.0.0.1"
        build = WORK / "build"
        with (WORK / "build.log").open("w") as log:
            run(["cmake", "-S", str(SPIKE), "-B", str(build), "-DBUILD_SHARED_LIBS=ON",
                 "-DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0"], stdout=log, stderr=subprocess.STDOUT)
            run(["cmake", "--build", str(build), "-j2"], stdout=log, stderr=subprocess.STDOUT)
        scenarios = {"long_outage": (30, 300), "peer_loss": (30, 12), "restart": (30, 12),
                     "revive": (2, 300), "revive_dispose": (2, 300)}
        selected = os.environ.get("DDIL_SCENARIOS", ",".join(scenarios)).split(",")
        assert selected and all(name in scenarios for name in selected), selected
        REPORT["selected_scenarios"] = selected
        for scenario in selected:
            outage, timeout = scenarios[scenario]
            exercise(build, scenario, outage, timeout)
        REPORT["status"] = (
            "COMPLETE_WITH_LIMITATIONS" if any(t["qualification"] == "LIMITATION_REPRODUCED"
                                              for t in REPORT["trials"]) else "PASS")
    except Exception:
        REPORT["error"] = traceback.format_exc()
        print(REPORT["error"], file=sys.stderr)
    finally:
        REPORT["binaries_unchanged"] = {name: digest(BUILD / name) for name in EXPECTED} == EXPECTED
        REPORT["private_shm_artifacts"] = [p.name for p in Path("/dev/shm").iterdir()
                                          if p.name.startswith(("RTI", "dds"))]
        if not REPORT["binaries_unchanged"] or REPORT["private_shm_artifacts"]:
            REPORT["status"] = "FAIL"
        (RESULTS / "report.json").write_text(json.dumps(REPORT, indent=2) + "\n")
        tag = "_".join(REPORT.get("selected_scenarios", ["setup"]))
        with tarfile.open(RESULTS / ("inputs-and-logs-" + tag + ".tar.gz"), "w:gz") as archive:
            for path in WORK.rglob("*"):
                if path.is_file() and "build" not in path.relative_to(WORK).parts:
                    archive.add(path, arcname=str(path.relative_to(WORK)))
    return 0 if REPORT["status"] in ("PASS", "COMPLETE_WITH_LIMITATIONS") else 1


if __name__ == "__main__":
    sys.exit(main())
