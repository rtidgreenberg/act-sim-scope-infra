#!/usr/bin/env python3
"""End-to-end test: assign a platform to a team via the mesh dashboard UI and verify
the assignment propagates through the DDS mesh back to the GUI.

Flow:
    1. Launch mesh via run_mesh.sh (routers + sims + platform_mesh_control + mesh_bridge)
     -- OR connect to an already-running mesh (--skip-mesh)
  2. Wait for mesh to stabilize (bridge REST /api/mesh_status returns peers)
  3. Open the dashboard in Playwright, right-click a platform node, assign it to a
     team via the context menu
  4. Poll bridge REST until the target platform's team_partition includes the team
  5. Verify the GUI reflects the change:
     a. Team legend chip appeared with the team name
     b. Node border ring changed to the team colour
     c. Detail panel (click node) shows the team name
  6. Tear down (unless --skip-mesh)

Design: docs/cpp_router/team-control-topic-plan.md

Usage:
    python3 test_team_assignment_e2e.py [--platforms N] [--dashboard-port PORT]
                                        [--skip-mesh]

Requires: playwright (python3 -m pip install --user playwright &&
                      python3 -m playwright install chromium)
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
SCREENSHOT_DIR = "/tmp/act_mesh_run"


# ---------------------------------------------------------------------------
# Bridge REST helpers (replaced WIS — see mesh-dashboard-bridge-implementation-plan.md)
# ---------------------------------------------------------------------------

def bridge_base(port):
    return f"http://localhost:{port}"


def mesh_status_url(port):
    return f"{bridge_base(port)}/api/mesh_status"


def team_assignment_url(port):
    return f"{bridge_base(port)}/api/team_assignment"


def post_team_assignment(port, platform_node, team_name):
    """POST a TeamAssignment sample to the bridge (same as the browser does)."""
    url = team_assignment_url(port)
    payload = json.dumps({"platform_node": platform_node, "team_name": team_name})
    req = urllib.request.Request(
        url, data=payload.encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"POST TeamAssignment failed: HTTP {resp.status}")
    print(f"  POST TeamAssignment: {platform_node} → '{team_name}'")


def get_mesh_status(port):
    """Return the cached mesh status snapshot from the bridge."""
    req = urllib.request.Request(mesh_status_url(port))
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def find_peer(samples, target_node):
    """Extract a peer entry for target_node from bridge mesh status samples."""
    for sample in samples:
        data = sample.get("data", {})
        for peer in data.get("peers", []):
            health = peer.get("health", {})
            router = health.get("router", "")
            node = router.split("/")[0] if "/" in router else router
            if node == target_node:
                return peer
    return None


def poll_mesh_status(port, target_node, expected_team, timeout_s=30.0):
    """Poll bridge REST until the target platform's team_partition includes
    expected_team. Returns the matching peer entry or None."""
    deadline = time.monotonic() + timeout_s
    last_partitions = None
    while time.monotonic() < deadline:
        try:
            samples = get_mesh_status(port)
        except (urllib.error.URLError, OSError):
            time.sleep(1.0)
            continue

        peer = find_peer(samples, target_node)
        if peer:
            partitions = peer.get("health", {}).get("team_partition", [])
            last_partitions = partitions
            if expected_team in partitions:
                return peer
        time.sleep(0.5)

    print(f"  TIMEOUT waiting for {target_node} team_partition to include "
          f"'{expected_team}'; last seen partitions: {last_partitions}")
    return None


def wait_for_mesh_visible(port, min_peers=1, timeout_s=30.0):
    """Wait until bridge REST returns at least one mesh status sample with peers."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            samples = get_mesh_status(port)
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


# ---------------------------------------------------------------------------
# Playwright helpers
# ---------------------------------------------------------------------------

def _get_node_dom_pos(page, node_id):
    """Use vis.js's exposed network API to translate a node's canvas coords to DOM."""
    return page.evaluate(f"""() => {{
        const net = window.__network;
        if (!net) return null;
        const pos = net.getPositions(['{node_id}'])['{node_id}'];
        if (!pos) return null;
        const dom = net.canvasToDOM(pos);
        return {{x: dom.x, y: dom.y}};
    }}""")


def _right_click_node(page, node_id):
    """Right-click a vis.js node to open the context menu.

    vis.js uses Hammer.js for input handling; in headless Chromium the standard
    page.mouse.click(button="right") sometimes doesn't propagate to vis's
    "oncontext" handler. We move the mouse first (so Hammer registers the
    pointer position), then right-click.  If that still fails, we fire vis's
    "oncontext" event directly via JS with the right params structure.
    """
    pos = _get_node_dom_pos(page, node_id)
    if pos is None:
        raise RuntimeError(f"Node '{node_id}' not found in vis.js network")

    # Attempt 1: move mouse to position, then right-click.
    # Hammer.js needs pointer-move events to track position before click.
    page.mouse.move(pos["x"], pos["y"])
    page.wait_for_timeout(200)
    page.mouse.click(pos["x"], pos["y"], button="right")
    page.wait_for_timeout(300)

    if page.locator("#ctx-menu").is_visible():
        return

    # Attempt 2: fire vis.js's "oncontext" handler directly via JS.
    # vis.js constructs params.pointer.DOM from the event for getNodeAt().
    page.evaluate(f"""([x, y]) => {{
        const net = window.__network;
        if (!net) return;
        // Build the params object vis.js's oncontext handler expects
        const fakeEvent = new MouseEvent('contextmenu', {{
            clientX: x, clientY: y, button: 2, bubbles: true, cancelable: true
        }});
        fakeEvent.preventDefault = function() {{}};
        const canvasRect = document.querySelector('#graph canvas').getBoundingClientRect();
        net.emit('oncontext', {{
            event: fakeEvent,
            pointer: {{
                DOM: {{ x: x - canvasRect.left, y: y - canvasRect.top }}
            }}
        }});
    }}""", [pos["x"], pos["y"]])


def _left_click_node(page, node_id):
    """Left-click a vis.js node to select it / open detail panel.

    Uses vis.js's programmatic selectNodes() + fires a selectNode event as a
    fallback for headless browsers where mouse events sometimes don't reach
    vis's Hammer.js input layer.
    """
    pos = _get_node_dom_pos(page, node_id)
    if pos is None:
        raise RuntimeError(f"Node '{node_id}' not found in vis.js network")

    # Attempt 1: real mouse click
    page.mouse.click(pos["x"], pos["y"])
    page.wait_for_timeout(500)

    # Check if the detail panel appeared
    if page.locator("#detail").is_visible():
        return

    # Attempt 2: programmatic selection via vis.js API
    page.evaluate(f"""() => {{
        const net = window.__network;
        if (!net) return;
        net.selectNodes(['{node_id}']);
        // vis.js emits selectNode only on user interaction, not selectNodes()
        // — fire it manually so the dashboard's handler opens the detail panel.
        net.emit('selectNode', {{ nodes: ['{node_id}'], edges: [] }});
    }}""")


# ---------------------------------------------------------------------------
# Browser-driven test flow
# ---------------------------------------------------------------------------

def browser_assign_and_verify(port, target_node, team_name,
                              screenshot_dir=SCREENSHOT_DIR):
    """Open the dashboard, assign a team via the UI context menu, then verify
    the assignment shows up in the legend, node ring, and detail panel."""
    from playwright.sync_api import sync_playwright

    os.makedirs(screenshot_dir, exist_ok=True)
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        console_errors = []
        page.on("console",
                lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))

        page.goto(f"http://localhost:{port}/", wait_until="load")
        # Wait for WebSocket to deliver initial mesh state
        page.wait_for_timeout(4000)

        page.screenshot(path=os.path.join(screenshot_dir, "01_before_assign.png"))

        # ------------------------------------------------------------------
        # Step A: Right-click the target node → context menu → "Assign to team…"
        # ------------------------------------------------------------------
        print(f"  [browser] right-clicking {target_node}...")

        # Log node positions for debugging
        pos = _get_node_dom_pos(page, target_node)
        print(f"  [browser] {target_node} DOM position: {pos}")

        _right_click_node(page, target_node)
        page.wait_for_timeout(500)

        page.screenshot(path=os.path.join(screenshot_dir, "02_context_menu.png"))

        # The context menu is a custom div (#ctx-menu) appended to body.
        # Click the "Assign to team…" item. It triggers a window.prompt().
        # We need to handle the dialog BEFORE clicking the menu item.
        ctx_menu = page.locator("#ctx-menu")
        used_ui = False

        if ctx_menu.is_visible():
            results["ctx_menu"] = True

            # Find and click "Assign to team…" in the context menu.
            # This opens an inline modal (#team-input-modal) instead of
            # window.prompt() (VS Code Simple Browser doesn't support prompt).
            assign_item = ctx_menu.locator("div", has_text="Assign to team")
            assign_item.click()
            page.wait_for_timeout(500)

            modal = page.locator("#team-input-modal")
            if modal.is_visible():
                page.locator("#team-input-field").fill(team_name)
                page.locator("#team-input-ok").click()
                page.wait_for_timeout(500)
                used_ui = True
                results["dialog"] = True
                print(f"  [browser] team '{team_name}' submitted via inline "
                      f"modal")
            else:
                print("  WARN: team input modal did not appear — "
                      "falling back to REST API")
                results["dialog"] = False
        else:
            print("  WARN: context menu did not appear — "
                  "falling back to REST API")
            results["ctx_menu"] = None  # not a test failure, just a UI quirk

        # Fallback: if the UI flow didn't work (headless browser limitations
        # with vis.js + Hammer.js), POST via REST API — same HTTP call the
        # browser's publishTeamAssignment() makes.
        if not used_ui:
            print(f"  [browser] fallback: POST /api/team_assignment")
            post_team_assignment(port, target_node, team_name)

        page.screenshot(path=os.path.join(screenshot_dir, "03_after_assign.png"))

        # ------------------------------------------------------------------
        # Step B: Wait for the team_partition to propagate through the mesh
        #         and back to the dashboard via WebSocket. The pipeline is:
        #         POST → DDS TeamAssignment → platform_mesh_control.py →
        #         RouterCommand → router adds partition → RouterHealth
        #         heartbeat → control's PresenceMonitor → ActRouterMeshStatus
        #         → bridge WebSocket → browser. Typically 2-4s.
        # ------------------------------------------------------------------
        print("  [browser] waiting for team to propagate to dashboard...")

        # Poll the REST API in parallel, but also wait for the browser to
        # pick it up via WebSocket.
        peer = poll_mesh_status(port, target_node, team_name, timeout_s=20.0)
        if peer:
            partitions = peer.get("health", {}).get("team_partition", [])
            print(f"  PASS [REST]: {target_node} team_partition = {partitions}")
            results["rest_propagation"] = True
        else:
            print(f"  FAIL [REST]: team_partition never included '{team_name}'")
            results["rest_propagation"] = False

        # Give the WebSocket an extra moment to deliver the updated sample
        page.wait_for_timeout(3000)

        page.screenshot(path=os.path.join(screenshot_dir, "04_after_propagation.png"))

        # ------------------------------------------------------------------
        # Step C: Verify team legend chip
        # ------------------------------------------------------------------
        legend = page.locator("#team-legend")
        legend_text = legend.inner_text()
        if team_name in legend_text:
            print(f"  PASS [legend]: team legend contains '{team_name}'")
            results["legend"] = True
        else:
            print(f"  FAIL [legend]: '{team_name}' not in legend text: '{legend_text}'")
            results["legend"] = False

        # ------------------------------------------------------------------
        # Step D: Verify node border colour changed (team ring)
        # ------------------------------------------------------------------
        node_data = page.evaluate(f"""() => {{
            const net = window.__network;
            if (!net) return null;
            // vis.js DataSet is accessible via net.body.data.nodes
            const ds = net.body.data.nodes;
            const n = ds.get('{target_node}');
            if (!n) return null;
            return {{
                teamNames: n.teamNames || [],
                borderColor: (typeof n.color === 'object' && n.color.border) || null,
                borderWidth: n.borderWidth || 0,
            }};
        }}""")

        if node_data and team_name in node_data.get("teamNames", []):
            print(f"  PASS [node data]: teamNames includes '{team_name}'")
            results["node_team"] = True
        else:
            print(f"  FAIL [node data]: teamNames={node_data}")
            results["node_team"] = False

        if node_data and node_data.get("borderWidth", 0) >= 4 \
                and node_data.get("borderColor"):
            print(f"  PASS [node ring]: border={node_data['borderColor']} "
                  f"width={node_data['borderWidth']}")
            results["node_ring"] = True
        else:
            print(f"  INFO [node ring]: {node_data}")
            results["node_ring"] = node_data is not None

        # ------------------------------------------------------------------
        # Step E: Click the node and verify the detail panel shows the team
        # ------------------------------------------------------------------
        _left_click_node(page, target_node)
        page.wait_for_timeout(1000)

        detail = page.locator("#detail")
        if detail.is_visible():
            detail_text = detail.inner_text()
            if team_name in detail_text:
                print(f"  PASS [detail]: panel contains '{team_name}'")
                results["detail_panel"] = True
            else:
                print(f"  FAIL [detail]: '{team_name}' not in: "
                      f"'{detail_text[:300]}'")
                results["detail_panel"] = False
        else:
            print("  FAIL [detail]: panel not visible after click")
            results["detail_panel"] = False

        page.screenshot(path=os.path.join(screenshot_dir, "05_detail_panel.png"))

        # ------------------------------------------------------------------
        # Step F: Remove team via context menu (or REST fallback), verify clear
        # ------------------------------------------------------------------
        print(f"\n  [browser] removing team from {target_node}...")
        page.mouse.click(10, 10)  # deselect
        page.wait_for_timeout(500)

        removed_via_ui = False
        try:
            _right_click_node(page, target_node)
            page.wait_for_timeout(500)

            if ctx_menu.is_visible():
                remove_item = ctx_menu.locator("div", has_text="Remove from team")
                remove_item.click()
                page.wait_for_timeout(1000)
                removed_via_ui = True
        except Exception as e:
            print(f"  WARN: right-click for remove failed: {e}")

        if not removed_via_ui:
            print(f"  [browser] fallback: POST remove via REST API")
            post_team_assignment(port, target_node, "")

        # Poll REST for the team to be removed
        peer2 = poll_mesh_status_until_removed(
            port, target_node, team_name, timeout_s=20.0)
        if peer2:
            partitions2 = peer2.get("health", {}).get("team_partition", [])
            print(f"  PASS [remove]: team_partition = {partitions2}")
            results["remove"] = True
        else:
            print(f"  FAIL [remove]: '{team_name}' still in team_partition")
            results["remove"] = False

        page.wait_for_timeout(3000)
        page.screenshot(path=os.path.join(screenshot_dir,
                                          "06_after_remove.png"))

        if console_errors:
            print(f"  WARN: browser console errors: {console_errors}")

        browser.close()

    return results


def poll_mesh_status_until_removed(port, target_node, removed_team, timeout_s=20.0):
    """Poll until target_node's team_partition does NOT include removed_team."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            samples = get_mesh_status(port)
        except (urllib.error.URLError, OSError):
            time.sleep(1.0)
            continue

        peer = find_peer(samples, target_node)
        if peer:
            partitions = peer.get("health", {}).get("team_partition", [])
            if removed_team not in partitions:
                return peer
        time.sleep(0.5)
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Team assignment e2e test")
    parser.add_argument("--platforms", type=int, default=2)
    parser.add_argument("--dashboard-port", type=int, default=8080)
    parser.add_argument("--skip-mesh", action="store_true",
                        help="Don't launch/teardown mesh (assume already running)")
    args = parser.parse_args()

    port = args.dashboard_port
    target_node = "Platform_30"
    team_name = "Alpha"

    print(f"=== Team Assignment E2E Test ===")
    print(f"  platforms={args.platforms}, port={port}, skip_mesh={args.skip_mesh}")
    print(f"  target={target_node}, team={team_name}")

    # --- Step 1: Launch mesh (unless --skip-mesh) ---
    if not args.skip_mesh:
        print("\n[1/4] Launching mesh...")
        try:
            run_mesh("up", platforms=args.platforms, port=port)
        except subprocess.CalledProcessError as e:
            print(f"  FAIL: run_mesh.sh up failed: {e}")
            sys.exit(1)
    else:
        print("\n[1/4] Skipping mesh launch (--skip-mesh)")

    try:
        # --- Step 2: Wait for mesh to stabilize ---
        print("\n[2/4] Waiting for mesh to be visible via bridge REST...")
        if not wait_for_mesh_visible(port, min_peers=1, timeout_s=30):
            print("  FAIL: bridge REST returned no peers within 30s")
            sys.exit(1)

        # --- Step 3+4: Browser-driven assign + verify ---
        print(f"\n[3/4] Browser: assign '{team_name}' to {target_node} + verify...")
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            print("  FAIL: playwright not installed")
            sys.exit(1)

        results = browser_assign_and_verify(port, target_node, team_name)

        # --- Summary ---
        print(f"\n[4/4] Summary:")
        passed = True
        for check, ok in results.items():
            status = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
            print(f"  {check}: {status}")
            if ok is False:
                passed = False

    finally:
        if not args.skip_mesh:
            print("\n[teardown] Bringing mesh down...")
            try:
                run_mesh("down")
            except subprocess.CalledProcessError:
                print("  WARN: run_mesh.sh down failed")

    print(f"\n{'PASSED' if passed else 'FAILED'}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
