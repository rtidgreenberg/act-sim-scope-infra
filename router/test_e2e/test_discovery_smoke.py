"""End-to-end discovery smoke: an observer participant sees a real router_main's tagged
participant and its RouterStatus publication via builtin discovery.

Python port of the former C++ router/test/test_discovery_smoke.cxx. The C++ version was a
synthetic self-check (it created its own tagged participant + RouterStatus reader/writer
and observed them in-process). This exercises the same builtin-discovery facts the
controller consumes, but against the *real* router_main binary: the observer confirms it
discovers (1) the router's participant carrying the act.router=<node>/<router> user-data
tag (D15) and (2) the router's ActRouterStatus / RouterStatus DataWriter (D26
DdsStatusPublisher), owned by that same participant.

router_main publishes RouterStatus write-only (the command reader is Phase 6), so unlike
the C++ smoke there is no RouterStatus subscription to observe — the publication side is
the meaningful assertion here.

Run from the repo root (see router/test_e2e/README.md).
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import render_config, start_router  # noqa: E402
from util.dds_probe import Probe  # noqa: E402

# D74 identity: EntityName name="<node>/<router>", role_name="act.router" — the router
# participant is detected by the role sentinel and named by the node/router pair.
EXPECTED_NAME_PREFIX = "Platform_30/"
ROUTER_ROLE = "act.router"
STATUS_TOPIC = "ActRouterStatus"
STATUS_TYPE = "RouterStatus"
TIMEOUT_S = 15.0
POLL_S = 0.2


def test_observer_discovers_router_participant_and_status_writer(
        router_binary, e2e_tmp_dir, unique_domains):
    config_path = render_config("e2e_platform_status.yaml", unique_domains, e2e_tmp_dir)

    # A single platform-role router; its admin participant ("platform_lan") lives on the
    # platform_lan domain and owns the ActRouterStatus writer.
    router = start_router(
        router_binary, config_path, "platform", e2e_tmp_dir,
        admin_participant="platform_lan")

    observer = Probe(unique_domains["platform_lan"])
    try:
        tagged_key = None
        status_writer = None
        deadline = time.monotonic() + TIMEOUT_S
        while time.monotonic() < deadline and (tagged_key is None or status_writer is None):
            assert router.is_alive(), (
                f"router exited early (rc={router.returncode}); log: {router.log_path}")

            if tagged_key is None:
                for key, (name, role) in observer.discovered_participant_names().items():
                    if role == ROUTER_ROLE and name.startswith(EXPECTED_NAME_PREFIX):
                        tagged_key = key
                        break

            for pub in observer.discovered_publications():
                if pub["topic"] == STATUS_TOPIC and pub["type"] == STATUS_TYPE:
                    status_writer = pub
                    break

            if tagged_key is not None and status_writer is not None:
                break
            time.sleep(POLL_S)

        assert tagged_key is not None, (
            f"observer never discovered a role={ROUTER_ROLE!r} participant named "
            f"{EXPECTED_NAME_PREFIX!r}*; router log: {router.log_path}")
        assert status_writer is not None, (
            f"observer never discovered the {STATUS_TOPIC}/{STATUS_TYPE} writer; "
            f"router log: {router.log_path}")
        assert status_writer["participant_key"] == tagged_key, (
            f"{STATUS_TOPIC} writer is owned by {status_writer['participant_key']}, not "
            f"the tagged router participant {tagged_key} — the status writer should live "
            f"on the router's admin participant (D26)")
    finally:
        observer.close()
        router.stop()
