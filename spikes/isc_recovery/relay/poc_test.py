#!/usr/bin/env python3
"""
Phase 1 ISC-relay PoC test — the M0 go/no-go gate (roadmap.md).

Question under test: does a pure-Python, ISC-enabled DP-to-DP relay preserve true DDS
`instance_state` per key, reproducing what a *direct* pair of ISC endpoints does — where
Routing Service does not (LP-1)?

Three paths per scenario, and the assertion is always **C must match A key-for-key**
(A = ground truth), while B (RS) is expected to differ:
  A (direct)  source writer --> dest reader, no gateway. ISC baseline == ground truth.
  C (relay)   source writer --> [Python ISC relay] --> dest reader. The proof.
  B (RS)      source writer --> Routing Service --> dest reader. Expected to diverge.
              Only runs if an RS bridging the B domains is reachable; else reported skipped.

Two scenarios (see README for why they are split):

  Scenario 1 — CONNECTED TRANSPARENCY. The dest reader stays matched throughout while the
  source applies the full transition matrix. Proves the relay mirrors every lifecycle
  transition live, INCLUDING NOT_ALIVE_NO_WRITERS. Deterministic.

  Scenario 2 — DISCONNECT / RECONNECT RECOVERY. The dest reader disconnects (its Subscriber
  is moved to an isolated PARTITION so the *same reader entity* unmatches, then rematches),
  transitions happen while it is gone, and ISC must recover them on reconnect. Proves
  DISPOSED + multi-transition replay + ALIVE recovery across a disconnection.

  NOTE on NO_WRITERS-under-disconnect: recovering NOT_ALIVE_NO_WRITERS for a reader that was
  fully unmatched during the transition is NOT achievable with durability alone — it needs the
  reader to stay continuously matched through a *transport-level* partition (packet loss), which
  bare-localhost QoS cannot produce. That case is deferred to the EMANE/netem phases (roadmap
  Phase 8; product-gaps LP-1 lists "NO_WRITERS mirroring under partition" as a residual). So
  Scenario 2 asserts C==A over the recoverable transitions; NO_WRITERS is covered by Scenario 1.

Per-key transition matrix:
  K1  ALIVE, then DISPOSED                    -> DISPOSED
  K2  ALIVE, then writer UNREGISTERs          -> NO_WRITERS   (Scenario 1 only; see note)
  K3  ALIVE -> DISPOSE -> re-write -> DISPOSE  -> DISPOSED     (multi-transition replay)
  K4  ALIVE, kept alive (control)             -> ALIVE

Needs only rti.connextdds on a plain network — no containers/EMANE/ACT stack.
"""

import sys
import threading
import time

import rti.connextdds as dds

import act_state
from isc_relay import IscRelay


# Domain plan (kept apart so paths never cross-talk).
DOM_A = 10          # path A: writer + reader together
DOM_C_UP = 11       # path C: source -> relay(leg1)
DOM_C_DOWN = 12     # path C: relay(leg2) -> dest
DOM_B_UP = 13       # path B: source -> RS input
DOM_B_DOWN = 14     # path B: RS output -> dest

TOPIC = "ActState"
KEYS = ["K1", "K2", "K3", "K4"]
ISOLATED = ["__isolated__"]   # non-matching partition used to "disconnect" a reader

SETTLE = 3.0        # seconds to let reliable/ISC reconciliation complete
REASSERT = 0.3      # state-topic re-publish period for still-active instances


def state_name(s) -> str:
    if s == dds.InstanceState.ALIVE:
        return "ALIVE"
    if s == dds.InstanceState.NOT_ALIVE_DISPOSED:
        return "DISPOSED"
    if s == dds.InstanceState.NOT_ALIVE_NO_WRITERS:
        return "NO_WRITERS"
    return "<absent>" if s is None else str(s)


# --------------------------------------------------------------------------- #
# Source: a state publisher that periodically re-asserts its still-active keys #
# (realistic for keyed state topics) and can apply the fault transitions.      #
# --------------------------------------------------------------------------- #
class Source:
    def __init__(self, domain: int, isc: bool = True):
        self.dp = dds.DomainParticipant(domain, act_state.participant_qos())
        topic = dds.Topic(self.dp, TOPIC, act_state.ActState)
        self.writer = dds.DataWriter(dds.Publisher(self.dp), topic, act_state.writer_qos(isc))
        self.active = set(KEYS)
        self.seq = {k: 0 for k in KEYS}
        self.lock = threading.Lock()
        self._run = False
        self._thread = None

    def _handle(self, key_id):
        key = act_state.ActState(key_id=key_id)
        h = self.writer.lookup_instance(key)
        return h if not h.is_nil else self.writer.register_instance(key)

    def _write(self, key_id):
        self.seq[key_id] += 1
        self.writer.write(act_state.ActState(key_id=key_id, seq=self.seq[key_id],
                                             payload=f"{key_id}-{self.seq[key_id]}"))

    def initial_writes(self):
        with self.lock:
            for k in KEYS:
                self._write(k)
        time.sleep(0.5)

    def start_reassert(self):
        """Periodically re-publish still-active instances (state-topic behavior)."""
        self._run = True

        def loop():
            while self._run:
                with self.lock:
                    for k in list(self.active):
                        self._write(k)
                time.sleep(REASSERT)

        self._thread = threading.Thread(target=loop, name="source-reassert", daemon=True)
        self._thread.start()

    def apply_faults(self):
        """K1 dispose; K3 dispose/re-write/dispose (replay); K2 unregister; K4 untouched."""
        with self.lock:
            self.active.discard("K1")
            self.writer.dispose_instance(self._handle("K1"))

            self.active.discard("K3")
            self.writer.dispose_instance(self._handle("K3"))
            self._write("K3")                       # transient re-birth
            self.writer.dispose_instance(self._handle("K3"))

            self.active.discard("K2")
            self.writer.unregister_instance(self._handle("K2"))
        time.sleep(0.5)

    def close(self):
        self._run = False
        if self._thread:
            self._thread.join(timeout=2)
        self.dp.close()


class Dest:
    """A destination reader whose match can be toggled via its Subscriber partition."""

    def __init__(self, domain: int, isc: bool = True):
        self.dp = dds.DomainParticipant(domain, act_state.participant_qos())
        self.topic = dds.Topic(self.dp, TOPIC, act_state.ActState)
        self.sub = dds.Subscriber(self.dp)
        self.reader = dds.DataReader(self.sub, self.topic, act_state.reader_qos(isc))
        self._key_cache = {}   # persists across drain()/observe() so state-only samples map

    def set_connected(self, connected: bool):
        qos = self.sub.qos
        qos.partition.name = [] if connected else ISOLATED
        self.sub.qos = qos

    def _read_into(self, final):
        # read() is NON-destructive: it reports the reader's *retained* per-instance
        # state, so an unchanged ALIVE instance still shows up even after write-once
        # (a take() would consume it and it would never reappear without a re-publish).
        for data, info in self.reader.read():
            key = act_state.resolve_key_id(self.reader, data, info, self._key_cache)
            if key is None:
                continue
            if final is not None:
                final[key] = info.state.instance_state

    def drain(self):
        # Warm the handle->key cache without consuming anything.
        self._read_into(None)

    def observe(self) -> dict:
        final = {}
        deadline = time.time() + SETTLE
        while time.time() < deadline:
            self._read_into(final)
            time.sleep(0.1)
        return final

    def close(self):
        self.dp.close()


# --------------------------------------------------------------------------- #
# Path runners                                                                 #
# --------------------------------------------------------------------------- #
def _run_path(source: Source, dest: Dest, relay: IscRelay, disconnect: bool) -> dict:
    # Write-once: no periodic re-assertion. If the source kept re-publishing, a fresh
    # sample after reconnect would update the reader and mask whether recovery actually
    # came from reconnect reconciliation. So each instance is written once and each
    # transition applied once, and the observed final state is attributable to recovery.
    source.initial_writes()
    time.sleep(1.0)
    dest.drain()

    if disconnect:
        dest.set_connected(False)      # same reader entity unmatches
        time.sleep(1.0)
        source.apply_faults()
        time.sleep(1.0)                # let the relay (if any) mirror onto leg 2
        dest.set_connected(True)       # rematch -> ISC recovery
    else:
        source.apply_faults()

    result = dest.observe()
    return result


def run_direct(disconnect: bool) -> dict:
    source = Source(DOM_A)
    dest = Dest(DOM_A)
    try:
        return _run_path(source, dest, None, disconnect)
    finally:
        dest.close()
        source.close()


def run_relay(disconnect: bool) -> dict:
    source = Source(DOM_C_UP)
    relay = IscRelay(DOM_C_UP, DOM_C_DOWN, TOPIC)
    relay.start()
    dest = Dest(DOM_C_DOWN)
    try:
        return _run_path(source, dest, relay, disconnect)
    finally:
        dest.close()
        relay.stop()
        source.close()


def run_rs(disconnect: bool) -> dict:
    source = Source(DOM_B_UP)
    dest = Dest(DOM_B_DOWN)
    try:
        source.initial_writes()
        source.start_reassert()
        time.sleep(1.5)
        if len(dest.reader.take()) == 0:
            raise RuntimeError("no data via RS path — is Routing Service running with rs_config.xml?")
        if disconnect:
            dest.set_connected(False)
            time.sleep(1.0)
            source.apply_faults()
            time.sleep(1.0)
            dest.set_connected(True)
        else:
            source.apply_faults()
        return dest.observe()
    finally:
        dest.close()
        source.close()


# --------------------------------------------------------------------------- #
# Reporting                                                                    #
# --------------------------------------------------------------------------- #
def report_path(label: str, result: dict):
    print(f"\n  {label}")
    for k in KEYS:
        print(f"    {k}: {state_name(result.get(k))}")


def matches(a: dict, c: dict, keys) -> bool:
    return all(a.get(k) == c.get(k) for k in keys)


def run_scenario(name: str, disconnect: bool, assert_keys) -> bool:
    print("\n" + "=" * 60)
    print(f"SCENARIO: {name}")
    print("=" * 60)

    a = run_direct(disconnect)
    report_path("Path A (direct / baseline = ground truth)", a)

    c = run_relay(disconnect)
    report_path("Path C (Python ISC relay)", c)

    c_ok = matches(a, c, assert_keys)
    print(f"\n  --> C matches A on {list(assert_keys)}: {'YES ✅' if c_ok else 'NO ❌'}")

    try:
        b = run_rs(disconnect)
        report_path("Path B (Routing Service)", b)
        b_ok = matches(a, b, assert_keys)
        print(f"  --> B matches A on {list(assert_keys)}: {b_ok}  "
              f"(expected NO — the LP-1 gap: RS does not carry instance state)")
    except RuntimeError as e:
        print(f"\n  Path B (Routing Service): SKIPPED — {e}")

    return c_ok


def main():
    print("Phase 1 — Python ISC-relay PoC · go/no-go gate")

    # Scenario 1 asserts ALL keys (incl. NO_WRITERS) while connected.
    s1 = run_scenario("1 · connected transparency (all transitions, incl. NO_WRITERS)",
                      disconnect=False, assert_keys=KEYS)

    # Scenario 2 asserts the durably-recoverable transitions across a reader disconnect.
    # K2/NO_WRITERS-under-disconnect is deferred to the EMANE/netem phases (see module docstring).
    recover_keys = ["K1", "K3", "K4"]
    s2 = run_scenario("2 · reader disconnect/reconnect recovery (ISC)",
                      disconnect=True, assert_keys=recover_keys)

    print("\n" + "#" * 60)
    print(f"Scenario 1 (connected transparency, incl NO_WRITERS): {'PASS' if s1 else 'FAIL'}")
    print(f"Scenario 2 (disconnect/reconnect recovery) .........: {'PASS' if s2 else 'FAIL'}")
    print("#" * 60)
    if s1 and s2:
        print("VERDICT: ✅ GO — the Python ISC relay reproduces the direct-ISC baseline")
        print("         end-to-end (lifecycle mirroring + disconnect recovery).")
        return 0
    print("VERDICT: ❌ NO-GO — relay did not reproduce the baseline; investigate before proceeding.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
