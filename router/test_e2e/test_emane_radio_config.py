"""Canonical EMANE radio limits, distinct from dashboard configuration tests."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path


ENTRYPOINT = Path(__file__).resolve().parents[2] / "docker/connext-7.7/node-entrypoint.sh"


def generated_parameters(filename):
    source = ENTRYPOINT.read_text()
    match = re.search(
        rf'cat > "\$config_dir/{re.escape(filename)}" <<[^\n]+\n(.*?)\nEOF',
        source,
        re.DOTALL,
    )
    assert match, f"Missing generated EMANE configuration: {filename}"
    root = ET.fromstring(match.group(1))
    return {parameter.attrib["name"]: parameter.attrib["value"]
            for parameter in root.findall("param")}


def test_virtual_transport_has_no_combined_send_receive_rate_limit():
    assert generated_parameters("transvirtual.xml")["bitrate"] == "0"


def test_radio_preserves_nominal_rate_and_timing():
    parameters = generated_parameters("rfpipemac.xml")
    assert parameters["datarate"] == "2M"
    assert parameters["delay"] == parameters["jitter"] == "0"
