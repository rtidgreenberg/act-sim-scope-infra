"""Read-only, allowlisted configuration snapshot; never a configuration editor."""

import hashlib
import time

try:
    from delivery_config import load_router_config, native_port_settings, qos_settings
except ModuleNotFoundError as exc:
    if exc.name != "delivery_config":
        raise
    from gui.mesh_dashboard.server.delivery_config import load_router_config, native_port_settings, qos_settings


def duration(seconds):
    if seconds is None:
        return "Infinite (no expiry)"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds / 60:g} min ({seconds:g} s)"
    return f"{seconds:g} s"


def load_system_config(path, provider_factory, infinite_duration):
    if path is None:
        return {"available": False, "reason": "No active router configuration supplied to the dashboard.",
                "sections": []}
    path, config, libraries, provider = load_router_config(path, provider_factory)
    router = config["router"]
    native = config.get("native")
    contract = router.get("wan_contract", "legacy")
    if contract not in ("legacy", "two_topic_union", "three_topic_union", "six_topic_union"):
        raise ValueError(f"Unknown WAN contract: {contract}")
    shared = contract != "legacy"
    six_topic = contract == "six_topic_union"
    separate_contacts = contract in ("three_topic_union", "six_topic_union")
    health_family = ("P1_Periodic" if (six_topic and native) else
                     ("Periodic" if native else "Telemetry")) if separate_contacts else "Status"
    aliases = config.get("qos_profiles", {})
    sections = []

    def section(identifier, title, description, columns):
        table = {"id": identifier, "title": title, "description": description,
                 "columns": columns, "rows": []}
        sections.append(table)
        return table["rows"]

    overview = section("system", "System overview",
                       ("Settings from the prepared native Routing Service XML for this node. "
                        "QoS includes native port and participant XML overrides." if native else
                        "Settings from the router YAML supplied to this dashboard, covering both roles. "
                        "Node identity and admin participant may be overridden by launcher arguments."),
                       ["Setting", "Configured value", "Meaning"])
    overview.extend([
        ["WAN contract",
         "Six-topic union" if six_topic else "Three-topic union" if separate_contacts else
         "Two-topic union" if shared else "Legacy per-topic",
         "Shared P1 (CS6), P2 (AF41), and P3 (AF31) Event, State and Periodic topics." if six_topic else
         f"Shared Event, State and {health_family} topics." if separate_contacts else
         "Shared Event and Status topics." if shared else "Application topics have separate WAN endpoints."],
        ["Configuration set", router.get("config_set", "Not specified"), "Deployment configuration family."],
        ["Forwarding mode", router.get("default_forwarding_mode", "Not specified"),
         "Default payload forwarding; individual routes may override it."],
        ["Configuration file", str(path), "Loaded once at dashboard startup; not a live router query."],
        ["Configuration SHA-256", hashlib.sha256(path.read_bytes()).hexdigest(),
         f"Fingerprint of the supplied {'native XML' if native else 'YAML'} file, "
         "not verification of every deployed node."],
        ["QoS libraries", "\n".join(map(str, libraries)), "Inherited QoS is resolved by the installed Connext provider."],
    ])
    if native:
        overview.extend([
            ["Node identity", f"{config['node']['name']} ({config['node']['role']})",
             "Explicit act.node and act.role; this snapshot covers only the supplied node XML."],
            ["Admin participant", native["admin_participant"], "Service-owned monitoring uses this LAN."],
            ["Type registry URI", native["types_uri"], "act.types_uri; no YAML or registry file is read by this snapshot."],
            ["Asynchronous WAN bandwidth", f"{native['async_bandwidth_bps']} bit/s",
             "act.async_bandwidth_bps; shared configured budget, not measured throughput."],
            ["WAN flow controller",
             f"DDS_HPF_FLOW_CONTROLLER_SCHED_POLICY ({native['async_bandwidth_bps'] // 800} B / 10 ms)",
             "act_rs token bucket shared across WAN writers; orders queued samples by publication priority."],
            ["Platform receive policy",
             "Mission-state CFT selection" if native["receive_policy"] == "mission" else "Static CFT bindings",
             "act.receive_policy; applies to platforms only. Live applied profile is shown in node health, "
             "separately from always-on outgoing reports."],
        ])
    presence = section("presence", "Presence and reachability",
                       "Router reachability is separate from application position freshness and DDS discovery.",
                       ["Setting", "Configured value", "Meaning / source"])
    presence_config = router.get("presence_participant", {})
    if isinstance(presence_config, str):
        presence_names = [presence_config]
    elif isinstance(presence_config, dict):
        presence_names = list(dict.fromkeys(presence_config.values()))
    else:
        raise ValueError("presence_participant must be a participant name or role mapping")
    presence.extend([
        ["Presence participants", ", ".join(presence_names) or "Disabled",
         "router.presence_participant; applies only to the matching local role."],
        ["Heartbeat publication period", "Not reported",
         "Compiled router policy; not supplied by YAML or runtime telemetry."],
        ["Dead-peer roster retention", "Not reported",
         "Compiled router policy; distinct from the final system timeout."],
    ])
    profiles = []
    if native:
        profiles = [(family, port["resolved_profile"], endpoint)
                    for family, ports in native["wan"].items() for endpoint, port in ports.items()]
    elif shared:
        families = [("Event", "wan_event"), (health_family, "wan_status")]
        if separate_contacts:
            families.insert(1, ("State", "wan_contacts"))
        profiles = [(topic, aliases[alias], endpoint)
                    for topic, alias in families
                    if alias in aliases for endpoint in ("writer", "reader")]
    else:
        if presence_names:
            profiles.extend(("RouterHealth", "ACT_QOS_LIB::router_health", endpoint)
                            for endpoint in ("writer", "reader"))
        for route in config["routes"]:
            for side in ("source_side", "destination_side"):
                for direction, endpoint, key in (("input", "reader", "reader_qos"),
                                                 ("output", "writer", "writer_qos")):
                    leg = route.get(side, {}).get(direction)
                    if not leg or not config["participants"][leg["participant"]].get("on_wan", False):
                        continue
                    profile = aliases[leg[key]] if leg.get(key) else None
                    for topic in route["topics"]:
                        entry = (topic["name"], profile, endpoint)
                        if entry not in profiles:
                            profiles.append(entry)
    qos_rows = section("wan-qos", "WAN endpoint quality of service",
                       "Resolved configured profiles, not discovered remote endpoint settings. "
                       "Reader and writer policies are shown separately.",
                       ["Topic", "Endpoint", "Profile", "Reliability", "History", "Durability",
                        "Deadline", "Liveliness", "Lease", "Transport priority / DSCP"])
    for topic, profile, endpoint in profiles:
        display_profile = native["profile_labels"].get(profile, profile) if native else profile
        if profile is None:
            qos_rows.append([topic, endpoint.title(), "Automatic QoS",
                             *["Not resolved (wire-learned)"] * 7])
            continue
        qos = (provider.datawriter_qos_from_profile(profile) if endpoint == "writer"
               else provider.datareader_qos_from_profile(profile))
        settings = qos_settings(qos, infinite_duration)
        history = settings["history"].replace("_", " ").title()
        if settings["history_depth"] is not None:
            history += f" / {settings['history_depth']} per instance"
        if endpoint == "writer":
            tp = getattr(getattr(qos, "transport_priority", None), "value", 0)
            tp_display = f"{tp} (DSCP {tp >> 2})" if tp else "0 (DSCP 0)"
        else:
            tp_display = "Not applicable"
        qos_rows.append([
            topic, endpoint.title(), display_profile,
            settings["reliability"].replace("_", " ").title(), history,
            settings["durability"].replace("_", " ").title(),
            duration(settings["deadline_seconds"]),
            settings["liveliness"].replace("_", " ").title(), duration(settings["lease_seconds"]),
            tp_display,
        ])
        if topic in ("Event", "P1_Event", "P2_Event") and endpoint == "writer":
            protocol = qos.protocol.rtps_reliable_writer
            if not any(row[0] == "Reliable heartbeat period" for row in presence):
                presence.extend([
                    ["Reliable heartbeat period", duration(float(protocol.heartbeat_period)),
                     "WAN_HB_PERIOD_SEC; separate from application health publication."],
                    ["Reliable-reader heartbeat retries", str(protocol.max_heartbeat_retries),
                     "Derived as ceil(WAN_TIMEOUT_SEC / WAN_HB_PERIOD_SEC). "
                     "A nominal inactivity budget, not an exact delivery or wall-clock guarantee."],
                ])
        if endpoint != "reader" or topic != (health_family if shared else "RouterHealth") or not presence_names:
            continue
        presence.append(["ALIVE to STALE", duration(settings["deadline_seconds"]),
                         f"{display_profile}: reader deadline. Only RouterHealth deadline misses trigger STALE."])
        if shared:
            value = qos.property.get("act.presence.dead_timeout_sec")
            if not value or not value.isascii() or not value.isdigit() or not 0 < int(value) <= 2_147_483_647:
                raise ValueError("act.presence.dead_timeout_sec must be a positive integer <= 2147483647")
            presence.extend([
                [f"Last {health_family} to DEAD", duration(int(value)),
                 f"{display_profile}: act.presence.dead_timeout_sec from WAN_TIMEOUT_SEC. Measured from the "
                 f"last received {health_family}, not from STALE; repeated misses do not restart it."],
                ["Recovery to ALIVE", f"Any valid received {health_family}",
                 "Restores ALIVE and cancels the pending countdown; Event traffic does not."],
                ["Health writer loss / dispose", "STALE, then the same final system timeout",
                 "A missing RouterHealth writer/instance uses the same non-restarting timer."],
            ])
        else:
            presence.append(["Writer liveliness loss to DEAD", duration(settings["lease_seconds"]),
                             f"{profile}: reader liveliness lease; legacy presence behavior."])
        presence.append(["DDS endpoint liveliness lease", duration(settings["lease_seconds"]),
                         "Not the final system timeout. " + settings["liveliness"].replace("_", " ").title()])

    participants = section("participants", "DDS participants and discovery",
                           "Configured roles/domains; this is not a fleet-wide inventory of running processes. "
                           "Discovery leases remove DDS participants, not application presence entries.",
                           ["Participant", "Role", "Domain", "Network", "Profile", "Transport",
                            "Discovery lease", "Initial peers", "Receive multicast"])
    for name, participant in config["participants"].items():
        alias = participant.get("qos")
        profile = aliases.get(alias, alias)
        if profile:
            qos = provider.participant_qos_from_profile(profile)
            mask = qos.transport_builtin.mask
            transport = ", ".join(name for name in ("UDPv4", "UDPv6", "SHMEM")
                                  if mask & getattr(type(mask), name)) or "None"
            lease = qos.discovery_config.participant_liveliness_lease_duration
            lease = duration(None if lease == infinite_duration else float(lease))
            peers = ", ".join(qos.discovery.initial_peers) or "None"
            multicast = ", ".join(qos.discovery.multicast_receive_addresses) or "Disabled"
        else:
            transport = lease = peers = multicast = "Default profile (not resolved)"
        participants.append([name, participant.get("role", "Any"), participant["domain"],
                             "WAN" if participant.get("on_wan", False) else "LAN",
                             native["profile_labels"].get(profile, profile) if native else profile or "Default",
                             transport, lease, peers, multicast])

    if native:
        lan_rows = section("lan-qos", "LAN endpoint quality of service",
                           "Configured native XML profiles, including endpoint-specific overrides. "
                           "Native auto-QoS can change effective policies after discovery; "
                           "these are not live inferred QoS values. "
                           "Minimum separation is reader input filtering, not WAN history or a publication rate.",
                           ["Topic", "Port", "Endpoint", "Type", "Profile", "Reliability",
                            "History", "Minimum separation"])
        for port in native["ports"].values():
            if config["participants"][port["participant"]]["on_wan"]:
                continue
            settings = native_port_settings(port, provider, infinite_duration)
            history = settings["history"].replace("_", " ").title()
            if settings["history_depth"] is not None:
                history += f" / {settings['history_depth']} per instance"
            separation = settings.get("minimum_separation_seconds")
            lan_rows.append([port["topic"], port["name"], port["endpoint"].title(), port["type"],
                             port["profile"] + (" (native auto-QoS)" if port["auto_configure_qos"] else ""),
                             settings["reliability"].replace("_", " ").title(),
                             history, ("Off" if separation == 0 else duration(separation))
                             if port["endpoint"] == "reader" else "Not applicable"])
        priorities = section("priorities", "WAN scheduling channels",
                             f"{'Six' if separate_contacts else 'Four'} logical priority classes share "
                             "the configured WAN family endpoints. "
                             "These are configured priorities, not delivery or overload guarantees.",
                             ["Binding direction", "Original topic", "WAN family", "Channel",
                              "Publication priority", "Recipients"])
        routes = section("routes", "Configured native bindings",
                         "Read-only startup snapshot; one explicit LAN/WAN direction per row. "
                         "This is not a route-management interface or a live state query.",
                         ["Binding direction", "From participant", "To participant", "Topic", "Enabled"])
        wan = next(name for name, participant in config["participants"].items() if participant["on_wan"])
        for binding in native["bindings"]:
            priorities.append([binding["name"], binding["topic"], binding["family"], binding["channel"],
                               str(binding["priority"]), binding["recipients"]])
            lan = wan if binding.get("internal") else native["ports"][binding["port"]]["participant"]
            routes.append([binding["name"], lan if binding["direction"] == "tx" else wan,
                           wan if binding["direction"] == "tx" else lan, binding["topic"],
                           "Yes" if binding["enabled"] else "No"])
        if native["monitoring"]:
            monitoring_specs = (
                (("RouterConfig", "P1_Event", "event_high", 600),
                 ("RouterHealth", health_family, "periodic_high", 600))
                if six_topic else
                (("RouterConfig", "Event", "event_normal", 300 if separate_contacts else 200),
                 ("RouterHealth", health_family, "periodic_high", 500 if separate_contacts else 300))
            )
            for topic, family, channel, priority in monitoring_specs:
                if any(binding["topic"] == topic for binding in native["bindings"]):
                    continue
                priorities.append(["Internal monitoring", topic, family, channel, str(priority),
                                   "Service-owned monitoring"])
        return {"available": True, "format": "routing_service_xml",
                "loaded_at_ms": round(time.time() * 1000), "sections": sections}

    routes = section("routes", "Configured routes",
                     "Startup configuration only. Operator changes to enabled state, teams or filters "
                     "are not reflected here; use the live Graph and Mission views for current state.",
                     ["Route", "From role", "Source side enabled", "To role",
                      "Destination side enabled", "Topics", "Forwarding"])
    for route in config["routes"]:
        enabled = route.get("enabled", False)
        routes.append([route["name"], route.get("source", "Not specified"),
                       "Yes" if route.get("source_side", {}).get("enabled", enabled) else "No",
                       route.get("destination", "Not specified"),
                       "Yes" if route.get("destination_side", {}).get("enabled", enabled) else "No",
                       ", ".join(topic["name"] for topic in route["topics"]),
                       route.get("forwarding_mode", router.get("default_forwarding_mode", "Not specified"))])
    return {"available": True, "loaded_at_ms": round(time.time() * 1000), "sections": sections}
