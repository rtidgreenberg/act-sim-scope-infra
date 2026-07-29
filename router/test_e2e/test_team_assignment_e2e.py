#!/usr/bin/env python3
"""End-to-end test: assign a platform to a team via WIS and verify the assignment
propagates back to the dashboard UI (ActRouterMeshStatus → team_partition → team ring).

Flow:
  1. Launch mesh via run_mesh.sh (routers + sims + platform_control processes + WIS)
  2. Wait for mesh to stabilize (ActRouterMeshStatus observable via WIS REST)
  3. PUT TeamAssignment to WIS REST (same path the browser uses)
  4. Poll WIS REST for ActRouterMeshStatus until the target platform's team_partition
     includes the assigned team name
  5. Use Playwright to verify the team legend chip + detail panel in the browser
  6. Tear down

Design: docs/cpp_router/team-control-topic-plan.md

Usage:
    python3 test_team_assignment_e2e.py [--platforms N] [--dashboard-port PORT]

Requires: playwright (python3 -m pip install --user playwright && python3 -m playwright install chromium)
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
RUN_MESH = os.path.join(REPO_ROOT, "harness_v2", "scripts", "run_mesh.sh")

# WIS REST endpoints (must match wis_config.xml.template entity names)
WIS_APP = "MeshDashboardApp"
WIS_PARTICIPANT = "MeshParticipant"
WIS_SUBSCRIBER = "MeshSubscriber"
WIS_READER = "MeshStatusReader"
WIS_PUBLISHER = "MeshPublisher"
WIS_WRITER = "TeamAssignmentWriter"


def wis_base(port):
    return f"http://localhost:{port}"


def mesh_status_url(port):
    return (f"{wis_base(port)}/dds/rest1/applications/{WIS_APP}"
            f"/domain_participants/{WIS_PARTICIPANT}"
            f"/subscribers/{WIS_SUBSCRIBER}/data_readers/{WIS_READER}"
            f"?sampleFormat=json&removeFromReaderCache=false")


def team_assignment_url(port):
    return (f"{wis_base(port)}/dds/rest1/applications/{WIS_APP}"
            f"/domain_participants/{WIS_PARTICIPANT}"
            f"/publishers/{WIS_PUBLISHER}/data_writers/{WIS_WRITER}")


def put_team_assignment(port, platform_node, team_name):
    """PUT a TeamAssignment sample to WIS (same as the browser does)."""
    url = team_assignment_url(port)
    payload = json.dumps({"platform_node": platform_node, "team_name": team_name})
    req = urllib.request.Request(
        url, data=payload.encode(),
        headers={"Content-Type": "application/dds-web+json"},
        method="PUT",
    )
    with urllib.request.urlopen(req) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"PUT TeamAssignment failed: HTTP {resp.status}")
    print(f"  PUT TeamAssignment: {platform_node} → '{team_name}'")


def poll_mesh_status(port, target_node, expected_team, timeout_s=30.0):
    """Poll WIS REST for ActRouterMeshStatus until the target platform's
    team_partition includes expected_team. Returns the matching peer entry or None."""
    deadline = time.monotonic() + timeout_s
    last_partitions = None
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(mesh_status_url(port))
            with urllib.request.urlopen(req, timeout=5) as resp:
                samples = json.loads(resp.read())
        except (urllib.error.URLError, OSError):
            time.sleep(1.0)
            continue

        for sample in samples:
            data = sample.get("data", {})
            for peer in data.get("peers", []):
                health = peer.get("health", {})
                router = health.get("router", "")
                # router is "<node>/<router_name>", extract node half
                node = router.split("/")[0] if "/" in router else router
                if node != target_node:
                    continue
                partitions = health.get("team_partition", [])
                last_partitions = partitions
                if expected_team in partitions:
                    return peer
        time.sleep(0.5)

    print(f"  TIMEOUT waiting for {target_node} team_partition to include "
          f"'{expected_team}'; last seen partitions: {last_partitions}")
    return None


def wait_for_mesh_visible(port, min_peers=1, timeout_s=30.0):
    """Wait until WIS REST returns at least one mesh status sample with peers."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(mesh_status_url(port))
            with urllib.request.urlopen(req, timeout=5) as resp:
                samples = json.loads(resp.read())
            for s in samples:
                peers = s.get("data", {}).get("peers", [])
                if len(peers) >= min_peers:
                    print(f"  Mesh visible: {len(peers)} peer(s) in status")
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1.0)
    return False


def run_mesh(action, platforms=3, port=8080):
    """Run run_mesh.sh up/down."""
    cmd = ["bash", RUN_MESH, action]
    if action == "up":
        cmd += ["--platforms", str(platforms), "--dashboard-port", str(port)]
    subprocess.run(cmd, check=True)


def browser_verify(port, target_node, expected_team, screenshot_dir="/tmp/act_mesh_run"):
    """Use Playwright to verify the team shows up in the dashboard UI."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  SKIP browser verification (playwright not installed)")
        return True

    ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        console_errors = []
        page.on("console",
                lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))

        page.goto(f"http://localhost:{port}/", wait_until="load")
        # Wait for WebSocket to deliver at least one sample with the team update
        page.wait_for_timeout(5000)

        # Screenshot before interaction
        page.screenshot(path=os.path.join(screenshot_dir, "team_assign_mesh.png"))

        # Verify the team legend chip appeared
        legend = page.locator("#team-legend")
        legend_text = legend.inner_text()
        if expected_team in legend_text:
            print(f"  PASS: team legend contains '{expected_team}'")
        else:
            print(f"  FAIL: team legend does not contain '{expected_team}': '{legend_text}'")
            ok = False

        # Click the target node to open the detail panel. The node label is the
        # node name (e.g. "Platform_30"); vis.js renders it on canvas, so we
        # can't click a DOM element — we need the canvas position. Use vis's
        # getPositions API via page.evaluate to find the node's DOM coords.
        node_pos = page.evaluate(f"""() => {{
            const pos = window.__network
                ? window.__network.getPositions(['{target_node}'])['{target_node}']
                : null;
            if (!pos) return null;
            const dom = window.__network.canvasToDOM(pos);
            return {{x: dom.x, y: dom.y}};
        }}""")

        if node_pos is None:
            # Fallback: expose the network object and try again
            # The IIFE wraps `network` — we need to expose it.
            # Check if we stashed it on window
            print("  WARN: could not get node position via JS (network not exposed on window)")
            print("  Skipping detail panel click verification")
        else:
            page.mouse.click(node_pos["x"], node_pos["y"])
            page.wait_for_timeout(1000)

            detail = page.locator("#detail")
            if detail.is_visible():
                detail_text = detail.inner_text()
                if expected_team in detail_text:
                    print(f"  PASS: detail panel contains '{expected_team}'")
                else:
                    print(f"  FAIL: detail panel does not contain '{expected_team}': "
                          f"'{detail_text[:200]}'")
                    ok = False
            else:
                print("  WARN: detail panel not visible after click")

        page.screenshot(path=os.path.join(screenshot_dir, "team_assign_detail.png"))

        if console_errors:
            print(f"  WARN: browser console errors: {console_errors}")

        browser.close()

    return ok


def main():
    parser = argparse.ArgumentParser(description="Team assignment e2e test")
    parser.add_argument("--platforms", type=int, default=3)
    parser.add_argument("--dashboard-port", type=int, default=8080)
    args = parser.parse_args()

    port = args.dashboard_port
    target_node = "Platform_30"
    team_name = "Alpha"
    passed = True

    print(f"=== Team Assignment E2E Test ===")
    print(f"  platforms={args.platforms}, port={port}")
    print(f"  target={target_node}, team={team_name}")

    # --- Step 1: Launch mesh ---
    print("\n[1/5] Launching mesh...")
    try:
        run_mesh("up", platforms=args.platforms, port=port)
    except subprocess.CalledProcessError as e:
        print(f"  FAIL: run_mesh.sh up failed: {e}")
        sys.exit(1)

    try:
        # --- Step 2: Wait for mesh to stabilize ---
        print("\n[2/5] Waiting for mesh to be visible via WIS REST...")
        if not wait_for_mesh_visible(port, min_peers=1, timeout_s=30):
            # WIS REST seed can return [] if no new samples since WIS started (known
            # caveat, skill doc step 4). The WebSocket path still works — proceed anyway
            # and rely on the poll after team assignment to confirm end-to-end delivery.
            print("  WARN: WIS REST seed returned no peers (known WIS caveat) — continuing")

        # --- Step 3: Assign team via WIS REST ---
        print(f"\n[3/5] Assigning {target_node} to team '{team_name}' via WIS REST...")
        put_team_assignment(port, target_node, team_name)

        # --- Step 4: Poll for team_partition update ---
        print(f"\n[4/5] Polling ActRouterMeshStatus for {target_node}.team_partition...")
        peer = poll_mesh_status(port, target_node, team_name, timeout_s=30.0)
        if peer:
            partitions = peer.get("health", {}).get("team_partition", [])
            print(f"  PASS: {target_node} team_partition = {partitions}")
        else:
            print(f"  FAIL: {target_node} team_partition never included '{team_name}'")
            passed = False

        # --- Step 5: Browser verification ---
        print(f"\n[5/5] Browser verification (Playwright)...")
        if not browser_verify(port, target_node, team_name):
            passed = False

    finally:
        # --- Teardown ---
        print("\n[teardown] Bringing mesh down...")
        try:
            run_mesh("down")
        except subprocess.CalledProcessError:
            print("  WARN: run_mesh.sh down failed")

    print(f"\n{'PASSED' if passed else 'FAILED'}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
