#!/usr/bin/env python3
"""Compare participant-scoped suppression on actual Routing Service builtin readers."""

import json
import argparse
from contextlib import ExitStack
from pathlib import Path
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET

import rti.connextdds as dds

from runner import (
    BUILD, CAPTURES, HERE, PARTICIPANT_VARIANTS, REPORTS, ROOT, TYPES, WORK,
    case, guid, sha, stop, wait,
)
from wire import capture_rows, is_builtin_writer

VARIANTS = ("participant_baseline", "participant_disabled")
MATRIX_VARIANTS = ("matrix_none", "matrix_reader", "matrix_participant", "matrix_both")
SEDP_WRITERS = ("000003c2", "000004c2")
report = {"status": "FAIL", "repetitions": 2, "trials": [], "comparisons": []}


def discovery_rows(capture, prefixes):
    rows = capture_rows(capture)
    acks = [r for r in rows if r["kind"] == "ACKNACK"
            and r["prefix"] in prefixes and r["destination"] in prefixes
            and r["prefix"] != r["destination"]
            and is_builtin_writer(r["writer"])]
    grouped = {}
    for row in acks:
        heartbeats = [h for h in rows if h["kind"] == "HEARTBEAT"
                      and h["prefix"] == row["destination"] and h["writer"] == row["writer"]
                      and h["destination"] in (None, "0" * 24, row["prefix"])
                      and h["time"] <= row["time"]]
        row["prior_heartbeat_count"] = len(heartbeats)
        counters = grouped.setdefault(row["writer"], {
            "acknacks": 0, "base_zero": 0, "base_zero_before_heartbeat": 0,
            "positive_acks": 0, "repair_requests": 0})
        counters["acknacks"] += 1
        counters["base_zero"] += int(row["base"] == 0)
        counters["base_zero_before_heartbeat"] += int(row["base"] == 0 and not heartbeats)
        counters["positive_acks"] += int(row["base"] > 0 and not row["missing"])
        counters["repair_requests"] += int(bool(row["missing"]))
    data_writers = sorted({r["writer"] for r in rows if r["kind"] == "DATA"
                           and r["prefix"] in prefixes and is_builtin_writer(r["writer"])})
    return {"by_builtin_writer": grouped, "acknacks": acks, "builtin_data_writer_ids": data_writers}


def native_case(variant):
    name = "native_" + variant
    with ExitStack() as cleanup:
        capture = CAPTURES / (name + ".pcapng")
        capture.write_bytes(b"")
        capture.chmod(0o666)
        log_path = REPORTS / (name + "_capture.log")
        log = cleanup.enter_context(log_path.open("w"))
        recorder = subprocess.Popen(
            ["dumpcap", "-i", "lo", "-f", "udp portrange 32900-33149",
             "-a", "duration:20", "-w", str(capture)], stdout=log, stderr=subprocess.STDOUT)
        cleanup.callback(stop, recorder)
        wait(lambda: capture.stat().st_size > 0, "native capture ready", 5)
        provider = dds.QosProvider(str(WORK / "combined.xml"))
        qos = provider.participant_qos_from_profile("PREEMPTIVE_SPIKE::" + variant)
        participants = []
        properties = {}
        property_name = "dds.participant.inter_participant_data_reader.protocol.disable_preemptive_nack"
        for node in ("A", "B"):
            participant = dds.DomainParticipant(102, qos)
            cleanup.callback(participant.close)
            participants.append(guid(participant.instance_handle)[:24])
            properties[node] = dict(participant.qos.property.get_all())
            assert properties[node][property_name] == (
                "true" if variant == "participant_disabled" else "false")
            time.sleep(1 if node == "A" else 4)
        stop(recorder)
        assert recorder.returncode == 0
        log.flush()
        assert "pcap:0/dumpcap:0" in log_path.read_text()
        capture.chmod(0o644)
        discovery = discovery_rows(capture, set(participants))
        assert discovery["by_builtin_writer"]["00010182"]["positive_acks"] > 0
        result = {"variant": variant, "native_participant_properties": properties,
                  "participant_prefixes": participants, "capture_sha256": sha(capture),
                  "discovery": discovery}
        (REPORTS / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n")
        return {k: v for k, v in discovery.items() if k != "acknacks"}


def normalize_configuration(text):
    root = ET.fromstring(text)
    wan_qos = root.find(
        "./routing_service/domain_route/participant[@name='wan']/domain_participant_qos")
    assert wan_qos.get("base_name") in {
        "PREEMPTIVE_SPIKE::participant_baseline", "PREEMPTIVE_SPIKE::participant_disabled"}
    wan_qos.set("base_name", "PREEMPTIVE_SPIKE::participant_baseline")
    property_name = "dds.participant.inter_participant_data_reader.protocol.disable_preemptive_nack"
    matching = [e for e in wan_qos.findall("./property/value/element")
                if e.findtext("name") == property_name]
    assert len(matching) == 1 and matching[0].findtext("value") in ("false", "true")
    matching[0].find("value").text = "false"
    for family in ("event", "state", "periodic"):
        readers = root.findall(
            "./routing_service/domain_route/session/topic_route/input/datareader_qos")
        for reader in readers:
            if reader.get("base_name") == "PREEMPTIVE_SPIKE::disabled_" + family:
                reader.set("base_name", "PREEMPTIVE_SPIKE::baseline_" + family)
    return ET.tostring(root)


def main(matrix=False):
    try:
        variants = MATRIX_VARIANTS if matrix else VARIANTS
        report["mode"] = "four_way_matrix" if matrix else "participant_comparison"
        assert {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}
        expected = {
            "libact_gateway_processor.so": "022941caaef2604dd41657da494b9144f17e5bbb7fd1a6c7b816e4dfd007f94b",
            "act_rs_config": "406e734e0f713443fd4dd2a16bcbd6e4665d9eae583660211abb9fd1820ab6bf"}
        for name, digest in expected.items():
            assert sha(BUILD / name) == digest, name
        paths = [HERE / name for name in (
            "participant.py", "runner.py", "wire.py", "test_wire.py", "qos.xml", "drop.c")]
        paths += [ROOT / "harness_v2/qos/act_qos_profiles.xml", TYPES]
        report["source_hashes"] = {str(p.relative_to(ROOT)): sha(p) for p in paths}
        report["binary_hashes"] = expected
        for repetition in range(1, 3):
            pair = {}
            for variant in variants:
                (WORK / (variant + "_drop")).unlink(missing_ok=True)
                application = case(variant)
                manifest = json.loads((REPORTS / (variant + "_manifest.json")).read_text())
                prefixes = {v["writers"]["Event"][:24] for v in manifest["endpoints"].values()}
                discovery = discovery_rows(CAPTURES / (variant + ".pcapng"), prefixes)
                (REPORTS / (variant + "_builtin.json")).write_text(json.dumps(discovery, indent=2) + "\n")
                trial = {"repetition": repetition, "variant": variant,
                         "reader_suppression": PARTICIPANT_VARIANTS[variant][0] == "disabled",
                         "participant_property": manifest["participant_properties"][
                             "dds.participant.inter_participant_data_reader.protocol.disable_preemptive_nack"],
                         "discovery": {k: v for k, v in discovery.items() if k != "acknacks"},
                         "application": {k: application[k] for k in (
                             "delivered", "drop_count", "all_wan_user_base_zero_count",
                             "missing_sequence_2_requests", "repair_request_precedes_sequence_2")}}
                report["trials"].append(trial)
                pair[variant] = trial
                assert application["delivered"] == 8 and application["drop_count"] == 1
                assert application["missing_sequence_2_requests"] > 0
                assert application["repair_request_precedes_sequence_2"]
                if trial["reader_suppression"]:
                    assert application["all_wan_user_base_zero_count"] == 0
                else:
                    assert application["all_wan_user_base_zero_count"] > 0
                for key in SEDP_WRITERS:
                    assert discovery["by_builtin_writer"][key]["positive_acks"] > 0
                for root in (REPORTS, CAPTURES):
                    for p in list(root.glob(variant + "*")):
                        p.rename(p.with_name("r" + str(repetition) + "_" + p.name))
                print(json.dumps(trial), flush=True)
            for node in ("A", "B"):
                before = (REPORTS / f"r{repetition}_{variants[0]}_{node}.xml").read_text()
                for variant in variants[1:]:
                    after = (REPORTS / f"r{repetition}_{variant}_{node}.xml").read_text()
                    assert normalize_configuration(after) == normalize_configuration(before)
            comparisons = ((("matrix_none", "matrix_participant"),
                            ("matrix_reader", "matrix_both")) if matrix else (VARIANTS,))
            for baseline, disabled in comparisons:
                before = pair[baseline]["discovery"]["by_builtin_writer"]
                after = pair[disabled]["discovery"]["by_builtin_writer"]
                stimulated = {key for key, value in before.items() if value["base_zero"] > 0}
                assert stimulated, "No builtin startup ACKNACK stimulus: cannot qualify suppression"
                assert all(before[key]["base_zero"] > 0 for key in SEDP_WRITERS)
                suppressed = {key for key in stimulated if after.get(key, {}).get("base_zero", 0) == 0}
                for key in suppressed:
                    for variant in (baseline, disabled):
                        discovery = pair[variant]["discovery"]
                        assert key in discovery["builtin_data_writer_ids"], (variant, key)
                        assert discovery["by_builtin_writer"][key]["positive_acks"] > 0, (variant, key)
                report["comparisons"].append({
                    "repetition": repetition, "baseline": baseline, "disabled": disabled,
                    "suppressed_builtin_writer_ids": sorted(suppressed),
                    "sedp_suppressed": all(key in suppressed for key in SEDP_WRITERS)})
        report["native_controls"] = {variant: native_case(variant) for variant in VARIANTS}
        for relative, digest in report["source_hashes"].items():
            assert sha(ROOT / relative) == digest, relative
        report["binaries_unchanged"] = all(sha(BUILD / n) == h for n, h in expected.items())
        assert report["binaries_unchanged"]
        report["protocol_checks_passed"] = True
        success = all(c["sedp_suppressed"] if matrix else c["suppressed_builtin_writer_ids"]
                      for c in report["comparisons"])
        report["status"] = ("PASS" if success else
                            "SEDP_SUPPRESSION_NOT_OBSERVED" if matrix else
                            "NO_SUPPRESSION_OBSERVED")
        print(json.dumps({k: report[k] for k in (
            "status", "protocol_checks_passed", "comparisons", "native_controls")}), flush=True)
    except Exception:
        report["error"] = traceback.format_exc()
        print(report["error"], file=sys.stderr)
    finally:
        report["private_shm_artifacts"] = [p.name for p in Path("/dev/shm").iterdir()
                                          if p.name.startswith(("RTI", "dds"))]
        if report["private_shm_artifacts"]:
            report["status"] = "FAIL"
        (REPORTS / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", action="store_true", help="Test all four reader/participant combinations")
    sys.exit(main(parser.parse_args().matrix))
