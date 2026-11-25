#!/usr/bin/env python3
"""Launch prepared Routing Service XML, or render it from development YAML."""

import argparse
import copy
import hashlib
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

SHUTDOWN_TIMEOUT = 10


def lan_participant_identity(properties):
    values = [properties[key] for key in ("act.node", "act.router", "act.lan_participant")]
    identity = "/".join(values)
    if len(identity.encode("utf-8")) <= 255:
        return identity
    canonical = "".join(f"{len(value.encode('utf-8'))}:{value}" for value in values)
    return "act.lan.sha256/" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    inputs = [spec for spec in model.get("inputs", [])
              if data_plane_only or spec["topic"] != "ActRouterDiagnostics"]
    outputs = list(model.get("outputs", []))
    if not data_plane_only:
        lan = model["admin_participant"]
        inputs.append({"name": "monitor_command", "topic": "ActRouterCommand",
                       "type": "RouterCommand", "participant": lan,
                       "profile": "ACT_QOS_LIB::router_admin_command"})
        for name, topic, type_name, profile in (
                ("ack", "ActRouterCommandAck", "RouterCommandAck", "router_admin_command_ack"),
                ("status", "ActRouterStatus", "RouterStatus", "router_admin_status"),
                ("mesh", "ActRouterMeshStatus", "RouterMeshStatus", "router_mesh_status"),
                ("links", "ActRouterLinkStats", "RouterLinkStats", "router_link_stats"),
                ("diagnostics", "ActRouterDiagnostics", "RouterDiagnostics", "lan_status")):
            outputs.append({"name": "monitor_" + name, "topic": topic, "type": type_name,
                            "participant": lan, "profile": "ACT_QOS_LIB::" + profile})
    for port in inputs + outputs:
        needed_types.setdefault(port["participant"], set()).add(port["type"])
    wan = model["wan"][0]["participant"]
    needed_types.setdefault(wan, set()).update(family["type"] for family in model["wan"])
    for participant in model["participants"]:
        name = participant["name"]
        if name not in needed_types:
            continue
        element = child(domain, "participant", name=name)
        child(element, "domain_id", participant["domain"])
        qos = child(element, "domain_participant_qos", base_name=participant["profile"])
        identity = child(qos, "participant_name")
        participant_identity = model["node"] + "/" + model["router"]
        if name == model["properties"]["act.lan_participant"]:
            participant_identity = lan_participant_identity(model["properties"])
        child(identity, "name", participant_identity)
        child(identity, "role_name", "act.router")
        if participant["partitions"]:
            names = child(child(qos, "partition"), "name")
            for partition in participant["partitions"]:
                child(names, "element", partition)
        if name == wan:
            profile = root.find(
                "./qos_library[@name='ACT_QOS_LIB']/qos_profile"
                "[@name='rs_priority_participant']/domain_participant_qos")
            properties = None if profile is None else profile.find("property")
            limit = None if profile is None else profile.find(
                "resource_limits/participant_property_list_max_length")
            if properties is None or limit is None:
                raise ValueError("production rs_priority_participant properties and "
                                 "participant_property_list_max_length are required")
            qos.append(copy.deepcopy(properties))
            limits = child(qos, "resource_limits")
            limits.append(copy.deepcopy(limit))
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
    explicit = dict(model["properties"])
    explicit["act.monitoring"] = str(not data_plane_only).lower()
    if not data_plane_only:
        for name, value in list(explicit.items()):
            if name.endswith(".topic_name") and value == "ActRouterDiagnostics":
                port_name = name[:-len("topic_name")] + "tx.port"
                if port_name in explicit:
                    explicit[port_name] = "monitor_diagnostics"
    for name, value in explicit.items():
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
                    **({"base_name": spec["profile"]} if spec.get("profile") else {}))
        if kind == "input":
            qos.append(copy.deepcopy(retention))
        return element

    for spec in inputs:
        port("input", spec)
    for spec in outputs:
        port("output", spec)
    for family in model["wan"]:
        spec = dict(family, topic=family["name"])
        incoming = port("input", dict(spec, name="wan_in_" + family["name"]))
        cft = child(incoming, "content_filter")
        expression = family["expression"]
        if not data_plane_only:
            contract = model.get("wan_contract") or model["properties"].get("act.wan_contract")
            internal = ("RouterConfig" if family["name"] == ("P1_Event" if contract == "six_topic_union" else "Event") else
                        "RouterHealth" if family["name"] in ("P1_Periodic", "Periodic", "Status") else None)
            if internal and f"original_topic = '{internal}'" not in expression:
                expression = (f"({expression}) OR "
                              f"(source_id <> %0 AND original_topic = '{internal}')")
        child(cft, "expression", expression)
        parameters = child(cft, "expression_parameters")
        for parameter in family["parameters"]:
            child(parameters, "element", parameter)
        port("output", dict(spec, name="wan_out_" + family["name"],
                            profile=family["writer_profile"]))
    # Keep WAN discovery, native histories and monitor/control alive independently
    # of each application's endpoint. All routes use the same native session.
    route.attrib["name"] = "wan_hub"
    def role_processor(target, role, topic=None):
        plugin = copy.deepcopy(processor)
        values = plugin.find("./property/value")
        for name, value in (("act.runtime_mode", "independent"), ("act.route_role", role),
                            *([("act.route_topic", topic)] if topic else [])):
            entry = child(values, "element")
            child(entry, "name", name)
            child(entry, "value", value)
        endpoints = target.findall("input") + target.findall("output")
        if endpoints:
            target.insert(list(target).index(endpoints[0]), plugin)
        else:
            target.append(plugin)

    route.remove(processor)
    role_processor(route, "hub")
    for kind, specs, role in (("input", model.get("inputs", []), "tx"),
                              ("output", model.get("outputs", []), "rx")):
        for spec in specs:
            if not data_plane_only and role == "tx" and spec["topic"] == "ActRouterDiagnostics":
                continue
            endpoint = route.find(f"./{kind}[@name='{spec['name']}']")
            route.remove(endpoint)
            lan_route = child(session, "topic_route", name=role + "_" + spec["name"])
            child(lan_route, "route_types", "false")
            role_processor(lan_route, role, spec["topic"])
            endpoint.find("creation_mode").text = "ON_DOMAIN_MATCH"
            endpoint.find("auto_configure_qos").text = "true"
            lan_route.append(endpoint)
    return ET.tostring(root, encoding="unicode")


def preflight(path, service_name="ACTGateway"):
    """Validate original XML entries before Routing Service collapses its property map."""
    root = path.getroot() if isinstance(path, ET.ElementTree) else ET.parse(path).getroot()
    service = root.find(f"./routing_service[@name='{service_name}']")
    if service is None:
        raise ValueError(f"missing routing_service {service_name}")
    processors = service.findall(".//topic_route/processor")
    # Validate the new topology first, then reuse v1's complete binding/type/QoS
    # validation against a flattened in-memory route.
    independent = any(entry.findtext("name") == "act.runtime_mode"
                      for processor in processors
                      for entry in processor.findall("./property/value/element"))
    if independent:
        return _preflight_independent(root, service, service_name)
    if len(processors) != 1:
        raise ValueError("v1 requires exactly one explicit gateway processor")
    processor = processors[0]
    properties = {}
    for entry in processor.findall("./property/value/element"):
        name, value = entry.findtext("name"), entry.findtext("value", "")
        if not name or name in properties:
            raise ValueError(f"empty or duplicate processor property: {name}")
        if "$(" in value or "${" in value:
            raise ValueError(f"unresolved processor variable: {name}")
        properties[name] = value
    globals_ = {"act.version", "act.types_uri", "act.node", "act.role", "act.router",
                "act.wan_contract", "act.monitoring", "act.teams", "act.admin_participant",
                "act.lan_participant", "act.wan_participant", "act.lan_domain", "act.wan_domain",
                "act.async_bandwidth_bps", "act.receive_policy"}
    required = globals_ - {"act.teams", "act.async_bandwidth_bps", "act.receive_policy"}
    for name in sorted(required):
        if not properties.get(name):
            raise ValueError(f"missing processor property: {name}")
    if properties["act.version"] != "1":
        raise ValueError("unsupported act.version (expected 1)")
    if properties["act.monitoring"] not in ("true", "false"):
        raise ValueError("act.monitoring must be true or false")
    if properties.get("act.receive_policy", "mission") not in ("mission", "static"):
        raise ValueError("act.receive_policy must be mission or static")
    if properties["act.wan_contract"] not in ("two_topic_union", "three_topic_union", "six_topic_union"):
        raise ValueError("unsupported act.wan_contract")
    participants = service.findall("./domain_route/participant")
    lan = [item for item in participants
           if item.attrib.get("name") == properties["act.lan_participant"]]
    if len(lan) != 1 or lan[0].findtext("domain_id") != properties["act.lan_domain"]:
        raise ValueError("LAN participant/domain mismatch")
    identity = lan_participant_identity(properties)
    if lan[0].findtext("domain_participant_qos/participant_name/name") != identity:
        raise ValueError("LAN participant identity mismatch")
    groups = {}
    fields = {"topic_name", "type", "channel", "branch", "instance_id",
              "tx.port", "rx.port", "tx.recipients", "rx.recipients",
              "tx.enabled", "rx.enabled"}
    for name, value in properties.items():
        if name in globals_:
            continue
        parts = name.split(".", 3)
        if (len(parts) != 4 or parts[:2] != ["act", "binding"]
                or not parts[2] or parts[3] not in fields):
            raise ValueError(f"unknown processor property: {name}")
        groups.setdefault(parts[2], {})[parts[3]] = value
    if not groups:
        raise ValueError("at least one explicit act.binding is required")
    route = next(route for route in service.findall(".//topic_route") if processor in list(route))
    ports = {}
    for kind in ("input", "output"):
        for port in route.findall(kind):
            name = port.attrib.get("name")
            if not name or name in ports:
                raise ValueError(f"empty or duplicate port: {name}")
            ports[name] = (kind, port)
    profiles = {library.attrib["name"] + "::" + profile.attrib["name"]: profile
                for library in root.findall("qos_library") for profile in library.findall("qos_profile")}

    def filter_separation(qos, seen=()):
        if qos is None:
            return {}
        separation = {}
        base = qos.attrib.get("base_name")
        if base:
            if base in seen:
                raise ValueError(f"cyclic QoS profile: {base}")
            profile = profiles.get(base)
            if profile is not None:
                separation.update(filter_separation(profile, seen + (base,)))
        if qos.tag == "qos_profile":
            separation.update(filter_separation(qos.find("datareader_qos"), seen))
        duration = qos.find("./time_based_filter/minimum_separation")
        if duration is not None:
            separation.update({item.tag: (item.text or "").strip() for item in duration})
        return separation

    topics, used = set(), set()
    for identifier, binding in groups.items():
        for field in ("topic_name", "type", "channel", "branch", "instance_id"):
            if not binding.get(field):
                raise ValueError(f"binding {identifier}: missing {field}")
        topic, channel = binding["topic_name"], binding["channel"]
        if topic in topics:
            raise ValueError(f"duplicate binding topic: {topic}")
        topics.add(topic)
        allowed_channels = {family + "_" + priority for family in ("event", "state", "periodic")
                            for priority in ("high", "normal")}
        if properties["act.wan_contract"] == "six_topic_union":
            allowed_channels.add("periodic_low")
        if channel not in allowed_channels:
            raise ValueError(f"unsupported channel: {channel}")
        directions = 0
        for direction, kind in (("tx", "input"), ("rx", "output")):
            name = binding.get(direction + ".port")
            if name is None:
                if direction + ".recipients" in binding or direction + ".enabled" in binding:
                    raise ValueError(f"binding {identifier}: direction settings without {direction}.port")
                continue
            directions += 1
            if not binding.get(direction + ".recipients"):
                raise ValueError(f"binding {identifier}: missing {direction}.recipients")
            if binding.get(direction + ".enabled", "true") not in ("true", "false"):
                raise ValueError(f"binding {identifier}: invalid {direction}.enabled")
            internal_diagnostics = (properties["act.monitoring"] == "true" and direction == "tx"
                                    and topic == "ActRouterDiagnostics" and name == "monitor_diagnostics")
            port_kind = "output" if internal_diagnostics else kind
            if name not in ports or ports[name][0] != port_kind or name in used:
                raise ValueError(f"binding {identifier}: missing/duplicate {kind} port {name}")
            used.add(name)
            port = ports[name][1]
            if port.attrib.get("participant") != properties["act.lan_participant"]:
                raise ValueError(f"binding {identifier}: port participant must be act.lan_participant")
            if (port.findtext("topic_name") != topic or
                    port.findtext("registered_type_name") != binding["type"]):
                raise ValueError(f"binding {identifier}: port topic/type mismatch")
            if kind == "input" and not channel.startswith("periodic_"):
                if any(value not in ("", "0") for value in
                       filter_separation(port.find("datareader_qos")).values()):
                    raise ValueError(f"Event/State LAN input filtering is unsupported: {topic}")
        if not directions:
            raise ValueError(f"binding {identifier}: missing tx.port or rx.port")
    if properties["act.monitoring"] == "true":
        for name in ("command", "ack", "status", "mesh", "links", "diagnostics"):
            if "monitor_" + name not in ports:
                raise ValueError(f"missing service-owned monitoring port: monitor_{name}")
    if properties["act.role"] == "platform" and properties.get("act.receive_policy", "mission") == "mission":
        bindings = {binding["topic_name"]: binding for binding in groups.values()}
        for topic in ("Mission", "Team", "PlatformMissionStatus", "ContactReport", "PlatformStatus"):
            binding = bindings.get(topic, {})
            if not binding.get("rx.port") or binding.get("rx.enabled", "true") != "true":
                raise ValueError(f"mission receive policy requires enabled static LAN output: {topic}")
        for topic in ("PlatformMissionStatus", "ContactReport"):
            binding = bindings[topic]
            if not binding.get("tx.port") or binding.get("tx.enabled", "true") != "true":
                raise ValueError(f"mission receive policy requires enabled local input: {topic}")
    return properties


def _preflight_independent(root, service, service_name):
    sessions = service.findall("./domain_route/session")
    if len(sessions) != 1:
        raise ValueError("independent gateway requires one native session")
    routes = sessions[0].findall("topic_route")
    common = None
    hub = None
    seen = set()
    flat = ET.Element("topic_route", name="gateway")
    role_keys = {"act.runtime_mode", "act.route_role", "act.route_topic"}
    for route in routes:
        processors = route.findall("processor")
        if len(processors) != 1:
            raise ValueError("independent route requires exactly one processor")
        processor = processors[0]
        props = {}
        for entry in processor.findall("./property/value/element"):
            key, value = entry.findtext("name"), entry.findtext("value", "")
            if not key or key in props:
                raise ValueError(f"empty or duplicate processor property: {key}")
            props[key] = value
        if props.get("act.runtime_mode") != "independent":
            raise ValueError("mixed or unsupported act.runtime_mode")
        role = props.get("act.route_role")
        topic = props.get("act.route_topic")
        if role not in ("hub", "tx", "rx") or (role == "hub") == bool(topic):
            raise ValueError("invalid independent route role/topic")
        identity = (role, topic)
        if identity in seen:
            raise ValueError("duplicate independent route role/topic")
        seen.add(identity)
        canonical = {key: value for key, value in props.items() if key not in role_keys}
        if common is not None and common != canonical:
            raise ValueError("independent routes must declare the identical gateway model")
        common = canonical
        if role == "hub":
            hub = route
            plugin = copy.deepcopy(processor)
            for entry in list(plugin.findall("./property/value/element")):
                if entry.findtext("name") in role_keys:
                    plugin.find("./property/value").remove(entry)
            flat.append(plugin)
        endpoints = route.findall("input") + route.findall("output")
        if role != "hub":
            if len(endpoints) != 1 or endpoints[0].tag != ("input" if role == "tx" else "output"):
                raise ValueError("LAN routes must have exactly one direction-specific port")
            endpoint = endpoints[0]
            if endpoint.findtext("topic_name") != topic:
                raise ValueError("LAN route topic does not match act.route_topic")
            if endpoint.findtext("creation_mode") != "ON_DOMAIN_MATCH":
                raise ValueError("independent LAN endpoints require ON_DOMAIN_MATCH")
            if endpoint.findtext("auto_configure_qos") not in ("true", "false"):
                raise ValueError("independent LAN auto_configure_qos must be explicit")
        else:
            for endpoint in endpoints:
                if (endpoint.findtext("creation_mode") != "IMMEDIATE" or
                        endpoint.findtext("auto_configure_qos") != "false"):
                    raise ValueError("hub ports require IMMEDIATE and explicit QoS")
                if endpoint.find("datareader_qos") is None and endpoint.find("datawriter_qos") is None:
                    raise ValueError("hub ports require explicit QoS")
        for endpoint in endpoints:
            flat.append(copy.deepcopy(endpoint))
    if hub is None:
        raise ValueError("missing always-on WAN hub")
    contract = common.get("act.wan_contract")
    families = (
        ("P1_Event", "P1_Periodic", "P2_Event", "P2_State", "P2_Periodic", "P3_Periodic")
        if contract == "six_topic_union" else
        ("Event", "State", "Periodic") if contract == "three_topic_union" else ("Event", "Status")
    )
    expected = {("input", "wan_in_" + family) for family in families}
    expected.update(("output", "wan_out_" + family) for family in families)
    if common.get("act.monitoring") == "true":
        expected.add(("input", "monitor_command"))
        expected.update(("output", "monitor_" + name)
                        for name in ("ack", "status", "mesh", "links", "diagnostics"))
    actual = [(endpoint.tag, endpoint.attrib.get("name")) for endpoint in
              hub.findall("input") + hub.findall("output")]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("hub must declare exactly one native pair per WAN family and its monitor ports")
    for endpoint in hub.findall("input") + hub.findall("output"):
        wan_port = endpoint.attrib["name"].startswith("wan_")
        if endpoint.attrib.get("participant") != common.get(
                "act.wan_participant" if wan_port else "act.lan_participant"):
            raise ValueError("hub port participant mismatch")
        qos = endpoint.find("datareader_qos" if endpoint.tag == "input" else "datawriter_qos")
        if qos is None or not qos.attrib.get("base_name"):
            raise ValueError("hub ports require an explicit XML QoS profile")
        if wan_port:
            family = endpoint.attrib["name"].split("_", 2)[2]
            if endpoint.findtext("topic_name") != family or endpoint.findtext("registered_type_name") != (
                    "WanFrame" if contract == "six_topic_union" else
                    "WanEvent" if family in ("Event", "State") else "WanStatus"):
                raise ValueError("hub WAN port topic/type mismatch")
    for route in routes:
        sessions[0].remove(route)
    sessions[0].append(flat)
    # Avoid a scratch file and a second parser by accepting an ElementTree internally.
    return preflight(ET.ElementTree(root), service_name)


def write_bundle(model, library, data_plane_only, path):
    """Keep the processor's external type registry beside the prepared service XML."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    types = path.with_name(path.stem + ".types.xml")
    source = Path(model["types"]).resolve()
    if source != types:
        shutil.copyfile(source, types)
    bundled = dict(model, types=str(types), properties=dict(model["properties"]))
    bundled["properties"]["act.types_uri"] = str(types)
    path.write_text(render(bundled, "", str(library), data_plane_only))
    return path


def launch_native(path, service_name):
    home = os.environ.get("NDDSHOME")
    if not home:
        raise ValueError("NDDSHOME must point to the Routing Service 7.7 installation")
    executables = list((Path(home) / "resource/app/bin").glob("*/rtiroutingserviceapp"))
    if len(executables) != 1:
        raise ValueError("expected exactly one native Routing Service architecture in NDDSHOME")
    executable = executables[0]
    environment = os.environ.copy()
    architecture = executable.parent.name
    library_paths = [str(Path(home) / "resource/app/lib" / architecture),
                     str(Path(home) / "lib" / architecture)]
    if environment.get("LD_LIBRARY_PATH"):
        library_paths.append(environment["LD_LIBRARY_PATH"])
    environment["LD_LIBRARY_PATH"] = os.pathsep.join(library_paths)
    process = subprocess.Popen(
        [str(executable), "-cfgFile", str(path), "-cfgName", service_name,
         "-verbosity", "WARN"], start_new_session=True, env=environment)
    return wait_for_service(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path, help="development-only YAML renderer input")
    source.add_argument("--xml", type=Path, help="prepared v1 XML; no YAML/helper dependency")
    parser.add_argument("--service-name", default="ACTGateway")
    parser.add_argument("--validate-only", action="store_true", help="preflight XML without launching DDS")
    parser.add_argument("--role", default="")
    parser.add_argument("--node-name", default="")
    parser.add_argument("--name", default="")
    parser.add_argument("--team", action="append", default=[],
                        help="initial exact team ID; repeat for multiple memberships")
    parser.add_argument("--build-dir", type=Path, default=Path("router/build/routing_service"))
    parser.add_argument("--render", type=Path,
                        help="persist XML and a sibling .types.xml registry without launching DDS")
    parser.add_argument("--data-plane-only", action="store_true",
                        help="omit ACT health/admin monitoring (act.monitoring=false)")
    args = parser.parse_args()
    try:
        if args.xml:
            if args.role or args.node_name or args.name or args.team or args.render:
                parser.error("--xml uses frozen XML identity/teams; development overrides are unsupported")
            path = args.xml.resolve()
            properties = preflight(path, args.service_name)
            if args.data_plane_only and properties["act.monitoring"] != "false":
                parser.error("--data-plane-only requires act.monitoring=false in prepared XML")
            if args.validate_only:
                return 0
            return launch_native(path, args.service_name)
        else:
            import yaml
            build = args.build_dir.resolve()
            library = build / "libact_gateway_processor.so"
            if not library.is_file():
                parser.error(f"processor library not built: {library}")
            description = subprocess.run(
                [str(build / "act_rs_config"), str(args.config.resolve()), args.role,
                 args.node_name, args.name, *args.team], check=True, text=True, capture_output=True)
            model = yaml.safe_load(description.stdout)
            for name, key in (("WAN_TIMEOUT_SEC", "timeout"), ("WAN_HB_PERIOD_SEC", "heartbeat"),
                              ("WAN_HB_RETRIES", "retries")):
                os.environ[name] = str(model[key])
        if args.render:
            path = write_bundle(model, library, args.data_plane_only, args.render)
            preflight(path, args.service_name)
            return 0
        with tempfile.TemporaryDirectory(prefix="act-routing-service-") as work:
            path = write_bundle(model, library, args.data_plane_only, Path(work) / "gateway.xml")
            preflight(path, args.service_name)
            if args.validate_only:
                return 0
            return launch_native(path, args.service_name)
    except subprocess.CalledProcessError as error:
        print(error.stderr or str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, ET.ParseError) as error:
        print(f"act Routing Service: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
