#!/usr/bin/env python3
"""Isolated automatic health-liveliness cadence and crash-detection measurement."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import json
import multiprocessing
from pathlib import Path
import signal
import statistics
import subprocess
import tempfile
import time

from spike import ROOT, capture_summary


QOS = Path(__file__).with_name("liveliness_qos.xml")
LIVELINESS_WRITER = 0x000200C2  # MIG_RTPS_OBJECT_ID_P2P_BUILTIN_PARTICIPANT_MESSAGE_WRITER.


class Node:
    def __init__(self, source, lease, domain):
        import rti.connextdds as dds

        self.dds, self.source = dds, source
        provider = dds.QosProvider(str(QOS))
        pq = provider.participant_qos_from_profile("HealthTiming::participant")
        name = pq.participant_name
        name.name = f"{source}/health-timing"
        pq.participant_name = name
        self.participant = dds.DomainParticipant(domain, pq)
        self.dtype = provider.type("HealthTimingSample")
        self.topic = dds.DynamicData.Topic(self.participant, "RouterHealthTiming", self.dtype)
        pubq = self.participant.default_publisher_qos
        subq = self.participant.default_subscriber_qos
        pubq.partition = dds.Partition(["TO_PLATFORM" if source == "Control" else "TO_CONTROL"])
        subq.partition = dds.Partition(["TO_CONTROL" if source == "Control" else "TO_PLATFORM"])
        self.publisher = dds.Publisher(self.participant, pubq)
        self.subscriber = dds.Subscriber(self.participant, subq)
        profile = f"HealthTiming::lease{lease}"
        self.writer = dds.DynamicData.DataWriter(
            self.publisher, self.topic, provider.datawriter_qos_from_profile(profile))
        self.reader = dds.DynamicData.DataReader(
            self.subscriber, self.topic, provider.datareader_qos_from_profile(profile))
        self.sent, self.received = {}, {}
        self.duplicates = 0
        self.next_send = time.monotonic()
        self.enabled = True
        self.effective_qos = dict(
            writer_lease_s=self.writer.qos.liveliness.lease_duration.sec,
            reader_lease_s=self.reader.qos.liveliness.lease_duration.sec,
            assertions_per_lease=self.writer.qos.liveliness.assertions_per_lease_duration,
            deadline_s=self.reader.qos.deadline.period.sec,
            participant_assert_s=self.participant.qos.discovery_config.participant_liveliness_assert_period.sec,
        )

    def poll(self):
        now = time.monotonic()
        if self.enabled and now >= self.next_send:
            self.next_send = now + 1
            sequence = len(self.sent) + 1
            data = self.dds.DynamicData(self.dtype)
            data["source"], data["sequence"], data["stamp"] = self.source, sequence, time.time_ns()
            self.writer.write(data)
            self.sent[sequence] = now
        for data, info in self.reader.take():
            if info.valid:
                sequence = int(data["sequence"])
                if sequence in self.received:
                    self.duplicates += 1
                self.received[sequence] = now

    def snapshot(self):
        self.poll()
        return dict(
            guid=str(self.participant.instance_handle)[:24].lower(),
            sent=self.sent.copy(), received=self.received.copy(), duplicates=self.duplicates,
            alive=self.reader.liveliness_changed_status.alive_count,
            deadline_total=self.reader.requested_deadline_missed_status.total_count,
            matched_readers=len(self.writer.matched_subscriptions),
            matched_writers=len(self.reader.matched_publications),
            effective_qos=self.effective_qos,
        )

    def close(self):
        for entity in (self.writer, self.reader, self.publisher, self.subscriber,
                       self.topic, self.participant):
            entity.close()


def worker(connection, lease, domain):
    node = Node("Platform", lease, domain)
    try:
        connection.send(node.snapshot())
        running = True
        while running:
            node.poll()
            if connection.poll(0.01):
                command = connection.recv()
                if command == "pause":
                    node.enabled = False
                elif command == "resume":
                    node.enabled = True
                elif command == "stop":
                    running = False
                elif command != "snapshot":
                    raise ValueError(f"unknown command {command}")
                connection.send(node.snapshot())
    finally:
        node.close()
        connection.close()


def rpc(connection, command):
    connection.send(command)
    if not connection.poll(10):
        raise TimeoutError(f"health worker did not respond to {command}")
    return connection.recv()


def audit(sender, receiver, start, end):
    eligible = [str(seq) for seq, stamp in sender["sent"].items() if start <= stamp < end - 2]
    received = {str(seq) for seq in receiver["received"]}
    if not eligible or any(seq not in received for seq in eligible) or receiver["duplicates"]:
        raise AssertionError("health delivery acceptance failed")
    return len(eligible)


def liveliness_frames(path, prefixes, start, end):
    import sys
    sys.path.insert(0, str(ROOT / "debug/scripts"))
    from traffic_accounting import entity_ids

    fields = ("frame.time_epoch", "frame.len", "rtps.guidPrefix.src",
              "rtps.sm.wrEntityId", "rtps.sm.id", "rtps.sm.seqNumber")
    command = ["tshark", "-n", "-r", str(path), "-Y", "rtps", "-T", "fields",
               "-E", "separator=|", "-E", "quote=d", "-E", "occurrence=a"]
    for field in fields:
        command.extend(["-e", field])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    events = {prefix: [] for prefix in prefixes}
    logical = {prefix: {} for prefix in prefixes}
    ambiguous = {prefix: 0 for prefix in prefixes}
    for row in csv.reader(result.stdout.splitlines(), delimiter="|"):
        if len(row) != len(fields):
            raise ValueError(f"malformed tshark row {row}")
        epoch, size, prefix, writers, submessages, sequences = row
        prefix = prefix.replace(":", "").lower()
        if (prefix in events and start <= float(epoch) < end
                and LIVELINESS_WRITER in entity_ids(writers)
                and 0x15 in entity_ids(submessages)):
            events[prefix].append((float(epoch), int(size)))
            if set(entity_ids(writers)) == {LIVELINESS_WRITER} and sequences and "," not in sequences:
                logical[prefix].setdefault(sequences, float(epoch))
            else:
                ambiguous[prefix] += 1
    return {
        prefix: dict(
            frames=len(values), whole_frame_bytes=sum(size for _, size in values),
            timestamps=[stamp for stamp, _ in values],
            logical_assertions=len(logical[prefix]),
            ambiguous_sequence_frames=ambiguous[prefix],
            median_logical_interval_s=statistics.median(
                b-a for a, b in zip(list(logical[prefix].values()),
                                   list(logical[prefix].values())[1:]))
            if len(logical[prefix]) > 1 else None,
            median_interval_s=statistics.median(
                b[0]-a[0] for a, b in zip(values, values[1:])) if len(values) > 1 else None,
        ) for prefix, values in events.items()
    }


def run_case(args):
    args.output.mkdir(parents=True, exist_ok=False)
    pcap = args.output / "traffic.pcapng"
    log = (args.output / "capture.log").open("w")
    lo = 7400 + 250 * args.domain
    capture = subprocess.Popen(
        ["dumpcap", "-i", "lo", "-f", f"udp portrange {lo}-{lo+249}",
         "-a", f"duration:{args.seconds+150}", "-w", str(pcap), "-q"],
        stdout=log, stderr=log)
    context = multiprocessing.get_context("spawn")
    connection, child = context.Pipe()
    process = context.Process(target=worker, args=(child, args.lease, args.domain))
    node = None
    process.start()
    child.close()
    try:
        if not connection.poll(20):
            raise TimeoutError("platform startup timed out")
        initial = connection.recv()
        node = Node("Control", args.lease, args.domain)

        def wait(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                node.poll()
                if capture.poll() is not None:
                    raise RuntimeError("capture stopped prematurely")
                if not process.is_alive():
                    raise RuntimeError(f"platform exited with {process.exitcode}")
                time.sleep(0.01)

        wait(5)
        state = node.snapshot()
        remote = rpc(connection, "snapshot")
        for snapshot in (state, remote):
            if snapshot["matched_readers"] != 1 or snapshot["matched_writers"] != 1 or snapshot["alive"] != 1:
                raise AssertionError("health pair did not discover and match")
            q = snapshot["effective_qos"]
            if q != dict(writer_lease_s=args.lease, reader_lease_s=args.lease,
                         assertions_per_lease=3, deadline_s=2, participant_assert_s=30):
                raise AssertionError(f"incorrect effective QoS {q}")
        start_mono, start_epoch = time.monotonic(), time.time()
        initial_deadlines = (state["deadline_total"], remote["deadline_total"])
        wait(args.seconds)
        end_mono, end_epoch = time.monotonic(), time.time()
        (args.output / "steady_window.json").write_text(json.dumps(
            dict(start_epoch=start_epoch, end_epoch=end_epoch), indent=2)+"\n")
        state, remote = node.snapshot(), rpc(connection, "snapshot")
        if (state["deadline_total"], remote["deadline_total"]) != initial_deadlines:
            raise AssertionError("health deadline missed during the healthy steady phase")
        deliveries = audit(state, remote, start_mono, end_mono) + audit(remote, state, start_mono, end_mono)
        heartbeat_intervals = {}
        for label, snapshot in (("Control", state), ("Platform", remote)):
            stamps = [stamp for stamp in snapshot["sent"].values() if start_mono <= stamp < end_mono]
            intervals = [b-a for a, b in zip(stamps, stamps[1:])]
            if not intervals or min(intervals) < 0.95 or max(intervals) > 1.1:
                raise AssertionError(f"heartbeat cadence changed: {label} {intervals}")
            heartbeat_intervals[label] = dict(
                min=min(intervals), max=max(intervals), mean=statistics.mean(intervals))
        rpc(connection, "pause")
        before = node.snapshot()
        last_receive = max(before["received"].values())
        paused_epoch = time.time()
        deadline_at = None
        deadline_mono = None
        hold_end = time.monotonic() + 35
        while time.monotonic() < hold_end:
            wait(0.02)
            current = node.snapshot()
            if current["alive"] != 1:
                raise AssertionError("automatic liveliness lost while the platform remained running")
            if current["deadline_total"] > before["deadline_total"] and deadline_at is None:
                deadline_at = time.time()
                deadline_mono = time.monotonic()
        if deadline_at is None:
            raise AssertionError("withheld heartbeat did not miss its deadline")
        if not 1.75 <= deadline_mono-last_receive <= 2.5:
            raise AssertionError("withheld-heartbeat deadline did not remain approximately two seconds")
        rpc(connection, "resume")
        wait(5)
        if node.snapshot()["alive"] != 1:
            raise AssertionError("platform was not alive before crash")
        rpc(connection, "snapshot")
        kill_epoch = time.time()
        process.kill()
        process.join(5)
        if process.is_alive() or process.exitcode != -signal.SIGKILL:
            raise AssertionError("owned crash process was not reaped")
        dead_epoch = None
        timeout = time.monotonic() + 2 * args.lease + 7
        while time.monotonic() < timeout:
            node.poll()
            if node.snapshot()["alive"] == 0:
                dead_epoch = time.time()
                break
            time.sleep(0.01)
        if dead_epoch is None:
            raise AssertionError("reader did not detect the crashed writer")
        if len(node.participant.discovered_participants()) != 1:
            raise AssertionError("participant purge preceded writer liveliness loss")
        guids = {state["guid"]: "Control", initial["guid"]: "Platform"}
        result = dict(
            lease_s=args.lease, steady_seconds=args.seconds, audited_receives=deliveries,
            missing=0, duplicates=0, effective_qos=state["effective_qos"], guid_map=guids,
            heartbeat_intervals_s=heartbeat_intervals,
            deadline_after_pause_s=deadline_at-paused_epoch,
            deadline_after_last_receive_s=deadline_mono-last_receive,
            pause_s=35, remained_alive_during_pause=True,
            kill_epoch=kill_epoch, dead_epoch=dead_epoch,
            dead_after_kill_s=dead_epoch-kill_epoch, participant_still_discovered_at_dead=True,
        )
    finally:
        if process.is_alive():
            try:
                rpc(connection, "stop")
                process.join(5)
            finally:
                if process.is_alive():
                    process.kill()
                    process.join(5)
        connection.close()
        if node is not None:
            node.close()
        if capture.poll() is None:
            capture.send_signal(signal.SIGINT)
        capture.wait(timeout=10)
        log.close()
    result["capture"] = capture_summary(pcap, guids, {"steady": (start_epoch, end_epoch)})
    result["liveliness"] = liveliness_frames(pcap, guids, start_epoch, end_epoch)
    for values in result["liveliness"].values():
        expected = args.lease/3
        if values["frames"] < 3 or abs(values["median_interval_s"]-expected) > 0.25:
            raise AssertionError(f"unexpected liveliness cadence: {values}, expected {expected}")
    killed = liveliness_frames(pcap, guids, start_epoch, kill_epoch)[initial["guid"]]
    result["dead_after_last_liveliness_s"] = dead_epoch-killed["timestamps"][-1]
    if not args.lease-0.25 <= result["dead_after_last_liveliness_s"] <= 2*args.lease+3:
        raise AssertionError(f"unexpected reader liveliness expiry {result['dead_after_last_liveliness_s']}")
    (args.output / "result.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("capture", "liveliness")}), flush=True)


def report(manifest, runs, output):
    expected = {(lease, r) for lease in (3, 30) for r in range(1, manifest["repetitions"]+1)}
    actual = [(r["lease_s"], r["repetition"]) for r in runs]
    if len(actual) != len(expected) or set(actual) != expected:
        raise AssertionError("incomplete liveliness comparison")
    aggregate = {}
    for lease in (3, 30):
        selected = [r for r in runs if r["lease_s"] == lease]
        measures = []
        for r in selected:
            window = r["capture"]["windows"]["steady"]
            if sum(window["class_bytes"].values()) != window["total_bytes"]:
                raise AssertionError("capture byte reconciliation failed")
            if sum(window["per_dp_bytes"].values()) != window["total_bytes"]:
                raise AssertionError("participant byte reconciliation failed")
            if r["missing"] or r["duplicates"] or r["capture"]["accounting_version"] != 2:
                raise AssertionError("invalid capture/delivery result")
            if len(r["liveliness"]) != 2 or set(r["liveliness"]) != set(r["guid_map"]):
                raise AssertionError("incorrect two-participant capture inventory")
            values = list(r["liveliness"].values())
            measures.append(dict(
                liveliness_interval_s=statistics.median(v["median_interval_s"] for v in values),
                liveliness_kbps=sum(v["whole_frame_bytes"] for v in values)*8/1000/window["duration_s"],
                discovery_kbps=window["discovery_tx_Bps"]*8/1000,
                total_kbps=window["total_tx_Bps"]*8/1000,
                stale_after_last_sample_s=r["deadline_after_last_receive_s"],
                dead_after_kill_s=r["dead_after_kill_s"],
                dead_after_last_assert_s=r["dead_after_last_liveliness_s"],
            ))
        aggregate[str(lease)] = {
            key: dict(median=statistics.median(m[key] for m in measures),
                      min=min(m[key] for m in measures), max=max(m[key] for m in measures))
            for key in measures[0]
        }
    reduction = 100*(1-aggregate["30"]["liveliness_kbps"]["median"]
                     /aggregate["3"]["liveliness_kbps"]["median"])
    retained = dict(manifest=manifest, runs=runs, aggregate=aggregate,
                    liveliness_byte_reduction_percent=reduction,
                    audited_receives=sum(r["audited_receives"] for r in runs),
                    missing=0, duplicates=0)
    lines = [
        "# RouterHealth automatic liveliness: 3 s versus 30 s",
        "",
        "Isolated Connext 7.7 loopback experiment; production QoS unchanged.",
        "Two reciprocal 1 Hz synthetic-health writers/readers. Participant assertion "
        "period stays 30 s; participant lease stays 100 s; health deadline stays 2 s. "
        "Only the health writer/reader automatic liveliness leases change.",
        "",
        f"{len(runs)} cases; {manifest['repetitions']} repetitions per lease; "
        f"{manifest['seconds']} s healthy windows. Rates are aggregate whole-frame decimal kb/s.",
        "",
        "| Health lease s | Captured liveliness interval s | Liveliness TX kb/s | All discovery TX kb/s | Total TX kb/s | STALE after last sample s | DEAD after crash s, observed range | DEAD after last assertion s, observed range |",
        "|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for lease in (3, 30):
        m = aggregate[str(lease)]
        dead, asserted = m["dead_after_kill_s"], m["dead_after_last_assert_s"]
        lines.append(
            f"| {lease} | {m['liveliness_interval_s']['median']:.3f} | "
            f"{m['liveliness_kbps']['median']:.3f} | {m['discovery_kbps']['median']:.3f} | "
            f"{m['total_kbps']['median']:.3f} | {m['stale_after_last_sample_s']['median']:.3f} | "
            f"{dead['min']:.2f}-{dead['max']:.2f} | {asserted['min']:.2f}-{asserted['max']:.2f} |")
    lines += [
        "",
        f"Captured participant-message-writer bytes fall by {reduction:.1f}%. "
        "This is not the percentage saving in all discovery or all WAN traffic.",
        f"Audited destination receives: {retained['audited_receives']}; missing 0; duplicates 0.",
        "",
        "Both cases retain approximately 1 Hz health writes and 2 s deadline detection. "
        "Withholding heartbeats for 35 s while the process remains running produces deadline "
        "misses but not liveliness loss in either case: AUTOMATIC liveliness is not a "
        "health-publishing-thread watchdog.",
        "",
        "A 30 s health lease with three assertions per lease produces approximately 10 s "
        "automatic assertions, not 30 s. Crash expiry is not an exact lease timer; "
        "the observed ranges include assertion phase and reader checking granularity.",
        "",
        "## Limits and attribution",
        "",
        "- Synthetic timing payload, not the full production RouterHealth type or router implementation.",
        "- Two direct UDPv4 participants, precise two-ID peer probes, no radio loss/rate shaping.",
        "- Participant-message DATA frames are identified by builtin writer 000200c2, verified "
        "as MIG_RTPS_OBJECT_ID_P2P_BUILTIN_PARTICIPANT_MESSAGE_WRITER in the installed SDK.",
        "- No claim of separately decoded TypeLookup or configuration-message bytes.",
        "- The earlier 32-platform scale workload uses a default infinite health-writer lease. "
        "Its 170/360 kb/s comparison must not be reinterpreted as this 3-to-30 s optimization.",
        f"- {manifest['repetitions']} repetitions give observed ranges, not confidence intervals "
        "or guaranteed failure-detection bounds.",
        "- Initial smoke observation used a lease+7 s crash timeout; the 30 s case did not "
        "expire within it. The repeated measurement allows two leases plus 7 s and records "
        "actual expiry rather than assuming a lease-exact timer.",
        "",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(json.dumps(retained, indent=2)+"\n")
    output.with_suffix(".md").write_text("\n".join(lines))
    return retained


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--lease", type=int, choices=(3, 30))
    parser.add_argument("--seconds", type=int, default=65)
    parser.add_argument("--domain", type=int, default=201)
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    if args.seconds < 35 or args.repetitions < 1 or not 201 <= args.domain <= 232:
        parser.error("at least 35 seconds, positive repetitions, and isolated domain 201-232 required")
    if args.inside:
        if args.output is None or args.lease is None:
            parser.error("inside mode requires output and lease")
        run_case(args)
        return
    if args.output is not None or args.lease is not None:
        parser.error("outer mode compares both leases and owns its temporary output directory")
    output = Path(tempfile.mkdtemp(prefix="health-liveliness-"))
    output.chmod(0o755)
    print(f"RAW_ARTIFACTS={output}", flush=True)
    hashes = {name: hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
              for name in ("liveliness.py", "liveliness_qos.xml", "spike.py",
                           "../../debug/scripts/traffic_accounting.py")}
    manifest = dict(seconds=args.seconds, repetitions=args.repetitions, source_hashes=hashes,
                    domain=args.domain, scenario="Two direct UDPv4 participants; reciprocal 1 Hz synthetic health",
                    production_qos_sha256=hashlib.sha256(
                        (ROOT/"harness_v2/qos/act_qos_profiles.xml").read_bytes()).hexdigest(),
                    image_id=subprocess.check_output(
                        ["docker", "image", "inspect", "--format", "{{.Id}}", "connext:7.7.0"],
                        text=True).strip())
    (output/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")

    def case(lease, repetition):
        case_id = f"lease{lease}-r{repetition}"
        name = f"health-timing-{output.name}-{case_id}"
        command = ["docker", "run", "--rm", "--network", "none", "--name", name,
                   "--cap-add", "NET_RAW", "--cap-add", "NET_ADMIN",
                   "-v", f"{ROOT}:/workspace:ro", "-v", f"{output}:/results",
                   "-v", "/home/dgreenberg/rti_connext_dds-7.7.0:/shared:ro",
                   "-e", "RTI_LICENSE_FILE=/shared/rti_license.dat",
                   "-e", "PYTHONPYCACHEPREFIX=/tmp/pycache", "-w", "/workspace",
                   "--entrypoint", "python3", "connext:7.7.0",
                   "spikes/wan_dp_route_tradeoffs/liveliness.py", "--inside",
                   "--lease", str(lease), "--seconds", str(args.seconds),
                   "--domain", str(args.domain), "--output", f"/results/{case_id}"]
        print(f"Starting {case_id}", flush=True)
        with (output/f"{case_id}.log").open("w") as log:
            try:
                subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT,
                               timeout=args.seconds+150)
            finally:
                state = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", name],
                                       capture_output=True, text=True)
                if state.returncode == 0 and state.stdout.strip() == "true":
                    subprocess.run(["docker", "stop", "--time", "15", name], check=True)
        result = json.loads((output/case_id/"result.json").read_text())
        result["repetition"] = repetition
        print(f"Completed {case_id}", flush=True)
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(case, lease, r) for r in range(1, args.repetitions+1)
                   for lease in (3, 30)]
        results = [f.result() for f in futures]
    (output/"results.json").write_text(json.dumps(dict(manifest=manifest, runs=results), indent=2)+"\n")
    for name, digest in hashes.items():
        if hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() != digest:
            raise AssertionError(f"runtime source changed during measurement: {name}")
    if hashlib.sha256((ROOT/"harness_v2/qos/act_qos_profiles.xml").read_bytes()).hexdigest() != manifest["production_qos_sha256"]:
        raise AssertionError("production QoS changed during the isolated measurement")
    report(manifest, results, ROOT/"debug/test_reports/health_liveliness_3_vs_30_20261107")
    print(f"RESULTS={output/'results.json'}", flush=True)


if __name__ == "__main__":
    main()
