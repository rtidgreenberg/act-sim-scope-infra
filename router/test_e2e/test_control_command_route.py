"""End-to-end: control_command route, real router_main pair, real WAN hop.

control app (control_lan) --[router control-role: control_lan -> control_wan]-->
  WAN --[router platform-role: platform_wan (CFT msg.destination=Platform_30) -> platform_lan]-->
    platform app (platform_lan)

Proves the same "simulate -> route -> subscribe" path through two real subprocess
router_main instances and a real WAN domain hop (see docs/cpp_router/design-decisions.md
D50 for what this fixture does and does not cover).

Absorbs the retired C++ test/test_dynamic_forward.cxx (D52 test-suite policy: integration
tests are Python). That test asserted DynamicData forwarding with a msg.destination
ContentFilteredTopic — addressed platform receives, others are filtered out — which is
exactly what this test asserts, but end-to-end through router_main instead of an
in-process EntityFactory.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from util.dds_probe import Probe, write_until_seen  # noqa: E402

TYPE_NAME = "control_command"
TOPIC_NAME = "ControlCommand"
THIS_NODE = "Platform_30"
OTHER_NODE = "Platform_31"


def test_command_reaches_only_addressed_platform(router_pair, unique_domains):
    control_proc, platform_proc, _ = router_pair(
        "e2e_control_command.yaml", unique_domains)

    control_app = Probe(unique_domains["control_lan"])
    platform_app = Probe(unique_domains["platform_lan"])
    try:
        writer = control_app.writer(TOPIC_NAME, TYPE_NAME)
        reader = platform_app.reader(TOPIC_NAME, TYPE_NAME)

        def write_both():
            writer.write(control_app.sample(
                TYPE_NAME, **{"msg.destination": THIS_NODE, "msg.source": "Control_20"}))
            writer.write(control_app.sample(
                TYPE_NAME, **{"msg.destination": OTHER_NODE, "msg.source": "Control_20"}))

        buckets = write_until_seen(
            write_both, reader,
            classify=lambda d: d["msg.destination"], stop_key=THIS_NODE,
            check_alive=lambda: control_proc.is_alive() and platform_proc.is_alive())
        seen_us = buckets.get(THIS_NODE, [])
        seen_other = buckets.get(OTHER_NODE, [])

        assert control_proc.returncode is None, \
            f"control router exited early, see {control_proc.log_path}"
        assert platform_proc.returncode is None, \
            f"platform router exited early, see {platform_proc.log_path}"

        assert seen_us, (
            f"ControlCommand addressed to {THIS_NODE} never arrived at the platform "
            f"reader; router logs: control={control_proc.log_path} "
            f"platform={platform_proc.log_path}")
        assert not seen_other, (
            "ControlCommand addressed to a different platform leaked through the "
            "content filter — should have been dropped by the platform_wan CFT")
    finally:
        control_app.close()
        platform_app.close()
