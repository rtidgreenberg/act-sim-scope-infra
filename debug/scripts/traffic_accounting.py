"""Physical-frame accounting for the UDPv4 RTPS monitor (no DDS participant)."""

import csv
import json
import logging
import math
import time
from pathlib import Path


DISCOVERY_KINDS = {0xc2, 0xc3, 0xc4, 0xc7}
# Connext 7.7 transport_common.h: SPDP2 bootstrap, config, and secure config writers.
SPDP2_DISCOVERY_WRITERS = {0x00010082, 0x00010182, 0xff010182}
WRITER_KINDS = {0x02, 0x03}
DATA_SUBMESSAGES = {0x02, 0x03, 0x10, 0x11, 0x14, 0x15, 0x16, 0x18, 0x81}
CONTROL_SUBMESSAGES = {0x06, 0x07, 0x08, 0x12, 0x13, 0x17, 0x19,
                       0x1a, 0x1b, 0x1c, 0x1d, 0x1e}
# Keep retired probe captures classified as infrastructure for historical reports.
INFRASTRUCTURE_TOPICS = {"RouterHealth", "RouterLinkProbe"}
FRAME_CLASSES = ("discovery", "data", "reliability", "mixed", "unknown")
TX_CLASSES = ("application", "infrastructure", "discovery", "reliability", "mixed", "unknown")
COUNTER_PREFIXES = (*FRAME_CLASSES, *(name + "_tx" for name in TX_CLASSES),
                    "data_tx", "tx", "rx", "total", "incomplete_fragment")
FIELDS = ("frame.time_epoch", "frame.len", "ip.src", "ip.dst", "ip.id",
          "ip.proto", "ip.frag_offset", "ip.flags.mf", "udp.dstport", "rtps.guidPrefix.src",
          "rtps.sm.wrEntityId", "rtps.sm.id", "rtps.param.endpoint_guid",
          "rtps.param.topicName", "data.data", "udp.payload")


def empty_counters():
    return {f"{prefix}_{unit}": 0 for prefix in COUNTER_PREFIXES
            for unit in ("packets", "bytes")}


def values(text):
    return [value.strip() for value in text.split(",") if value.strip()]


def entity_ids(text):
    return [int(value.replace(":", ""), 16) for value in values(text)]


def is_discovery_entity(entity):
    return entity & 0xff in DISCOVERY_KINDS or entity in SPDP2_DISCOVERY_WRITERS


def classify_packet(entity_text, submessage_text):
    entities = entity_ids(entity_text)
    kinds = {entity & 0xff for entity in entities}
    submessages = {int(value, 0) for value in values(submessage_text)}
    classes = set()
    if submessages & DATA_SUBMESSAGES:
        if any(is_discovery_entity(entity) for entity in entities):
            classes.add("discovery")
        if kinds & WRITER_KINDS:
            classes.add("data")
    if submessages & CONTROL_SUBMESSAGES:
        classes.add("reliability")
    if len(classes) > 1:
        return "mixed"
    return next(iter(classes), "unknown")


def tshark_fields():
    args = ["-n", "-T", "fields", "-E", "separator=|", "-E", "occurrence=a", "-E", "quote=d"]
    for field in FIELDS:
        args.extend(["-e", field])
    return args


class FrameAccounting:
    def __init__(self, domains, local_ips, accumulator, topic_cache=None, wire_capture=None):
        self.domains = set(domains)
        self.local_ips = set(local_ips)
        self.accumulator = accumulator
        self.fragments = {}
        self.topics = {}
        self.topic_cache = Path(topic_cache) if topic_cache else None
        self.wire_capture = wire_capture
        self.orphan_packets = 0
        self.orphan_bytes = 0
        self.last_saved = 0
        self.dirty = False
        if self.topic_cache and self.topic_cache.exists():
            saved = json.loads(self.topic_cache.read_text())
            if not isinstance(saved, dict) or any(
                not isinstance(value, dict)
                or not isinstance(value.get("topic"), str) or not value["topic"]
                or isinstance(value.get("seen_at"), bool)
                or not isinstance(value.get("seen_at"), (int, float))
                or not math.isfinite(value["seen_at"]) for value in saved.values()
            ):
                raise ValueError(f"Invalid topic cache: {self.topic_cache}")
            self.topics = {key: value for key, value in saved.items()
                           if time.time() - value["seen_at"] < 86400}

    def save_topics(self):
        if not self.topic_cache or not self.dirty:
            return
        self.topic_cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.topic_cache.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.topics, separators=(",", ":")))
        temporary.replace(self.topic_cache)
        self.dirty = False
        self.last_saved = time.time()

    def _learn(self, domain, guids, topics, timestamp):
        names, endpoints = values(topics), values(guids)
        if not names:
            return
        if len(names) != len(endpoints):
            logging.warning("Ambiguous SEDP topic/endpoint correspondence; retaining unknown attribution")
            return
        for endpoint, topic in zip(endpoints, names):
            guid = endpoint.replace(":", "").lower()
            if len(guid) != 32 or any(char not in "0123456789abcdef" for char in guid):
                logging.warning("Invalid SEDP endpoint GUID; retaining unknown attribution")
                continue
            if int(guid[-2:], 16) in WRITER_KINDS:
                self.topics[f"{domain}:{guid}"] = {"topic": topic, "seen_at": timestamp}
                self.dirty = True
        if len(self.topics) > 10000:
            self.topics = dict(sorted(self.topics.items(),
                                     key=lambda item: item[1]["seen_at"])[-10000:])

    def _tx_class(self, domain, prefix, entity_text, submessage_text):
        entities = entity_ids(entity_text)
        submessages = {int(value, 0) for value in values(submessage_text)}
        discovery = any(is_discovery_entity(entity) for entity in entities)
        if not submessages & DATA_SUBMESSAGES:
            if discovery:
                return "discovery"
            return "reliability" if submessages & CONTROL_SUBMESSAGES else "unknown"
        roles = set()
        if discovery:
            roles.add("discovery")
        for entity in entities:
            if entity & 0xff not in WRITER_KINDS:
                continue
            key = f"{domain}:{prefix.replace(':', '').lower()}{entity:08x}"
            entry = self.topics.get(key)
            if not entry:
                roles.add("unknown")
            else:
                entry["seen_at"] = time.time()
                self.dirty = True
                roles.add("infrastructure" if entry["topic"] in INFRASTRUCTURE_TOPICS
                          else "application")
        return next(iter(roles), "unknown") if len(roles) <= 1 else "mixed"

    def _emit(self, domain, source, size, timestamp, frame_class, tx_class, incomplete=False):
        self.accumulator.add_packet(domain, size, frame_class, source in self.local_ips,
                                    tx_class, incomplete=incomplete, timestamp_ms=int(timestamp * 1000))

    def expire(self, now):
        for key, group in list(self.fragments.items()):
            if now - group["at"] < 5:
                continue
            self.fragments.pop(key)
            if group["domain"] in self.domains:
                for timestamp, size in group["frames"]:
                    self._emit(group["domain"], key[0], size, timestamp,
                               "unknown", "unknown", incomplete=True)
            else:
                self.orphan_packets += group["packets"]
                self.orphan_bytes += group["bytes"]
                self.accumulator.orphan_fragment_packets = self.orphan_packets
                self.accumulator.orphan_fragment_bytes = self.orphan_bytes
            self.accumulator.release_fragment(key)
        if self.dirty and now - self.last_saved >= 5:
            self.save_topics()

    def process(self, line):
        """Account a frame and return its normalized UDP fields for replay tracking."""
        parts = next(csv.reader([line], delimiter="|"))
        if len(parts) != len(FIELDS):
            raise ValueError("unexpected tshark field count")
        row = dict(zip(FIELDS, parts))
        if not row["ip.src"]:
            return
        # ICMP error quotations can contain a second IP header and apparent RTPS.
        protocols = values(row["ip.proto"])
        if not protocols:
            raise ValueError("IP packet missing protocol")
        if protocols[0] != "17":
            return
        for field in ("ip.src", "ip.dst", "ip.id", "ip.frag_offset", "ip.flags.mf", "udp.dstport"):
            row[field] = row[field].split(",", 1)[0]
        timestamp = float(row["frame.time_epoch"])
        if not math.isfinite(timestamp) or timestamp <= 0:
            raise ValueError("Invalid capture timestamp")
        self.expire(timestamp)
        size = int(row["frame.len"])
        port = int(row["udp.dstport"]) if row["udp.dstport"] else None
        domain = (port - 7400) // 250 if port is not None and port >= 7400 else None
        fragmented = row["ip.flags.mf"] == "1" or int(row["ip.frag_offset"] or 0) > 0
        group = None
        if fragmented:
            key = (row["ip.src"], row["ip.dst"], row["ip.id"])
            group = self.fragments.setdefault(key, {
                "at": timestamp, "domain": None, "bytes": 0, "packets": 0, "frames": []})
            self.accumulator.hold_fragment(key, int(timestamp * 1000))
            group["bytes"] += size
            group["packets"] += 1
            group["frames"].append((timestamp, size))
            if row["ip.frag_offset"] == "0" and row["data.data"]:
                payload = bytes.fromhex(row["data.data"].replace(":", ""))
                if len(payload) >= 12 and payload[8:12] == b"RTPS":
                    group["domain"] = (int.from_bytes(payload[2:4], "big") - 7400) // 250
            if not row["rtps.guidPrefix.src"]:
                return row
        if domain not in self.domains or not row["rtps.guidPrefix.src"]:
            if group:
                self.fragments.pop(key)
                self.accumulator.release_fragment(key)
            return row
        self._learn(domain, row["rtps.param.endpoint_guid"], row["rtps.param.topicName"], timestamp)
        frame_class = classify_packet(row["rtps.sm.wrEntityId"], row["rtps.sm.id"])
        tx_class = self._tx_class(domain, row["rtps.guidPrefix.src"],
                                 row["rtps.sm.wrEntityId"], row["rtps.sm.id"])
        for captured_at, frame_size in group["frames"] if group else [(timestamp, size)]:
            self._emit(domain, row["ip.src"], frame_size, captured_at, frame_class, tx_class)
        if group:
            self.fragments.pop(key)
            self.accumulator.release_fragment(key)
        if self.wire_capture:
            self.wire_capture.process(row, domain)
        return row
