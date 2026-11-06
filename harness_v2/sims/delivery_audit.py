import json
import os
import time
from pathlib import Path


class DeliveryAudit:
    def __init__(self, node_name, run_id):
        debug_dir = os.environ.get("NODE_DEBUG_DIR")
        self.path = Path(debug_dir, "events.jsonl") if debug_dir else None
        self.node_name = node_name
        self.run_id = run_id
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event, topic, sample):
        if not self.path:
            return
        record = {
            "event": event,
            "run_id": sample["run_id"],
            "node": self.node_name,
            "topic": topic,
            "source_node": sample["source_node"],
            "sequence": int(sample["audit_sequence"]),
            "sent_at_ns": int(sample["sent_at_ns"]),
            "recorded_at_ns": time.time_ns(),
        }
        for field in ("source", "destination", "command_id"):
            try:
                record[field] = sample[field]
            except Exception as error:
                if error.__class__.__name__ != "InvalidArgumentError":
                    raise
                pass
        with self.path.open("a", encoding="utf-8") as events:
            events.write(json.dumps(record, sort_keys=True) + "\n")
