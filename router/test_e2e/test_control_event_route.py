"""End-to-end: control_event route, real router_main pair, real WAN hop.

control app (control_lan) --[router control-role: control_lan -> control_wan]-->
  WAN --[router platform-role: platform_wan -> platform_lan]-->
    platform app (platform_lan)

Unfiltered counterpart to test_control_command_route.py: control_event broadcasts every
sample to every platform (no ContentFilteredTopic), unlike control_command which uses a
msg.destination CFT. Exercises TeamAssignment (ActTeamAssignment topic, from
RouterAdminTypes.idl/ActTypes.idl, wire-learned via D70) — the existing
test_team_assignment_e2e.py is a standalone argparse script using WIS, not a pytest test
through router_main (docs/cpp_router/debug-tooling-and-missing-tests.md #2a).

Both WAN legs use the CONTROL partition (publisher_partition/subscriber_partition,
router/config/e2e_control_event.yaml), but the app probes here are on the LAN legs, which
have no partition — matching the production data flow.
"""

import sys
from pathlib import Path

import rti.connextdds as dds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from util.dds_probe import Probe, write_until_seen  # noqa: E402

TOPIC_NAME = "ActTeamAssignment"
TYPE_NAME = "TeamAssignment"


def test_control_event_broadcasts_to_all_platforms(
        router_pair, unique_domains, admin_types_xml):
    control_proc, platform_proc, _ = router_pair(
        "e2e_control_event.yaml", unique_domains)

    provider = dds.QosProvider(str(admin_types_xml))
    ta_type = provider.type(TYPE_NAME)

    control_app = Probe(unique_domains["control_lan"])
    platform_app = Probe(unique_domains["platform_lan"])
    try:
        writer = control_app.writer(TOPIC_NAME, TYPE_NAME, dtype=ta_type)
        reader = platform_app.reader(TOPIC_NAME, TYPE_NAME, dtype=ta_type)

        def write_both():
            s1 = dds.DynamicData(ta_type)
            s1["platform_node"] = "Platform_30"
            s1["team_name"] = "Alpha"
            writer.write(s1)
            s2 = dds.DynamicData(ta_type)
            s2["platform_node"] = "Platform_31"
            s2["team_name"] = "Bravo"
            writer.write(s2)

        buckets = write_until_seen(
            write_both, reader,
            classify=lambda d: d["platform_node"], stop_key="Platform_30",
            check_alive=lambda: control_proc.is_alive() and platform_proc.is_alive())

        assert control_proc.returncode is None, \
            f"control router exited early, see {control_proc.log_path}"
        assert platform_proc.returncode is None, \
            f"platform router exited early, see {platform_proc.log_path}"

        # Both arrive (unfiltered broadcast — unlike control_command's CFT).
        assert buckets.get("Platform_30"), (
            "Platform_30 sample never arrived; router logs: "
            f"control={control_proc.log_path} platform={platform_proc.log_path}")
        assert buckets.get("Platform_31"), (
            "Platform_31 sample never arrived (route should be unfiltered); router "
            f"logs: control={control_proc.log_path} platform={platform_proc.log_path}")
    finally:
        control_app.close()
        platform_app.close()
