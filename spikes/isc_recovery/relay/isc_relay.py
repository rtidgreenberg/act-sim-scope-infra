#!/usr/bin/env python3
"""
Python ISC relay — Phase 1 PoC (roadmap.md, M0 go/no-go gate).

A standalone DomainParticipant-to-DomainParticipant relay:

    upstream domain ──▶ [ ISC DataReader (leg 1) ] ──▶ [ ISC DataWriter (leg 2) ] ──▶ downstream domain

Native Instance State Consistency runs *per leg* (source-writer↔relay-reader, and
relay-writer↔dest-reader). The relay's job is to make the two legs behave as one
transparent hop by **mirroring its reader's instance lifecycle onto its writer**:

    reader sample valid            ──▶ writer.write(sample)
    reader NOT_ALIVE_DISPOSED      ──▶ writer.dispose_instance(key)
    reader NOT_ALIVE_NO_WRITERS    ──▶ writer.unregister_instance(key)

This is the workaround for LP-1 (ISC is *not* carried across Routing Service). Because
each leg is a genuine pair of ISC-enabled matched endpoints, true DDS instance_state
converges end-to-end — which Routing Service cannot do.

Runs pure `rti.connextdds` on a plain network — no containers/EMANE/ACT stack.
"""

import argparse
import threading

import rti.connextdds as dds

import act_state


class IscRelay:
    """One-topic, one-key-type ISC relay across two domains (or two topics)."""

    def __init__(
        self,
        upstream_domain: int,
        downstream_domain: int,
        topic_name: str,
        verbose: bool = False,
        isc: bool = True,
        rqos=None,
        wqos=None,
    ):
        self.topic_name = topic_name
        self.verbose = verbose
        # Optional QoS overrides (e.g. perf.py injects a throughput profile); default to
        # the ISC state-topic QoS builders.
        rqos = rqos if rqos is not None else act_state.reader_qos(isc)
        wqos = wqos if wqos is not None else act_state.writer_qos(isc)

        # Two participants so the two legs are genuinely separate DDS "hops".
        self._up_dp = dds.DomainParticipant(upstream_domain, act_state.participant_qos())
        self._down_dp = dds.DomainParticipant(downstream_domain, act_state.participant_qos())

        up_topic = dds.Topic(self._up_dp, topic_name, act_state.ActState)
        down_topic = dds.Topic(self._down_dp, topic_name, act_state.ActState)

        # Leg 1: ISC reader on the upstream side.
        self.reader = dds.DataReader(dds.Subscriber(self._up_dp), up_topic, rqos)
        # Leg 2: ISC writer on the downstream side.
        self.writer = dds.DataWriter(dds.Publisher(self._down_dp), down_topic, wqos)

        self._running = False
        self._thread = None
        self._key_cache = {}   # reader instance_handle -> key_id (for state-only samples)
        # Read-condition-driven wakeups; take() drains on each notification.
        self._waitset = dds.WaitSet()
        self._read_cond = dds.ReadCondition(self.reader, dds.DataState.any)
        self._waitset += self._read_cond

    # -- lifecycle mirroring ------------------------------------------------

    def _pump(self):
        """Drain the reader and mirror every sample / state change onto the writer."""
        # Consume through the same condition the WaitSet triggers on.
        for data, info in self.reader.select().condition(self._read_cond).take():
            if info.valid:
                # Live data — forward as-is (preserves the key, so the instance maps 1:1).
                self._key_cache[info.instance_handle] = data.key_id
                self.writer.write(data)
                if self.verbose:
                    print(f"[relay] fwd  key={data.key_id} seq={data.seq}")
                continue

            # Invalid sample == instance-state change only; recover the key.
            key_id = act_state.resolve_key_id(self.reader, data, info, self._key_cache)
            if key_id is None:
                continue   # key genuinely unrecoverable — nothing we can faithfully mirror
            state = info.state.instance_state
            # dispose/unregister are by InstanceHandle in this binding, so look the
            # handle up on the writer (registering it if the writer hasn't seen the key).
            key = act_state.ActState(key_id=key_id)
            handle = self.writer.lookup_instance(key)
            if handle.is_nil:
                handle = self.writer.register_instance(key)
            if state == dds.InstanceState.NOT_ALIVE_DISPOSED:
                self.writer.dispose_instance(handle)
                if self.verbose:
                    print(f"[relay] DISPOSE key={key_id}")
            elif state == dds.InstanceState.NOT_ALIVE_NO_WRITERS:
                self.writer.unregister_instance(handle)
                if self.verbose:
                    print(f"[relay] NO_WRITERS(unregister) key={key_id}")

    def _loop(self):
        while self._running:
            # Block until the reader has something, then drain. A single bad sample
            # must never kill the relay thread, so isolate faults per wake.
            try:
                if self._waitset.wait(dds.Duration(1)):  # 1s wake to re-check _running
                    self._pump()
            except Exception as e:  # noqa: BLE001 — keep the relay alive
                if self.verbose:
                    print(f"[relay] pump error (continuing): {e}")

    # -- start/stop ---------------------------------------------------------

    def start(self):
        # With TRANSIENT_LOCAL, wait for the source's durable state before forwarding
        # so the relay starts from a complete instance picture, not mid-stream.
        try:
            self.reader.wait_for_historical_data(dds.Duration(5))
        except Exception:
            pass  # no historical writer yet — fine, we'll get live data
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="isc-relay", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        self._waitset.detach_all()   # release the ReadCondition before closing entities
        self._up_dp.close()
        self._down_dp.close()


def main():
    ap = argparse.ArgumentParser(description="Python ISC DP-to-DP relay (Phase 1 PoC)")
    ap.add_argument("--upstream-domain", type=int, required=True)
    ap.add_argument("--downstream-domain", type=int, required=True)
    ap.add_argument("--topic", default="ActState")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    relay = IscRelay(
        args.upstream_domain, args.downstream_domain, args.topic, args.verbose
    )
    relay.start()
    print(
        f"ISC relay running: domain {args.upstream_domain} -> {args.downstream_domain} "
        f"topic '{args.topic}'. Ctrl-C to stop."
    )
    try:
        while True:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        print("\nstopping relay...")
        relay.stop()


if __name__ == "__main__":
    main()
