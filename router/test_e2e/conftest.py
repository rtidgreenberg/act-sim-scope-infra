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

# The production qos_libraries: files (harness_v2/qos/*.xml) are templated with
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

# --- Domain isolation (review 2026-08-11, H3) ---------------------------------------
#
# The e2e suite must never allocate a domain the LIVE system uses, or a test run and a
# `harness_v2/scripts/run_mesh.sh` mesh on this VM discover each other. The live band is:
#
#   20        control_lan            (control-platform.yaml, run_mesh.sh --domain 20)
#   30..99    platform_lan           (one per platform; start_platform_sim.sh's ID range)
#   200       control_wan/platform_wan (the shared WAN)
#
# The allocator used to start at 40, i.e. squarely inside the platform_lan band — every
# test past the first few handed itself a live platform's domain.
#
# The band has a hard upper bound. RTPS maps a domain to a UDP port as
# PB + DG*D (= 7400 + 250*D), which exceeds 65535 at D = 233 and then WRAPS mod 65536 —
# landing anywhere, including the privileged range. Measured on this install:
#
#   domain  232 -> port 65400        ok (the last domain that fits)
#   domain  233 -> port 65650 -> 114  wraps into <1024
#   domain 1023 -> port 263150 -> 1006 wraps into <1024
#
# That last line is not hypothetical: a first attempt at this fix used base 1000 and the
# suite failed with "RTIOsapiSocket_bindWithIP: OS bind() failure ... Port 1006 with error
# 0XD (Permission denied)" on Domain=1023, plus a wire-frugality test whose WAN port range
# computed as [268900, 269149]. Note the repo guardrail in copilot-instructions.md quotes
# "~5900-6000" as the ceiling; that is one observed instance of the wrap landing low, not
# the limit. The limit is 232.
#
# So the usable non-live windows are 0-19, 21-29, 101-199 and 201-232. 101-199 is the only
# one with real room: 99 ids = 33 triples, against 27 collected tests today.
E2E_DOMAIN_BASE = 101
E2E_DOMAIN_MAX = 199

# The literal domains `router/config/control-platform.yaml` declares. That file is
# deliberately runnable "as literally authored" (router/README.md), so it carries no
# __DOMAIN_*__ placeholders — render_config() rewrites these values instead, mapping each
# onto the test's own unique_domains triple. Keyed by the unique_domains role name.
PRODUCTION_DOMAINS = {"control_lan": 20, "wan": 200, "platform_lan": 30}


@pytest.fixture(scope="session")
def admin_types_xml():
    """Return the committed generated XML type description (ActTypes.xml) containing
    all system types — application payload types + router admin types. Generated from
    harness_v2/datamodel/ActTypes.idl via rtiddsgen -convertToXml."""
    xml = REPO_ROOT / "harness_v2" / "datamodel" / "gen" / "ActTypes.xml"
    if not xml.exists():
        pytest.skip(f"ActTypes.xml not found at {xml} — run: "
                    f"rtiddsgen -convertToXml -d harness_v2/datamodel/gen "
                    f"harness_v2/datamodel/ActTypes.idl")
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
    concurrent runs are unlikely to collide, and allocated from a band the live system
    never uses (see E2E_DOMAIN_BASE above — H3). Basic domain-id isolation only for now —
    DomainParticipant-level PARTITION isolation (stronger: mismatched partitions block
    discovery entirely) is deferred to a later pass, once the router_main wiring + e2e
    suite are confirmed working end-to-end (docs/cpp_router/design-decisions.md D50)."""
    base = E2E_DOMAIN_BASE + next(_domain_counter) * 3
    assert base + 2 <= E2E_DOMAIN_MAX, (
        f"e2e domain allocator reached {base}, past the {E2E_DOMAIN_MAX} ceiling of the "
        f"{E2E_DOMAIN_BASE}-{E2E_DOMAIN_MAX} band. Do NOT just raise the ceiling: domain "
        f"ids above 232 make 7400+250*D exceed 65535, wrap mod 65536, and can land on a "
        f"privileged port (bind fails with 'Permission denied'). Use 201-232, or free up "
        f"ids, or move to PARTITION-based isolation (D50).")
    return {"control_lan": base, "wan": base + 1, "platform_lan": base + 2}


@pytest.fixture
def e2e_tmp_dir(tmp_path, request):
    # tmp_path is already under the system temp dir (local fs, never the repo/share);
    # this just gives each test a clearly-named subdir for its rendered configs/logs.
    d = TMP_ROOT / re.sub(r"[^A-Za-z0-9_.-]", "_", request.node.name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def render_config(fixture_name, domains, e2e_tmp_dir):
    """Copy router/config/<fixture_name> into e2e_tmp_dir with its domains rewritten to
    this test's `domains` triple, and return the rendered path.

    Two shapes are supported, and one of them MUST apply — a config that neither carries
    placeholders nor declares the production domains would otherwise be rendered
    unchanged and silently run on whatever domains it happened to name (H3):

    1. e2e fixtures carry __DOMAIN_CONTROL_LAN__ / __DOMAIN_WAN__ /
       __DOMAIN_PLATFORM_LAN__ placeholders.
    2. control-platform.yaml is deliberately runnable as literally authored, so it has no
       placeholders — its literal live domains (PRODUCTION_DOMAINS) are rewritten instead.
       Matched with a trailing \\b so `domain: 20` never eats the `20` of `domain: 200`.
    """
    src = CONFIG_DIR / fixture_name
    text = src.read_text()

    placeholders = {
        "__DOMAIN_CONTROL_LAN__": domains["control_lan"],
        "__DOMAIN_WAN__": domains["wan"],
        "__DOMAIN_PLATFORM_LAN__": domains["platform_lan"],
    }
    substituted = 0
    for token, value in placeholders.items():
        if token in text:
            text = text.replace(token, str(value))
            substituted += 1

    if substituted == 0:
        # Production-config path: rewrite the literal domains. Longest-first plus \b so
        # the 20 -> N mapping cannot corrupt 200.
        for role, live in sorted(PRODUCTION_DOMAINS.items(),
                                 key=lambda kv: -kv[1]):
            pattern = re.compile(r"(domain:\s*)%d\b" % live)
            text, n = pattern.subn(r"\g<1>%d" % domains[role], text)
            assert n > 0, (
                f"{fixture_name} has no __DOMAIN_*__ placeholders and no literal "
                f"'domain: {live}' for {role} — it cannot be domain-isolated, so it "
                f"would run on live domains. Add placeholders, or update "
                f"PRODUCTION_DOMAINS in conftest.py to match the config.")
            substituted += n
        assert not re.search(r"domain:\s*(20|30|200)\b", text), (
            f"{fixture_name} still names a live domain after rendering: "
            f"{re.findall(r'domain:.*', text)}")

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

    def kill(self):
        """SIGKILL — the crash under test for presence (Phase 8): no graceful DDS
        cleanup, so peers must detect death via RouterHealth liveliness (D75). Safe on
        this rig: router participants are UDPv4-only (no /dev/shm segments to leak)."""
        if self._popen.poll() is None:
            self._popen.kill()
        self._popen.wait()

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
        # Identity overrides: both processes load the same file, and RouterHealth is
        # keyed by the "<node>/<router>" name (D79) — sharing the file's node.name
        # would merge the two routers into one presence instance.
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
