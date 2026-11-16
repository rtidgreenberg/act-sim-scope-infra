"""Private-namespace integration proof: native LAN auto-QoS plus shared typed WAN."""

import argparse
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import xml.etree.ElementTree as ET

import rti.connextdds as dds


HERE = Path(__file__).resolve().parent
CHANNELS = [
    ("event_high", "Event", 600, "command", "UaqCommand"),
    ("periodic_high", "Periodic", 500, "status", "UaqStatus"),
    ("state_high", "State", 400, "status", "UaqStatus"),
    ("event_normal", "Event", 300, "status", "UaqStatus"),
    ("state_normal", "State", 200, "status", "UaqStatus"),
    ("periodic_normal", "Periodic", 100, "status", "UaqStatus"),
]


def element(parent, tag, text=None, **attributes):
    node = ET.SubElement(parent, tag, attributes)
    if text is not None:
        node.text = str(text)
    return node


def until(description, predicate, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("Timeout: " + description)


def export_catalog():
    """Bounded discovery/export stage, finished before the service is deployed."""
    qos = dds.QosProvider(str(HERE / "qos.xml"))
    fixture = dds.QosProvider(str(HERE / "application.xml"))
    producer = dds.DomainParticipant(87, qos.participant_qos_from_profile("UAQ::participant"))
    observer = dds.DomainParticipant(87, qos.participant_qos_from_profile("UAQ::participant"))
    names = ("UaqPoint", "UaqCommand", "UaqStatus")
    writers = []
    try:
        builtin = observer.publication_reader
        for name in names:
            topic = dds.DynamicData.Topic(producer, "Export_" + name, fixture.type(name))
            writers.append(dds.DynamicData.DataWriter(
                dds.Publisher(producer), topic, qos.datawriter_qos_from_profile("UAQ::app")))
        learned = {}

        def discovery():
            for sample in builtin.read():
                if sample.info.valid and sample.data.topic_name.startswith("Export_"):
                    if sample.data.type is not None:
                        learned[sample.data.type_name] = sample.data.type
            return len(learned) == 3

        until("complete original structures from LAN discovery", discovery)
        path = Path("exported_application.idl").resolve()
        path.write_text("\n".join(learned[name].to_string() for name in names))
        output = Path("exported")
        output.mkdir()
        converted = subprocess.run(
            [str(Path(os.environ["NDDSHOME"]) / "bin/rtiddsgen"),
             "-convertToXml", "-d", str(output), str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert converted.returncode == 0, converted.stdout + "\n" + path.read_text()
        exported = output / "exported_application.xml"
        verified = dds.QosProvider(str(exported.resolve()))
        for name in names:
            assert verified.type(name) == learned[name], "Export changed discovered type: " + name
        print("PASS 3 discovery-learned structures exported to IDL/XML with native exact type equality", flush=True)
        return exported
    finally:
        observer.close()
        producer.close()


def generate_types(destination, exported):
    """Deployable deterministic union XML from the pre-exported application catalog."""
    root = ET.parse(exported).getroot()
    types = root.find("types")
    for family, branch, original in (
        ("Event", "command", "UaqCommand"),
        ("State", "status", "UaqStatus"),
        ("Periodic", "status", "UaqStatus"),
    ):
        union = element(types, "union", name="Uaq" + family + "Payload")
        element(union, "discriminator", type="long")
        case = element(union, "case")
        element(case, "caseDiscriminator", value="1")
        element(case, "member", name=branch, type="nonBasic", nonBasicTypeName=original)
        if family == "Event":
            case = element(union, "case")
            element(case, "caseDiscriminator", value="2")
            element(case, "member", name="status", type="nonBasic", nonBasicTypeName="UaqStatus")
        struct = element(types, "struct", name="Uaq" + family)
        for name, bound in (("source", "64"), ("original_topic", "128"), ("instance_id", "64")):
            element(struct, "member", name=name, type="string", stringMaxLength=bound, key="true")
        element(struct, "member", name="priority", type="long")
        element(struct, "member", name="payload", type="nonBasic",
                nonBasicTypeName="Uaq" + family + "Payload")
    ET.indent(root)
    ET.ElementTree(root).write(destination, encoding="unicode")


def configuration(types, library, auto, domain, destination):
    root = ET.parse(HERE / "config.xml").getroot()
    root.insert(0, copy.deepcopy(ET.parse(types).getroot().find("types")))
    root.insert(1, copy.deepcopy(ET.parse(HERE / "qos.xml").getroot().find("qos_library")))
    root.find("./plugin_library/processor_plugin/dll").text = library
    root.find(".//participant/domain_id").text = str(domain)
    session = root.find(".//session")

    def add(direction, name, family, priority, branch, type_name):
        logical = element(session, "topic_route", name=direction + "_" + name)
        element(logical, "route_types", "false")
        period = element(logical, "periodic_action")
        element(period, "sec", 0)
        element(period, "nanosec", 20000000)
        proc = element(logical, "processor", plugin_name="Union::Adapter")
        props = element(element(proc, "property"), "value")
        for key, value in {
            "types": str(types), "qos": str(HERE / "qos.xml"), "wan_domain": domain + 1,
            "direction": direction, "topic": name, "family": family,
            "priority": priority, "branch": branch,
        }.items():
            item = element(props, "element")
            element(item, "name", key)
            element(item, "value", value)
        port = element(logical, "input" if direction == "tx" else "output",
                       name="lan", participant="lan")
        element(port, "topic_name", name)
        element(port, "registered_type_name", type_name)
        element(port, "creation_mode", "ON_DOMAIN_MATCH")
        element(port, "auto_configure_qos", str(auto).lower())

    for channel, family, priority, branch, type_name in CHANNELS:
        for direction in ("tx", "rx"):
            add(direction, direction.upper() + "_" + channel, family, priority, branch, type_name)
    add("tx", "AbsentTopic", "Event", 300, "command", "UaqCommand")
    ET.indent(root)
    ET.ElementTree(root).write(destination, encoding="unicode")


def value(types, type_name, key, sequence):
    data = dds.DynamicData(types.type(type_name))
    data["id"] = key
    data["audit_sequence"] = sequence
    data["timestamp"] = 1794856000000000123 + sequence
    if type_name == "UaqCommand":
        data["destination"] = "Platform_30"
        data.set_double_values("limits", [0.125, -17.25, 1000000.5])
        sequence_field = "points"
    else:
        with data.loan_value("position") as nested:
            nested.data["north"] = -72.125
            nested.data["east"] = 179.999
        data.set_int32_values("motors", [-9, 0, 7, 42])
        data["note"] = "nested status: full structure, not mapped fields"
        sequence_field = "history"
    points = []
    for index in range(3):
        point = dds.DynamicData(types.type("UaqPoint"))
        point["north"] = sequence + index + 0.25
        point["east"] = -sequence - index - 0.75
        points.append(point)
    data.set_complex_values(sequence_field, points)
    return data


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def run_case(build, generated, index, auto, timing, reliable):
    work = Path("case_" + str(index))
    work.mkdir()
    types = dds.QosProvider(str(generated))
    qos = dds.QosProvider(str(HERE / "qos.xml"))
    domain = 91 + index * 2
    assert domain + 1 <= 232
    lan = dds.DomainParticipant(domain, qos.participant_qos_from_profile("UAQ::participant"))
    wan = dds.DomainParticipant(domain + 1, qos.participant_qos_from_profile("UAQ::participant"))
    lan_pubs, lan_subs = lan.publication_reader, lan.subscription_reader
    wan_pubs, wan_subs = wan.publication_reader, wan.subscription_reader
    app_writers, app_readers = {}, {}
    wan_writers, wan_readers, filtered = {}, {}, {}
    process = None
    try:
        for family in ("Event", "State", "Periodic"):
            topic = dds.DynamicData.Topic(wan, family, types.type("Uaq" + family))
            expression = "source = 'gateway'"
            if family == "Event":
                expression += (
                    " AND ((payload._d = 1 AND payload.command.destination = 'Platform_30')"
                    " OR payload._d = 2)")
            cft = dds.DynamicData.ContentFilteredTopic(topic, "remote_" + family, dds.Filter(expression))
            wan_readers[family] = dds.DynamicData.DataReader(
                dds.Subscriber(wan), cft, qos.datareader_qos_from_profile("UAQ::" + family))
            wan_writers[family] = dds.DynamicData.DataWriter(
                dds.Publisher(wan), topic, qos.datawriter_qos_from_profile("UAQ::" + family))
            filtered[family] = cft

        def apps():
            for channel, _, _, _, type_name in CHANNELS:
                tx = dds.DynamicData.Topic(lan, "TX_" + channel, types.type(type_name))
                rx = dds.DynamicData.Topic(lan, "RX_" + channel, types.type(type_name))
                profile = "UAQ::app_reliable" if reliable else "UAQ::app"
                app_writers[channel] = dds.DynamicData.DataWriter(
                    dds.Publisher(lan), tx, qos.datawriter_qos_from_profile(profile))
                app_readers[channel] = dds.DynamicData.DataReader(
                    dds.Subscriber(lan), rx, qos.datareader_qos_from_profile("UAQ::app"))

        if timing == "before":
            apps()
        assert not [s for s in lan_subs.read() if s.info.valid and s.data.topic_name.startswith("TX_")]
        assert not [s for s in lan_pubs.read() if s.info.valid and s.data.topic_name.startswith("RX_")]
        xml = work / "service.xml"
        configuration(generated, str(build / "libunion_auto_qos.so"), auto, domain, xml)
        arch = "x64Linux4gcc8.5.0"
        home = Path(os.environ["NDDSHOME"])
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = ":".join([
            str(home / "resource/app/lib" / arch), str(home / "lib" / arch),
            env.get("LD_LIBRARY_PATH", ""),
        ])
        with (work / "service.log").open("w") as log:
            process = subprocess.Popen(
                [str(home / "resource/app/bin" / arch / "rtiroutingserviceapp"),
                 "-cfgFile", str(xml.resolve()), "-cfgName", "UnionAuto", "-verbosity", "WARN"],
                stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            if timing == "after":
                time.sleep(1)
                assert process.poll() is None, (work / "service.log").read_text()
                assert not [s for s in lan_subs.read() if s.info.valid and s.data.topic_name.startswith("TX_")]
                assert not [s for s in lan_pubs.read() if s.info.valid and s.data.topic_name.startswith("RX_")]
                apps()

            def endpoints():
                assert process.poll() is None, (work / "service.log").read_text()
                subs = {s.data.topic_name: s.data for s in lan_subs.read()
                        if s.info.valid and s.data.topic_name.startswith("TX_")}
                pubs = {s.data.topic_name: s.data for s in lan_pubs.read()
                        if s.info.valid and s.data.topic_name.startswith("RX_")}
                return (subs, pubs) if len(subs) == len(pubs) == 6 else None

            subscriptions, publications = until("12 independent native LAN endpoints", endpoints)
            expected_rel = dds.ReliabilityKind.RELIABLE if reliable and auto else dds.ReliabilityKind.BEST_EFFORT
            expected_dur = dds.DurabilityKind.TRANSIENT_LOCAL if reliable and auto else dds.DurabilityKind.VOLATILE
            for item in subscriptions.values():
                assert item.reliability.kind == expected_rel, str(item.reliability.kind) + " expected " + str(expected_rel)
                assert item.durability.kind == expected_dur, str(item.durability)
            for item in publications.values():
                assert item.reliability.kind == dds.ReliabilityKind.RELIABLE
                assert item.durability.kind == (
                    dds.DurabilityKind.TRANSIENT_LOCAL if auto else dds.DurabilityKind.VOLATILE)

            own_writers = {str(w.instance_handle) for w in wan_writers.values()}
            own_readers = {str(r.instance_handle) for r in wan_readers.values()}

            def inventory():
                pubs = [s.data for s in wan_pubs.read() if s.info.valid
                        and s.data.topic_name in wan_writers and str(s.info.instance_handle) not in own_writers]
                subs = [s.data for s in wan_subs.read() if s.info.valid
                        and s.data.topic_name in wan_readers and str(s.info.instance_handle) not in own_readers]
                # Builtin discovery excludes this participant's own entities on this install.
                return (pubs, subs) if len(pubs) == len(subs) == 3 else None

            remote_pubs, remote_subs = until("exact shared WAN endpoint inventory", inventory)
            assert sorted(x.topic_name for x in remote_pubs) == ["Event", "Periodic", "State"]
            assert sorted(x.topic_name for x in remote_subs) == ["Event", "Periodic", "State"]
            for item in remote_pubs:
                periodic = item.topic_name == "Periodic"
                assert item.reliability.kind == (
                    dds.ReliabilityKind.BEST_EFFORT if periodic else dds.ReliabilityKind.RELIABLE)
                assert item.durability.kind == (
                    dds.DurabilityKind.VOLATILE if periodic else dds.DurabilityKind.TRANSIENT_LOCAL)

            expected_tx, expected_rx, got_tx, got_rx = {}, {}, {}, {}
            for number, (channel, family, priority, branch, type_name) in enumerate(CHANNELS, 1):
                expected_tx["TX_" + channel] = value(types, type_name, "tx/" + channel, number)
                expected_rx[channel] = value(types, type_name, "rx/" + channel, number + 10)

            def exchange():
                for channel, family, priority, branch, _ in CHANNELS:
                    app_writers[channel].write(expected_tx["TX_" + channel])
                    frame = dds.DynamicData(types.type("Uaq" + family))
                    frame["source"] = "remote"
                    frame["original_topic"] = "RX_" + channel
                    frame["instance_id"] = "rx/" + channel
                    frame["priority"] = priority
                    with frame.loan_value("payload") as payload:
                        payload.data[branch] = expected_rx[channel]
                    wan_writers[family].write(frame)
                time.sleep(0.05)
                for family, reader in wan_readers.items():
                    for sample, info in reader.take():
                        if not info.valid:
                            continue
                        original = sample["original_topic"]
                        channel = original.removeprefix("TX_")
                        spec = next(c for c in CHANNELS if c[0] == channel)
                        assert sample["priority"] == spec[2]
                        assert sample["instance_id"] == expected_tx[original]["id"]
                        with sample.loan_value("payload") as payload:
                            assert payload.data[spec[3]] == expected_tx[original], original
                        got_tx[original] = True
                for channel, reader in app_readers.items():
                    for sample, info in reader.take():
                        if info.valid:
                            assert sample == expected_rx[channel], channel
                            got_rx[channel] = True
                return len(got_tx) == len(got_rx) == 6

            if auto:
                until("all six channels whole-structure round trips", exchange)
            else:
                end = time.monotonic() + 2
                while time.monotonic() < end:
                    exchange()
                assert len(got_tx) == 6, got_tx
                assert not got_rx, got_rx

            absent_before = [s for s in lan_subs.read() if s.info.valid and s.data.topic_name == "AbsentTopic"]
            assert not absent_before, "Absent input should remain pending, not instantiated"
            log.flush()
            started = (work / "service.log").read_text()
            assert "START tx AbsentTopic" not in started
            for family in ("Event", "State", "Periodic"):
                assert f"WAN_OWNER {family} writer=1 reader=1 async=true channels=2 shared_flow=uaq" in started
            late_equalities = 0
            typed_filter_exclusions = 0
            if auto:
                before_keys = sorted(str(x.key) for x in remote_pubs + remote_subs)
                late_topic = dds.DynamicData.Topic(lan, "AbsentTopic", types.type("UaqCommand"))
                late_writer = dds.DynamicData.DataWriter(
                    dds.Publisher(lan), late_topic,
                    qos.datawriter_qos_from_profile("UAQ::app_reliable" if reliable else "UAQ::app"))
                late_value = value(types, "UaqCommand", "late/key", 99)

                def late():
                    late_writer.write(late_value)
                    for frame, info in wan_readers["Event"].take():
                        if info.valid and frame["original_topic"] == "AbsentTopic":
                            with frame.loan_value("payload") as payload:
                                assert payload.data["command"] == late_value
                            return True
                    return False

                until("formerly absent route independently activates", late)
                pubs_after, subs_after = inventory()
                assert sorted(str(x.key) for x in pubs_after + subs_after) == before_keys
                assert "START tx AbsentTopic" in (work / "service.log").read_text()
                late_equalities = 1
                for reader in wan_readers.values():
                    reader.take()
                excluded = value(types, "UaqCommand", "excluded/key", 101)
                excluded["destination"] = "Platform_99"
                for _ in range(5):
                    app_writers["event_high"].write(excluded)
                    time.sleep(0.1)
                end = time.monotonic() + 0.5
                while time.monotonic() < end:
                    for frame, info in wan_readers["Event"].take():
                        assert not info.valid or frame["instance_id"] != "excluded/key"
                    time.sleep(0.05)
                typed_filter_exclusions = 1
            result = {
                "auto": auto, "application_timing": timing, "reliable_source": reliable,
                "input_reliability": str(expected_rel), "input_durability": str(expected_dur),
                "output_durability": "TRANSIENT_LOCAL" if auto else "VOLATILE",
                "native_lan_endpoints": 12, "missing_topic_pending": True,
                "wan_writers": sorted(x.topic_name for x in remote_pubs),
                "wan_readers": sorted(x.topic_name for x in remote_subs),
                "tx_full_equalities": len(got_tx), "rx_full_equalities": len(got_rx),
                "late_topic_full_equalities": late_equalities,
                "wan_inventory_unchanged_after_late_topic": bool(auto),
                "typed_union_filter_exclusions": typed_filter_exclusions,
            }
            print("PASS " + json.dumps(result, sort_keys=True), flush=True)
            return result
    finally:
        if process is not None:
            stop(process)
            if process.returncode not in (0, -signal.SIGTERM):
                print((work / "service.log").read_text(), flush=True)
        lan.close()
        wan.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("build", type=Path)
    args = parser.parse_args()
    generated = Path("generated_types.xml").resolve()
    exported = export_catalog()
    generate_types(generated, exported)
    provider = dds.QosProvider(str(generated))
    for family in ("Event", "State", "Periodic"):
        assert provider.type("Uaq" + family).name == "Uaq" + family
    print("PASS generated stable typed union XML loaded by native QosProvider", flush=True)
    results = []
    for index, (auto, timing, reliable) in enumerate([
        (False, "before", False), (True, "before", False),
        (False, "after", False), (True, "after", False),
        (False, "before", True), (True, "before", True),
    ]):
        results.append(run_case(args.build.resolve(), generated, index, auto, timing, reliable))
    Path("results.json").write_text(json.dumps(results, indent=2) + "\n")
    print("PASS 6 cases; 39 automatic full-payload equalities; no all-topic startup barrier", flush=True)


if __name__ == "__main__":
    main()
