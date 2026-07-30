"""End-to-end: platform_init_status route, real router_main pair, real WAN hop.

platform app (platform_lan) --[router platform-role: platform_lan -> platform_wan]-->
  WAN --[router control-role: control_wan -> control_lan]--> control app (control_lan)

See docs/cpp_router/design-decisions.md D50 for what this fixture does and does not
cover (single type, qos: "").

Absorbs the end-to-end forwarding coverage of the retired C++ test/test_route_forward.cxx
(D52 test-suite policy: integration tests are Python). That test forwarded one route's
sample source->sink and checked ROUTE_ENABLED status in-process; here the same forward is
exercised through the real router_main pair. (The one thing test_route_forward.cxx also
touched — the *generated*-type forwarding lane, RouterStatus — is a library detail
router_main doesn't use; it runs DynamicData-only. That lane stays covered by the C++
unit-level type test test_admin_types.)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from util.dds_probe import Probe, write_until_seen  # noqa: E402

TYPE_NAME = "platform_init_status"
TOPIC_NAME = "PlatformInitStatus"
THIS_NODE = "Platform_30"


def test_status_reaches_control(router_pair, unique_domains):
    control_proc, platform_proc, _ = router_pair(
        "e2e_platform_status.yaml", unique_domains)

    platform_app = Probe(unique_domains["platform_lan"])
    control_app = Probe(unique_domains["control_lan"])
    try:
        writer = platform_app.writer(TOPIC_NAME, TYPE_NAME)
        reader = control_app.reader(TOPIC_NAME, TYPE_NAME)

        def write_status():
            writer.write(platform_app.sample(
                TYPE_NAME, **{"source": THIS_NODE}))

        buckets = write_until_seen(
            write_status, reader,
            classify=lambda d: d["source"], stop_key=THIS_NODE, grace_s=0.0,
            check_alive=lambda: control_proc.is_alive() and platform_proc.is_alive())
        seen = buckets.get(THIS_NODE, [])

        assert control_proc.returncode is None, \
            f"control router exited early, see {control_proc.log_path}"
        assert platform_proc.returncode is None, \
            f"platform router exited early, see {platform_proc.log_path}"

        assert seen, (
            f"PlatformInitStatus from {THIS_NODE} never arrived at the control "
            f"reader; router logs: control={control_proc.log_path} "
            f"platform={platform_proc.log_path}")
    finally:
        platform_app.close()
        control_app.close()
