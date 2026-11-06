"""Layer-specific, interval-normalized traffic aggregates."""

import math
import time


TRAFFIC_PREFIXES = (
    "discovery", "data", "reliability", "mixed", "unknown",
    "application_tx", "infrastructure_tx", "discovery_tx", "reliability_tx",
    "mixed_tx", "unknown_tx", "data_tx", "tx", "rx", "total", "incomplete_fragment",
)
MAC_PREFIXES = tuple(f"mac_{direction}_{kind}" for direction in ("tx", "rx")
                     for kind in ("unicast", "broadcast"))


def counter_fields(mac=False):
    return tuple(f"{prefix}_{unit}" for prefix in (MAC_PREFIXES if mac else TRAFFIC_PREFIXES)
                 for unit in (("bytes", "packets", "drops") if mac else ("bytes", "packets")))


def validate_sample(sample, mac=False):
    if not isinstance(sample, dict) or not isinstance(sample.get("observer"), str) or not sample["observer"]:
        raise ValueError("observer must be a nonempty string")
    for field in ("interval_ms", "capture_timestamp"):
        value = sample.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{field} must be finite and positive")
    if not mac:
        domain = sample.get("domain_id")
        if isinstance(domain, bool) or not isinstance(domain, int) or not 0 <= domain <= 232:
            raise ValueError("domain_id must be between 0 and 232")
    for field in counter_fields(mac) + (() if mac else ("capture_invalid_rows", "capture_restarts")):
        value = sample.get(field, None if mac else 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must be a nonnegative integer")


def aggregate(samples, expected, mac=False, now_ms=None):
    if not samples:
        return None
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    expected = set(expected or samples)
    selected = {name: sample for name, sample in samples.items() if name in expected
                and -1000 <= now_ms - sample["capture_timestamp"]
                <= max(5000, sample["interval_ms"] * 3)}
    result = {
        "scope": "network", "observer": "network",
        "measurement_layer": "emane_mac" if mac else "ethernet",
        "aggregated_at_ms": now_ms,
        "capture_timestamp": max(sample["capture_timestamp"]
                                 for sample in (selected or samples).values()),
        "interval_ms": max(sample["interval_ms"] for sample in (selected or samples).values()),
        "contributors": sorted(selected),
        "missing_contributors": sorted(expected - selected.keys()),
        "observer_count": len(selected),
        "complete": expected == selected.keys(),
        "stale": not selected,
        "source_intervals_ms": {name: sample["interval_ms"] for name, sample in selected.items()},
        "fresh_until_ms": min((sample["capture_timestamp"] + max(5000, sample["interval_ms"] * 3)
                              for sample in selected.values()), default=now_ms),
    }
    for field in counter_fields(mac):
        result[field] = sum(sample.get(field, 0) for sample in selected.values())
        result[f"{field}_per_second"] = sum(
            sample.get(field, 0) * 1000 / sample["interval_ms"] for sample in selected.values())
        if not mac:
            result[f"mean_{field}"] = result[field] / len(selected) if selected else 0
    if mac:
        for direction in ("tx", "rx"):
            for unit in ("bytes", "packets", "drops"):
                name = f"mac_{direction}_{unit}"
                for suffix in ("", "_per_second"):
                    result[name + suffix] = sum(
                        result[f"mac_{direction}_{kind}_{unit}{suffix}"]
                        for kind in ("unicast", "broadcast"))
    else:
        result["schema_version"] = min((s.get("schema_version", 1) for s in selected.values()), default=1)
        result["capture_complete"] = bool(selected) and all(
            sample.get("capture_status") == "live"
            and sample.get("capture_invalid_rows_interval",
                           sample.get("capture_invalid_rows", 0)) == 0
            and not sample.get("capture_gap", False)
            and sample.get("orphan_fragment_packets", 0) == 0
            and sample.get("incomplete_fragment_packets", 0) == 0 for sample in selected.values())
        result["classification_complete"] = (result["schema_version"] >= 2
                                              and result["unknown_tx_bytes"] == 0
                                              and result["mixed_tx_bytes"] == 0)
        result["capture_restarts"] = sum(s.get("capture_restarts", 0) for s in selected.values())
        result["wire_delivery_enabled"] = bool(selected) and all(
            sample.get("wire_delivery_enabled", False) for sample in selected.values())
        for field in ("wire_incomplete_fragments", "wire_unknown_destinations"):
            result[field] = sum(sample.get(field, 0) for sample in selected.values())
        result["kernel_drop_count"] = None
    return result
