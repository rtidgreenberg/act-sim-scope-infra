"""Configured WAN QoS for display only, independent of wire-flow classification."""

import time
from pathlib import Path

import yaml


INTERNAL_WAN_PROFILES = {
    "RouterHealth": "ACT_QOS_LIB::router_health",
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


def load_delivery_config(path, provider_factory, infinite_duration):
    if path is None:
        return {"available": False, "reason": "No active router configuration supplied to the dashboard.",
                "topics": {}}
    path = Path(path).resolve(strict=True)
    with path.open() as source:
        config = yaml.safe_load(source)
    if not isinstance(config, dict):
        raise ValueError(f"{path}: router configuration must be a mapping")
    root = Path(__file__).resolve().parents[3]
    libraries = [(root / library).resolve(strict=True) for library in config["qos_libraries"]]
    if not libraries:
        raise ValueError(f"{path}: no QoS libraries configured")
    provider = provider_factory(";".join(map(str, libraries)))
    aliases = config.get("qos_profiles", {})
    participants = config["participants"]
    topics = {}

    def add(topic, route, participant, endpoint, profile, enabled=True):
        participant_config = participants[participant]
        if not participant_config.get("on_wan", False):
            return
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
