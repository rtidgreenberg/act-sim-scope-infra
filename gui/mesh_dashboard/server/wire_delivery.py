"""Rolling logical-sample delivery from passive WAN RTPS captures only."""

import json
import logging
import time


logger = logging.getLogger(__name__)


class WireDelivery:
    RELIABLE_GRACE_MS = 15_000
    OTHER_GRACE_MS = 2_000

    def __init__(self):
        self.run_id = None
        self.offsets = {}
        self.sent = {}
        self.received = {}
        self.retransmitted = {}
        self.endpoints = {}
        self.invalid_records = 0

    @staticmethod
    def identity(event):
        return (event["domain_id"], event["writer_guid"], event["sequence"],
                event["source_ip"], event["destination_ip"])

    def ingest(self, event):
        if not isinstance(event, dict):
            raise ValueError("wire observation must be an object")
        if event.get("scope") != "wan_wire" or event.get("schema_version") != 1:
            raise ValueError("not a WAN wire observation")
        kind = event.get("event")
        if kind not in {"sent", "received", "endpoint", "retransmitted"}:
            raise ValueError("unknown wire event")
        if type(event.get("timestamp_ns")) is not int or event["timestamp_ns"] <= 0:
            raise ValueError("invalid wire timestamp")
        if type(event.get("domain_id")) is not int or not 0 <= event["domain_id"] <= 232:
            raise ValueError("invalid wire domain")
        guid = event.get("writer_guid", "")
        if not isinstance(guid, str) or len(guid) != 32 or any(c not in "0123456789abcdef" for c in guid):
            raise ValueError("invalid writer GUID")
        if kind == "endpoint":
            if not isinstance(event.get("topic"), str) or not event["topic"]:
                raise ValueError("missing discovered topic")
            reliability = event.get("reliability", "unknown")
            if reliability not in {"reliable", "best_effort", "unknown"}:
                raise ValueError("invalid discovered reliability")
            key = (event["domain_id"], guid)
            if event["timestamp_ns"] >= self.endpoints.get(key, {}).get("timestamp_ns", 0):
                self.endpoints[key] = event
            return
        required = {"domain_id", "sequence", "source", "recipient", "source_ip", "destination_ip", "node"}
        if not required.issubset(event) or type(event["sequence"]) is not int or event["sequence"] <= 0:
            raise ValueError("incomplete wire sample identity")
        if any(not isinstance(event[field], str) or not event[field]
               for field in ("source", "recipient", "source_ip", "destination_ip", "node")):
            raise ValueError("invalid wire node/IP identity")
        if event.get("reliability", "unknown") not in {"reliable", "best_effort", "unknown"}:
            raise ValueError("invalid wire reliability")
        if event.get("topic") is not None and not isinstance(event["topic"], str):
            raise ValueError("invalid wire topic")
        completed = event.get("completed_at_ns", event["timestamp_ns"])
        if type(completed) is not int or completed < event["timestamp_ns"]:
            raise ValueError("invalid wire completion timestamp")
        if kind == "retransmitted":
            requested = event.get("requested_at_ns")
            reader = event.get("reader_guid", "")
            if (type(requested) is not int or not 0 < requested <= event["timestamp_ns"]
                    or not isinstance(reader, str) or len(reader) != 32
                    or any(c not in "0123456789abcdef" for c in reader)):
                raise ValueError("retry requires a preceding, identified repair request")
        expected_observer = event["recipient"] if kind == "received" else event["source"]
        if event["node"] != expected_observer:
            raise ValueError("observation is not at its source/destination WAN interface")
        key = self.identity(event)
        store = {"sent": self.sent, "received": self.received, "retransmitted": self.retransmitted}[kind]
        previous = store.get(key)
        field = "completed_at_ns" if kind == "received" else "timestamp_ns"
        if previous is None or event.get(field, event["timestamp_ns"]) < previous.get(field, previous["timestamp_ns"]):
            store[key] = event

    def poll(self, paths, run_id):
        if run_id != self.run_id:
            self.__init__()
            self.run_id = run_id
        changed = False
        for path in paths:
            offset = self.offsets.get(path, 0)
            if path.stat().st_size < offset:
                offset = 0
            with path.open(encoding="utf-8") as source:
                source.seek(offset)
                while True:
                    start = source.tell()
                    line = source.readline()
                    if not line:
                        break
                    if not line.endswith("\n"):
                        source.seek(start)
                        break
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError("wire observation must be an object")
                        if event.get("run_id") == run_id:
                            self.ingest(event)
                            changed = True
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                        self.invalid_records += 1
                        logger.warning("Invalid WAN wire record in %s: %s", path, exc)
                self.offsets[path] = source.tell()
        cutoff = time.time_ns() - 120_000_000_000
        self.sent = {key: event for key, event in self.sent.items() if event["timestamp_ns"] >= cutoff}
        self.received = {key: event for key, event in self.received.items() if event["timestamp_ns"] >= cutoff}
        self.retransmitted = {key: event for key, event in self.retransmitted.items()
                              if event["timestamp_ns"] >= cutoff}
        return changed

    def snapshot(self, window_ms=30_000, now_ns=None):
        now = time.time_ns() if now_ns is None else now_ns
        flows = {}
        for identity, sent in self.sent.items():
            if not now - window_ms * 1_000_000 <= sent["timestamp_ns"] <= now:
                continue
            metadata = self.endpoints.get((sent["domain_id"], sent["writer_guid"]), sent)
            reliability = metadata.get("reliability", "unknown")
            topic = metadata.get("topic") or "Unknown topic (" + sent["writer_guid"] + ")"
            flow = (reliability, topic, sent["source"], sent["recipient"])
            flows.setdefault(flow, []).append((identity, sent))
        rows = []
        for (reliability, topic, source, recipient), messages in sorted(flows.items()):
            settle_ms = self.RELIABLE_GRACE_MS if reliability == "reliable" else self.OTHER_GRACE_MS
            settled = [(key, event) for key, event in messages
                       if now - event["timestamp_ns"] >= settle_ms * 1_000_000]
            received = sum(key in self.received for key, _ in messages)
            settled_received = sum(key in self.received for key, _ in settled)
            settled_count = len(settled)
            lost = settled_count - settled_received
            pending = sum(key not in self.received for key, event in messages
                          if now - event["timestamp_ns"] < settle_ms * 1_000_000)
            recoveries = []
            if reliability == "reliable":
                for key, sent in messages:
                    received_event = self.received.get(key)
                    retry = self.retransmitted.get(key)
                    if received_event and retry:
                        completed = received_event.get("completed_at_ns", received_event["timestamp_ns"])
                        if sent["timestamp_ns"] < retry["timestamp_ns"] <= completed <= now:
                            recoveries.append((completed - sent["timestamp_ns"]) / 1_000_000)
            rows.append({
                "reliability": reliability, "topic": topic, "source": source, "recipient": recipient,
                "sent": len(messages), "received": received, "settled": settled_count,
                "expected": settled_count, "settled_received": settled_received,
                "lost": lost, "missing": lost, "pending": pending, "settle_ms": settle_ms,
                "percentage_ready": bool(settled_count),
                "delivery_percentage": 100 * settled_received / settled_count if settled_count else None,
                "loss_percentage": 100 * lost / settled_count if settled_count else None,
                "recovered_samples": len(recoveries),
                "avg_recovery_ms": sum(recoveries) / len(recoveries) if recoveries else None,
                "sent_event_version": max(event["timestamp_ns"] for _, event in messages),
                "received_event_version": max((self.received[key]["timestamp_ns"]
                                              for key, _ in messages if key in self.received), default=0),
            })
        return {"scope": "wan_wire", "window_ms": window_ms, "rows": rows,
                "invalid_records": self.invalid_records,
                "unknown_qos_flows": sum(row["reliability"] == "unknown" for row in rows)}
