"""Passive RTPS sample observations on a node's WAN interface."""

import bisect
import ipaddress
import json
import logging
import math
import struct
from pathlib import Path


class SequenceRanges:
    """Remember identities without retaining every sample of a periodic writer."""

    def __init__(self):
        self.ranges = []

    def add(self, sequence):
        index = bisect.bisect_right(self.ranges, (sequence, math.inf))
        if index and self.ranges[index - 1][1] >= sequence:
            return False
        low = high = sequence
        if index and self.ranges[index - 1][1] + 1 == sequence:
            low = self.ranges.pop(index - 1)[0]
            index -= 1
        if index < len(self.ranges) and self.ranges[index][0] == sequence + 1:
            high = self.ranges.pop(index)[1]
        self.ranges.insert(index, (low, high))
        return True

    def contains(self, sequence):
        index = bisect.bisect_right(self.ranges, (sequence, math.inf))
        return bool(index and self.ranges[index - 1][1] >= sequence)


def submessages(payload):
    if len(payload) < 20 or payload[:4] != b"RTPS":
        raise ValueError("missing RTPS header")
    prefix, destination = payload[8:20], None
    offset = 20
    while offset < len(payload):
        if len(payload) - offset < 4:
            raise ValueError("truncated RTPS submessage header")
        kind, flags = payload[offset:offset + 2]
        endian = "<" if flags & 1 else ">"
        size = struct.unpack_from(endian + "H", payload, offset + 2)[0]
        end = offset + 4 + size if size or kind in (0x01, 0x09) else len(payload)
        if end > len(payload):
            raise ValueError("truncated RTPS submessage")
        body = payload[offset + 4:end]
        if kind == 0x0e:
            if len(body) < 12:
                raise ValueError("truncated INFO_DST")
            destination = body[:12].hex() if any(body[:12]) else None
        elif kind == 0x0c:
            if len(body) < 20:
                raise ValueError("truncated INFO_SRC")
            prefix = body[8:20]
        elif kind in (0x06, 0x12):
            minimum = 24 if kind == 0x06 else 28
            if len(body) < minimum:
                raise ValueError("truncated ACKNACK/NACK_FRAG")
            high, low = struct.unpack_from(endian + "iI", body, 8)
            sequence = (high << 32) + low
            bitmap_offset = 20 if kind == 0x06 else 24
            bits = struct.unpack_from(endian + "I", body, bitmap_offset - 4)[0]
            if bits > 256 or len(body) < minimum + 4 * ((bits + 31) // 32):
                raise ValueError("invalid ACKNACK/NACK_FRAG bitmap")
            requested = [
                index for index in range(bits)
                if struct.unpack_from(endian + "I", body, bitmap_offset + 4 * (index // 32))[0]
                & (1 << (31 - index % 32))]
            if requested and sequence <= 0:
                raise ValueError("invalid requested writer sequence")
            if destination and requested:
                for number in ([sequence + index for index in requested]
                               if kind == 0x06 else [sequence]):
                    yield {
                        "kind": "repair_request", "writer_guid": destination + body[4:8].hex(),
                        "reader_guid": prefix.hex() + body[:4].hex(), "sequence": number,
                    }
        elif kind in (0x15, 0x16):
            minimum = 32 if kind == 0x16 else 20
            if len(body) < minimum:
                raise ValueError("truncated DATA/DATA_FRAG")
            inline_offset = 4 + struct.unpack_from(endian + "H", body, 2)[0]
            if not minimum <= inline_offset <= len(body):
                raise ValueError("invalid inline QoS offset")
            cursor = inline_offset
            if flags & 2:
                while True:
                    if cursor + 4 > len(body):
                        raise ValueError("truncated inline QoS")
                    pid, length = struct.unpack_from(endian + "HH", body, cursor)
                    cursor += 4
                    if pid == 1:
                        break
                    cursor += (length + 3) & ~3
                    if cursor > len(body):
                        raise ValueError("truncated inline QoS parameter")
            # DATA's D flag and DATA_FRAG's K flag have different meanings.
            has_data = not flags & 4 if kind == 0x16 else bool(flags & 4)
            if has_data:
                high, low = struct.unpack_from(endian + "iI", body, 12)
                sequence = (high << 32) + low
                if sequence <= 0:
                    raise ValueError("invalid writer sequence number")
                item = {
                    "writer_guid": prefix.hex() + body[8:12].hex(),
                    "sequence": sequence, "destination_prefix": destination,
                    "reader_id": body[4:8].hex(),
                    "payload": body[cursor:],
                }
                if kind == 0x16:
                    first, count, fragment_size, sample_size = struct.unpack_from(endian + "IHHI", body, 20)
                    if not first or not count or not fragment_size or not sample_size:
                        raise ValueError("invalid DATA_FRAG dimensions")
                    total = (sample_size + fragment_size - 1) // fragment_size
                    if first + count - 1 > total or sample_size > 16 * 1024 * 1024:
                        raise ValueError("DATA_FRAG outside sample")
                    required = min(count * fragment_size, sample_size - (first - 1) * fragment_size)
                    if len(item["payload"]) < required:
                        raise ValueError("truncated fragment payload")
                    item.update(first=first, count=count, fragment_size=fragment_size, sample_size=sample_size)
                yield item
        offset = end


def endpoint_metadata(payload):
    if len(payload) < 4 or payload[:2] not in (b"\x00\x02", b"\x00\x03"):
        return None
    endian = "<" if payload[1] == 3 else ">"
    result = {}
    offset = 4
    terminated = False
    while offset + 4 <= len(payload):
        pid, length = struct.unpack_from(endian + "HH", payload, offset)
        offset += 4
        if pid == 1:
            terminated = True
            break
        value = payload[offset:offset + length]
        if len(value) != length:
            raise ValueError("truncated SEDP parameter")
        if pid == 0x005a and length == 16:
            result["writer_guid"] = value.hex()
        elif pid == 0x0005 and length >= 4:
            size = struct.unpack_from(endian + "I", value)[0]
            if not 1 <= size <= length - 4:
                raise ValueError("invalid SEDP topic name")
            result["topic"] = value[4:4 + size - 1].decode("utf-8")
        elif pid == 0x001a:
            if length < 12:
                raise ValueError("truncated SEDP reliability policy")
            result["reliability"] = {1: "best_effort", 2: "reliable"}.get(
                struct.unpack_from(endian + "I", value)[0], "unknown")
        offset += (length + 3) & ~3
    if not terminated:
        raise ValueError("unterminated SEDP parameter list")
    if result.get("writer_guid", "").endswith(("02", "03")) and result.get("topic"):
        # DDSI-RTPS discovery restores the DDS DataWriter default for an omitted PID.
        result.setdefault("reliability", "reliable")
        return result
    return None


class WireCapture:
    def __init__(self, path, observer, run_id, nodes, local_ips):
        if not run_id or observer not in nodes.values():
            raise ValueError("wire delivery requires an active run ID and mapped observer")
        self.observer, self.run_id = observer, run_id
        self.nodes, self.local_ips = nodes, local_ips
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.output = self.path.open("a", encoding="utf-8")
        self.seen, self.fragments, self.endpoints, self.requests = {}, {}, {}, {}
        self.sent_paths = {}
        self.incomplete_fragments = 0
        self.unknown_destinations = 0

    def close(self):
        self.output.close()

    def emit(self, event):
        self.output.write(json.dumps(dict(event, scope="wan_wire", schema_version=1,
                                         node=self.observer, run_id=self.run_id),
                                     separators=(",", ":")) + "\n")
        self.output.flush()

    def expire(self, now):
        self.requests = {key: request for key, request in self.requests.items()
                         if now - request["at"] <= 120}
        for key, group in list(self.fragments.items()):
            if now - group["at"] > 60:
                del self.fragments[key]
                self.incomplete_fragments += 1

    def process(self, row, domain):
        source, destination = row["ip.src"], row["ip.dst"]
        if source in self.local_ips:
            event = "sent"
        elif destination in self.local_ips:
            event = "received"
        else:
            return
        timestamp = float(row["frame.time_epoch"])
        self.expire(timestamp)
        raw = row.get("udp.payload", "")
        if not raw:
            raise ValueError("RTPS row missing UDP payload for wire delivery")
        for item in submessages(bytes.fromhex(raw.replace(":", ""))):
            timestamp = float(row["frame.time_epoch"])
            writer = item["writer_guid"]
            if item.get("kind") == "repair_request":
                if (event == "received" and source in self.nodes and destination in self.nodes
                        and int(writer[-2:], 16) in (2, 3)):
                    request_key = (domain, writer, item["sequence"], destination, source)
                    request = self.requests.setdefault(
                        request_key, {"at": timestamp, "readers": {}, "emitted": False})
                    request["at"] = timestamp
                    request["readers"][item["reader_guid"]] = timestamp
                continue
            discovery = int(writer[-2:], 16) in (0xc2, 0xc3)
            key = (domain, writer, item["sequence"], destination, event)
            seen = self.seen.setdefault((domain, writer, destination, event), SequenceRanges())
            request = self.requests.get((domain, writer, item["sequence"], source, destination))
            repeated_path = False
            port = str(row.get("udp.dstport", ""))
            if event == "sent" and int(writer[-2:], 16) in (2, 3) and port.isdigit():
                path = self.sent_paths.setdefault((domain, writer, destination, port), SequenceRanges())
                repeated_path = path.contains(item["sequence"])
                path.add(item["sequence"])
            repeated = seen.contains(item["sequence"])
            if not repeated and "first" in item and key in self.fragments:
                repeated = any(number in self.fragments[key]["parts"]
                               for number in range(item["first"], item["first"] + item["count"]))
            if (event == "sent" and request and not request["emitted"] and repeated and repeated_path
                    and source in self.nodes and destination in self.nodes):
                readers = sorted(reader for reader in request["readers"]
                                 if (not item["destination_prefix"]
                                     or reader[:24] == item["destination_prefix"])
                                 and item["reader_id"] in ("00000000", reader[24:]))
                if readers:
                    self.emit({
                        "event": "retransmitted", "domain_id": domain, "writer_guid": writer,
                        "sequence": item["sequence"], "timestamp_ns": round(timestamp * 1e9),
                        "source": self.nodes[source], "recipient": self.nodes[destination],
                        "source_ip": source, "destination_ip": destination,
                        "requested_at_ns": round(request["readers"][readers[0]] * 1e9),
                        "reader_guid": readers[0], "destination_port": int(port),
                    })
                    request["emitted"] = True
            if seen.contains(item["sequence"]):
                continue
            if "first" in item:
                group = self.fragments.setdefault(key, {
                    "at": timestamp, "parts": {}, "size": item["sample_size"],
                    "fragment_size": item["fragment_size"]})
                if (group["size"], group["fragment_size"]) != (item["sample_size"], item["fragment_size"]):
                    raise ValueError("inconsistent DATA_FRAG dimensions")
                for index in range(item["count"]):
                    number = item["first"] + index
                    start = index * item["fragment_size"]
                    group["parts"][number] = item["payload"][start:start + item["fragment_size"]]
                total = (group["size"] + group["fragment_size"] - 1) // group["fragment_size"]
                if len(group["parts"]) != total:
                    continue
                item["payload"] = b"".join(group["parts"][number] for number in range(1, total + 1))[:group["size"]]
                timestamp = group["at"]
                del self.fragments[key]
            seen.add(item["sequence"])
            if discovery:
                metadata = endpoint_metadata(item["payload"])
                if metadata and self.endpoints.get((domain, metadata["writer_guid"])) != metadata:
                    self.endpoints[domain, metadata["writer_guid"]] = metadata
                    self.emit(dict(metadata, event="endpoint", domain_id=domain,
                                   timestamp_ns=round(timestamp * 1e9)))
                continue
            if int(writer[-2:], 16) not in (2, 3):
                continue
            if source not in self.nodes or destination not in self.nodes:
                self.unknown_destinations += 1
                logging.warning("Unmapped or multicast WAN data path %s -> %s; not inferring recipients",
                                source, destination)
                continue
            if ipaddress.ip_address(destination).is_multicast:
                raise ValueError("multicast delivery requires explicit receiver membership")
            metadata = self.endpoints.get((domain, writer), {})
            self.emit({
                "event": event, "domain_id": domain, "writer_guid": writer,
                "sequence": item["sequence"], "timestamp_ns": round(timestamp * 1e9),
                "completed_at_ns": round(float(row["frame.time_epoch"]) * 1e9),
                "source": self.nodes[source], "recipient": self.nodes[destination],
                "source_ip": source, "destination_ip": destination,
                "destination_prefix": item["destination_prefix"],
                "topic": metadata.get("topic"), "reliability": metadata.get("reliability", "unknown"),
            })
