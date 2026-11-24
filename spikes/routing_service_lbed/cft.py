#!/usr/bin/env python3
"""Change actual Routing Service-owned CFTs and measure remote writer filtering."""

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

import rti.connextdds as dds
from rti.rpc import Requester

import runner as rig


EXPRESSION = "NOT (original_topic > %0)"
RESOURCE = ("/routing_services/GatewayB/domain_routes/bridge/sessions/routes/"
            "routes/rxEvent/inputs/input")
PHASES = (
    ("initial", ("alpha",)),
    ("expand", ("beta",)),
    ("expand_all", ("gamma",)),
    ("narrow", ("alpha",)),
)
VARIANTS = {"sedp": ("sedp", False), "lbed_static": ("spdp2_lbed", True),
            "lbed_broad": ("spdp2_lbed", False)}


def content_filter(parent, parameters, static=False):
    attributes = {"name": "Selection", "kind": "builtin.sql"} if static else {}
    element = rig.child(parent, "content_filter", **attributes)
    rig.child(element, "expression", EXPRESSION)
    values = rig.child(element, "expression_parameters")
    for value in parameters:
        rig.child(values, "element", "'" + value + "'")
    return element


def make_catalog(path, filtered):
    rig.catalog(path, False)
    root = ET.parse(path).getroot()
    if filtered:
        reader = root.find("./domain_participant_library/"
                           "domain_participant[@name='GatewayB']/subscriber/"
                           "data_reader[@name='Event']")
        element = content_filter(reader, PHASES[0][1], static=True)
        reader.remove(element)
        reader.insert(0, element)
    rig.save(root, path)


def make_service(path, node, variant):
    rig.service(path, node, variant)
    root = ET.parse(path).getroot()
    service = root.find("routing_service")
    if node == "B":
        admin = ET.Element("administration")
        rig.child(admin, "domain_id", 113)
        rig.child(admin, "domain_participant_qos", base_name="LBEDSpike::participant")
        rig.child(admin, "datareader_qos", base_name="LBEDSpike::event")
        rig.child(admin, "datawriter_qos", base_name="LBEDSpike::event")
        service.insert(0, admin)
        reader = service.find(".//topic_route[@name='rxEvent']/input")
        content_filter(reader, PHASES[0][1])
    rig.save(root, path)


class Admin:
    def __init__(self, provider, types):
        self.participant = dds.DomainParticipant(
            113, provider.participant_qos_from_profile("LBEDSpike::participant"))
        self.request_type = types.type("RTI::Service::Admin::CommandRequest")
        self.requester = Requester(
            self.request_type, types.type("RTI::Service::Admin::CommandReply"),
            self.participant,
            request_topic="rti/service/admin/command_request",
            reply_topic="rti/service/admin/command_reply",
            datawriter_qos=provider.datawriter_qos_from_profile("LBEDSpike::event"),
            datareader_qos=provider.datareader_qos_from_profile("LBEDSpike::event"),
            require_matching_service_on_send_request=False)
        rig.wait(lambda: self.requester.request_datawriter.publication_matched_status.current_count > 0,
                 "native administration request reader", timeout=10)
        self.history = []

    def command(self, action, body=""):
        request = dds.DynamicData(self.request_type)
        request["instance_id"] = 1
        request["application_name"] = "GatewayB"
        request["action"] = action
        request["resource_identifier"] = RESOURCE
        request["string_body"] = body
        request["octet_body"] = []
        identity = self.requester.send_request(request)
        if not self.requester.wait_for_replies(dds.Duration(10), related_request_id=identity):
            raise TimeoutError("Routing Service did not acknowledge the CFT administration request")
        replies = [sample.data for sample in self.requester.take_replies(
            related_request_id=identity) if sample.info.valid]
        assert len(replies) == 1, len(replies)
        reply = replies[0]
        record = {"action": action, "retcode": int(reply["retcode"]),
                  "native_retcode": reply["native_retcode"], "body": reply["string_body"]}
        assert record["retcode"] == 0, record
        self.history.append({
            "action": action, "retcode": record["retcode"],
            "native_retcode": record["native_retcode"],
            "reply_bytes": len(record["body"].encode()),
            "reply_sha256": hashlib.sha256(record["body"].encode()).hexdigest(),
        })
        return record["body"]

    def update(self, parameters):
        root = ET.Element("input")
        values = rig.child(rig.child(root, "content_filter"), "expression_parameters")
        for value in parameters:
            rig.child(values, "element", "'" + value + "'")
        xml = ET.tostring(root, encoding="unicode")
        self.command(2, 'str://"' + xml + '"')
        body = self.command(1)
        # GET must confirm the new parameters, not merely return a successful update.
        parsed = ET.fromstring(body)
        actual = [element.text for element in parsed.findall(
            ".//content_filter/expression_parameters/element")]
        assert actual == ["'" + value + "'" for value in parameters], (actual, body)

    def close(self):
        self.requester.close()
        self.participant.close()


def run_case(work, variant, provider, admin_types):
    base_variant, static_filter = VARIANTS[variant]
    directory = work / variant
    directory.mkdir()
    catalog = directory / "catalog.xml"
    make_catalog(catalog, static_filter)
    os.environ["LBED_CATALOG"] = str(catalog)
    result = {"variant": variant, "static_catalog_filter": static_filter,
              "phases": [], "administration": []}
    with ExitStack() as cleanup:
        capture = rig.CAPTURE / f"{variant}.pcapng"
        capture.touch()
        capture.chmod(0o666)
        capture_log = cleanup.enter_context((rig.REPORT / f"{variant}-capture.log").open("w"))
        recorder = subprocess.Popen(
            ["dumpcap", "-i", "lo", "-f", "udp dst portrange 35150-35399",
             "-a", "duration:100", "-w", str(capture)],
            stdout=capture_log, stderr=subprocess.STDOUT)
        cleanup.callback(rig.stop, recorder)
        rig.wait(lambda: capture.exists() and capture.stat().st_size > 0,
                 "CFT capture ready", timeout=5)
        processes = []
        for node in rig.DOMAINS:
            path = directory / f"{node}.xml"
            make_service(path, node, base_variant)
            log = cleanup.enter_context((rig.REPORT / f"{variant}-{node}.log").open("w"))
            process = subprocess.Popen(
                [str(rig.SDK / "resource/app/bin/x64Linux4gcc8.5.0/rtiroutingserviceapp"),
                 "-cfgFile", str(path), "-cfgName", "Gateway" + node, "-verbosity", "3"],
                stdout=log, stderr=subprocess.STDOUT)
            cleanup.callback(rig.stop, process)
            processes.append(process)
        probes = {node: rig.Probe(node, provider) for node in rig.DOMAINS}
        for probe in probes.values():
            cleanup.callback(probe.close)
        rig.wait(lambda: all(probe.matched() for probe in probes.values()),
                 "CFT LAN matches", processes)
        admin = Admin(provider, admin_types)
        cleanup.callback(admin.close)
        # Allow administration discovery to match before invoking Requester.
        time.sleep(3)
        admin.command(1)
        writer = probes["A"].writers["Event"]
        reader = probes["B"].readers["Event"]
        for phase, parameters in PHASES:
            if phase != "initial":
                admin.update(parameters)
                # Bound propagation time for the SEDP control and LBED alike.
                time.sleep(1)
            received = []
            begin = time.time()
            offered = {}
            for ordinal, selector in enumerate(("alpha", "beta", "gamma"), 1):
                sample, fields = rig.sample(provider.type(rig.TYPE), "A", "Event",
                                            phase, ordinal)
                fields["original_topic"] = selector
                sample["original_topic"] = selector
                offered[fields["instance_id"]] = fields
                writer.write(sample)
                time.sleep(0.1)
            until = time.monotonic() + 2
            while time.monotonic() < until:
                for data, info in reader.take():
                    if info.valid:
                        expected = offered[data["instance_id"]]
                        actual = {key: list(data[key]) if key == "payload" else data[key]
                                  for key in expected}
                        assert actual == expected, (actual, expected)
                        received.append(data["original_topic"])
                time.sleep(0.02)
            result["phases"].append({
                "phase": phase, "parameters": parameters, "start": begin, "end": time.time(),
                "offered": ["alpha", "beta", "gamma"], "received": received,
                "expected": [value for value in ("alpha", "beta", "gamma")
                             if value <= parameters[0]],
            })
        result["administration"] = admin.history
        for process in processes:
            rig.stop(process)
        rig.stop(recorder)
        rows = rig.capture_rows(capture)
        # No probe joins the WAN. The Event writer ID is fixed by external XML.
        event_data = [row for row in rows
                      if row["kind"] == "DATA" and row["writer"] == "0003e902"]
        prefixes = {row["prefix"] for row in event_data}
        assert len(prefixes) == 1, prefixes
        acknowledgments = [
            row for row in rows
            if row["kind"] == "ACKNACK" and row["writer"] == "0003e902"
            and row["destination"] in prefixes]
        reader_guids = {row["prefix"] + row["reader"] for row in acknowledgments}
        assert len(reader_guids) == 1, reader_guids
        for phase in result["phases"]:
            sequences = {row["sequence"] for row in event_data
                         if phase["start"] <= row["time"] <= phase["end"]}
            phase["wan_data_sequences"] = sorted(sequences)
            phase["wan_unique_writes"] = len(sequences)
            phase["correct_delivery"] = sorted(phase["received"]) == phase["expected"]
            phase["writer_filter_matches"] = len(sequences) == len(phase["expected"])
            phase_readers = {
                row["prefix"] + row["reader"] for row in acknowledgments
                if phase["start"] <= row["time"] <= phase["end"]}
            assert phase_readers == reader_guids, (phase["phase"], phase_readers, reader_guids)
        result["sedp_submessages"] = sum(
            row["writer"] in {"000003c2", "000004c2"} for row in rows)
        assert (result["sedp_submessages"] > 0) == (variant == "sedp"), result
        assert all(phase["correct_delivery"] for phase in result["phases"]), result
        if variant == "sedp":
            assert all(phase["writer_filter_matches"] for phase in result["phases"]), result
        result["event_writer_prefixes"] = sorted(prefixes)
        result["event_reader_guids"] = sorted(reader_guids)
        result["dynamic_writer_filtering"] = all(
            phase["correct_delivery"] and phase["writer_filter_matches"]
            for phase in result["phases"])
        capture_text = (rig.REPORT / f"{variant}-capture.log").read_text()
        drops = re.search(r"Packets received/dropped on interface .*?: (\d+)/(\d+)",
                          capture_text)
        assert drops and int(drops[2]) == 0, capture_text
        result["capture_dropped"] = int(drops[2])
        errors = []
        for node in rig.DOMAINS:
            log = (rig.REPORT / f"{variant}-{node}.log").read_text()
            errors.extend(line for line in log.splitlines()
                          if re.match(r"^(ERROR|FATAL)\b", line))
        assert not errors, errors
        result["native_error_lines"] = len(errors)
        log = (rig.REPORT / f"{variant}-B.log").read_text()
        enabled = log.count(RESOURCE + "|ENABLE] stream=Event")
        updates = log.count("ROUTERDdsStreamReader_propagateFilter:filter expression changed")
        assert (enabled, updates) == (1, 3), (enabled, updates)
        result["reader_enable_count"] = enabled
        result["filter_update_count"] = updates
        for path in directory.glob("*.xml"):
            (rig.REPORT / f"{variant}-{path.name}").write_bytes(path.read_bytes())
        (rig.REPORT / f"{variant}.json").write_text(json.dumps(result, indent=2) + "\n")
        return result


def main():
    results = []
    libraries = [rig.SDK / "resource/app/lib/x64Linux4gcc8.5.0",
                 rig.SDK / "lib/x64Linux4gcc8.5.0"]
    os.environ["LD_LIBRARY_PATH"] = ":".join(
        [str(path) for path in libraries] + [os.environ.get("LD_LIBRARY_PATH", "")])
    try:
        with tempfile.TemporaryDirectory(prefix="routing-service-lbed-cft-") as directory:
            work = Path(directory)
            os.environ["LBED_CATALOG"] = str(work / "unused.xml")
            config = work / "probe.xml"
            rig.save(rig.document(), config)
            provider = dds.QosProvider(str(config))
            for name in ("ServiceCommon", "ServiceAdmin"):
                subprocess.run(
                    [str(rig.SDK / "bin/rtiddsgen"), "-convertToXml", "-unboundedSupport",
                     "-I", str(rig.SDK / "resource/idl"), "-d", str(work),
                     str(rig.SDK / f"resource/idl/{name}.idl")], check=True)
            os.chdir(work)
            admin_types = dds.QosProvider(str(work / "ServiceAdmin.xml"))
            for variant in VARIANTS:
                result = run_case(work, variant, provider, admin_types)
                results.append(result)
                print(json.dumps({"variant": variant, "dynamic_writer_filtering":
                                  result["dynamic_writer_filtering"],
                                  "phases": result["phases"]}), flush=True)
        remnants = [str(path) for path in Path("/dev/shm").iterdir()
                    if path.name.startswith(("RTI", "dds"))]
        assert not remnants, remnants
        supported = all(result["dynamic_writer_filtering"] for result in results)
        report = {
            "result": "PASS" if supported else "DYNAMIC_WRITER_FILTERING_NOT_OBSERVED",
            "experiment_checks_passed": True, "variants": results,
            "shared_memory_remnants": remnants,
            "source_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in (rig.HERE / "cft.py", rig.HERE / "runner.py",
                                           rig.HERE / "qos.xml", rig.HERE / "run.sh")},
        }
        (rig.REPORT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        if supported:
            (rig.REPORT / "PASS").write_text("PASS\n")
        return 0 if supported else 1
    except Exception as error:
        (rig.REPORT / "failure.json").write_text(json.dumps({
            "result": "FAIL", "error": str(error), "completed_variants": results}, indent=2) + "\n")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
