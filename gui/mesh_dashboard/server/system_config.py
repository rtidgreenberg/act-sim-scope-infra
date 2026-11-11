"""Read-only, allowlisted configuration snapshot; never a configuration editor."""

import hashlib
import time

try:
    from delivery_config import load_router_config, qos_settings
except ModuleNotFoundError as exc:
    if exc.name != "delivery_config":
        raise
    from gui.mesh_dashboard.server.delivery_config import load_router_config, qos_settings


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
    contract = router.get("wan_contract", "legacy")
    if contract not in ("legacy", "two_topic_union"):
        raise ValueError(f"Unknown WAN contract: {contract}")
    shared = contract == "two_topic_union"
    aliases = config.get("qos_profiles", {})
    sections = []

    def section(identifier, title, description, columns):
        table = {"id": identifier, "title": title, "description": description,
                 "columns": columns, "rows": []}
        sections.append(table)
        return table["rows"]

    overview = section("system", "System overview",
                       "Settings from the router YAML supplied to this dashboard, covering both roles. "
                       "Node identity and admin participant may be overridden by launcher arguments.",
                       ["Setting", "Configured value", "Meaning"])
    overview.extend([
        ["WAN contract", "Two-topic union" if shared else "Legacy per-topic",
         "Shared Event and Status topics." if shared else "Application topics have separate WAN endpoints."],
        ["Configuration set", router.get("config_set", "Not specified"), "Deployment configuration family."],
        ["Forwarding mode", router.get("default_forwarding_mode", "Not specified"),
         "Default payload forwarding; individual routes may override it."],
        ["Configuration file", str(path), "Loaded once at dashboard startup; not a live router query."],
        ["Configuration SHA-256", hashlib.sha256(path.read_bytes()).hexdigest(),
         "Fingerprint of the supplied YAML file, not verification of every deployed node."],
        ["QoS libraries", "\n".join(map(str, libraries)), "Inherited QoS is resolved by the installed Connext provider."],
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
         "Compiled router policy; distinct from the STALE-to-DEAD timeout."],
    ])
    profiles = []
    if shared:
        profiles = [(topic, aliases[alias], endpoint)
                    for topic, alias in (("Event", "wan_event"), ("Status", "wan_status"))
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
                        "Deadline", "Liveliness", "Lease"])
    for topic, profile, endpoint in profiles:
        if profile is None:
            qos_rows.append([topic, endpoint.title(), "Automatic QoS",
                             *["Not resolved (wire-learned)"] * 6])
            continue
        qos = (provider.datawriter_qos_from_profile(profile) if endpoint == "writer"
               else provider.datareader_qos_from_profile(profile))
        settings = qos_settings(qos, infinite_duration)
        history = settings["history"].replace("_", " ").title()
        if settings["history_depth"] is not None:
            history += f" / {settings['history_depth']} per instance"
        qos_rows.append([
            topic, endpoint.title(), profile,
            settings["reliability"].replace("_", " ").title(), history,
            settings["durability"].replace("_", " ").title(),
            duration(settings["deadline_seconds"]),
            settings["liveliness"].replace("_", " ").title(), duration(settings["lease_seconds"]),
        ])
        if endpoint != "reader" or topic != ("Status" if shared else "RouterHealth") or not presence_names:
            continue
        presence.append(["ALIVE to STALE", duration(settings["deadline_seconds"]),
                         f"{profile}: reader deadline. Only RouterHealth deadline misses trigger STALE."])
        if shared:
            value = qos.property.get("act.presence.dead_after_stale_ms")
            if not value or not value.isascii() or not value.isdigit() or int(value) <= 0:
                raise ValueError("act.presence.dead_after_stale_ms must be a positive integer")
            presence.extend([
                ["STALE to DEAD", duration(int(value) / 1000),
                 f"{profile}: act.presence.dead_after_stale_ms. Starts at the first missing-health "
                 "event; repeated misses do not restart it. This is not time since the last sample."],
                ["Recovery to ALIVE", "Any valid received Status",
                 "Restores ALIVE and cancels the pending countdown; Event traffic does not."],
                ["Health writer loss / dispose", "STALE, then the same DEAD countdown",
                 "A missing RouterHealth writer/instance uses the same non-restarting timer."],
            ])
        else:
            presence.append(["Writer liveliness loss to DEAD", duration(settings["lease_seconds"]),
                             f"{profile}: reader liveliness lease; legacy presence behavior."])
        presence.append(["DDS endpoint liveliness lease", duration(settings["lease_seconds"]),
                         "Not the STALE-to-DEAD timer. " + settings["liveliness"].replace("_", " ").title()])

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
                             "WAN" if participant.get("on_wan", False) else "LAN", profile or "Default",
                             transport, lease, peers, multicast])

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
