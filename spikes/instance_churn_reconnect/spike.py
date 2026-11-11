#!/usr/bin/env python3
"""Native production-wire instance recovery in an owned network namespace."""

import argparse
from collections import Counter
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import traceback
import xml.etree.ElementTree as ET

import rti.connextdds as dds

from workload import (
    STATIONARY_CONTACT_ID, converged, discrepancies, initial_identities, operations,
    positive_seconds, tick_seconds,
)


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PROFILE = "ACT_QOS_LIB::"
SCHEMA_VERSION = 3


class Blackout:
    def __init__(self, domain):
        if (os.environ.get("ACT_INSTANCE_CHURN_ISOLATED") != "1"
                or not Path("/.dockerenv").exists()
                or {name for _, name in socket.if_nameindex()} != {"lo"}):
            raise RuntimeError("Packet impairment requires an owned --network none container")
        if not 0 <= domain <= 232:
            raise ValueError("DDS domain must be <= 232")
        self.chain = f"CHURN_{os.getpid()}"
        base = 7400 + 250 * domain
        self.rule = ["OUTPUT", "-o", "lo", "-p", "udp",
                     "--dport", f"{base}:{base + 249}", "-j", self.chain]
        self.active = False
        self.created = False
        self.started = None
        self.finished = None
        self.counters = ""

    def command(self, *args):
        return subprocess.check_output(["iptables", "-w", "5", *args], text=True)

    def start(self):
        self.command("-N", self.chain)
        self.created = True
        self.command("-A", self.chain, "-j", "DROP")
        self.command("-A", *self.rule)
        self.active = True
        self.started = time.monotonic()

    def stop(self):
        if self.active:
            self.counters = self.command("-L", self.chain, "-nvx")
            self.command("-D", *self.rule)
            self.active = False
            self.finished = time.monotonic()
        if self.created:
            self.command("-F", self.chain)
            self.command("-X", self.chain)
            self.created = False


def providers():
    params = dds.QosProviderParams()
    params.url_profile = [
        str(ROOT / "harness_v2/qos/act_qos_profiles.xml"), str(HERE / "qos.xml")]
    params.ignore_environment_profile = True
    params.ignore_user_profile = True
    qos = dds.QosProvider(params)
    types = dds.QosProvider(str(ROOT / "harness_v2/datamodel/gen/ActTypes.xml"))
    return qos, types


def wait_for(predicate, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise TimeoutError("DDS endpoints did not become ready")


def frame(types, family, identity, sequence, run_id, topic=None):
    data = dds.DynamicData(types.type("Wan" + family))
    topic = topic or ("ContactReport" if family == "Event" else "PlatformInitStatus")
    source = f"Node_{identity}" if topic == "PlatformInitStatus" else "ChurnSource"
    instance = source if topic == "PlatformInitStatus" else f"contact_{identity}"
    if topic == "ControlCommand":
        instance = source
    data["source_id"] = source
    data["original_topic"] = topic
    data["instance_id"] = instance
    data["schema_version"] = SCHEMA_VERSION
    data["source_role"] = "platform"
    data["run_id"] = run_id
    data["session_id"] = 1
    data["audit_sequence"] = sequence
    data["sent_at_ns"] = time.time_ns()
    data["audience.control"] = True
    data["audience.global"] = topic == "PlatformInitStatus"
    branch = {"ContactReport": "contact_report", "PlatformInitStatus": "navigation",
              "ControlCommand": "command"}[topic]
    with data.loan_value("payload") as payload:
        with payload.data.loan_value(branch) as value:
            value.data["source"] = source
            value.data["run_id"] = run_id
            value.data["source_node"] = source
            value.data["audit_sequence"] = sequence
            value.data["sent_at_ns"] = data["sent_at_ns"]
            if topic == "ContactReport":
                value.data["contact_id"] = instance
            elif topic == "ControlCommand":
                value.data["destination"] = "Receiver"
                value.data["command_id"] = f"probe_{sequence}"
                value.data["command_type"] = "CHURN_PROBE"
    return data


def key(data):
    return "|".join(str(data[name]) for name in ("source_id", "original_topic", "instance_id"))


def memory_kib():
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    raise RuntimeError("VmRSS not available")


class NativeEndpoints:
    def __init__(self, family, filtered, event_durability="production"):
        self.cleanup = ExitStack()
        self.family = family
        self.event_durability = event_durability
        self.event_writer_profile = (PROFILE + "wan_event" if event_durability == "production"
                                     else "ChurnSpike::event_transient_local")
        self.event_reader_profile = ("ChurnSpike::event_reader" if event_durability == "production"
                                     else "ChurnSpike::event_transient_local")
        self.qos, self.types = providers()
        self.domain = 201
        self.readers = {}
        self.writers = {}
        self.topics = {}
        try:
            participants = []
            for name in ("Source", "Receiver"):
                qos = self.qos.participant_qos_from_profile(PROFILE + "wan_two_topic_participant")
                qos.participant_name.name = name
                participant = dds.DomainParticipant(self.domain, qos)
                self.cleanup.callback(participant.close)
                participants.append(participant)
            for endpoint_family in ("Event", "Status"):
                topics = [dds.DynamicData.Topic(p, endpoint_family, self.types.type("Wan" + endpoint_family))
                          for p in participants]
                self.topics[endpoint_family] = topics
                profile = (self.event_writer_profile if endpoint_family == "Event"
                           else PROFILE + "wan_union_status")
                writer = dds.DynamicData.DataWriter(
                    dds.Publisher(participants[0]), topics[0],
                    self.qos.datawriter_qos_from_profile(profile))
                description = topics[1]
                if filtered:
                    description = dds.DynamicData.ContentFilteredTopic(
                        topics[1], endpoint_family + "Filtered",
                        dds.Filter("source_id <> 'Receiver' AND audience.control = TRUE"))
                reader = dds.DynamicData.DataReader(
                    dds.Subscriber(participants[1]), description,
                    self.qos.datareader_qos_from_profile(
                        self.event_reader_profile if endpoint_family == "Event"
                        else "ChurnSpike::status_reader"))
                self.writers[endpoint_family] = writer
                self.readers[endpoint_family] = reader
            self.writer = self.writers[family]
            self.reader = self.readers[family]
            self.command_writer = self.writers["Event"]
            source_topic, ack_topic = self.topics["Event"]
            self.ack_writer = dds.DynamicData.DataWriter(
                dds.Publisher(participants[1]), ack_topic,
                self.qos.datawriter_qos_from_profile(self.event_writer_profile))
            ack_cft = dds.DynamicData.ContentFilteredTopic(
                source_topic, "AckFiltered",
                dds.Filter("original_topic = 'PlatformCommandAck'"))
            self.ack_reader = dds.DynamicData.DataReader(
                dds.Subscriber(participants[0]), ack_cft,
                self.qos.datareader_qos_from_profile(self.event_reader_profile))
            if event_durability == "transient_local":
                for entity in (self.writers["Event"], self.readers["Event"],
                               self.ack_writer, self.ack_reader):
                    if entity.qos.durability.kind != dds.DurabilityKind.TRANSIENT_LOCAL:
                        raise RuntimeError("Event experiment endpoint is not transient local")
            wait_for(lambda: all(writer.publication_matched_status.current_count >= 1
                                 for writer in self.writers.values())
                     and self.ack_writer.publication_matched_status.current_count >= 1)
            self.participants = participants
        except Exception:
            self.cleanup.close()
            raise

    def close(self):
        self.cleanup.close()

    def send(self, operation, sequence, run_id):
        data = frame(self.types, self.family, operation.identity, sequence, run_id)
        if operation.kind == "dispose":
            handle = self.writer.lookup_instance(data)
            if handle == dds.InstanceHandle.nil:
                raise RuntimeError(f"No registered source instance to dispose: {key(data)}")
            self.writer.dispose_instance(handle)
        else:
            self.writer.write(data)
        return key(data)

    def acknowledge(self, command):
        ack = dds.DynamicData(self.types.type("WanEvent"))
        for field in ("run_id", "session_id", "audit_sequence"):
            ack[field] = command[field]
        ack["source_id"] = "Receiver"
        ack["original_topic"] = "PlatformCommandAck"
        ack["instance_id"] = "Receiver"
        ack["schema_version"] = SCHEMA_VERSION
        ack["source_role"] = "control"
        ack["audience.control"] = True
        with ack.loan_value("payload") as payload:
            with payload.data.loan_value("command_ack") as value:
                value.data["source"] = "Receiver"
                value.data["destination"] = "ChurnSource"
                value.data["command_id"] = f"probe_{command['audit_sequence']}"
                value.data["accepted"] = True
        self.ack_writer.write(ack)

    def heartbeat(self, sequence, run_id):
        health = dds.DynamicData(self.types.type("WanStatus"))
        health["source_id"] = "ChurnSource"
        health["original_topic"] = "RouterHealth"
        health["instance_id"] = "ChurnSource/spike"
        health["schema_version"] = SCHEMA_VERSION
        health["run_id"] = run_id
        health["session_id"] = 1
        health["audit_sequence"] = sequence
        health["audience.control"] = True
        with health.loan_value("payload") as payload:
            with payload.data.loan_value("health") as value:
                value.data["router"] = "ChurnSource/spike"
                value.data["session_id"] = 1
                value.data["heartbeat_seq"] = sequence
        self.writers["Status"].write(health)

    def metrics(self):
        cache = self.writer.datawriter_cache_status
        protocol = self.writer.datawriter_protocol_status
        activity = self.writer.reliable_reader_activity_changed_status
        values = {
            "cache_samples": cache.sample_count,
            "alive_instances": cache.alive_instance_count,
            "disposed_instances": cache.disposed_instance_count,
            "unacked": self.writer.reliable_writer_cache_changed_status.unacknowledged_sample_count,
            "pushed_bytes": protocol.pushed_sample_bytes,
            "pulled_bytes": protocol.pulled_sample_bytes,
            "pushed_samples": protocol.pushed_sample_count,
            "pulled_samples": protocol.pulled_sample_count,
            "nacks": protocol.received_nack_count,
            "inactive_readers": activity.inactive_count,
            "matched_readers": self.writer.publication_matched_status.current_count,
            "rss_kib": memory_kib(),
        }
        return {name: value.total if isinstance(value, (dds.EventCount32, dds.EventCount64)) else value
                for name, value in values.items()}

    def effective_qos(self):
        qos = self.writer.qos
        return {
            "reliability": str(qos.reliability.kind),
            "history": str(qos.history.kind),
            "durability": str(qos.durability.kind),
            "durability_writer_depth": qos.durability.writer_depth,
            "liveliness_lease": str(qos.liveliness.lease_duration),
            "publish_mode": str(qos.publish_mode.kind),
            "resource_limits": str(qos.resource_limits),
            "heartbeat_retries": qos.protocol.rtps_reliable_writer.max_heartbeat_retries,
            "heartbeat_period": str(qos.protocol.rtps_reliable_writer.heartbeat_period),
            "participant_discovery_lease": str(
                self.participants[0].qos.discovery_config.participant_liveliness_lease_duration),
        }

    def late_join(self, truth):
        qos = self.qos.participant_qos_from_profile(PROFILE + "wan_two_topic_participant")
        qos.participant_name.name = "LateReceiver"
        participant = dds.DomainParticipant(self.domain, qos)
        self.cleanup.callback(participant.close)
        topic = dds.DynamicData.Topic(participant, "Event", self.types.type("WanEvent"))
        description = dds.DynamicData.ContentFilteredTopic(
            topic, "LateContacts", dds.Filter("original_topic = 'ContactReport'"))
        reader = dds.DynamicData.DataReader(
            dds.Subscriber(participant), description,
            self.qos.datareader_qos_from_profile(self.event_reader_profile))
        actual = {}
        valid = 0
        disposed = set()
        first_observations = []
        started = time.monotonic()
        until = started + 10
        while time.monotonic() < until:
            for item in reader.take():
                data = item.data if item.info.valid else reader.key_value(item.info.instance_handle)
                instance = key(data)
                sequence = (int(data["audit_sequence"]) if item.info.valid
                            else actual.get(instance, {}).get("sequence", 0))
                state = ("alive" if item.info.state.instance_state == dds.InstanceState.ALIVE else
                         "disposed" if item.info.state.instance_state == dds.InstanceState.NOT_ALIVE_DISPOSED
                         else "no_writers")
                actual[instance] = {"state": state, "sequence": sequence}
                valid += item.info.valid
                if state == "disposed":
                    disposed.add(instance)
                if len(first_observations) < 20:
                    first_observations.append({"key": instance, "sequence": sequence,
                                               "state": state, "valid": item.info.valid})
            if converged(truth, actual) and self.metrics()["unacked"] == 0:
                break
            time.sleep(0.01)
        return {
            "passed": converged(truth, actual),
            "elapsed_s": time.monotonic() - started,
            "reader_durability": str(reader.qos.durability.kind),
            "valid_samples": valid, "disposed_keys_observed": len(disposed),
            "expected_disposed_keys": sum(value["state"] == "disposed" for value in truth.values()),
            "final_discrepancies": discrepancies(truth, actual),
            "first_observations": first_observations,
            "scope": "new WAN reader, original writer still alive; no router restart",
        }


def run_case(endpoints, pattern, outage, baseline, recovery, offset=0.0, filtered=True,
             outage_seconds=2):
    run_id = f"churn-{endpoints.family}-{pattern}-{int(time.time() * 1000)}"
    truth, actual = {}, {}
    observations = []
    samples = []
    counts = Counter()
    blackout_counts = Counter()
    errors = []
    sequence = 0
    command_sends, acknowledgments = {}, []
    command_events = []
    health_sends, health_events = {}, []
    maximum_lag = 0.0
    maximum_api = 0.0
    received_during_blackout = 0
    first_recovery_convergence = None
    drop = Blackout(endpoints.domain)

    def drain():
        nonlocal received_during_blackout
        for family, reader in endpoints.readers.items():
            for item in reader.take():
                now = time.monotonic()
                if drop.active:
                    received_during_blackout += 1
                if item.info.valid:
                    data = item.data
                    topic = str(data["original_topic"])
                    if topic == "ControlCommand":
                        endpoints.acknowledge(data)
                        continue
                    if topic == "RouterHealth":
                        sent = health_sends.get(int(data["audit_sequence"]))
                        if sent is not None:
                            health_events.append({"at": now, "age_s": now - sent})
                        continue
                    if topic not in ("ContactReport", "PlatformInitStatus"):
                        continue
                    instance = key(data)
                    value_sequence = int(data["audit_sequence"])
                else:
                    data = reader.key_value(item.info.instance_handle)
                    if str(data["original_topic"]) not in ("ContactReport", "PlatformInitStatus"):
                        continue
                    instance = key(data)
                    value_sequence = actual.get(instance, {}).get("sequence", 0)
                state = item.info.state.instance_state
                state_name = ("alive" if state == dds.InstanceState.ALIVE else
                              "disposed" if state == dds.InstanceState.NOT_ALIVE_DISPOSED else "no_writers")
                actual[instance] = {"state": state_name, "sequence": value_sequence}
                observations.append({"at": now, "key": instance, "valid": item.info.valid,
                                     "state": state_name, "sequence": value_sequence})
        for item in endpoints.ack_reader.take():
            if item.info.valid:
                number = int(item.data["audit_sequence"])
                if number in command_sends and number not in acknowledgments:
                    now = time.monotonic()
                    acknowledgments.append(number)
                    command_events.append({"sent": command_sends[number], "received": now,
                                           "rtt_s": now - command_sends[number]})

    def send(operation, measured=False):
        nonlocal sequence, maximum_api
        sequence += 1
        before = time.monotonic()
        instance = endpoints.send(operation, sequence, run_id)
        maximum_api = max(maximum_api, time.monotonic() - before)
        truth[instance] = {"state": "disposed" if operation.kind == "dispose" else "alive",
                           "sequence": sequence}
        if measured:
            counts[operation.kind] += 1
            if drop.active:
                blackout_counts[operation.kind] += 1

    try:
        from workload import Operation
        for identity in initial_identities(pattern):
            send(Operation("create", identity))
        stationary_key = None
        if endpoints.family == "Event":
            send(Operation("create", STATIONARY_CONTACT_ID))
            stationary_key = key(frame(
                endpoints.types, "Event", STATIONARY_CONTACT_ID, sequence, run_id))
        wait_for(lambda: (drain() is None and converged(truth, actual)), seconds=15)
        start = time.monotonic()
        outage_at = start + baseline + offset
        stop_at = outage_at + (outage_seconds if outage else 0) + recovery
        next_tick, next_metrics, next_probe = start, start, start
        tick = 0
        while time.monotonic() < stop_at:
            now = time.monotonic()
            if outage and drop.started is None and now >= outage_at:
                drain()
                drop.start()
            if drop.active and now >= drop.started + outage_seconds:
                drop.stop()
            if now >= next_tick:
                maximum_lag = max(maximum_lag, now - next_tick)
                for operation in operations(tick, pattern):
                    send(operation, measured=True)
                tick += 1
                next_tick = start + tick * tick_seconds(pattern)
            if now >= next_probe:
                probe_sequence = int(next_probe - start) + 1
                command_sends[probe_sequence] = time.monotonic()
                endpoints.command_writer.write(frame(
                    endpoints.types, "Event", 0, probe_sequence, run_id, "ControlCommand"))
                health_sends[probe_sequence] = time.monotonic()
                endpoints.heartbeat(probe_sequence, run_id)
                next_probe += 1
            drain()
            if now >= next_metrics:
                if (drop.finished is not None and first_recovery_convergence is None
                        and converged(truth, actual)):
                    first_recovery_convergence = now
                samples.append({"at": now, **endpoints.metrics(),
                                "discrepancies": {name: len(values) for name, values
                                                  in discrepancies(truth, actual).items()}})
                next_metrics += 0.1
            time.sleep(0.001)
        drop.stop()
        stopped_producing = time.monotonic()
        terminal_converged = None
        until = stopped_producing + 10
        while time.monotonic() < until:
            drain()
            now = time.monotonic()
            current_metrics = endpoints.metrics()
            samples.append({"at": now, **current_metrics})
            reliable = endpoints.family == "Event"
            if (drop.finished is not None and first_recovery_convergence is None
                    and converged(truth, actual)):
                first_recovery_convergence = now
            if (converged(truth, actual) and len(acknowledgments) == len(command_sends)
                    and (not reliable or current_metrics["unacked"] == 0)):
                terminal_converged = now
                break
            time.sleep(0.05)
        final = discrepancies(truth, actual)
        if any(final.values()):
            errors.append("Destination state did not converge: " + json.dumps(
                {name: len(values) for name, values in final.items()}))
        missing_acks = sorted(set(command_sends) - set(acknowledgments))
        if missing_acks:
            errors.append(f"Missing command ACKs: {missing_acks}")
        if endpoints.family == "Event" and samples[-1]["unacked"] != 0:
            errors.append(f"Reliable queue did not drain: {samples[-1]['unacked']} unacknowledged")
        if maximum_lag > 0.05:
            errors.append(f"Workload scheduler lag exceeded one 50-ms tick: {maximum_lag:.6f}s")
        if outage:
            if not drop.counters or "DROP" not in drop.counters:
                errors.append("No impairment counter evidence")
            if drop.started is None or drop.finished is None:
                errors.append("Outage did not complete")
            elif not outage_seconds - 0.01 <= drop.finished - drop.started <= outage_seconds + 0.05:
                errors.append(
                    f"Outage duration outside {outage_seconds}s tolerance: "
                    f"{drop.finished - drop.started}")
        stationary_observations = [
            item for item in observations if item["key"] == stationary_key]
        if any(item["state"] != "alive" for item in stationary_observations):
            errors.append("Never-disposed stationary contact changed instance state")
        restored = drop.finished if outage else outage_at
        if (outage and (first_recovery_convergence is None
                        or first_recovery_convergence - restored > 10)):
            errors.append("Current state did not converge within proposed 10s recovery budget")
        fresh_commands = [event for event in command_events if event["sent"] >= restored]
        if fresh_commands and max(event["rtt_s"] for event in fresh_commands) > 2:
            errors.append("Post-restoration command RTT exceeded proposed 2s budget")
        late_join = None
        if endpoints.family == "Event" and endpoints.event_durability == "transient_local":
            late_join = endpoints.late_join(truth)
            if not late_join["passed"]:
                errors.append("Transient-local late joiner did not converge")
        return {
            "run_id": run_id, "family": endpoints.family, "pattern": pattern,
            "outage": outage, "filtered": filtered, "passed": not errors, "errors": errors,
            "started": start, "stopped_producing": stopped_producing,
            "outage_started": drop.started, "outage_finished": drop.finished,
            "requested_outage_s": outage_seconds,
            "drop_counters": drop.counters, "received_during_blackout": received_during_blackout,
            "operations": dict(counts), "blackout_operations": dict(blackout_counts),
            "scheduler_lag_max_s": maximum_lag, "operation_api_max_s": maximum_api,
            "terminal_converged_at": terminal_converged, "final_discrepancies": final,
            "first_recovery_convergence_s": (first_recovery_convergence - restored
                                              if first_recovery_convergence is not None else None),
            "effective_qos": endpoints.effective_qos(), "metrics": samples,
            "event_durability_experiment": endpoints.event_durability, "late_join": late_join,
            "stationary_contact": {
                "key": stationary_key, "observations": stationary_observations,
                "final": actual.get(stationary_key),
            },
            "commands": command_events, "missing_command_acks": missing_acks,
            "health": health_events, "observations": observations,
            "source_truth": truth, "destination_state": actual,
            "scope": "native DDS endpoints; not production-router or radio qualification",
        }
    finally:
        drop.stop()


def write_junit(results, path):
    suite = ET.Element("testsuite", name="instance_churn_reconnect",
                       tests=str(len(results)),
                       failures=str(sum(not result["passed"] for result in results)))
    for result in results:
        case = ET.SubElement(suite, "testcase", name=result["run_id"])
        if not result["passed"]:
            ET.SubElement(case, "failure", message="; ".join(result["errors"]))
    ET.ElementTree(suite).write(path, encoding="unicode", xml_declaration=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=float, default=20)
    parser.add_argument("--recovery", type=float, default=20)
    parser.add_argument("--outage-seconds", type=positive_seconds, default=2)
    parser.add_argument("--family", choices=["Event", "Status", "both"], default="both")
    parser.add_argument("--event-durability", choices=["production", "transient_local"],
                        default="production")
    parser.add_argument("--pattern", choices=["steady", "short", "revival"], default="steady")
    parser.add_argument("--control-only", action="store_true")
    parser.add_argument("--unfiltered", action="store_true")
    parser.add_argument("--offset", type=float, default=0)
    args = parser.parse_args()
    if min(args.baseline, args.recovery) < 2 or not 0 <= args.offset < 2:
        parser.error("baseline/recovery must be >=2s and offset must be in [0,2)")
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for family in (("Event", "Status") if args.family == "both" else (args.family,)):
        modes = (False,) if args.control_only else (False, True)
        for outage in modes:
            endpoints = None
            try:
                endpoints = NativeEndpoints(family, not args.unfiltered, args.event_durability)
                result = run_case(endpoints, args.pattern, outage, args.baseline,
                                  args.recovery, args.offset, not args.unfiltered,
                                  args.outage_seconds)
            except Exception:
                result = {"run_id": f"{family}-{args.pattern}-outage-{outage}",
                          "passed": False, "errors": [traceback.format_exc()]}
            finally:
                if endpoints is not None:
                    endpoints.close()
            results.append(result)
            (args.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            write_junit(results, args.output / "junit.xml")
            print(json.dumps({name: result[name] for name in ("run_id", "passed", "errors")}),
                  flush=True)
    raise SystemExit(0 if all(result["passed"] for result in results) else 1)


if __name__ == "__main__":
    main()
