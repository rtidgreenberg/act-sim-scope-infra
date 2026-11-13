#!/usr/bin/env python3
"""Render and launch the opt-in, single-route Routing Service data-plane variant."""

import argparse
import copy
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

import yaml


SHUTDOWN_TIMEOUT = 10


def _signal_process_group(process, signum):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        if process.poll() is None:
            raise


def wait_for_service(process):
    deadline = None
    old_handlers = {}

    def stop(signum, _frame):
        nonlocal deadline
        if deadline is None:
            deadline = time.monotonic() + SHUTDOWN_TIMEOUT
            _signal_process_group(process, signum)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            old_handlers[signum] = signal.signal(signum, stop)
        while True:
            timeout = 0.2 if deadline is None else max(0, deadline - time.monotonic())
            try:
                return process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if deadline is not None and time.monotonic() >= deadline:
                    break
    finally:
        try:
            if process.poll() is None:
                if deadline is None:
                    deadline = time.monotonic() + SHUTDOWN_TIMEOUT
                    _signal_process_group(process, signal.SIGTERM)
                try:
                    process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    print("act Routing Service: shutdown grace period expired; "
                          "forcing termination of owned process group", file=sys.stderr)
                    _signal_process_group(process, signal.SIGKILL)
                    process.wait()
        finally:
            for signum, handler in old_handlers.items():
                signal.signal(signum, handler)
    return process.returncode


def child(parent, tag, text=None, **attributes):
    element = ET.SubElement(parent, tag, attributes)
    if text is not None:
        element.text = str(text)
    return element


def render(model, config, library, data_plane_only):
    root = ET.Element("dds")
    variables = {}
    for path in model["qos_libraries"]:
        source = ET.parse(path).getroot()
        for variable in source.findall("./configuration_variables/element"):
            variables[variable.findtext("name")] = variable.findtext("value")
        for qos in source.findall("qos_library"):
            profiles = copy.deepcopy(qos)
            # RS schema validation requires symbolic spellings of these DDS sentinels.
            for tag, numeric, symbolic in (
                ("type_object_max_serialized_length", "-2", "LENGTH_AUTO"),
                ("min_send_window_size", "-1", "LENGTH_UNLIMITED"),
                ("max_send_window_size", "-1", "LENGTH_UNLIMITED"),
            ):
                for element in profiles.iter(tag):
                    if element.text and element.text.strip() == numeric:
                        element.text = symbolic
            root.append(profiles)
    # Routing Service and QosProvider use different configuration-variable wrappers.
    configuration = ET.Element("configuration_variables")
    variables["RS_FLOW_BYTES_PER_PERIOD"] = str(model["async_bandwidth_bps"] // 800)
    value = child(configuration, "value")
    for name, default in variables.items():
        entry = child(value, "element")
        child(entry, "name", name)
        child(entry, "value", default if name == "RS_FLOW_BYTES_PER_PERIOD"
              else os.environ.get(name, default))
    root.insert(0, configuration)
    root.append(copy.deepcopy(ET.parse(model["types"]).getroot().find("types")))

    plugins = child(root, "plugin_library", name="ACT")
    plugin = child(plugins, "processor_plugin", name="Gateway")
    child(plugin, "dll", library)
    child(plugin, "create_function", "ActGatewayPlugin_create_processor_plugin")
    # Grouping is node-local: fleet-wide grouping would suppress all WAN peers.
    service = child(root, "routing_service", name="ACTGateway", group_name=model["node"])
    domain = child(service, "domain_route", name="gateway")
    needed_types = {}
    for port in model.get("inputs", []) + model.get("outputs", []):
        needed_types.setdefault(port["participant"], set()).add(port["type"])
    wan = model["wan"][0]["participant"]
    needed_types.setdefault(wan, set()).update(("WanEvent", "WanStatus"))
    for participant in model["participants"]:
        name = participant["name"]
        if name not in needed_types:
            continue
        element = child(domain, "participant", name=name)
        child(element, "domain_id", participant["domain"])
        qos = child(element, "domain_participant_qos", base_name=participant["profile"])
        identity = child(qos, "participant_name")
        child(identity, "name", model["node"] + "/" + model["router"])
        child(identity, "role_name", "act.router")
        if participant["partitions"]:
            names = child(child(qos, "partition"), "name")
            for partition in participant["partitions"]:
                child(names, "element", partition)
        if name == wan:
            profile = root.find(
                "./qos_library[@name='ACT_QOS_LIB']/qos_profile"
                "[@name='rs_priority_participant']/domain_participant_qos/property")
            if profile is None:
                raise ValueError("production rs_priority_participant profile is required")
            qos.append(copy.deepcopy(profile))
        for type_name in sorted(needed_types[name]):
            child(element, "register_type", name=type_name, type_ref=type_name)

    session = child(domain, "session", name="gateway")
    route = child(session, "topic_route", name="gateway")
    child(route, "route_types", "false")
    duration = child(route, "periodic_action")
    child(duration, "sec", 0)
    child(duration, "nanosec", 10000000)
    processor = child(route, "processor", plugin_name="ACT::Gateway")
    properties = child(child(processor, "property"), "value")
    for name, value in {
        "config": config, "role": model["role"], "node": model["node"],
        "router": model["router"], "data_plane_only": str(data_plane_only).lower(),
        "teams": yaml.safe_dump(model["teams"], default_flow_style=True).strip(),
    }.items():
        entry = child(properties, "element")
        child(entry, "name", name)
        child(entry, "value", value)

    libraries = {qos.attrib["name"]: qos for qos in root.findall("qos_library")}
    retention = libraries["ACT_QOS_LIB"].find(
        "./qos_profile[@name='router_lifecycle_reader']/datareader_qos/reader_data_lifecycle")
    if retention is None:
        raise ValueError("production router_lifecycle_reader profile is required")

    def port(kind, spec):
        element = child(route, kind, name=spec["name"], participant=spec["participant"])
        child(element, "topic_name", spec["topic"])
        child(element, "registered_type_name", spec["type"])
        child(element, "creation_mode", "IMMEDIATE")
        child(element, "auto_configure_qos", "false")
        qos = child(element, "datareader_qos" if kind == "input" else "datawriter_qos",
                    base_name=spec["profile"])
        if kind == "input":
            qos.append(copy.deepcopy(retention))
        return element

    for spec in model.get("inputs", []):
        port("input", spec)
    for spec in model.get("outputs", []):
        port("output", spec)
    for family in model["wan"]:
        spec = dict(family, type="Wan" + family["name"], topic=family["name"])
        incoming = port("input", dict(spec, name="wan_in_" + family["name"]))
        cft = child(incoming, "content_filter")
        child(cft, "expression", family["expression"])
        parameters = child(cft, "expression_parameters")
        for parameter in family["parameters"]:
            child(parameters, "element", parameter)
        port("output", dict(spec, name="wan_out_" + family["name"],
                            profile=family["writer_profile"]))
    return ET.tostring(root, encoding="unicode")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--role", default="")
    parser.add_argument("--node-name", default="")
    parser.add_argument("--name", default="")
    parser.add_argument("--team", action="append", default=[],
                        help="initial exact team ID; repeat for multiple memberships")
    parser.add_argument("--build-dir", type=Path, default=Path("router/build/routing_service"))
    parser.add_argument("--render", type=Path, help="write XML without launching DDS")
    parser.add_argument("--data-plane-only", action="store_true",
                        help="explicitly omit unimplemented ACT health/admin compatibility")
    args = parser.parse_args()
    build = args.build_dir.resolve()
    library = build / "libact_gateway_processor.so"
    if not library.is_file():
        parser.error(f"processor library not built: {library}")
    try:
        description = subprocess.run(
            [str(build / "act_rs_config"), str(args.config.resolve()), args.role,
             args.node_name, args.name, *args.team], check=True, text=True, capture_output=True)
        model = yaml.safe_load(description.stdout)
        if not args.data_plane_only:
            parser.error("full ACT parity is not implemented; --data-plane-only is required")
        for name, key in (("WAN_TIMEOUT_SEC", "timeout"), ("WAN_HB_PERIOD_SEC", "heartbeat"),
                          ("WAN_HB_RETRIES", "retries")):
            os.environ[name] = str(model[key])
        xml = render(model, str(args.config.resolve()), str(library), args.data_plane_only)
        if args.render:
            args.render.write_text(xml)
            return 0
        home = os.environ.get("NDDSHOME")
        if not home:
            parser.error("NDDSHOME must point to the Routing Service 7.7 installation")
        executables = list((Path(home) / "resource/app/bin").glob("*/rtiroutingserviceapp"))
        if len(executables) != 1:
            parser.error("expected exactly one native Routing Service architecture in NDDSHOME")
        executable = executables[0]
        environment = os.environ.copy()
        architecture = executable.parent.name
        library_paths = [str(Path(home) / "resource/app/lib" / architecture),
                         str(Path(home) / "lib" / architecture)]
        if environment.get("LD_LIBRARY_PATH"):
            library_paths.append(environment["LD_LIBRARY_PATH"])
        environment["LD_LIBRARY_PATH"] = os.pathsep.join(library_paths)
        with tempfile.TemporaryDirectory(prefix="act-routing-service-") as work:
            path = Path(work) / "gateway.xml"
            path.write_text(xml)
            process = subprocess.Popen(
                [str(executable), "-cfgFile", str(path), "-cfgName", "ACTGateway",
                 "-verbosity", "WARN"], start_new_session=True, env=environment)
            return wait_for_service(process)
    except subprocess.CalledProcessError as error:
        print(error.stderr or str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, ET.ParseError, yaml.YAMLError) as error:
        print(f"act Routing Service: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
