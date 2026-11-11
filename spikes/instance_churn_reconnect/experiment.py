"""Apply XML-authored experiment policies only to a disposable source snapshot."""

from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as ET


def transient_local_snapshot(workspace):
    workspace = Path(workspace)
    production = workspace / "harness_v2/qos/act_qos_profiles.xml"
    overrides = workspace / "spikes/instance_churn_reconnect/qos.xml"
    tree = ET.parse(production)
    profile = tree.find("./qos_library[@name='ACT_QOS_LIB']/qos_profile[@name='wan_event']")
    experiment = ET.parse(overrides).find(
        "./qos_library[@name='ChurnSpike']/qos_profile[@name='event_transient_local']")
    if profile is None or experiment is None:
        raise ValueError("Missing canonical Event or transient-local experiment XML profile")
    for endpoint in ("datawriter_qos", "datareader_qos"):
        target = profile.find(endpoint)
        policy = experiment.find(f"{endpoint}/durability/kind")
        if target is None or policy is None:
            raise ValueError(f"Missing XML-authored durability policy for {endpoint}")
        durability = target.find("durability")
        if durability is None:
            durability = ET.SubElement(target, "durability")
        previous = durability.find("kind")
        if previous is not None:
            durability.remove(previous)
        durability.append(deepcopy(policy))
    tree.write(production, encoding="utf-8", xml_declaration=True)
