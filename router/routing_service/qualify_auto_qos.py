"""Characterize native LAN QoS inference in an owned private network namespace."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

import rti.connextdds as dds

from run import render


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixtures") / "auto_qos.xml"


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def model(direction, domain):
    properties = {
        "act.version": "1",
        "act.types_uri": str(ROOT / "harness_v2/datamodel/gen/ActTypes.xml"),
        "act.node": "Boat30", "act.role": "platform", "act.router": "rs",
        "act.wan_contract": "two_topic_union", "act.monitoring": "false",
        "act.receive_policy": "static", "act.teams": "",
        "act.admin_participant": "lan", "act.lan_participant": "lan",
        "act.wan_participant": "wan", "act.lan_domain": str(domain),
        "act.wan_domain": str(domain + 1),
        "act.binding.contact.topic_name": "ContactReport",
        "act.binding.contact.type": "contact_report",
        "act.binding.contact.channel": "event_normal",
        "act.binding.contact.branch": "contact_report",
        "act.binding.contact.instance_id": "contact_id",
        f"act.binding.contact.{direction}.port": "application_port",
        f"act.binding.contact.{direction}.recipients": "control",
    }
    spec = {"name": "application_port", "topic": "ContactReport",
            "type": "contact_report", "participant": "lan",
            "profile": "AUTOQOS_TEST::router"}
    return {
        "node": "Boat30", "role": "platform", "router": "rs",
        "types": properties["act.types_uri"],
        "qos_libraries": [str(ROOT / "harness_v2/qos/act_qos_profiles.xml"), str(FIXTURE)],
        "async_bandwidth_bps": 2000000,
        "properties": properties, "admin_participant": "lan",
        "participants": [
            {"name": "lan", "domain": domain, "profile": "AUTOQOS_TEST::participant",
             "partitions": []},
            {"name": "wan", "domain": domain + 1,
             "profile": "ACT_QOS_LIB::wan_two_topic_participant", "partitions": []},
        ],
        "inputs": [spec] if direction == "tx" else [],
        "outputs": [spec] if direction == "rx" else [],
        "wan": [
            {"name": family, "type": "Wan" + family, "participant": "wan",
             "profile": profile, "writer_profile": "ACT_QOS_LIB::rs_wan_" + family.lower(),
             "expression": "source_id <> %0", "parameters": ["'Boat30'"]}
            for family, profile in (("Event", "ACT_QOS_LIB::wan_event"),
                                    ("Status", "ACT_QOS_LIB::wan_union_status"))
        ],
    }


def run_case(auto, direction, timing, profile_mode, creation_mode, index, verbose,
             application_profile, build, absent_input):
    domain = 91 + index * 2
    assert domain + 1 <= 232
    result = {"auto": auto, "direction": direction, "timing": timing,
              "profile": profile_mode, "creation_mode": creation_mode,
              "application_profile": application_profile, "absent_input": absent_input}
    with ExitStack() as cleanup:
        work = Path(cleanup.enter_context(tempfile.TemporaryDirectory(prefix="autoqos-")))
        provider = dds.QosProvider(str(FIXTURE))
        types = dds.QosProvider(str(ROOT / "harness_v2/datamodel/gen/ActTypes.xml"))
        participant = dds.DomainParticipant(
            domain, provider.participant_qos_from_profile("AUTOQOS_TEST::participant"))
        cleanup.callback(participant.close)
        builtin = participant.subscription_reader if direction == "tx" else participant.publication_reader
        topic = dds.DynamicData.Topic(participant, "ContactReport", types.type("contact_report"))
        wan_qos = dds.QosProvider(str(ROOT / "harness_v2/qos/act_qos_profiles.xml"))
        wan_participant = dds.DomainParticipant(
            domain + 1, wan_qos.participant_qos_from_profile(
                "ACT_QOS_LIB::wan_two_topic_participant"))
        cleanup.callback(wan_participant.close)
        wan_topic = dds.DynamicData.Topic(wan_participant, "Event", types.type("WanEvent"))
        if direction == "tx":
            wan_endpoint = dds.DynamicData.DataReader(
                dds.Subscriber(wan_participant), wan_topic,
                wan_qos.datareader_qos_from_profile("ACT_QOS_LIB::wan_event"))
        else:
            wan_endpoint = dds.DynamicData.DataWriter(
                dds.Publisher(wan_participant), wan_topic,
                wan_qos.datawriter_qos_from_profile("ACT_QOS_LIB::wan_event"))

        def application():
            if direction == "tx":
                return dds.DynamicData.DataWriter(
                    dds.Publisher(participant), topic,
                    provider.datawriter_qos_from_profile("AUTOQOS_TEST::" + application_profile))
            return dds.DynamicData.DataReader(
                dds.Subscriber(participant), topic,
                provider.datareader_qos_from_profile("AUTOQOS_TEST::" + application_profile))

        endpoint = application() if timing == "before" else None
        configuration = model(direction, domain)
        if absent_input:
            configuration["inputs"].append({
                "name": "absent_input", "topic": "PlatformStatus", "type": "platform_init_status",
                "participant": "lan", "profile": "AUTOQOS_TEST::router"})
            configuration["properties"].update({
                "act.binding.navigation.topic_name": "PlatformStatus",
                "act.binding.navigation.type": "platform_init_status",
                "act.binding.navigation.branch": "navigation",
                "act.binding.navigation.channel": "periodic_normal",
                "act.binding.navigation.instance_id": "source",
                "act.binding.navigation.tx.port": "absent_input",
                "act.binding.navigation.tx.recipients": "control",
            })
        root = ET.fromstring(render(configuration, "", str(
            build / "libact_gateway_processor.so"), True))
        port = root.find(".//topic_route/" + ("input" if direction == "tx" else "output")
                         + "[@name='application_port']")
        assert port.attrib["name"] == "application_port"
        port.find("auto_configure_qos").text = str(auto).lower()
        port.find("creation_mode").text = creation_mode
        if profile_mode == "defaults":
            port.remove(port.find("datareader_qos" if direction == "tx" else "datawriter_qos"))
        elif profile_mode == "deadline":
            port.find("datareader_qos" if direction == "tx" else "datawriter_qos").set(
                "base_name", "AUTOQOS_TEST::router_deadline")
        if absent_input:
            absent = root.find(".//topic_route/input[@name='absent_input']")
            absent.find("auto_configure_qos").text = str(auto).lower()
            absent.find("creation_mode").text = creation_mode
            absent.remove(absent.find("datareader_qos"))
        xml = work / "gateway.xml"
        ET.ElementTree(root).write(xml)
        log_path = work / "service.log"
        log = cleanup.enter_context(log_path.open("w"))
        command = [sys.executable, str(ROOT / "router/routing_service/run.py"),
                   "--xml", str(xml)]
        environment = os.environ.copy()
        if verbose:
            home = Path(environment["NDDSHOME"])
            arch = "x64Linux4gcc8.5.0"
            command = [str(home / "resource/app/bin" / arch / "rtiroutingserviceapp"),
                       "-cfgFile", str(xml), "-cfgName", "ACTGateway", "-verbosity", "LOCAL"]
            environment["LD_LIBRARY_PATH"] = ":".join(
                [str(home / "resource/app/lib" / arch), str(home / "lib" / arch),
                 environment.get("LD_LIBRARY_PATH", "")])
        process = subprocess.Popen(
            command, env=environment, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True)
        cleanup.callback(stop, process)
        deadline = time.monotonic() + 15
        while "RTI Routing Service 7.7.0 executing" not in log_path.read_text() and time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(log_path.read_text())
            time.sleep(0.05)
        if "RTI Routing Service 7.7.0 executing" not in log_path.read_text():
            raise AssertionError("startup timeout\n" + log_path.read_text())
        if endpoint is None:
            time.sleep(0.5)
            endpoint = application()
        observations = {}
        deadline = time.monotonic() + 10
        matched = False
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(log_path.read_text())
            for data, info in builtin.read():
                if info.valid and data.topic_name == "ContactReport":
                    observations[str(info.instance_handle)] = {
                        "reliability": str(data.reliability.kind),
                        "durability": str(data.durability.kind),
                        "deadline": str(data.deadline.period),
                    }
            status = (endpoint.publication_matched_status if direction == "tx"
                      else endpoint.subscription_matched_status)
            matched = status.current_count > 0
            time.sleep(0.05)
        delivered = False
        if matched:
            original = dds.DynamicData(types.type("contact_report"))
            original["source"] = "Boat30" if direction == "tx" else "Injected"
            original["contact_id"] = "autoqos-check"
            original["run_id"] = "autoqos-check"
            original["audit_sequence"] = 42
            if direction == "rx":
                frame = dds.DynamicData(types.type("WanEvent"))
                frame["source_id"] = "Injected"
                frame["original_topic"] = "ContactReport"
                frame["instance_id"] = "autoqos-check"
                frame["schema_version"] = 3
                frame["source_role"] = "control"
                with frame.loan_value("payload") as payload:
                    with payload.data.loan_value("contact_report") as value:
                        for field in ("source", "contact_id", "run_id", "audit_sequence"):
                            value.data[field] = original[field]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not delivered:
                (endpoint if direction == "tx" else wan_endpoint).write(
                    original if direction == "tx" else frame)
                for data, info in (wan_endpoint if direction == "tx" else endpoint).take():
                    if info.valid:
                        if direction == "tx":
                            with data.loan_value("payload") as payload:
                                with payload.data.loan_value("contact_report") as value:
                                    delivered = value.data == original
                        else:
                            delivered = data == original
                time.sleep(0.05)
        result.update(matched=matched, delivered=delivered,
                      observations=list(observations.values()),
                      processor_started="rs.gateway.started" in log_path.read_text())
        result["log"] = log_path.read_text()
        stop(process)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--profile", choices=["explicit", "defaults", "deadline"], default="explicit")
    parser.add_argument("--creation-mode", choices=["ON_DOMAIN_MATCH"], default="ON_DOMAIN_MATCH",
                        help="independent LAN bindings require ON_DOMAIN_MATCH")
    parser.add_argument("--direction", choices=["tx", "rx"])
    parser.add_argument("--timing", choices=["before", "after"])
    parser.add_argument("--only-auto", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--absent-input", action="store_true")
    parser.add_argument("--application-profile", default="application", choices=[
        "application", "application_infinite_deadline", "application_writer_reliable"])
    args = parser.parse_args()
    if os.environ.get("ACT_RS_AUTOQOS_ISOLATED") != "1":
        parser.error("requires ACT_RS_AUTOQOS_ISOLATED=1 in an owned private network namespace")
    build = args.build_dir.resolve()
    if not (build / "libact_gateway_processor.so").is_file():
        parser.error(f"processor library not built: {build}")
    for key, value in {
        **{"WAN_PEER" + (str(i) if i > 1 else ""): "127.0.0.1" for i in range(1, 12)},
    }.items():
        os.environ[key] = value
    for index, (direction, timing, auto) in enumerate(
            (d, t, a) for d in ([args.direction] if args.direction else ("tx", "rx"))
            for t in ([args.timing] if args.timing else ("before", "after"))
            for a in ((True,) if args.only_auto else (False, True))):
        run_case(auto, direction, timing, args.profile, args.creation_mode, index, args.verbose,
                 args.application_profile, build, args.absent_input)
