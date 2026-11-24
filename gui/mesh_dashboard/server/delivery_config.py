"""Configured WAN QoS for display only, independent of wire-flow classification."""

import copy
import os
import re
import time
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

INTERNAL_WAN_PROFILES = {
    "RouterHealth": "ACT_QOS_LIB::router_health",
}
WAN_EVENT_TOPICS = {
    "Mission", "Team", "ActPlatformStatusMode",
    "PlatformCommandAck", "ContactReport", "RouterConfig",
}


def qos_settings(qos, infinite_duration):
    def seconds(duration):
        return None if duration == infinite_duration else float(duration)

    def kind(value):
        return str(value).rsplit(".", 1)[-1]

    history = kind(qos.history.kind)
    return {
        "reliability": kind(qos.reliability.kind),
        "history": history,
        "history_depth": qos.history.depth if history == "KEEP_LAST" else None,
        "durability": kind(qos.durability.kind),
        "deadline_seconds": seconds(qos.deadline.period),
        "liveliness": kind(qos.liveliness.kind),
        "lease_seconds": seconds(qos.liveliness.lease_duration),
    }


def load_router_config(path, provider_factory):
    path = Path(path).resolve(strict=True)
    text = path.read_text(encoding="utf-8-sig")
    if text.lstrip().startswith("<"):
        return _load_native_xml(path, text, provider_factory)
    import yaml
    config = yaml.safe_load(text)
    if not isinstance(config, dict):
        raise ValueError(f"{path}: router configuration must be a mapping")
    root = Path(__file__).resolve().parents[3]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from harness_v2.qos.wan_policy import configure_wan_policy

    configure_wan_policy()
    libraries = [(root / library).resolve(strict=True) for library in config["qos_libraries"]]
    if not libraries:
        raise ValueError(f"{path}: no QoS libraries configured")
    provider = provider_factory(";".join(map(str, libraries)))
    return path, config, libraries, provider


def _load_native_xml(path, text, provider_factory):
    root = ET.fromstring(text)
    variables = {}
    for entry in root.findall("./configuration_variables/value/element"):
        name = entry.findtext("name")
        if not name or name in variables:
            raise ValueError(f"{path}: empty or duplicate XML configuration variable: {name}")
        variables[name] = entry.findtext("value", "")
    pattern = re.compile(r"\$\(([^)]+)\)")

    def resolve(value, stack=()):
        def substitute(match):
            name = match[1]
            if name in stack:
                raise ValueError(f"{path}: cyclic XML configuration variable: {name}")
            value = os.environ.get(name, variables.get(name))
            if value is None:
                raise ValueError(f"{path}: unresolved XML configuration variable: {name}")
            return resolve(value, stack + (name,))
        return pattern.sub(substitute, value)

    for element in root.iter():
        if element.text:
            element.text = resolve(element.text)
        element.attrib.update({key: resolve(value) for key, value in element.attrib.items()})
    routes = root.findall("./routing_service/domain_route/session/topic_route")

    def processor_properties(processor):
        properties = {}
        for entry in processor.findall("./property/value/element"):
            name = entry.findtext("name")
            if not name or name in properties:
                raise ValueError(f"{path}: empty or duplicate processor property: {name}")
            properties[name] = entry.findtext("value", "")
        return properties

    processors = [(route, processor, processor_properties(processor))
                  for route in routes for processor in route.findall("processor")]
    independent = any(properties.get("act.runtime_mode") == "independent"
                      for _, _, properties in processors)
    if independent:
        hubs = [entry for entry in processors if entry[2].get("act.route_role") == "hub"]
        if len(hubs) != 1:
            raise ValueError(f"{path}: expected one native gateway hub processor")
        route, processor, hub_properties = hubs[0]
        role_keys = {"act.route_role", "act.route_topic"}
        properties = {key: value for key, value in hub_properties.items() if key not in role_keys}
        registrations = set()
        for _, _, current in processors:
            if ({key: value for key, value in current.items() if key not in role_keys}
                    != properties):
                raise ValueError(f"{path}: inconsistent native gateway processor properties")
            role, topic = current.get("act.route_role"), current.get("act.route_topic")
            if role == "hub":
                if topic is not None:
                    raise ValueError(f"{path}: hub must not declare act.route_topic")
            elif role not in ("tx", "rx") or not topic or (role, topic) in registrations:
                raise ValueError(f"{path}: invalid or duplicate native binding registration")
            else:
                registrations.add((role, topic))
    else:
        if (len(processors) != 1 or
                any(key in properties for _, _, properties in processors
                    for key in ("act.runtime_mode", "act.route_role", "act.route_topic"))):
            raise ValueError(f"{path}: expected one native gateway processor")
        route, processor, properties = processors[0]

    def required(name):
        if not properties.get(name):
            raise ValueError(f"{path}: missing processor property: {name}")
        return properties[name]

    if required("act.version") != "1":
        raise ValueError(f"{path}: unsupported act.version")
    contract = required("act.wan_contract")
    if contract not in ("two_topic_union", "three_topic_union", "six_topic_union"):
        raise ValueError(f"Unknown WAN contract: {contract}")
    monitoring = required("act.monitoring")
    if monitoring not in ("true", "false"):
        raise ValueError(f"{path}: act.monitoring must be true or false")
    wan = required("act.wan_participant")
    lan = required("act.lan_participant")
    if required("act.admin_participant") != lan:
        raise ValueError(f"{path}: monitoring must use the declared LAN participant")
    node, role = required("act.node"), required("act.role")
    receive_policy = properties.get("act.receive_policy", "mission")
    if receive_policy not in ("mission", "static"):
        raise ValueError(f"{path}: act.receive_policy must be mission or static")
    config = {
        "format": "routing_service_xml",
        "router": {"wan_contract": contract, "default_forwarding_mode": "dynamic_data",
                   "presence_participant": wan if monitoring == "true" else ""},
        "node": {"name": node, "role": role},
        "qos_profiles": {}, "participants": {}, "routes": [],
    }
    native = {"bindings": [], "ports": {}, "wan": {}, "profile_labels": {},
              "monitoring": monitoring == "true", "types_uri": required("act.types_uri"),
              "admin_participant": lan, "receive_policy": receive_policy,
              "runtime_mode": "independent" if independent else "combined"}
    config["native"] = native
    qos_root = ET.Element("dds")
    for library in root.findall("qos_library"):
        qos_root.append(copy.deepcopy(library))
    if not len(qos_root):
        raise ValueError(f"{path}: native configuration has no embedded QoS libraries")
    names = {library.attrib["name"] for library in qos_root}
    library_name = "ACT_DashboardSnapshot"
    while library_name in names:
        library_name += "_"
    extracted = ET.SubElement(qos_root, "qos_library", name=library_name)

    def profile(element, name, tag, allow_defaults=False):
        qos = element.find(tag)
        if qos is None:
            if not allow_defaults:
                raise ValueError(f"{path}: missing explicit {tag} for {name}")
            qos = ET.Element(tag)
        resolved = f"{library_name}::{name}"
        wrapper = ET.SubElement(extracted, "qos_profile", name=name)
        wrapper.append(copy.deepcopy(qos))
        declared = qos.attrib.get("base_name", "Inline XML QoS")
        native["profile_labels"][resolved] = declared
        return declared, resolved

    domain = next(domain for domain in root.findall("./routing_service/domain_route")
                  if processor in domain.findall("./session/topic_route/processor"))
    if independent:
        sessions = domain.findall("session")
        if (len(sessions) != 1 or
                set(sessions[0].findall("topic_route")) != {entry[0] for entry in processors}):
            raise ValueError(f"{path}: independent bindings require one gateway session")
    for index, participant in enumerate(domain.findall("participant")):
        name = participant.attrib["name"]
        if name in config["participants"]:
            raise ValueError(f"{path}: duplicate participant: {name}")
        declared, resolved = profile(participant, f"participant_{index}", "domain_participant_qos")
        config["participants"][name] = {
            "role": role, "domain": int(participant.findtext("domain_id")),
            "on_wan": name == wan, "qos": resolved, "declared_profile": declared}
    for name in (lan, wan):
        if name not in config["participants"]:
            raise ValueError(f"{path}: missing declared participant: {name}")
    ports = [(port, properties) for route, _, properties in processors
             for port in route if port.tag in ("input", "output")]
    for kind, endpoint, qos_tag in (("input", "reader", "datareader_qos"),
                                     ("output", "writer", "datawriter_qos")):
        for index, (port, owner_properties) in enumerate(
                (port, properties) for port, properties in ports if port.tag == kind):
            name = port.attrib["name"]
            if name in native["ports"]:
                raise ValueError(f"{path}: duplicate native port: {name}")
            owner_role = owner_properties.get("act.route_role")
            declared, resolved = profile(
                port, f"{kind}_{index}", qos_tag,
                allow_defaults=independent and owner_role in ("tx", "rx"))
            participant = port.attrib["participant"]
            if participant not in config["participants"]:
                raise ValueError(f"{path}: port {name} has unknown participant {participant}")
            auto_qos = port.findtext("auto_configure_qos", "false").strip()
            if auto_qos not in ("true", "false", "1", "0"):
                raise ValueError(f"{path}: port {name} has invalid auto_configure_qos")
            spec = {"name": name, "topic": port.findtext("topic_name"),
                    "type": port.findtext("registered_type_name"), "participant": participant,
                    "endpoint": endpoint, "profile": declared, "resolved_profile": resolved,
                    "auto_configure_qos": auto_qos in ("true", "1"),
                    "creation_mode": port.findtext("creation_mode", "IMMEDIATE"),
                    "route_role": owner_role, "route_topic": owner_properties.get("act.route_topic")}
            native["ports"][name] = spec
            if participant == wan:
                if independent and owner_role != "hub":
                    raise ValueError(f"{path}: WAN port {name} must belong to the hub")
                family = spec["topic"]
                if endpoint in native["wan"].setdefault(family, {}):
                    raise ValueError(f"{path}: duplicate {family} WAN {endpoint}")
                native["wan"][family][endpoint] = spec
            elif independent and owner_role == "hub" and not name.startswith("monitor_"):
                raise ValueError(f"{path}: application LAN port {name} must have an independent route")
    expected = (
        {"P1_Event", "P1_Periodic", "P2_Event", "P2_State", "P2_Periodic", "P3_Periodic"}
        if contract == "six_topic_union" else
        {"Event", "State", "Periodic"} if contract == "three_topic_union" else {"Event", "Status"}
    )
    if set(native["wan"]) != expected or any(set(ports) != {"reader", "writer"}
                                             for ports in native["wan"].values()):
        raise ValueError(f"{path}: native WAN endpoints must be exactly {sorted(expected)} reader/writer pairs")
    groups = {}
    for key, value in properties.items():
        if key.startswith("act.binding."):
            parts = key.split(".", 3)
            if len(parts) != 4 or not parts[2]:
                raise ValueError(f"{path}: malformed binding property: {key}")
            groups.setdefault(parts[2], {})[parts[3]] = value
    priorities = {"event_high": 600, "periodic_high": 500, "state_high": 400,
                  "event_normal": 300, "state_normal": 200, "periodic_normal": 100}
    if contract == "six_topic_union":
        priorities = {
            "event_high": 400, "event_normal": 300,
            "state_high": 350, "state_normal": 250,
            "periodic_high": 300, "periodic_normal": 200, "periodic_low": 100,
        }
    elif contract == "two_topic_union":
        priorities.update(event_high=400, event_normal=200, periodic_high=300)
    used_ports = set()
    for identifier, binding in groups.items():
        for field in ("topic_name", "channel", "type", "branch", "instance_id"):
            if not binding.get(field):
                raise ValueError(f"{path}: binding {identifier} is missing {field}")
        channel = binding["channel"]
        if channel not in priorities or (contract == "two_topic_union" and channel.startswith("state_")):
            raise ValueError(f"{path}: unsupported channel: {channel}")
        if contract == "six_topic_union":
            family = {
                "event": "P2_Event",
                "state": "P2_State",
                "periodic": "P3_Periodic" if channel == "periodic_low" else "P2_Periodic",
            }[channel.split("_")[0]]
        else:
            family = {"event": "Event", "state": "State", "periodic":
                      "Periodic" if contract == "three_topic_union" else "Status"}[channel.split("_")[0]]
        for direction, endpoint in (("tx", "reader"), ("rx", "writer")):
            port_name = binding.get(direction + ".port")
            if port_name is None:
                continue
            internal_diagnostics = (
                direction == "tx" and binding["topic_name"] == "ActRouterDiagnostics" and
                port_name == "monitor_diagnostics" and native["monitoring"]
            )
            if internal_diagnostics:
                used_ports.add(port_name)
                enabled = binding.get(direction + ".enabled", "true")
                if enabled not in ("true", "false") or not binding.get(direction + ".recipients"):
                    raise ValueError(f"{path}: binding {identifier}: invalid {direction} metadata")
                name = f"{identifier}.{direction}"
                native["bindings"].append({
                    "name": name, "topic": binding["topic_name"], "type": binding["type"],
                    "branch": binding["branch"], "instance_id": binding["instance_id"],
                    "channel": channel, "family": family, "priority": priorities[channel],
                    "direction": direction, "port": port_name, "enabled": enabled == "true",
                    "recipients": binding[direction + ".recipients"], "internal": True})
                config["routes"].append({"name": name, "enabled": enabled == "true",
                                         "topics": [{"name": binding["topic_name"]}],
                                         "forwarding_mode": "dynamic_data"})
                continue
            port = native["ports"].get(port_name)
            if (not port or port_name in used_ports or port["endpoint"] != endpoint or
                    port["participant"] != lan or port["topic"] != binding["topic_name"] or
                    port["type"] != binding["type"]):
                raise ValueError(f"{path}: binding {identifier}: invalid {direction} port {port_name}")
            if independent and (port["route_role"] != direction or
                                port["route_topic"] != binding["topic_name"]):
                raise ValueError(f"{path}: binding {identifier}: inconsistent native route registration")
            used_ports.add(port_name)
            enabled = binding.get(direction + ".enabled", "true")
            if enabled not in ("true", "false") or not binding.get(direction + ".recipients"):
                raise ValueError(f"{path}: binding {identifier}: invalid {direction} metadata")
            name = f"{identifier}.{direction}"
            native["bindings"].append({
                "name": name, "topic": binding["topic_name"], "type": binding["type"],
                "branch": binding["branch"], "instance_id": binding["instance_id"],
                "channel": channel, "family": family, "priority": priorities[channel],
                "direction": direction, "port": port_name, "enabled": enabled == "true",
                "recipients": binding[direction + ".recipients"]})
            config["routes"].append({"name": name, "enabled": enabled == "true",
                                     "topics": [{"name": binding["topic_name"]}],
                                     "forwarding_mode": "dynamic_data"})
    if not native["bindings"]:
        raise ValueError(f"{path}: no explicit native bindings")
    if independent:
        expected = {(binding["direction"], binding["topic"])
                    for binding in native["bindings"] if not binding.get("internal")}
        if registrations != expected:
            raise ValueError(f"{path}: native route registrations must match declared bindings")
        for route, _, current in processors:
            if current.get("act.route_role") != "hub" and len([
                    port for port in route if port.tag in ("input", "output")]) != 1:
                raise ValueError(f"{path}: native LAN binding routes require exactly one port")
    bandwidth = properties.get("act.async_bandwidth_bps", "2000000")
    if not bandwidth.isascii() or not bandwidth.isdigit() or int(bandwidth) < 2000000 or int(bandwidth) % 800:
        raise ValueError(f"{path}: invalid act.async_bandwidth_bps")
    native["async_bandwidth_bps"] = int(bandwidth)
    document = ET.tostring(qos_root, encoding="unicode")
    try:
        from rti.connextdds import QosProviderParams
    except ModuleNotFoundError as error:
        if error.name not in ("rti", "rti.connextdds"):
            raise
        # Allows injected, entity-free providers in tests without an installed SDK.
        source = document
    else:
        # URI lists split at semicolons, including those in escaped XML entities.
        source = QosProviderParams()
        source.string_profile = [document]
        source.ignore_user_profile = True
        source.ignore_environment_profile = True
    return path, config, [path], provider_factory(source)


def native_port_settings(port, provider, infinite_duration):
    getter = (provider.datareader_qos_from_profile if port["endpoint"] == "reader"
              else provider.datawriter_qos_from_profile)
    qos = getter(port["resolved_profile"])
    result = qos_settings(qos, infinite_duration)
    if port["endpoint"] == "reader":
        separation = qos.time_based_filter.minimum_separation
        result["minimum_separation_seconds"] = (
            None if separation == infinite_duration else float(separation))
    return result


def _native_delivery_config(path, config, libraries, provider, infinite_duration):
    native = config["native"]
    topics = {}

    def entry(port, family, routes, enabled=True):
        participant = config["participants"][port["participant"]]
        return {"participant": port["participant"], "role": participant["role"],
                "domain_id": participant["domain"], "endpoint": port["endpoint"],
                "profile": port["profile"], "settings": native_port_settings(port, provider, infinite_duration),
                "enabled": enabled, "routes": routes, "port": port["name"], "family": family}

    for family, ports in native["wan"].items():
        topics[family] = [entry(port, family, ["Shared WAN family"]) for port in ports.values()]
    for binding in native["bindings"]:
        family = binding["family"]
        endpoint = "writer" if binding["direction"] == "tx" else "reader"
        value = entry(native["wan"][family][endpoint], family, [binding["name"]], binding["enabled"])
        if binding.get("internal"):
            continue
        lan = native["ports"][binding["port"]]
        value.update(channel=binding["channel"], publication_priority=binding["priority"],
                     recipients=binding["recipients"],
                     lan={"port": lan["name"], "endpoint": lan["endpoint"],
                          "profile": lan["profile"], "type": lan["type"],
                          "auto_configure_qos": lan["auto_configure_qos"],
                          "creation_mode": lan["creation_mode"],
                          "settings": native_port_settings(lan, provider, infinite_duration)})
        topics.setdefault(binding["topic"], []).append(value)
    if native["monitoring"]:
        six_topic = config["router"]["wan_contract"] == "six_topic_union"
        monitoring_specs = (
            (("RouterConfig", "P1_Event", "event_high", 600),
             ("RouterHealth", "P1_Periodic", "periodic_high", 600))
            if six_topic else
            (("RouterConfig", "Event", "event_normal", 300 if "Periodic" in native["wan"] else 200),
             ("RouterHealth", "Periodic" if "Periodic" in native["wan"] else "Status",
              "periodic_high", 500 if "Periodic" in native["wan"] else 300))
        )
        for topic, family, channel, priority in monitoring_specs:
            if topic in topics:
                continue
            topics[topic] = [dict(entry(port, family, ["Internal service-owned monitoring"]),
                                  channel=channel, publication_priority=priority)
                             for port in native["wan"][family].values()]
    return {"available": True, "config_file": str(path), "format": "routing_service_xml",
            "qos_libraries": list(map(str, libraries)), "loaded_at_ms": round(time.time() * 1000),
            "async_bandwidth_bps": native["async_bandwidth_bps"], "topics": topics}


def load_delivery_config(path, provider_factory, infinite_duration):
    if path is None:
        return {"available": False, "reason": "No active router configuration supplied to the dashboard.",
                "topics": {}}
    path, config, libraries, provider = load_router_config(path, provider_factory)
    if config.get("format") == "routing_service_xml":
        return _native_delivery_config(path, config, libraries, provider, infinite_duration)
    aliases = config.get("qos_profiles", {})
    participants = config["participants"]
    topics = {}
    contract = config["router"].get("wan_contract", "legacy")
    if contract not in ("legacy", "two_topic_union", "three_topic_union", "six_topic_union"):
        raise ValueError(f"Unknown WAN contract: {contract}")

    def add(topic, route, participant, endpoint, profile, enabled=True):
        participant_config = participants[participant]
        if not participant_config.get("on_wan", False):
            return
        if contract != "legacy":
            alias = "wan_contacts" if contract == "three_topic_union" and topic == "ContactReport" else (
                "wan_event" if topic in WAN_EVENT_TOPICS else "wan_periodic")
            profile = aliases[alias]
        settings = None
        if profile is not None:
            qos = (provider.datawriter_qos_from_profile(profile) if endpoint == "writer"
                   else provider.datareader_qos_from_profile(profile))
            settings = qos_settings(qos, infinite_duration)
        entry = {
            "participant": participant, "role": participant_config["role"],
            "domain_id": participant_config["domain"], "endpoint": endpoint,
            "profile": profile, "settings": settings, "enabled": enabled,
        }
        entries = topics.setdefault(topic, [])
        previous = next((value for value in entries
                         if all(value[key] == value_ for key, value_ in entry.items())), None)
        if previous is None:
            entries.append(dict(entry, routes=[route]))
        elif route not in previous["routes"]:
            previous["routes"].append(route)

    for route in config["routes"]:
        for side in ("source_side", "destination_side"):
            side_config = route.get(side, {})
            enabled = side_config.get("enabled", route.get("enabled", False))
            for direction, endpoint, alias_key in (
                    ("input", "reader", "reader_qos"), ("output", "writer", "writer_qos")):
                leg = side_config.get(direction)
                if not leg:
                    continue
                alias = leg.get(alias_key)
                profile = aliases[alias] if alias else None
                for topic in route["topics"]:
                    add(topic["name"], route["name"], leg["participant"], endpoint,
                        profile, enabled)

    presence = config["router"].get("presence_participant", {})
    if isinstance(presence, str):
        presence = [presence]
    elif isinstance(presence, dict):
        presence = presence.values()
    else:
        raise ValueError(f"{path}: presence_participant must be a participant name or role mapping")
    for participant in presence:
        for topic, profile in INTERNAL_WAN_PROFILES.items():
            for endpoint in ("writer", "reader"):
                add(topic, "Internal WAN presence/link monitoring", participant, endpoint, profile)

    return {"available": True, "config_file": str(path), "qos_libraries": list(map(str, libraries)),
            "loaded_at_ms": round(time.time() * 1000), "topics": topics}
