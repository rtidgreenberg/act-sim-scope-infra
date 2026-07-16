"""pytest fixtures for the router Python e2e suite.

Launches the real `router_main` binary (built by CMake under router/build/) as a
subprocess pair — one control-role, one platform-role — against a trimmed e2e config
(see router/config/e2e_*.yaml and docs/cpp_router/design-decisions.md D50), then drives
DDS traffic through it from Python using util/dds_probe.py.

Repo filesystem rule (CLAUDE.md): runtime artifacts never go on the vboxsf share. All
per-test config renders and subprocess logs here go under /tmp.
"""

import itertools
import os
import re
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ROUTER_MAIN = REPO_ROOT / "router" / "build" / "router_main"
CONFIG_DIR = REPO_ROOT / "router" / "config"
TMP_ROOT = Path("/tmp/router_e2e")

os.environ.setdefault("NDDSHOME", "/home/rti/rti_connext_dds-7.7.0")
os.environ.setdefault("RTI_LICENSE_FILE", os.path.join(os.environ["NDDSHOME"], "rti_license.dat"))

# The production qos_libraries: files (harness/act/config/qos/*.xml) are templated with
# 14 env vars (peer locators + WAN tuning) and fail to parse without them. Loopback/test
# values, validated by spikes/qos_alias/ (WAN_TIMEOUT_SEC must be > the hardcoded 30s
# participant_liveliness_assert_period in wan_qos_lib.xml — D60/D65). Tests that load
# these libs (in-process via rti.connextdds, or by launching router_main against a
# config that names them) call set_wan_qos_env() BEFORE importing rti.connextdds.
WAN_QOS_ENV_DEFAULTS = {
    "CONTROL_LAN_PEER1": "127.0.0.1", "CONTROL_LAN_PEER2": "127.0.0.1",
    "CONTROL_LAN_PEER3": "127.0.0.1", "CONTROL_WAN_PEER1": "127.0.0.1",
    "PLATFORM_LAN_PEER1": "127.0.0.1", "PLATFORM_LAN_PEER2": "127.0.0.1",
    "PLATFORM_LAN_PEER3": "127.0.0.1", "PLATFORM_WAN_PEER1": "127.0.0.1",
    "WAN_HB_PERIOD_SEC": "1", "WAN_HB_RETRIES": "10", "WAN_MAX_BLOCKING_SEC": "1",
    "WAN_TIMEOUT_SEC": "100", "WAN_TTL": "1", "WAN_RECEIVE_MULTICAST": "0",
}


def set_wan_qos_env():
    for k, v in WAN_QOS_ENV_DEFAULTS.items():
        os.environ.setdefault(k, v)

_domain_counter = itertools.count()


@pytest.fixture(scope="session")
def admin_types_xml():
    """Generate an XML type description of the router admin types (RouterStatus et al.)
    from admin/RouterAdminTypes.idl via rtiddsgen -convertToXml, so the Python e2e suite
    can subscribe to ActRouterStatus as DynamicData. Generated at test time into a local
    tmp dir (the repo keeps no committed generated code — see router/CMakeLists.txt)."""
    ndds = os.environ["NDDSHOME"]
    rtiddsgen = Path(ndds) / "bin" / "rtiddsgen"
    idl = REPO_ROOT / "router" / "admin" / "RouterAdminTypes.idl"
    if not rtiddsgen.exists() or not idl.exists():
        pytest.skip(f"rtiddsgen or RouterAdminTypes.idl not found "
                    f"(rtiddsgen={rtiddsgen}, idl={idl})")
    out_dir = TMP_ROOT / "admin_types"
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(rtiddsgen), "-convertToXml", str(idl), "-d", str(out_dir)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    xml = out_dir / "RouterAdminTypes.xml"
    if not xml.exists():
        pytest.skip(f"rtiddsgen did not produce {xml}")
    return xml


@pytest.fixture(scope="session")
def router_binary():
    if not ROUTER_MAIN.exists():
        pytest.skip(f"router_main not built — run: cd router && cmake --build build "
                    f"(expected at {ROUTER_MAIN})")
    return ROUTER_MAIN


@pytest.fixture
def unique_domains():
    """Three domain ids (control_lan, wan, platform_lan), spread out per test so
    concurrent runs are unlikely to collide. Basic domain-id isolation only for now —
    DomainParticipant-level PARTITION isolation (stronger: mismatched partitions block
    discovery entirely) is deferred to a later pass, once the router_main wiring + e2e
    suite are confirmed working end-to-end (docs/cpp_router/design-decisions.md D50)."""
    base = 40 + next(_domain_counter) * 3
    return {"control_lan": base, "wan": base + 1, "platform_lan": base + 2}


@pytest.fixture
def e2e_tmp_dir(tmp_path, request):
    # tmp_path is already under the system temp dir (local fs, never the repo/share);
    # this just gives each test a clearly-named subdir for its rendered configs/logs.
    d = TMP_ROOT / re.sub(r"[^A-Za-z0-9_.-]", "_", request.node.name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def render_config(fixture_name, domains, e2e_tmp_dir):
    """Copy router/config/<fixture_name> into e2e_tmp_dir with domain placeholders
    substituted, and return the rendered path."""
    src = CONFIG_DIR / fixture_name
    text = src.read_text()
    text = text.replace("__DOMAIN_CONTROL_LAN__", str(domains["control_lan"]))
    text = text.replace("__DOMAIN_WAN__", str(domains["wan"]))
    text = text.replace("__DOMAIN_PLATFORM_LAN__", str(domains["platform_lan"]))
    out = e2e_tmp_dir / fixture_name
    out.write_text(text)
    return out


class RouterProcess:
    def __init__(self, popen, log_path):
        self._popen = popen
        self.log_path = log_path

    def stop(self, timeout=5.0):
        if self._popen.poll() is not None:
            return
        self._popen.send_signal(signal.SIGTERM)
        try:
            self._popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._popen.kill()
            self._popen.wait(timeout=timeout)

    @property
    def returncode(self):
        # Popen.returncode is only ever updated by poll()/wait()/communicate() — it
        # does NOT refresh itself just because the child exited, so this must poll()
        # to actually observe a crash instead of always reading the stale None default.
        return self._popen.poll()

    def is_alive(self):
        return self.returncode is None


def _log_contains(log_path, substring):
    try:
        return substring in log_path.read_text()
    except FileNotFoundError:
        return False


def wait_for_mutual_discovery(control_proc, platform_proc, timeout_s=35.0, poll_s=0.2):
    """Block until both router processes' logs show they've discovered each other's
    participant (DiscoveryDispatcher's "participant_router_tagged" line), or timeout_s.

    SPDP participant discovery happens independently of any data topic. Connext's
    default periodic SPDP re-announcement is 30s (validated against 7.7 docs), so a
    process that misses the other's brief initial-announcement burst — plausible given
    ordinary subprocess-spawn timing jitter — can otherwise sit for up to ~30s before
    the next retry, eating most of write_until_seen's own timeout and making the test
    body's failure look like a routing bug rather than a discovery-timing race. Waiting
    for it explicitly here, before the timed test body starts, keeps that variance out
    of the test's own assertions. Returns (control_ok, platform_ok)."""
    control_ok = platform_ok = False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not (control_ok and platform_ok):
        control_ok = control_ok or _log_contains(
            control_proc.log_path, "participant_router_tagged")
        platform_ok = platform_ok or _log_contains(
            platform_proc.log_path, "participant_router_tagged")
        if control_ok and platform_ok:
            break
        time.sleep(poll_s)
    return control_ok, platform_ok


def start_router(router_binary, config_path, role, e2e_tmp_dir,
                 node_name=None, name=None, admin_participant=None):
    log_path = e2e_tmp_dir / f"router_{role}_{os.path.basename(config_path)}.log"
    args = [str(router_binary), "--config", str(config_path), "--role", role]
    if node_name:
        args += ["--node-name", node_name]
    if name:
        args += ["--name", name]
    if admin_participant:
        args += ["--admin-participant", admin_participant]
    with open(log_path, "w") as log_file:
        popen = subprocess.Popen(
            args, cwd=str(REPO_ROOT), stdout=log_file, stderr=subprocess.STDOUT)
    return RouterProcess(popen, log_path)


@pytest.fixture
def router_pair(router_binary, e2e_tmp_dir):
    """Launches two router_main processes (control-role, platform-role) from the same
    rendered config and tears both down on teardown. Yields a function
    `start(fixture_name, domains) -> (control_proc, platform_proc, config_path)`."""
    started = []

    def start(fixture_name, domains):
        # Explicit for both roles (not "control_lan" override + implicit YAML default
        # for platform) so a copy-pasted fixture with a stale admin_participant default
        # can't silently make both processes resolve to the same admin participant.
        platform_admin_participant = "platform_lan"
        control_admin_participant = "control_lan"
        assert platform_admin_participant != control_admin_participant

        config_path = render_config(fixture_name, domains, e2e_tmp_dir)
        platform_proc = start_router(
            router_binary, config_path, "platform", e2e_tmp_dir,
            admin_participant=platform_admin_participant)
        control_proc = start_router(
            router_binary, config_path, "control", e2e_tmp_dir,
            node_name="Control_20", name="e2e-control-role",
            admin_participant=control_admin_participant)
        started.extend([platform_proc, control_proc])
        control_ok, platform_ok = wait_for_mutual_discovery(control_proc, platform_proc)
        assert control_ok and platform_ok, (
            "mutual participant discovery did not complete in time "
            f"(control saw platform: {control_ok}, platform saw control: {platform_ok}); "
            f"logs: control={control_proc.log_path} platform={platform_proc.log_path}")
        return control_proc, platform_proc, config_path

    yield start

    for proc in started:
        proc.stop()
