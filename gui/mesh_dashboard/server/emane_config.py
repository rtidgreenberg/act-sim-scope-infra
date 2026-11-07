"""Read-only, bounded snapshots of the dashboard node's EMANE configuration."""

import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)
CACHE_SECONDS = 5
QUERY_TIMEOUT_SECONDS = 3


def parse_configuration(output, nem_id):
    components = {}
    for line in output.splitlines():
        match = re.fullmatch(r"\s*nem\s+(\d+)\s+(\S+)\s+(\S+)\s*=\s*(.*?)\s*", line)
        if match and int(match[1]) == nem_id:
            components.setdefault(match[2], {})[match[3]] = match[4]
    for component, parameter in (("mac", "datarate"), ("transport", "bitrate"),
                                 ("phy", "bandwidth"), ("phy", "frequency")):
        value = components.get(component, {}).get(parameter)
        if value is None or not value:
            raise ValueError(f"Missing runtime parameter {component}.{parameter}")
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"Invalid runtime parameter {component}.{parameter}: {value}")
    return components


def model_libraries(config_dir, nem_id):
    platform = ET.parse(config_dir / "platform.xml").getroot()
    nem = next((entry for entry in platform.findall("nem")
                if entry.get("id") == str(nem_id)), None)
    if nem is None or not nem.get("definition"):
        raise ValueError(f"Generated platform configuration does not contain NEM {nem_id}")
    nem_path = config_dir / nem.get("definition")
    stack = ET.parse(nem_path).getroot()
    libraries = {}
    for kind in ("transport", "shim", "mac"):
        for index, component in enumerate(stack.findall(kind)):
            definition = component.get("definition")
            if not definition:
                raise ValueError(f"Missing {kind} model definition")
            library = ET.parse(nem_path.parent / definition).getroot().get("library")
            if not library:
                raise ValueError(f"Missing {kind} model library")
            libraries[f"shim{index}" if kind == "shim" else kind] = library
    return libraries


class EmaneConfiguration:
    def __init__(self):
        self.enabled = os.environ.get("EMANE_ENABLED") == "1"
        self.observer_node = os.environ.get("NODE_NAME", "dashboard node")
        raw_id = os.environ.get("EMANE_NEM_ID", "")
        self.nem_id = int(raw_id) if raw_id.isdecimal() else None
        self.config_dir = Path("/tmp/emane")
        self._lock = threading.Lock()
        self._cached = None
        self._expires_at = 0

    def snapshot(self):
        with self._lock:
            if self._cached is not None and time.monotonic() < self._expires_at:
                return self._cached
            result = {
                "available": False,
                "observer_node": self.observer_node,
                "nem_id": self.nem_id,
                "cache_ttl_ms": CACHE_SECONDS * 1000,
                "source": "Local emanesh runtime query; model libraries from generated XML",
                "components": {},
                "models": {},
            }
            if not self.enabled:
                result["unavailable_reason"] = "EMANE is not enabled on the dashboard node"
            else:
                try:
                    if self.nem_id is None or self.nem_id < 1:
                        raise ValueError("EMANE_NEM_ID must be a positive integer")
                    executable = shutil.which("emanesh")
                    if not executable:
                        raise RuntimeError("emanesh is not installed")
                    completed = subprocess.run(
                        [executable, "localhost", "get", "config", str(self.nem_id), "all"],
                        capture_output=True, text=True, timeout=QUERY_TIMEOUT_SECONDS)
                    if completed.returncode:
                        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip()
                                           or f"emanesh exited {completed.returncode}")
                    components = parse_configuration(completed.stdout, self.nem_id)
                    models = model_libraries(self.config_dir, self.nem_id)
                    result.update(available=True, components=components, models=models)
                except (OSError, RuntimeError, ValueError, ET.ParseError,
                        subprocess.TimeoutExpired) as exc:
                    result["unavailable_reason"] = str(exc)
                    logger.warning("EMANE configuration read failed: %s", exc)
            result["checked_at_ms"] = int(time.time() * 1000)
            self._cached = result
            self._expires_at = time.monotonic() + CACHE_SECONDS
            return result
