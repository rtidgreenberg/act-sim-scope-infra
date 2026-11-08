#!/usr/bin/env python3
"""Fourteen synthetic mission types, squad-wide pose, and team-scoped contacts."""

import argparse
from collections import Counter
import json
import math
import multiprocessing
from pathlib import Path
import resource
import subprocess
import time
import traceback
import uuid

from schema_profile import POSE, PROFILES, profiles, size_payload
from goal_model import (SQUAD_KINDS, datamodel, initial_membership, team_kinds,
                        swap_membership, reassignment_membership, active_kinds,
                        squad_kinds as model_squad_kinds, endpoint_inventory,
                        OPTIMIZED_FIELDS, OPTIMIZED_SAMPLE_VALUES)
from spike import (QOS, SCALE_SOURCE_FILES, Rig, capture_integrity, capture_summary,
                   execution_provenance, stop_capture, validate_provenance)
from liveliness import liveliness_frames


PRE, ENABLE, STABLE, LOADED = 1, 2, 3, 4
SWAP, REASSIGN, SWAP_SETTLED, REASSIGN_SETTLED = 5, 6, 7, 8
HEALTH = 14
CAPTURE_BUFFER_MIB = 64


def recipients(source, kind, platforms, team_size, team_topic_count=1,
               membership=None, assigned=True, optimized=False):
    if kind == HEALTH:
        return list(range(1, platforms + 1)) if source == 0 else [0]
    if kind == 0:
        return [1]
    if kind in (model_squad_kinds(optimized) if team_topic_count > 1 else (POSE,)):
        return [i for i in range(platforms + 1) if i != source]
    if kind in team_kinds(team_topic_count, optimized):
        if not assigned:
            return [0]
        membership = membership if membership is not None else initial_membership(platforms, team_size)
        return [0] + [i for i in range(1, platforms + 1)
                      if i != source and membership[i] == membership[source]]
    return [0]


def node_worker(connection, node, variant, platforms, team_size, schemas, health_profile,
                team_topic_count=1, protocol="SPDP2", model_version=1):
    import rti.connextdds as dds

    qos = dds.QosProvider(str(QOS))
    types = dds.QosProvider(str(schemas))
    two, staged = variant == "A", variant == "C"
    goal = team_topic_count > 1
    optimized = goal and model_version == 2
    inventory = profiles(goal, optimized)
    kinds = active_kinds(optimized)
    selected_team_kinds = team_kinds(team_topic_count, optimized)
    squad_kinds = model_squad_kinds(optimized) if goal else (POSE,)
    participants, topics, endpoints, learned_types = {}, {}, {}, {}
    builtin_readers = {}
    sent, received = {}, {}
    sequence, phase, activated = 0, PRE, not staged
    session = uuid.uuid4().int & 0xffffffff
    last = {}
    team = (node - 1) // team_size if node else -1
    membership = initial_membership(platforms, team_size)
    assigned = False
    phase_ends = {}
    participant_partition_updates = endpoint_parent_updates = 0

    def add_participant(label, partitions):
        profile = "scale_participant_spdp" if protocol == "SPDP" else "scale_participant"
        policy = qos.participant_qos_from_profile(f"Tradeoffs::{profile}")
        policy.partition = dds.Partition(partitions)
        name = policy.participant_name
        name.name = f"P{node}/{label}"
        policy.participant_name = name
        factory = dds.DomainParticipant.participant_factory_qos
        disabled = dds.DomainParticipant.participant_factory_qos
        disabled.entity_factory.autoenable_created_entities = False
        dds.DomainParticipant.participant_factory_qos = disabled
        try:
            participant = dds.DomainParticipant(201, policy)
            participants[label] = participant
            # Builtin readers are lazy and do not backfill earlier discovery.
            builtin_readers[label] = (participant.publication_reader,
                                      participant.subscription_reader)
        finally:
            dds.DomainParticipant.participant_factory_qos = factory
        participant.enable()
        plugins = (dds.DiscoveryConfigBuiltinPluginKindMask.SPDP if protocol == "SPDP"
                   else dds.DiscoveryConfigBuiltinPluginKindMask.SPDP2)
        expected = plugins | dds.DiscoveryConfigBuiltinPluginKindMask.SEDP
        if participant.qos.discovery_config.builtin_discovery_plugins != expected:
            raise AssertionError(f"effective discovery protocol differs from {protocol}")

    add_participant("main", ["*"] if node == 0 else
                    ([f"P{node}"] if two else ["SHARED"]))
    if two and node:
        add_participant("position", ["POSITION"])

    def endpoint(kind, writer, partitions, learned=None):
        label = "position" if kind in squad_kinds and two and node else "main"
        p = participants[label]
        topic_key = (label, kind)
        dtype = learned if learned is not None else types.type(
            ("ScaleRouterHealth" if optimized else "ScaleSpeed")
            if kind == HEALTH else f"Scale{inventory[kind][0]}")
        topic_name = "ScaleHealth" if kind == HEALTH else f"Scale{inventory[kind][0]}"
        if topic_key not in topics:
            topics[topic_key] = dds.DynamicData.Topic(p, topic_name, dtype)
        periodic = kind == HEALTH or inventory[kind][3] > 0
        profile = "Tradeoffs::scale_periodic" if periodic else "Tradeoffs::sample"
        if kind == HEALTH:
            profile = f"Tradeoffs::{health_profile}"
        if writer:
            policy = p.default_publisher_qos
            policy.partition = dds.Partition(partitions)
            parent = dds.Publisher(p, policy)
            entity = dds.DynamicData.DataWriter(
                parent, topics[topic_key], qos.datawriter_qos_from_profile(profile))
        else:
            policy = p.default_subscriber_qos
            policy.partition = dds.Partition(partitions)
            parent = dds.Subscriber(p, policy)
            entity = dds.DynamicData.DataReader(
                parent, topics[topic_key], qos.datareader_qos_from_profile(profile))
        endpoints[(kind, writer)] = (entity, parent, dtype, label)

    endpoint(HEALTH, True, ["TO_PLATFORMS" if node == 0 else "TO_CONTROL"])
    endpoint(HEALTH, False, ["TO_CONTROL" if node == 0 else "TO_PLATFORMS"])
    endpoint(POSE, node != 0, ["POSITION"])
    if node:
        endpoint(POSE, False, ["POSITION"])
    endpoint(0, node == 0, ["COMMAND_P1" if node == 0 else f"COMMAND_P{node}"])

    def add_operational():
        if node == 0:
            return
        for kind in kinds:
            if kind in (0, POSE):
                continue
            if goal and kind in squad_kinds:
                partitions = ["POSITION"]
            elif goal and kind in selected_team_kinds and two:
                partitions = ["PLATFORM", "TEAM_DATA"]
            else:
                partitions = (["PLATFORM", f"TEAM_{team}"]
                              if kind == 12 and variant != "B" and not goal else ["PLATFORM"])
            endpoint(kind, True, partitions)
            if goal and kind in squad_kinds:
                endpoint(kind, False, ["POSITION"])
        if node:
            for kind in selected_team_kinds:
                partitions = (["TEAM_DATA"] if goal and two else
                              [f"P{node}" if variant == "B" else f"TEAM_{team}"])
                endpoint(kind, False, partitions)

    if not staged:
        add_operational()

    def learn_control_types():
        if node != 0 or not activated:
            return
        needed = {f"Scale{inventory[kind][0]}": kind for kind in kinds
                  if kind not in (0, POSE) and (kind, False) not in endpoints}
        if not needed:
            return
        for data, info in builtin_readers["main"][0].select().state(dds.DataState.any).read():
            if not info.valid or data.topic_name not in needed:
                continue
            kind = needed[data.topic_name]
            dtype = data.type
            if dtype is not None and (kind, False) not in endpoints:
                learned_types[kind] = dtype
                endpoint(kind, False, ["POSITION"] if goal and kind in squad_kinds
                         else ["PLATFORM"], learned=dtype)

    def update_membership(updated):
        nonlocal membership, team, assigned, participant_partition_updates, endpoint_parent_updates
        membership = updated
        if node == 0:
            return
        new_team = membership[node]
        if assigned and new_team == team:
            return
        team = new_team
        if two:
            policy = participants["main"].qos
            policy.partition = dds.Partition([f"P{node}", f"TEAM_{team}"])
            participants["main"].qos = policy
            participant_partition_updates += 1
        else:
            for kind in selected_team_kinds:
                for writer in (True, False):
                    parent = endpoints[(kind, writer)][1]
                    policy = parent.qos
                    policy.partition = dds.Partition(
                        ["PLATFORM", f"TEAM_{team}"] if writer else [f"TEAM_{team}"])
                    parent.qos = policy
                    endpoint_parent_updates += 1
        assigned = True

    def enable():
        nonlocal activated, assigned
        if goal:
            update_membership(membership)
        elif two and node:
            policy = participants["main"].qos
            policy.partition = dds.Partition([f"P{node}", f"TEAM_{team}"])
            participants["main"].qos = policy
        if not goal and variant == "B" and node:
            for writer in (True, False):
                parent = endpoints[(12, writer)][1]
                policy = parent.qos
                policy.partition = dds.Partition(
                    ["PLATFORM", f"TEAM_{team}"] if writer else [f"TEAM_{team}"])
                parent.qos = policy
        if not activated:
            add_operational()
            activated = True
        assigned = True

    def send(kind, target_size=None, feedback_kind=1):
        nonlocal sequence
        entity, _, dtype, _ = endpoints[(kind, True)]
        sequence += 1
        sample = dds.DynamicData(dtype)
        sample["source"] = node
        sample["instance"] = sequence % 10 if kind == (12 if optimized else 2) else 0
        sample["stamp"] = (phase << 56) | sequence
        sample["session"] = session
        if optimized and kind != HEALTH:
            for field, value in OPTIMIZED_SAMPLE_VALUES[kind].items():
                sample[field] = value
        if optimized and kind == 5:
            if feedback_kind not in range(1, 6):
                raise ValueError("feedback kind must identify ack, control/transfer report, or transfer/control request")
            sample["feedback_kind"] = feedback_kind
            sample["command_id"] = sequence
        size = target_size if target_size is not None else (
            82 if kind == HEALTH else
            inventory[0][1] if kind == 0 else
            284 if kind == (12 if optimized else 2) else (inventory[kind][1] + inventory[kind][2]) // 2)
        profile = inventory[13] if kind == HEALTH else inventory[kind]
        measured = size_payload(sample, size, profile[1], profile[2])
        key = f"{node}:{session}:{sequence}"
        when = time.monotonic()
        entity.write(sample)
        sent[key] = dict(source=node, kind=kind, phase=phase, sent_at=when,
                         serialized_bytes=measured, target_bytes=size)
        if goal:
            sent[key]["expected_receivers"] = recipients(
                node, kind, platforms, team_size, team_topic_count, membership, assigned, optimized)
        if optimized and kind == 5:
            sent[key]["feedback_kind"] = feedback_kind
        if optimized and kind != HEALTH:
            sent[key]["content"] = {field: sample[field] for field, _ in OPTIMIZED_FIELDS[kind]}
        return key

    def drain():
        for (kind, writer), (entity, _, _, _) in endpoints.items():
            if writer:
                continue
            for sample, info in entity.take():
                if not info.valid or int(sample["source"]) == node:
                    continue
                stamp = int(sample["stamp"])
                key = f"{sample['source']}:{sample['session']}:{stamp & ((1 << 56) - 1)}"
                received[key] = dict(kind=kind, phase=stamp >> 56,
                                     received_at=time.monotonic())
                if optimized and kind != HEALTH:
                    received[key]["content"] = {field: sample[field] for field, _ in OPTIMIZED_FIELDS[kind]}

    def snapshot(ledger, metadata):
        drain()
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result = dict(
            node=node, cpu_s=usage.ru_utime + usage.ru_stime, max_rss_kib=usage.ru_maxrss,
            goal_model_version=model_version if goal else 0,
            endpoint_count=len(endpoints), control_wire_learned_types=len(learned_types),
            membership=membership.copy(), assigned=assigned,
            phase_ends=phase_ends.copy(),
            participant_partition_updates=participant_partition_updates,
            endpoint_parent_updates=endpoint_parent_updates,
            participants={label: dict(guid=str(p.instance_handle)[:24].lower(),
                                      peer_count=len(p.discovered_participants()),
                                      partitions=list(p.qos.partition.name),
                                      protocol_plugins=int(p.qos.discovery_config.builtin_discovery_plugins))
                          for label, p in participants.items()},
            endpoints={f"{kind}:{int(writer)}": dict(
                participant=label,
                identity=str(entity.instance_handle),
                matches=len(entity.matched_subscriptions if writer else entity.matched_publications))
                for (kind, writer), (entity, _, _, label) in endpoints.items()},
            health_qos={str(int(writer)): dict(
                lease_s=entity.qos.liveliness.lease_duration.sec,
                deadline_s=entity.qos.deadline.period.sec,
                assertions_per_lease=entity.qos.liveliness.assertions_per_lease_duration)
                for (kind, writer), (entity, _, _, _) in endpoints.items() if kind == HEALTH},
            sent_count=len(sent), received_count=len(received))
        if metadata:
            for (kind, writer), (_, parent, _, _) in endpoints.items():
                result["endpoints"][f"{kind}:{int(writer)}"]["partitions"] = list(parent.qos.partition.name)
            for label, p in participants.items():
                for category, reader in zip(("publications", "subscriptions"),
                                            builtin_readers[label]):
                    owners = {}
                    for data, info in reader.select().state(dds.DataState.any).read():
                        handle = str(info.instance_handle)
                        if info.valid and info.state.instance_state == dds.InstanceState.ALIVE:
                            owners[handle] = "".join(f"{int(word):08x}"
                                                    for word in data.participant_key.value)[:24]
                        else:
                            owners.pop(handle, None)
                    result["participants"][label][category+"_by_owner"] = dict(Counter(owners.values()))
        if ledger:
            result.update(sent=sent, received=received)
        return result

    connection.send({"ready": node})
    try:
        running = True
        while running:
            if connection.poll(0.01):
                request = connection.recv()
                operation = request["op"]
                if operation == "snapshot":
                    result = snapshot(request.get("ledger", False), request.get("metadata", False))
                elif operation == "phase":
                    if phase != request["phase"]:
                        phase_ends[phase] = time.monotonic()
                    phase = request["phase"]
                    result = phase
                elif operation == "enable":
                    enable()
                    result = True
                elif operation == "membership" and goal:
                    update_membership(request["membership"])
                    result = True
                elif operation == "send":
                    result = send(request["kind"], request.get("size"), request.get("feedback_kind", 1))
                elif operation == "stop":
                    result, running = True, False
                else:
                    raise ValueError(f"unknown operation {operation}")
                connection.send({"result": result})
            now = time.monotonic()
            if running:
                learn_control_types()
                schedules = [(HEALTH, 1), (0, 0.1 if phase != ENABLE else 2)] if node == 0 else [
                    (HEALTH, 1), (POSE, 1)]
                if node and goal and activated:
                    schedules.extend((kind, 1) for kind in squad_kinds if kind != POSE)
                    schedules.extend((kind, inventory[kind][3]) for kind in selected_team_kinds)
                if node and phase == LOADED and activated:
                    schedules.extend((kind, inventory[kind][3]) for kind in kinds
                                     if kind != POSE and inventory[kind][3] > 0)
                    schedules = list(dict(schedules).items())
                for kind, rate in schedules:
                    if now - last.get(kind, -100) >= 1 / rate:
                        send(kind)
                        last[kind] = now
                drain()
    finally:
        for p in reversed(list(participants.values())):
            p.close()


def worker_entry(*args):
    try:
        node_worker(*args)
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
        args[0].send({"error": error})
        raise
    finally:
        args[0].close()


class ScaleRig(Rig):
    def __init__(self, variant, platforms, team_size, schemas, workdir, health_profile,
                 team_topic_count=1, protocol="SPDP2", model_version=1):
        super().__init__(variant, 201, workdir)
        self.platforms, self.team_size, self.schemas = platforms, team_size, schemas
        self.health_profile = health_profile
        self.team_topic_count, self.protocol = team_topic_count, protocol
        self.model_version = model_version
        self.optimized = team_topic_count > 1 and model_version == 2

    def start_nodes(self):
        for node in range(self.platforms + 1):
            parent, child = self.context.Pipe()
            process = self.context.Process(target=worker_entry, args=(
                child, node, self.variant, self.platforms, self.team_size, self.schemas,
                self.health_profile, self.team_topic_count, self.protocol, self.model_version))
            process.start()
            child.close()
            self.workers[node] = (parent, process)
        deadline = time.monotonic() + 90
        for node, (connection, _) in self.workers.items():
            if not connection.poll(max(0, deadline - time.monotonic())):
                raise TimeoutError(f"node {node} failed to start")
            result = connection.recv()
            if result != {"ready": node}:
                raise RuntimeError(f"startup failed: {result}")

    def snapshots(self, ledger=False, metadata=False):
        return {node: self.rpc(node, "snapshot", ledger=ledger, metadata=metadata) for node in self.workers}


def validate_metadata_scope(snapshots, variant, team_size, assigned, membership=None):
    snapshots = {int(node): snapshot for node, snapshot in snapshots.items()}
    owners = {p["guid"]: (node, label) for node, s in snapshots.items()
              for label, p in s["participants"].items()}
    visible = {}
    for node, snapshot in snapshots.items():
        total = 0
        for label, participant in snapshot["participants"].items():
            expected = set()
            for prefix, (other, other_label) in owners.items():
                if prefix == participant["guid"]:
                    continue
                if (node == 0 or other == 0 or variant != "A"
                        or label == other_label == "position"
                        or (assigned and label == other_label == "main"
                            and ((membership[node] == membership[other]) if membership is not None
                                 else (node-1)//team_size == (other-1)//team_size))):
                    expected.add(prefix)
            for category, writer in (("publications", True), ("subscriptions", False)):
                observed = {p: count for p, count in participant[category+"_by_owner"].items()
                            if p != participant["guid"]}
                wanted = {
                    p: sum(e["participant"] == owners[p][1] and key.endswith(f":{int(writer)}")
                           for key, e in snapshots[owners[p][0]]["endpoints"].items())
                    for p in expected
                }
                wanted = {p: count for p, count in wanted.items() if count}
                if observed != wanted:
                    raise AssertionError(
                        f"endpoint metadata scope failed P{node}/{label}/{category}: "
                        f"observed={observed}, expected={wanted}")
                total += sum(observed.values())
        if node:
            visible[node] = total
    return visible


def audit(snapshots, platforms, team_size, phases, team_topic_count=1):
    cutoff = time.monotonic() - 2
    sends, receives, missed, leaks, latencies = 0, 0, [], [], []
    sizes = {}
    for source, snapshot in snapshots.items():
        for key, sample in snapshot["sent"].items():
            phase_cutoff = cutoff
            if team_topic_count > 1:
                phase_cutoff = min(cutoff, snapshot.get("phase_ends", {}).get(
                    sample["phase"], cutoff + 2) - 2)
            if sample["phase"] not in phases or sample["sent_at"] > phase_cutoff:
                continue
            if (team_topic_count > 1 and sample["phase"] == ENABLE
                    and sample["kind"] in team_kinds(
                        team_topic_count, snapshot.get("goal_model_version") == 2)):
                continue
            sends += 1
            sizes.setdefault(str(sample["kind"]), set()).add(sample["serialized_bytes"])
            targets = set(sample["expected_receivers"] if "expected_receivers" in sample
                          else recipients(source, sample["kind"], platforms, team_size))
            actual = {node for node, other in snapshots.items() if key in other["received"]}
            missed.extend((key, node) for node in targets - actual)
            leaks.extend((key, node) for node in actual - targets)
            receives += len(actual & targets)
            for node in actual & targets:
                if ("content" in sample
                        and snapshots[node]["received"][key].get("content") != sample["content"]):
                    raise AssertionError(f"consolidated payload mismatch for {key} at P{node}")
                latencies.append((sample["kind"], sample["phase"],
                                  (snapshots[node]["received"][key]["received_at"]
                                   - sample["sent_at"]) * 1000))
    if missed or leaks:
        raise AssertionError(f"delivery failed: missing={missed[:10]} leaks={leaks[:10]}")
    return dict(sends=sends, destination_receives=receives, missing=0, unintended=0,
                serialized_sizes={kind: sorted(values) for kind, values in sizes.items()},
                enable_command_max_latency_ms=max(
                    (delay for kind, phase, delay in latencies if kind == 0 and phase == ENABLE),
                    default=None),
                pose_max_latency_ms=max((delay for kind, _, delay in latencies if kind == POSE),
                                        default=None))


def goal_requirements(snapshots, platforms, team_topic_count, membership, variant, assigned,
                      optimized=False):
    requirements = []
    selected = team_kinds(team_topic_count, optimized)
    for node, snapshot in snapshots.items():
        for key in snapshot["endpoints"]:
            kind, writer = (int(value) for value in key.split(":"))
            if kind == HEALTH:
                count = platforms if node == 0 else 1
            elif kind == 0:
                count = 1 if node == 0 else int(node == 1)
            elif node == 0:
                count = platforms
            elif kind in model_squad_kinds(optimized):
                count = platforms + 1 if writer else platforms
            elif kind in selected:
                if assigned:
                    count = sum(team == membership[node] for team in membership.values()) + int(writer)
                else:
                    count = (2 if writer else 1) if variant == "A" else int(writer)
            else:
                count = 1
            requirements.append((node, key, count))
    return requirements


def validate_goal_partitions(snapshots, membership, team_topic_count, variant, assigned,
                             optimized=False):
    for node, snapshot in snapshots.items():
        if snapshot["membership"] != membership:
            raise AssertionError(f"P{node} has stale membership")
        if node == 0:
            continue
        team = membership[node]
        wanted_dp = ([f"P{node}"] + ([f"TEAM_{team}"] if assigned else [])
                     if variant == "A" else ["SHARED"])
        if snapshot["participants"]["main"]["partitions"] != wanted_dp:
            raise AssertionError(f"P{node} has incorrect participant partitions")
        for kind in team_kinds(team_topic_count, optimized):
            for writer in (True, False):
                if variant == "A":
                    wanted = ["PLATFORM", "TEAM_DATA"] if writer else ["TEAM_DATA"]
                elif assigned:
                    wanted = ["PLATFORM", f"TEAM_{team}"] if writer else [f"TEAM_{team}"]
                else:
                    wanted = ["PLATFORM"] if writer else [f"P{node}"]
                actual = snapshot["endpoints"][f"{kind}:{int(writer)}"]["partitions"]
                if actual != wanted:
                    raise AssertionError(f"P{node}/{kind}/{writer}: {actual} != {wanted}")


def wait_goal_convergence(rig, membership, assigned, timeout=60):
    start = time.monotonic()
    failure = "no snapshots"
    while time.monotonic() - start < timeout:
        snapshots = rig.snapshots(metadata=True)
        requirements = goal_requirements(
            snapshots, rig.platforms, rig.team_topic_count, membership, rig.variant, assigned,
            rig.optimized)
        try:
            expected_endpoints = endpoint_inventory(rig.platforms, rig.team_topic_count, rig.optimized)
            if sum(snapshot["endpoint_count"] for snapshot in snapshots.values()) != expected_endpoints:
                raise AssertionError("goal endpoints are not all active")
            if any(snapshots[node]["endpoints"][key]["matches"] != count
                   for node, key, count in requirements):
                raise AssertionError("goal endpoint match counts have not converged")
            validate_goal_partitions(snapshots, membership, rig.team_topic_count, rig.variant,
                                     assigned, rig.optimized)
            visible = validate_metadata_scope(snapshots, rig.variant, rig.team_size, assigned, membership)
        except AssertionError as exc:
            failure = str(exc)
            time.sleep(0.05)
            continue
        return snapshots, visible, (time.monotonic() - start) * 1000
    raise TimeoutError(f"goal membership convergence timed out: {failure}")


def probe_goal_receivers(rig, membership, timeout=10):
    probes = {}
    for node in range(1, rig.platforms + 1):
        for kind in (*model_squad_kinds(rig.optimized), *team_kinds(rig.team_topic_count, rig.optimized)):
            key = rig.rpc(node, "send", kind=kind)
            probes[key] = set(recipients(
                node, kind, rig.platforms, rig.team_size,                 rig.team_topic_count, membership, optimized=rig.optimized))
    deadline = time.monotonic() + timeout
    while True:
        snapshots = rig.snapshots(ledger=True)
        missing, unintended = [], []
        for key, expected in probes.items():
            actual = {node for node, snapshot in snapshots.items() if key in snapshot["received"]}
            missing.extend((key, node) for node in expected - actual)
            unintended.extend((key, node) for node in actual - expected)
        if unintended:
            raise AssertionError(f"post-change probes leaked: {unintended[:10]}")
        if not missing:
            return len(probes)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"post-change probes missing: {missing[:10]}")
        time.sleep(0.05)


def measure_change(rig, name, phase, settled_phase, before, after, windows, hold_seconds):
    before_snapshots = rig.snapshots(metadata=True)
    before_metadata = validate_metadata_scope(
        before_snapshots, rig.variant, rig.team_size, True, before)
    changed = [node for node in sorted(before) if before[node] != after[node]]
    rig.phase(phase)
    start, monotonic_start = time.time(), time.monotonic()
    for node in rig.workers:
        rig.rpc(node, "membership", membership=after)
    snapshots, after_metadata, _ = wait_goal_convergence(rig, after, True)
    convergence_ms = (time.monotonic() - monotonic_start) * 1000
    guids_preserved = all(
        before_snapshots[node]["participants"][label]["guid"] == participant["guid"]
        for node, snapshot in snapshots.items()
        for label, participant in snapshot["participants"].items())
    endpoints_preserved = all(
        {key: endpoint["identity"] for key, endpoint in before_snapshots[node]["endpoints"].items()}
        == {key: endpoint["identity"] for key, endpoint in snapshot["endpoints"].items()}
        for node, snapshot in snapshots.items())
    if not guids_preserved or not endpoints_preserved:
        raise AssertionError("membership update recreated a participant or endpoint")
    dp_updates = sum(s["participant_partition_updates"] - before_snapshots[node]["participant_partition_updates"]
                     for node, s in snapshots.items())
    parent_updates = sum(s["endpoint_parent_updates"] - before_snapshots[node]["endpoint_parent_updates"]
                         for node, s in snapshots.items())
    if (dp_updates != (len(changed) if rig.variant == "A" else 0)
            or parent_updates != (2 * rig.team_topic_count * len(changed) if rig.variant == "B" else 0)):
        raise AssertionError("membership changes did not update the intended QoS surfaces")
    probes_sent = probe_goal_receivers(rig, after)
    time.sleep(2)
    windows[f"{name}_change"] = (start, time.time())
    rig.phase(settled_phase)
    settled_start = time.time()
    time.sleep(hold_seconds)
    windows[f"{name}_settled"] = (settled_start, time.time())
    ledger = rig.snapshots(ledger=True)
    strict = audit(ledger, rig.platforms, rig.team_size, {settled_phase}, rig.team_topic_count)
    if strict["sends"] < rig.platforms:
        raise AssertionError("settled membership window lacks application sends")
    return dict(
        name=name, window=f"{name}_change", settled_window=f"{name}_settled",
        membership_before=before, membership_after=after, changed_nodes=changed,
        participant_partition_updates=dp_updates, endpoint_parent_updates=parent_updates,
        convergence_ms=convergence_ms, guids_preserved=guids_preserved,
        endpoints_preserved=endpoints_preserved, probes_sent=probes_sent,
        probe_missing=0, probe_unintended=0, before_metadata_records=before_metadata,
        after_metadata_records=after_metadata, settled_hold_seconds=hold_seconds,
        strict_audit=strict), rig.snapshots(metadata=True)


def run(args):
    inputs = {"schemas.xml": args.schemas,
              "schema_manifest.json": args.schemas.with_name("schema_manifest.json")}
    provenance = execution_provenance(SCALE_SOURCE_FILES, inputs)
    if args.manifest is not None:
        validate_provenance(provenance, json.loads(args.manifest.read_text()))
    args.output.mkdir(parents=True, exist_ok=False)
    pcap = args.output / "scale.pcapng"
    capture_log = (args.output / "capture.log").open("w")
    capture = subprocess.Popen(
        ["dumpcap", "-i", "lo", "-B", str(CAPTURE_BUFFER_MIB),
         "-f", "udp portrange 57650-57899",
         "-a", f"duration:{args.seconds + 600}", "-w", str(pcap), "-q"],
        stdout=capture_log, stderr=capture_log)
    rig = ScaleRig(args.variant, args.platforms, args.team_size, args.schemas, args.output,
                   args.health_profile, args.team_topic_count, args.protocol, args.goal_model_version)
    windows, checkpoints = {}, {}
    goal = args.team_topic_count > 1
    optimized = goal and args.goal_model_version == 2
    inventory = profiles(goal, optimized)
    kinds = active_kinds(optimized)
    membership = initial_membership(args.platforms, args.team_size)
    changes, pre_team_receivers = [], {}
    start = time.time()
    try:
        time.sleep(0.5)
        if capture.poll() is not None:
            raise RuntimeError("capture did not start")
        rig.start_nodes()
        base_requirements = [
            (0, f"{HEALTH}:1", args.platforms), (0, f"{HEALTH}:0", args.platforms),
            (0, f"{POSE}:0", args.platforms), (0, "0:1", 1)]
        base_requirements.extend((node, f"{POSE}:1", args.platforms + 1)
                                 for node in range(1, args.platforms + 1))
        rig.wait_matches(base_requirements, timeout=60)
        if args.variant != "C":
            rig.wait_matches([(0, f"{kind}:0", args.platforms) for kind in kinds if kind != 0],
                             timeout=60)
        if goal:
            wait_goal_convergence(rig, membership, False)
        rig.phase(PRE)
        time.sleep(3)
        probe_receivers = None
        if args.variant in ("A", "B"):
            key = rig.rpc(1, "send", kind=12, size=inventory[12][1])
            deadline = time.monotonic()+10
            while time.monotonic() < deadline:
                observed = rig.snapshots(ledger=True)
                actual = {node for node, snapshot in observed.items() if key in snapshot["received"]}
                if actual:
                    time.sleep(0.5)
                    observed = rig.snapshots(ledger=True)
                    actual = {node for node, snapshot in observed.items() if key in snapshot["received"]}
                    if actual != {0}:
                        raise AssertionError(f"team data delivered before assignment: {actual}")
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("pre-assignment contact did not reach control")
            probe_receivers = sorted(actual)
        checkpoints["before_assignment"] = rig.snapshots(metadata=True)
        before_metadata = validate_metadata_scope(
            checkpoints["before_assignment"], args.variant, args.team_size, False)
        if goal:
            for kind in team_kinds(args.team_topic_count, optimized):
                key = rig.rpc(1, "send", kind=kind)
                deadline = time.monotonic() + 10
                while True:
                    observed = rig.snapshots(ledger=True)
                    actual = sorted(node for node, snapshot in observed.items()
                                    if key in snapshot["received"])
                    if any(node != 0 for node in actual):
                        raise AssertionError(f"team topic {kind} leaked before assignment: {actual}")
                    if actual == [0]:
                        pre_team_receivers[str(kind)] = actual
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"team topic {kind} did not reach control before assignment")
                    time.sleep(0.05)
        windows["startup"] = (start, time.time())
        rig.phase(ENABLE)
        enable_start, enable_monotonic = time.time(), time.monotonic()
        for node in rig.workers:
            rig.rpc(node, "enable")
        if goal:
            wait_goal_convergence(rig, membership, True)
            probe_goal_receivers(rig, membership)
        else:
            requirements = [(0, f"{kind}:0", args.platforms) for kind in range(1, 14)]
            for node in range(1, args.platforms+1):
                team = (node-1)//args.team_size
                count = min(args.team_size, args.platforms-team*args.team_size)
                requirements.extend(((node, "12:0", count), (node, "12:1", count+1)))
            rig.wait_matches(requirements, timeout=60)
        enable_match_ms = (time.monotonic() - enable_monotonic) * 1000
        time.sleep(2)
        windows["activation"] = (enable_start, time.time())
        checkpoints["after_assignment"] = rig.snapshots(metadata=True)
        after_metadata = validate_metadata_scope(
            checkpoints["after_assignment"], args.variant, args.team_size, True)
        if args.health_profile == "scale_health30":
            for snapshot in checkpoints["after_assignment"].values():
                if any(policy != dict(lease_s=30, deadline_s=2, assertions_per_lease=3)
                       for policy in snapshot["health_qos"].values()):
                    raise AssertionError("health endpoints did not load the 30 s lease profile")
        learned_count = len(kinds) - 2
        if checkpoints["after_assignment"][0]["control_wire_learned_types"] != learned_count:
            raise AssertionError(f"control did not learn {learned_count} operational types from discovery")
        rig.phase(STABLE)
        stable_start = math.ceil(time.time())
        time.sleep(max(0, stable_start - time.time()))
        stable_cpu = {node: s["cpu_s"] for node, s in rig.snapshots().items()}
        while time.time() < stable_start + args.seconds:
            if capture.poll() is not None:
                raise RuntimeError("capture exited during stable measurement")
            time.sleep(max(0, min(1, stable_start + args.seconds - time.time())))
        windows["stable_position"] = (stable_start, stable_start + args.seconds)
        checkpoints["stable"] = rig.snapshots()
        if goal:
            swapped = swap_membership(membership)
            change, checkpoint = measure_change(
                rig, "swap", SWAP, SWAP_SETTLED, membership, swapped,
                windows, args.change_hold_seconds)
            changes.append(change)
            checkpoints["after_swap"] = checkpoint
            reassigned = reassignment_membership(swapped)
            change, checkpoint = measure_change(
                rig, "reassignment", REASSIGN, REASSIGN_SETTLED, swapped, reassigned,
                windows, args.change_hold_seconds)
            changes.append(change)
            checkpoints["after_reassignment"] = checkpoint
        rig.phase(LOADED)
        burst_start = time.time()
        for kind in (kinds if optimized else (1, 3, 4, 5, 6, 7, 8, 11)):
            if kind in (0, POSE):
                continue
            rig.rpc(1, "send", kind=kind, size=inventory[kind][1])
        if optimized:
            for feedback_kind in range(1, 6):
                rig.rpc(1, "send", kind=5, feedback_kind=feedback_kind)
        rig.rpc(0, "send", kind=0, size=inventory[0][1])
        for node, kind in (((0, 0), (1, 12), (1, 4), (1, 9))
                           if optimized else ((0, 0), (1, 2), (1, 4))):
            rig.rpc(node, "send", kind=kind, size=inventory[kind][2])
        time.sleep(2)
        windows["mission_burst"] = (burst_start, time.time())
        load_start = time.time()
        time.sleep(12)
        windows["operational"] = (load_start, time.time())
        time.sleep(2.2)
        snapshots = rig.snapshots(ledger=True)
        delivery = audit(snapshots, args.platforms, args.team_size,
                         {ENABLE, STABLE, LOADED, SWAP_SETTLED, REASSIGN_SETTLED},
                         args.team_topic_count)
        prefixes = {p["guid"]: f"P{node}/{label}" for node, s in snapshots.items()
                    for label, p in s["participants"].items()}
        endpoint_total = sum(s["endpoint_count"] for s in snapshots.values())
        expected_endpoints = (endpoint_inventory(args.platforms, args.team_topic_count, optimized)
                              if goal else 18 * args.platforms + 16)
        if endpoint_total != expected_endpoints:
            raise AssertionError(f"endpoint count {endpoint_total} != {expected_endpoints}")
    finally:
        try:
            rig.close()
        finally:
            try:
                stopped = stop_capture(capture)
            finally:
                capture_log.close()
    capture_result = capture_summary(pcap, prefixes, windows)
    capture_result["integrity"] = capture_integrity(
        args.output / "capture.log", stopped, capture_result, windows)
    if execution_provenance(SCALE_SOURCE_FILES, inputs) != provenance:
        raise ValueError("execution sources or generated schemas changed during measurement")
    participant_liveliness = liveliness_frames(
        pcap, prefixes, *windows["stable_position"])
    if args.health_profile == "scale_health30" and args.protocol == "SPDP2":
        for prefix, measurements in participant_liveliness.items():
            interval = 30 if prefixes[prefix].endswith("/position") else 10
            if args.seconds >= 2*interval:
                actual = measurements["median_logical_interval_s"]
                if actual is None or abs(actual-interval) > 0.25:
                    raise AssertionError(
                        f"unexpected {prefixes[prefix]} assertion cadence {actual}; expected {interval}")
    for summary in capture_result["windows"].values():
        summary["sedp_writer_frames_bytes"] = sum(
            count for key, count in summary["writer_entity_set_frame_bytes"].items()
            if key != "no_writer_field" and set(key.split(",")) <= {"000003c2", "000004c2"})
        if summary["sedp_writer_frames_bytes"] > summary["class_bytes"].get("discovery", 0):
            raise AssertionError("SEDP writer-frame bytes exceed classified discovery bytes")
    expected_names = {f"Scale{inventory[kind][0]}" for kind in kinds}
    if optimized:
        expected_names.add("ScaleHealth")
    if not expected_names <= set(capture_result["observed_topics"]):
        raise AssertionError("capture did not observe every exported topic name")
    result = dict(
        variant=args.variant, platforms=args.platforms, team_size=args.team_size,
        repetition=args.repetition, application_type_count=len(kinds), steady_seconds=args.seconds,
        health_profile=args.health_profile, capture_buffer_mib=CAPTURE_BUFFER_MIB,
        assignment_model_version=3 if goal else 2,
        assignment_validation=dict(
            pre_assignment_contact_receivers=probe_receivers,
            before_metadata_records=before_metadata, after_metadata_records=after_metadata),
        participant_liveliness=participant_liveliness,
        total_wan_dps=len(prefixes), active_endpoints=endpoint_total,
        enable_to_all_control_matches_ms=enable_match_ms,
        cpu_seconds_during_stable=sum(
            s["cpu_s"] - stable_cpu[node] for node, s in checkpoints["stable"].items()),
        worker_peak_rss_kib_sum=sum(s["max_rss_kib"] for s in snapshots.values()),
        delivery=delivery, capture=capture_result, guid_map=prefixes, checkpoints=checkpoints,
        execution_provenance=provenance,
        fidelity="Fourteen synthetic mission schemas; similar serialized samples, no TypeObject-size padding")
    if goal:
        result.update(
            goal_model_version=args.goal_model_version, team_topic_count=args.team_topic_count,
            protocol=args.protocol, protocol_verified=all(
                participant["protocol_plugins"] == (3 if args.protocol == "SPDP" else 18)
                for snapshot in snapshots.values() for participant in snapshot["participants"].values()),
            goal_datamodel=datamodel(args.team_topic_count, optimized), changes=changes,
            fidelity="Proposed squad/team model with compact synthetic coordination states; not reference schemas")
        result["assignment_validation"]["pre_assignment_team_receivers"] = pre_team_receivers
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in
                      ("variant", "platforms", "repetition", "total_wan_dps",
                       "active_endpoints", "enable_to_all_control_matches_ms")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--schemas", type=Path, required=True)
    parser.add_argument("--platforms", type=int, required=True)
    parser.add_argument("--variant", choices=list("ABC"), required=True)
    parser.add_argument("--seconds", type=int, default=150)
    parser.add_argument("--health-profile", choices=("scale_periodic", "scale_health30"),
                        default="scale_periodic")
    parser.add_argument("--team-size", type=int, default=3)
    parser.add_argument("--team-topic-count", type=int, choices=(1, 3, 6), default=1)
    parser.add_argument("--goal-model-version", type=int, choices=(1, 2), default=1)
    parser.add_argument("--protocol", choices=("SPDP", "SPDP2"), default="SPDP2")
    parser.add_argument("--change-hold-seconds", type=int, default=10)
    parser.add_argument("--repetition", type=int, default=1)
    args = parser.parse_args()
    if args.team_topic_count > 1 and args.goal_model_version == 2 and args.team_topic_count != 3:
        parser.error("optimized model requires three consolidated team topics")
    if not 2 <= args.platforms <= 32 or args.seconds < 1 or not 1 <= args.team_size <= 32:
        parser.error("platforms must be 2-32; durations positive; team size 1-32")
    if args.team_topic_count > 1 and (
            args.variant == "C" or args.team_size < 2 or args.platforms % args.team_size
            or args.platforms // args.team_size < 3 or args.change_hold_seconds < 8):
        parser.error("goal model needs A/B, at least three complete teams of size >=2, and change holds >=8 s")
    if args.team_topic_count == 1 and args.protocol != "SPDP2":
        parser.error("historical contact-only mode is retained for SPDP2 reproduction; use goal counts 3/6 for protocol comparisons")
    run(args)
