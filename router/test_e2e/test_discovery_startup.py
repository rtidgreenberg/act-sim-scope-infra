"""Regression test for the disabled-startup discovery fix (D52).

Two `router_main` processes launched back-to-back must reliably discover each other's
participant. Before D52, participants were enabled at construction — i.e. *before* the
builtin-reader ReadConditions were attached and the AsyncWaitSet was started. A peer's
SPDP announcement arriving in that enable -> aws.start() window set a ReadCondition's
trigger before the AWS was dispatching, and because the AWS's handler dispatch is
edge-triggered, a condition already true at start() that never re-transitioned was never
serviced. One side would then show ZERO `participant_router_tagged` lines for the whole
run (~50%+ failure across repeated runs; see D51 in docs/cpp_router/design-decisions.md).

This test launches the pair on fresh domains and asserts BOTH sides log mutual
participant discovery within a TIGHT bound — comfortably above SEDP + route-build, far
below Connext's 30s periodic SPDP re-announcement that the pre-D52 flake used to hide
behind. It is parametrized over several iterations to expose the race if it regresses.

It deliberately does NOT use the `router_pair` fixture: that fixture's
`wait_for_mutual_discovery` mitigation (35s budget) would mask exactly the failure this
test exists to catch. Here discovery must be *prompt*, not merely eventual.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import render_config, start_router, wait_for_mutual_discovery  # noqa: E402

import pytest  # noqa: E402

# Healthy discovery completes in ~1s. This budget is generous enough to absorb ordinary
# subprocess-spawn jitter yet well under the 30s SPDP retry, so a stranded initial-burst
# (the pre-D52 bug) can no longer pass by hiding behind the periodic re-announcement.
DISCOVERY_BUDGET_S = 10.0
ITERATIONS = 6


@pytest.mark.parametrize("iteration", range(ITERATIONS))
def test_mutual_participant_discovery_is_prompt(
        router_binary, e2e_tmp_dir, unique_domains, iteration):
    config_path = render_config("e2e_platform_status.yaml", unique_domains, e2e_tmp_dir)

    platform_proc = start_router(
        router_binary, config_path, "platform", e2e_tmp_dir,
        admin_participant="platform_lan")
    control_proc = start_router(
        router_binary, config_path, "control", e2e_tmp_dir,
        node_name="Control_20", name="e2e-control-role",
        admin_participant="control_lan")
    try:
        start = time.monotonic()
        control_ok, platform_ok = wait_for_mutual_discovery(
            control_proc, platform_proc, timeout_s=DISCOVERY_BUDGET_S, poll_s=0.1)
        elapsed = time.monotonic() - start

        assert control_proc.is_alive() and platform_proc.is_alive(), (
            f"a router process crashed during discovery (iteration {iteration}): "
            f"control rc={control_proc.returncode} platform rc={platform_proc.returncode}; "
            f"logs: control={control_proc.log_path} platform={platform_proc.log_path}")

        assert control_ok and platform_ok, (
            f"mutual participant discovery did not complete within {DISCOVERY_BUDGET_S}s "
            f"(iteration {iteration}, control saw platform: {control_ok}, "
            f"platform saw control: {platform_ok}) — the D52 disabled-startup race may "
            f"have regressed; logs: control={control_proc.log_path} "
            f"platform={platform_proc.log_path}")

        print(f"iteration {iteration}: mutual discovery in {elapsed:.2f}s")
    finally:
        control_proc.stop()
        platform_proc.stop()
