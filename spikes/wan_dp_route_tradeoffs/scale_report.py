#!/usr/bin/env python3
"""Retain compact, customer-shaped synthetic scale measurements and fidelity limits."""

import argparse
import json
import math
from pathlib import Path
import statistics
from spike import validate_capture_integrity, validate_provenance
from goal_model import datamodel, endpoint_inventory, team_kinds


LABELS = {
    "A": "2 DPs/platform, eager",
    "B": "1 DP/platform, eager",
    "C": "1 DP/platform, staged topics",
}

def setup_discovery_comparison(cases):
    for r in cases.values():
        values = [r[field] for field in
                  ("startup_discovery_bytes", "activation_discovery_bytes", "convergence_discovery_bytes")]
        if (any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
                for value in values) or values[0] + values[1] != values[2]):
            raise ValueError("invalid startup/activation discovery totals")
    totals = {variant: r["convergence_discovery_bytes"] for variant, r in cases.items()}
    winners = {variant for variant, total in totals.items() if total == min(totals.values())}
    difference = (totals["B"] - totals["A"]) / totals["A"] * 100 if totals["A"] else None
    return difference, winners


def row(result):
    windows = result["capture"]["windows"]
    startup, activation = windows["startup"], windows["activation"]
    stable, operational = windows["stable_position"], windows["operational"]
    startup_discovery = startup["class_bytes"].get("discovery", 0)
    activation_discovery = activation["class_bytes"].get("discovery", 0)
    assignment = result.get("assignment_validation")
    reported = dict(
        platforms=result["platforms"], variant=result["variant"],
        team_size=result["team_size"],
        repetition=result["repetition"], wan_dps=result["total_wan_dps"],
        application_type_count=result["application_type_count"],
        steady_window_seconds=stable["duration_s"],
        capture_accounting_version=result["capture"].get("accounting_version", 1),
        health_profile=result.get("health_profile", "scale_periodic"),
        assignment_model_version=result.get("assignment_model_version", 1),
        assignment_validation=assignment,
        remote_endpoint_records_before=statistics.mean(assignment["before_metadata_records"].values())
        if assignment is not None else None,
        remote_endpoint_records_after=statistics.mean(assignment["after_metadata_records"].values())
        if assignment is not None else None,
        participant_liveliness=result.get("participant_liveliness"),
        execution_provenance=result.get("execution_provenance"),
        steady_participant_message_frames_kbps=sum(
            v["whole_frame_bytes"] for v in result["participant_liveliness"].values())*8/1000/stable["duration_s"]
        if "participant_liveliness" in result else None,
        active_endpoints=result["active_endpoints"],
        preassignment_endpoints=sum(
            s["endpoint_count"] for s in result["checkpoints"]["before_assignment"].values()),
        control_wire_learned_types=(
            result["checkpoints"]["after_assignment"]["0"]["control_wire_learned_types"]),
        startup_discovery_bytes=startup_discovery,
        activation_discovery_bytes=activation_discovery,
        convergence_discovery_bytes=startup_discovery + activation_discovery,
        startup_sedp_writer_frames_bytes=startup["sedp_writer_frames_bytes"],
        activation_sedp_writer_frames_bytes=activation["sedp_writer_frames_bytes"],
        convergence_sedp_writer_frames_bytes=(startup["sedp_writer_frames_bytes"]
                                              + activation["sedp_writer_frames_bytes"]),
        steady_discovery_kbps=stable["discovery_tx_Bps"] * 8 / 1000,
        steady_discovery_peak_kbps=stable["discovery_peak_1s_Bps"] * 8 / 1000,
        steady_total_kbps=stable["total_tx_Bps"] * 8 / 1000,
        operational_total_kbps=operational["total_tx_Bps"] * 8 / 1000,
        mission_burst_total_bytes=windows["mission_burst"]["total_bytes"],
        enable_to_all_control_matches_ms=result["enable_to_all_control_matches_ms"],
        enable_command_max_latency_ms=result["delivery"]["enable_command_max_latency_ms"],
        pose_max_latency_ms=result["delivery"]["pose_max_latency_ms"],
        stable_worker_cpu_seconds=result["cpu_seconds_during_stable"],
        worker_peak_rss_mib_sum=result["worker_peak_rss_kib_sum"] / 1024,
        audited_sends=result["delivery"]["sends"],
        audited_destination_receives=result["delivery"]["destination_receives"],
        missing=result["delivery"]["missing"], unintended=result["delivery"]["unintended"],
        serialized_sizes=result["delivery"]["serialized_sizes"],
        guid_map=result["guid_map"], capture=result["capture"],
    )
    if "goal_model_version" in result:
        reported.update(
            goal_model_version=result["goal_model_version"],
            team_topic_count=result["team_topic_count"],
            protocol=result["protocol"],
            protocol_verified=result.get("protocol_verified"),
            goal_datamodel=result["goal_datamodel"],
            changes=result.get("changes", []),
        )
        for change in reported["changes"]:
            name = change["name"]
            window = windows[change["window"]]
            settled = windows[change["settled_window"]]
            reported.update({
                f"{name}_total_bytes": window["total_bytes"],
                f"{name}_discovery_bytes": window["class_bytes"].get("discovery", 0),
                f"{name}_sedp_writer_frames_bytes": window["sedp_writer_frames_bytes"],
                f"{name}_discovery_peak_kbps": window["discovery_peak_1s_Bps"] * 8 / 1000,
                f"{name}_duration_seconds": window["duration_s"],
                f"{name}_convergence_ms": change["convergence_ms"],
                f"{name}_participant_partition_updates": change["participant_partition_updates"],
                f"{name}_endpoint_parent_updates": change["endpoint_parent_updates"],
                f"{name}_remote_endpoint_records_before": statistics.mean(
                    change["before_metadata_records"].values()),
                f"{name}_remote_endpoint_records_after": statistics.mean(
                    change["after_metadata_records"].values()),
                f"{name}_settled_discovery_kbps": settled["discovery_tx_Bps"] * 8 / 1000,
                f"{name}_settled_discovery_bytes": settled["class_bytes"].get("discovery", 0),
                f"{name}_settled_total_bytes": settled["total_bytes"],
                f"{name}_settled_sedp_writer_frames_bytes": settled["sedp_writer_frames_bytes"],
                f"{name}_settled_discovery_peak_kbps": settled["discovery_peak_1s_Bps"] * 8 / 1000,
                f"{name}_settled_hold_seconds": change["settled_hold_seconds"],
            })
    return reported


def group_key(population, variant, team_size, team_topic_count=None, protocol=None):
    if team_topic_count is not None or protocol is not None:
        if team_topic_count is None or protocol is None:
            raise ValueError("goal grouping requires both team-topic count and protocol")
        return f"{population}:t{team_size}:k{team_topic_count}:{protocol}:{variant}"
    return f"{population}:{variant}" if team_size in (None, 3) else f"{population}:t{team_size}:{variant}"


def aggregates(rows):
    goal_versions = {r.get("goal_model_version") for r in rows}
    if len(goal_versions) > 1:
        raise ValueError("cannot aggregate historical and goal model rows")
    if len({r.get("capture_accounting_version", 1) for r in rows}) > 1:
        raise ValueError("cannot aggregate different capture accounting versions")
    if (len({r.get("health_profile", "scale_periodic") for r in rows}) > 1
            or len({r.get("assignment_model_version", 1) for r in rows}) > 1):
        raise ValueError("cannot aggregate different health profiles or assignment models")
    has_teams = ["team_size" in r for r in rows]
    if any(has_teams) and not all(has_teams):
        raise ValueError("cannot combine historical rows without team sizes and team-aware rows")
    groups = {}
    dimensions = {(r["platforms"], r.get("team_size"), r["variant"],
                   r.get("team_topic_count"), r.get("protocol")) for r in rows}
    for population, team_size, variant, team_topic_count, protocol in sorted(dimensions):
        selected = [r for r in rows if r["platforms"] == population and r["variant"] == variant
                    and r.get("team_size") == team_size
                    and r.get("team_topic_count") == team_topic_count
                    and r.get("protocol") == protocol]
        numeric = {}
        for name, value in selected[0].items():
            if name in ("platforms", "team_size", "repetition", "capture_accounting_version",
                        "assignment_model_version", "goal_model_version", "team_topic_count",
                        "protocol_verified") or not isinstance(value, (int, float)):
                continue
            values = [r[name] for r in selected]
            numeric[name] = dict(median=statistics.median(values), min=min(values), max=max(values))
        group = dict(
            population=population, team_size=team_size, variant=variant,
            repetitions=len(selected), metrics=numeric)
        if team_topic_count is not None:
            group.update(team_topic_count=team_topic_count, protocol=protocol,
                         goal_model_version=selected[0]["goal_model_version"])
        groups[group_key(population, variant, team_size, team_topic_count, protocol)] = group
    return groups


def validate_rows(rows, manifest):
    if "goal_model_version" in manifest or any("goal_model_version" in r for r in rows):
        validate_goal_rows(rows, manifest)
        return
    if "team_sizes" in manifest:
        expected = {
            (population, team_size, variant, repetition)
            for population in manifest["populations"]
            for team_size in manifest["team_sizes"]
            for variant in manifest["variants"]
            for repetition in range(1, manifest["repetitions"] + 1)
        }
        actual = [(r["platforms"], r["team_size"], r["variant"], r["repetition"]) for r in rows]
    else:
        # Historical manifests used a fixed team size of three.
        if any("team_size" in r and r["team_size"] != 3 for r in rows):
            raise ValueError("historical manifest cannot describe a different team size")
        expected = {
            (population, variant, repetition)
            for population in manifest["populations"]
            for variant in manifest["variants"]
            for repetition in range(1, manifest["repetitions"] + 1)
        }
        actual = [(r["platforms"], r["variant"], r["repetition"]) for r in rows]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("incomplete or duplicate population/team/design/repetition matrix")
    for r in rows:
        population, variant = r["platforms"], r["variant"]
        if "execution_provenance_version" in manifest:
            if manifest["execution_provenance_version"] != 1:
                raise ValueError("unsupported execution provenance version")
            validate_provenance(r.get("execution_provenance"), manifest)
        if "capture_integrity_version" in manifest:
            if manifest["capture_integrity_version"] != 1:
                raise ValueError("unsupported capture integrity version")
            validate_capture_integrity(r.get("capture", {}).get("integrity"), r.get("capture", {}))
        if ("health_profile" in manifest and r.get("health_profile") != manifest["health_profile"]):
            raise ValueError("health profile mismatch")
        if "assignment_model_version" in manifest:
            proof = r.get("assignment_validation")
            if r.get("assignment_model_version") != manifest["assignment_model_version"] or proof is None:
                raise ValueError("assignment model/proof mismatch")
            if variant in ("A", "B") and proof["pre_assignment_contact_receivers"] != [0]:
                raise ValueError("team route was not closed before assignment")
        if ("capture_accounting_version" in manifest
                and r.get("capture_accounting_version") != manifest["capture_accounting_version"]):
            raise ValueError(f"capture accounting version mismatch: {population}/{variant}")
        if r["application_type_count"] != 14 or r["steady_window_seconds"] != manifest["seconds"]:
            raise ValueError(f"incorrect type count or steady duration: {population}/{variant}")
        expected_dps = 2 * population + 1 if variant == "A" else population + 1
        expected_endpoints = 18 * population + 16
        expected_preassignment = 5 * population + 4 if variant == "C" else expected_endpoints
        if (r["wan_dps"] != expected_dps
                or r["active_endpoints"] != expected_endpoints
                or r["preassignment_endpoints"] != expected_preassignment):
            raise ValueError(f"incorrect participant/endpoint inventory: {population}/{variant}")
        if r["control_wire_learned_types"] != 12:
            raise ValueError(f"incomplete discovered operational types: {population}/{variant}")
        if r["missing"] or r["unintended"]:
            raise ValueError(f"delivery acceptance failed: {population}/{variant}")
        if r["enable_command_max_latency_ms"] is None:
            raise ValueError(f"no measured activation command delivery: {population}/{variant}")


def _nonnegative(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _metadata(records):
    return (isinstance(records, dict) and bool(records)
            and all(_nonnegative(value) for value in records.values()))


def validate_goal_rows(rows, manifest):
    """Reject incomplete goal matrices and unproved membership/capture transitions."""
    if (manifest.get("goal_model_version") not in (1, 2)
            or manifest.get("assignment_model_version") != 3):
        raise ValueError("unsupported goal/assignment model")
    for key, version in (("execution_provenance_version", 1),
                         ("capture_integrity_version", 1),
                         ("capture_accounting_version", 2)):
        if manifest.get(key) != version:
            raise ValueError(f"goal report requires {key}={version}")
    if (manifest.get("health_profile") != "scale_health30"
            or set(manifest.get("required_changes", [])) != {"swap", "reassignment"}
            or len(manifest["required_changes"]) != 2):
        raise ValueError("goal report requires scale_health30 and both membership changes")
    if (not manifest.get("team_topic_counts")
            or not set(manifest["team_topic_counts"]) <= {3, 6}
            or not manifest.get("protocols")
            or not set(manifest["protocols"]) <= {"SPDP", "SPDP2"}
            or set(manifest.get("variants", [])) != {"A", "B"}):
        raise ValueError("unsupported goal matrix dimensions")
    optimized = manifest["goal_model_version"] == 2
    if optimized and manifest["team_topic_counts"] != [3]:
        raise ValueError("optimized goal requires three consolidated team topics")
    expected = {
        (p, t, k, protocol, variant, repetition)
        for p in manifest["populations"] for t in manifest["team_sizes"]
        for k in manifest["team_topic_counts"] for protocol in manifest["protocols"]
        for variant in manifest["variants"]
        for repetition in range(1, manifest["repetitions"] + 1)
    }
    actual = [(r["platforms"], r["team_size"], r["team_topic_count"], r["protocol"],
               r["variant"], r["repetition"]) for r in rows]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("incomplete or duplicate population/team/k/protocol/design/repetition matrix")
    for k in manifest["team_topic_counts"]:
        models = {json.dumps(r.get("goal_datamodel"), sort_keys=True)
                  for r in rows if r["team_topic_count"] == k}
        if len(models) != 1:
            raise ValueError("inconsistent goal datamodel within a team-topic dimension")
    for r in rows:
        n, t, k, variant = r["platforms"], r["team_size"], r["team_topic_count"], r["variant"]
        if n % t or n // t < 3:
            raise ValueError("goal changes require at least three complete teams")
        if (r.get("goal_model_version") != manifest["goal_model_version"] or r.get("assignment_model_version") != 3
                or r.get("protocol_verified") is not True
                or r.get("health_profile") != manifest["health_profile"]
                or r.get("capture_accounting_version") != 2
                or not isinstance(r.get("goal_datamodel"), dict) or not r["goal_datamodel"]):
            raise ValueError("goal model/protocol/datamodel proof mismatch")
        if optimized and r["goal_datamodel"] != datamodel(k, True):
            raise ValueError("optimized consolidation datamodel mismatch")
        validate_provenance(r.get("execution_provenance"), manifest)
        capture = r.get("capture", {})
        validate_capture_integrity(capture.get("integrity"), capture)
        inventory = endpoint_inventory(n, k, optimized)
        if (r["wan_dps"] != (2 * n + 1 if variant == "A" else n + 1)
                or r["active_endpoints"] != inventory
                or r["preassignment_endpoints"] != inventory
                or r["application_type_count"] != (6 if optimized else 14)
                or r["steady_window_seconds"] != manifest["seconds"]):
            raise ValueError("incorrect goal participant/endpoint/type/window inventory")
        if (r["missing"] != 0 or r["unintended"] != 0
                or not _nonnegative(r["enable_command_max_latency_ms"])
                or not _nonnegative(r["audited_sends"]) or r["audited_sends"] <= 0
                or not _nonnegative(r["audited_destination_receives"])
                or r["audited_destination_receives"] <= 0
                or r["control_wire_learned_types"] != (4 if optimized else 12)):
            raise ValueError("goal delivery/type-discovery acceptance failed")
        proof = r.get("assignment_validation")
        kinds = {str(kind) for kind in team_kinds(k, optimized)}
        if (not isinstance(proof, dict)
                or proof.get("pre_assignment_contact_receivers") != [0]
                or proof.get("pre_assignment_team_receivers") != {kind: [0] for kind in kinds}
                or not _metadata(proof.get("before_metadata_records"))
                or not _metadata(proof.get("after_metadata_records"))):
            raise ValueError("all selected team topics must be closed before assignment")
        changes = r.get("changes", [])
        if (len(changes) != 2
                or {change.get("name") for change in changes} != {"swap", "reassignment"}):
            raise ValueError("missing or duplicate required membership change")
        windows = capture.get("windows", {})
        required_windows = {"startup", "activation", "stable_position", "mission_burst",
                            "operational", "swap_change", "swap_settled",
                            "reassignment_change", "reassignment_settled"}
        if not required_windows <= windows.keys():
            raise ValueError("incomplete goal capture windows")
        for name in required_windows:
            window = windows[name]
            values = [window.get(field) for field in
                      ("duration_s", "total_bytes", "discovery_tx_Bps",
                       "discovery_peak_1s_Bps", "sedp_writer_frames_bytes")]
            if (not all(_nonnegative(value) for value in values)
                    or window["duration_s"] <= 0
                    or not _nonnegative(window.get("class_bytes", {}).get("discovery", 0))):
                raise ValueError("invalid goal capture window metrics")
        if any(not _nonnegative(r[field]) for field in
               ("steady_discovery_kbps", "steady_discovery_peak_kbps",
                "steady_total_kbps", "operational_total_kbps")):
            raise ValueError("invalid goal traffic rate")
        for change in changes:
            name = change["name"]
            before, after = change.get("membership_before", {}), change.get("membership_after", {})
            if len(before) != n or before.keys() != after.keys():
                raise ValueError("incomplete authoritative membership maps")
            before_counts = {team: list(before.values()).count(team) for team in set(before.values())}
            after_counts = {team: list(after.values()).count(team) for team in set(after.values())}
            if (before_counts != after_counts or any(count != t for count in before_counts.values())):
                raise ValueError("membership changes must preserve balanced complete teams")
            changed = {node for node in before if before[node] != after[node]}
            declared = [str(node) for node in change.get("changed_nodes", [])]
            if (set(declared) != changed or len(declared) != len(changed)
                    or len(changed) != (2 if name == "swap" else n)):
                raise ValueError("incorrect changed-node set")
            if name == "reassignment":
                for team in before_counts:
                    if (len({after[node] for node in before if before[node] == team}) < 2
                            or len({before[node] for node in after if after[node] == team}) < 2):
                        raise ValueError("reassignment must mix previous peer groups")
            if (change.get("window") != f"{name}_change"
                    or change.get("settled_window") != f"{name}_settled"
                    or change.get("participant_partition_updates") != (len(changed) if variant == "A" else 0)
                    or change.get("endpoint_parent_updates") != (0 if variant == "A" else 2 * k * len(changed))
                    or change.get("guids_preserved") is not True
                    or change.get("endpoints_preserved") is not True
                    or not _nonnegative(change.get("convergence_ms"))
                    or not _metadata(change.get("before_metadata_records"))
                    or not _metadata(change.get("after_metadata_records"))):
                raise ValueError("membership update/convergence/identity proof failed")
            hold = change.get("settled_hold_seconds")
            if (not _nonnegative(hold) or hold < manifest.get("change_hold_seconds", 10)
                    or windows[f"{name}_settled"]["duration_s"] < hold):
                raise ValueError("incomplete settled membership hold")
            audit = change.get("strict_audit", {})
            if (not _nonnegative(change.get("probes_sent")) or change["probes_sent"] <= 0
                    or change.get("probe_missing") != 0 or change.get("probe_unintended") != 0
                    or not _nonnegative(audit.get("sends")) or audit["sends"] <= 0
                    or not _nonnegative(audit.get("destination_receives"))
                    or audit["destination_receives"] <= 0
                    or audit.get("missing") != 0 or audit.get("unintended") != 0):
                raise ValueError("settled strict membership delivery audit failed")


def _saving(baseline, candidate):
    """Positive means the candidate uses less; an empty baseline has no percentage."""
    return (baseline - candidate) / baseline * 100 if baseline else None


def write_goal_report(rows, manifest, grouped, output):
    def metrics(n, t, k, protocol, variant):
        return {name: value["median"] for name, value in
                grouped[group_key(n, variant, t, k, protocol)]["metrics"].items()}

    comparisons = []
    dimensions = [(n, t, k) for n in manifest["populations"]
                  for t in manifest["team_sizes"] for k in manifest["team_topic_counts"]]
    measured_fields = ("convergence_discovery_bytes", "steady_discovery_kbps",
                       "steady_discovery_peak_kbps", "swap_discovery_bytes",
                       "reassignment_discovery_bytes")
    for n, t, k in dimensions:
        for protocol in manifest["protocols"]:
            a, b = metrics(n, t, k, protocol, "A"), metrics(n, t, k, protocol, "B")
            _, winners = setup_discovery_comparison({"A": a, "B": b})
            comparisons.append(dict(
                kind="architecture", population=n, team_size=t, team_topic_count=k,
                protocol=protocol, baseline="A", candidate="B",
                setup_winners=sorted(winners),
                savings_percent={field: _saving(a[field], b[field]) for field in measured_fields},
            ))
        if {"SPDP", "SPDP2"} <= set(manifest["protocols"]):
            for variant in manifest["variants"]:
                spdp = metrics(n, t, k, "SPDP", variant)
                spdp2 = metrics(n, t, k, "SPDP2", variant)
                comparisons.append(dict(
                    kind="protocol", population=n, team_size=t, team_topic_count=k,
                    variant=variant, baseline="SPDP", candidate="SPDP2",
                    savings_percent={field: _saving(spdp[field], spdp2[field]) for field in measured_fields},
                ))
    limits = list(manifest.get("limitations", [])) + [
        "This is a proposed compact synthetic coordination-state model, not vendor/source-schema "
        "fidelity, calibrated TypeObject sizes, or an RF/production forecast. Only unchanged "
        "types retain historical serialized-size ranges/layouts.",
        "Health is identical synthetic C2-to-platform/platform-to-C2 traffic in A/B, "
        "not the full production RouterHealth observer roster.",
        "All enabled endpoints are eager; participant partitions are discovery scope, not DDS Security.",
        "Change-window bytes include normally transmitting background traffic, not isolated "
        "serialized membership-transaction bytes.",
        "Transitional team samples are captured but not strict-audited. Strict scope/delivery "
        "audits run after convergence with authoritative membership and ledger targets saved at send time.",
        "Goal delivery eligibility excludes the final two seconds of each strict phase using "
        "per-worker phase_ends; expected_receivers are recorded at send time. Historical audits are unchanged.",
        "Whole-frame SEDP writer totals are not pure serialized endpoint metadata bytes; "
        "other builtin discovery remains in total discovery.",
        "Worker peak RSS sums double-count shared pages and are not host physical memory usage.",
        f"{manifest['repetitions']} repetitions show observed spread, not confidence intervals.",
        "Protocol comparisons hold architecture/k/team fixed; architecture comparisons hold protocol/k/team fixed.",
    ]
    retained = dict(
        manifest=manifest, runs=rows, aggregate=grouped, comparisons=comparisons,
        goal_datamodels=[dict(team_topic_count=k, datamodel=next(
            r["goal_datamodel"] for r in rows if r["team_topic_count"] == k))
            for k in manifest["team_topic_counts"]],
        limitations=limits, full_windows=manifest["seconds"] >= 150,
        totals=dict(audited_sends=sum(r["audited_sends"] for r in rows),
                    destination_receives=sum(r["audited_destination_receives"] for r in rows),
                    missing=sum(r["missing"] for r in rows),
                    unintended=sum(r["unintended"] for r in rows)),
    )
    optimized = manifest["goal_model_version"] == 2
    scope_lines = ([
        "| Consolidated topic | Audience | Logical contents |",
        "|---|---|---|",
        "| NavigationState | Squad-wide + C2 | Pose, velocity, speed |",
        "| MissionState | Team + C2 | Mission state and task progress |",
        "| SituationalAwareness | Team + C2 | Contacts and shared observations |",
        "| CoordinationState | Team + C2 | Rendezvous and coordination intent |",
        "| ControlCommand | C2 to platform | Mission/operator commands |",
        "| ControlFeedback | Platform to C2 | Tagged acknowledgments, reports and requests |",
        "| RouterHealth | C2/platform | Separate synthetic health |",
        "",
        "Six application topics plus separate health; seven DDS types/topics. "
        "Consolidated snapshots publish navigation at 1 Hz, mission/coordination at 0.2 Hz "
        "and awareness at 0.8 Hz. Feedback is a reliable tagged event envelope. "
        "This changes payload sizes and update granularity; earlier expanded-model measurements "
        "are not optimized-model measurements.",
    ] if optimized else [
        "| Goal families (14 distinct application types) | k=3 scope | k=6 scope |",
        "|---|---|---|",
        "| Pose, Velocity, Speed | Squad-wide | Squad-wide |",
        "| TaskProgress, MissionState, ContactReport | Team + C2 | Team + C2 |",
        "| SharedObservation, RendezvousState, CoordinationIntent | C2 only | Team + C2 |",
        "| MissionAdd, TransferRequest, ControlReport, TransferReport, ControlRequest | C2 only | C2 only |",
    ])
    lines = [
        "# Goal datamodel: WAN discovery and membership comparison", "",
        "Isolated synthetic DDS measurements; not real-router, production-schema, or RF forecasts.", "",
        "## Scope and setup", "",
        f"- {len(rows)} cases; N={manifest['populations']}; team sizes={manifest['team_sizes']}; "
        f"k={manifest['team_topic_counts']}; protocols={manifest['protocols']}; "
        f"{manifest['repetitions']} repetitions; {manifest['seconds']} s steady windows.",
        f"- Goal model v{manifest['goal_model_version']} / assignment model v3; native protocol "
        "selection/effective QoS verified in every case.",
        "- Health profile `scale_health30`: automatic 30 s liveliness leases, identical in both designs; "
        "not the deployed production lease. All enabled endpoints are eager.",
        ("- A: 2N+1 WAN DPs; squad-wide NavigationState on position DP; operational types on main DP. "
         if optimized else "- A: 2N+1 WAN DPs; squad-wide Pose, Velocity and Speed on position DP; operational types on main DP. ") +
        "Private+team main-DP partitions gate discovery; team readers stay TEAM_DATA and writers "
        "stay TEAM_DATA + PLATFORM. Only DP partitions change on membership updates.",
        "- B: N+1 WAN DPs; main DP stays SHARED; every selected team topic changes both "
        "its writer Publisher and reader Subscriber partition parents.",
        ("- Per platform 12 endpoints; control 8; total 12N+8."
         if optimized else "- Per platform 19+k endpoints (22 or 25); control 16; total (19+k)N+16."),
        "- Execution source/image hashes and graceful, zero-drop, boundary-complete owned captures are mandatory. " +
        ("Control must learn exactly 4 operational type identities from discovery."
         if optimized else "Control must learn exactly 12 native type identities (all 14 except MissionAdd and Pose)."),
        "",
        *scope_lines,
        "",
        "Proposed compact state/progress/location/intent primitives replace full-plan fields "
        "for coordination topics; their sizes are not inherited multi-KiB mission-plan averages.",
        "",
        "Sample-size targets and modeled rates come from the captured manifest/datamodel, "
        "not constants in this report. Actual serialization histograms remain in each run. "
        "These modeled operational "
        "rates do not imply that all families transmit during the discovery steady window.",
        "",
        "### Captured schema parameters", "",
        "| Manifest schema parameter | Captured value |",
        "|---|---|",
    ]
    for name, value in manifest.get("schema_manifest", {}).items():
        captured = json.dumps(value, ensure_ascii=False, sort_keys=True).replace("|", "&#124;")
        lines.append(f"| {name} | {captured} |")
    if not manifest.get("schema_manifest"):
        lines.append("| Schema parameters | See per-run goal_datamodel in retained JSON |")
    lines += [
        "",
        "## Setup and steady state (medians)", "",
        "| N | Team | k | Protocol | Design | DPs | Endpoints | Startup discovery B | "
        "Assignment discovery B | Setup discovery B | Setup SEDP frame B | Steady discovery kb/s | "
        "Steady peak kb/s | Steady total kb/s | Remote records before/after |",
        "|---:|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for n, t, k in dimensions:
        for protocol in manifest["protocols"]:
            for variant in manifest["variants"]:
                m = metrics(n, t, k, protocol, variant)
                lines.append(
                    f"| {n} | {t} | {k} | {protocol} | {variant} | {m['wan_dps']:g} | "
                    f"{m['active_endpoints']:g} | {m['startup_discovery_bytes']:.0f} | "
                    f"{m['activation_discovery_bytes']:.0f} | {m['convergence_discovery_bytes']:.0f} | "
                    f"{m['convergence_sedp_writer_frames_bytes']:.0f} | "
                    f"{m['steady_discovery_kbps']:.2f} | {m['steady_discovery_peak_kbps']:.2f} | "
                    f"{m['steady_total_kbps']:.2f} | {m['remote_endpoint_records_before']:g}/"
                    f"{m['remote_endpoint_records_after']:g} |")
    lines += [
        "", "## Membership changes (medians)", "",
        "Swap exchanges two members across two teams. Reassignment changes every platform's "
        "team, mixes previous peer groups, and preserves balanced team counts. GUIDs and "
        "endpoint inventory are preserved; settled positive-send audits have zero missing/unintended deliveries.",
        "",
        "**Window totals include background traffic. Transitional team samples are not strict-audited.** "
        "Strict audits begin after convergence; expected recipients are saved at send time.",
        "",
        "| N | Team | k | Protocol | Design | Change | Window s | Total B | Discovery B | "
        "SEDP frame B | Discovery peak kb/s | Convergence ms | DP updates | Parent updates | "
        "Remote records before/after | Settled hold s | Settled discovery kb/s |",
        "|---:|---:|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|",
    ]
    for n, t, k in dimensions:
        for protocol in manifest["protocols"]:
            for variant in manifest["variants"]:
                m = metrics(n, t, k, protocol, variant)
                for name in manifest["required_changes"]:
                    lines.append(
                        f"| {n} | {t} | {k} | {protocol} | {variant} | {name} | "
                        f"{m[name + '_duration_seconds']:.2f} | {m[name + '_total_bytes']:.0f} | "
                        f"{m[name + '_discovery_bytes']:.0f} | {m[name + '_sedp_writer_frames_bytes']:.0f} | "
                        f"{m[name + '_discovery_peak_kbps']:.2f} | {m[name + '_convergence_ms']:.2f} | "
                        f"{m[name + '_participant_partition_updates']:g} | "
                        f"{m[name + '_endpoint_parent_updates']:g} | "
                        f"{m[name + '_remote_endpoint_records_before']:g}/"
                        f"{m[name + '_remote_endpoint_records_after']:g} | "
                        f"{m[name + '_settled_hold_seconds']:g} | {m[name + '_settled_discovery_kbps']:.2f} |")
    lines += ["", "## Measured comparisons", "",
              "Positive percentages mean less traffic for the candidate; negative means more. "
              "Zero baselines have no defined percentage. These are comparisons of medians, not paired confidence estimates.", ""]
    def percentage(value):
        return "undefined (zero baseline)" if value is None else f"{value:+.2f}%"

    for comparison in comparisons:
        scope = (f"N{comparison['population']}/T{comparison['team_size']}/"
                 f"k{comparison['team_topic_count']}")
        savings = comparison["savings_percent"]
        if comparison["kind"] == "architecture":
            heading = (f"Architecture {scope}/{comparison['protocol']}: B versus A; "
                       f"lowest setup discovery: {' and '.join(comparison['setup_winners'])}")
        else:
            heading = f"Protocol {scope}/{comparison['variant']}: SPDP2 versus SPDP"
        lines.append(
            f"- {heading}. Setup saving {percentage(savings['convergence_discovery_bytes'])}; "
            f"steady discovery saving {percentage(savings['steady_discovery_kbps'])}; "
            f"peak saving {percentage(savings['steady_discovery_peak_kbps'])}; "
            f"swap discovery saving {percentage(savings['swap_discovery_bytes'])}; "
            f"reassignment discovery saving {percentage(savings['reassignment_discovery_bytes'])}.")
    lines += ["", "JSON retains each run, per-dimension min/median/max, authoritative memberships, "
              "strict change audits, complete windows, provenance, GUID maps and captured goal datamodels.",
              "", "## Limits", ""]
    lines.extend(f"- {limit}" for limit in limits)
    if manifest["seconds"] < 150:
        lines += ["", "**Smoke-only windows; periodic discovery savings are not established.**"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(json.dumps(retained, indent=2) + "\n")
    output.with_suffix(".md").write_text("\n".join(lines) + "\n")
    return retained


def write(source, output):
    results = json.loads((source / "results.json").read_text())
    manifest = json.loads((source / "manifest.json").read_text())
    rows = [row(result) for result in results]
    validate_rows(rows, manifest)
    grouped = aggregates(rows)
    if manifest.get("goal_model_version") in (1, 2):
        return write_goal_report(rows, manifest, grouped, output)
    limits = manifest["limitations"] + [
        "The sum of worker peak RSS double-counts shared pages; it is not host physical memory usage",
        "Steady phase keeps squad-wide pose and health active; other operational topics have endpoints but no periodic sends",
        "Operational phase adds 1 Hz velocity/speed, 0.4 Hz contact parents, and 0.8 Hz aggregate keyed contact elements",
        "Control transfer/mission schemas are modeled as sparse platform reports except the addressed MissionAdd command",
        "Mission bursts are discrete test events, not the historical mission event frequency",
        "Only contact-parent peer sharing is enabled; its 0.4 Hz team fanout is a modeled extension, not a measured customer peer rate",
        "SEDP writer-frame totals count whole frames attributed solely to 000003c2/000004c2, not pure serialized metadata bytes",
        "Discovery on other builtin writers, including type-service exchanges, remains in total discovery; no assertion/response attribution",
        "Synthetic schema layouts are not an exact reproduction of customer schemas or TypeObject wire sizes",
        "Synthetic CDR alignment permits up to three bytes of target rounding; actual histograms are retained, not exact customer min/max reproduction",
        f"{manifest['repetitions']} repetition(s) provide observed runs, not statistical confidence intervals",
        "Delivery audits exclude the final two seconds of each phase for delivery grace; counts apply to eligible sends",
    ]
    if manifest.get("capture_accounting_version") != 2:
        limits.append("Historical frame-category accounting: pure discovery DATA/control bundles may be in mixed, understating discovery-only totals")
    if manifest.get("execution_provenance_version") != 1:
        limits.append("Historical provenance uses the original run manifest, not per-case execution verification")
    if manifest.get("capture_integrity_version") != 1:
        limits.append("Historical capture drops/completion are unverified; reconciled bytes alone do not certify capture completeness")
    retained = dict(manifest=manifest, runs=rows, aggregate=grouped, limitations=limits,
                    full_windows=manifest["seconds"] >= 150,
                    totals=dict(audited_sends=sum(r["audited_sends"] for r in rows),
                                destination_receives=sum(r["audited_destination_receives"] for r in rows),
                                missing=sum(r["missing"] for r in rows),
                                unintended=sum(r["unintended"] for r in rows)))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(json.dumps(retained, indent=2) + "\n")

    team_sizes = manifest["team_sizes"] if "team_sizes" in manifest else [3]
    scenarios = [(p, t) for p in manifest["populations"] for t in team_sizes]

    def metrics(population, team_size, variant):
        group = grouped[group_key(population, variant, team_size)]
        return {name: value["median"] for name, value in group["metrics"].items()}

    schema = manifest["schema_manifest"]
    if "calibration" in schema:
        schema_description = (
            f"- Historical TypeObject inventory target: {schema['target_total_bytes']:,} bytes. "
            f"TypeObject-v1 calibration proxy: {schema['measured_v1_proxy_total_bytes']:,} bytes. "
            "This older optional-field workload is not the replacement mission-type workload.")
    else:
        schema_description = (
            f"- Sizing: {schema['sizing']}. Fourteen named mission schemas use "
            "normal fields and bounded payloads; no artificial TypeObject-size matching.")
    lines = [
        "# Fourteen-type WAN DP scale measurements",
        "",
        "Local synthetic scenario; no UMAA definitions, customer captures, or live-mesh changes.",
        "",
        "## Workload and fidelity",
        "",
        f"- {len(rows)} cases; populations {manifest['populations']}; "
        f"team sizes {team_sizes}; "
        f"{manifest['repetitions']} repetitions/design/population; "
        f"{manifest['seconds']}-second steady windows; "
        f"{manifest['concurrent_cases']} concurrent isolated cases.",
        "- One control node; squad-wide pose at 1 Hz throughout; shared WAN without multi-hop forwarding.",
        f"- Health profile: {manifest.get('health_profile', 'scale_periodic')}; "
        "scale_health30 uses automatic 30 s writer/reader leases, 2 s deadlines and latest-state "
        "transient-local history depth 1. The legacy scale_periodic profile inherits infinite leases.",
        "- Assignment model v2: endpoints are eager in A/B. A adds the team to the operational "
        "participant partition after assignment; B retains SHARED participant discovery and "
        "changes contact publisher/subscriber partitions after assignment."
        if manifest.get("assignment_model_version") == 2 else
        "- Historical assignment model: B contact partitions were preconfigured before assignment.",
        "- Discovery accounting v2 includes pure builtin DATA/control bundles; genuinely mixed traffic remains separate."
        if manifest.get("capture_accounting_version") == 2 else
        "- Historical accounting: discovery-only columns exclude some builtin DATA/control bundles classified as mixed.",
        "- Fixed-size teams per case; a final partial team is possible when population is not divisible by team size.",
        "- 14 distinct application topic/type identities, plus health using the synthetic Speed schema.",
        schema_description,
        "- Operational phase: 1 Hz pose/velocity/speed, approximately 0.4 Hz contact parents and "
        "0.8 Hz contact-element updates across keyed slots. Other topics remain idle except mission bursts.",
        "- Samples are measured in CDR and target the supplied ranges with up to three bytes "
        "of alignment rounding; actual size histograms are retained. Maximum-size mission/contact/waypoint "
        "targets are explicit burst tests, not continuous maximum-rate traffic.",
        "- The control creates twelve operational readers using types obtained from discovery, not a local-type fallback.",
        "",
        "A = two WAN DPs/platform, eager endpoints. B = one WAN DP/platform, eager endpoints. "
        "C = one WAN DP/platform, non-core topics created after team assignment.",
        "",
        "## Main comparison",
        "",
        "Comparison, activation, and resource tables use medians across repetitions; "
        "peak columns are medians of per-run full-one-second maxima, not P95. "
        "Rates are decimal kb/s; KiB means bytes/1024. "
        "Startup + activation includes both phases needed to reach the final working set.",
        "",
        "| Platforms | Team size | Design | WAN DPs | Active endpoints | Startup + activation discovery KiB | Steady discovery avg kb/s | Steady discovery peak kb/s | Steady total TX kb/s | Operational total TX kb/s |",
        "|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for population, team_size in scenarios:
        for variant in manifest["variants"]:
            m = metrics(population, team_size, variant)
            lines.append(
                f"| {population} | {team_size} | {variant} | {m['wan_dps']:.0f} | "
                f"{m['active_endpoints']:.0f} | {m['convergence_discovery_bytes']/1024:.1f} | "
                f"{m['steady_discovery_kbps']:.2f} | {m['steady_discovery_peak_kbps']:.2f} | "
                f"{m['steady_total_kbps']:.2f} | {m['operational_total_kbps']:.2f} |")
    if manifest.get("assignment_model_version") == 2:
        lines += [
            "", "## Verified endpoint-metadata visibility", "",
            "Remote endpoint records per platform, summed over its participant caches; "
            "its own participant records are excluded. Two DPs can duplicate control metadata. "
            "Counts come from builtin publication/subscription data, not bootstrap-only participant handles.",
            "",
            "| Platforms | Team size | Design | Before assignment | After assignment | Participant-message writer-frame kb/s |",
            "|---:|---:|---|---:|---:|---:|",
        ]
        for population, team_size in scenarios:
            for variant in manifest["variants"]:
                m = metrics(population, team_size, variant)
                lines.append(f"| {population} | {team_size} | {variant} | "
                             f"{m['remote_endpoint_records_before']:.0f} | "
                             f"{m['remote_endpoint_records_after']:.0f} | "
                             f"{m['steady_participant_message_frames_kbps']:.2f} |")
        lines += [
            "",
            "The control remains a wildcard discovery observer in both designs. "
            "Pre-assignment contact probes reach control but no platform peer. "
            "After assignment all peer metadata owners and record counts are checked: A sees only "
            "same-team operational participants plus control, while B still sees the full squad.",
            "Participant-message DATA fanout is deduplicated by writer sequence number for cadence "
            "checks; transmitted frame bytes still count every destination copy.",
            "Broad identical 0-95 initial-peer probing remains in both designs and can dominate "
            "periodic bootstrap traffic. This is not an optimized deployment peer plan.",
        ]
    lines += [
        "", "## Endpoint activation and discovery cost", "",
        "| Platforms | Team size | Design | Pre-assignment endpoints | Startup discovery KiB | Activation discovery KiB | Startup + activation SEDP writer-frame KiB | Enable to all control matches ms | Max command callback latency during enable ms |",
        "|---:|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for population, team_size in scenarios:
        for variant in manifest["variants"]:
            m = metrics(population, team_size, variant)
            lines.append(
                f"| {population} | {team_size} | {variant} | {m['preassignment_endpoints']:.0f} | "
                f"{m['startup_discovery_bytes']/1024:.1f} | {m['activation_discovery_bytes']/1024:.1f} | "
                f"{m['convergence_sedp_writer_frames_bytes']/1024:.1f} | "
                f"{m['enable_to_all_control_matches_ms']:.1f} | {m['enable_command_max_latency_ms']:.1f} |")
    lines += ["", "## Measured consolidation deltas", ""]
    for population, team_size in scenarios:
        if "A" not in manifest["variants"]:
            continue
        base = metrics(population, team_size, "A")["convergence_discovery_bytes"]
        for variant in (v for v in manifest["variants"] if v != "A"):
            value = metrics(population, team_size, variant)["convergence_discovery_bytes"]
            direction = "reduction" if value <= base else "increase"
            lines.append(f"- {population} platforms, teams of {team_size}, A to {variant}: cumulative discovery "
                         f"{base/1024:.1f} to {value/1024:.1f} KiB "
                         f"({abs(base-value)/base*100:.1f}% {direction}).")
            if manifest.get("capture_accounting_version") == 2:
                baseline = metrics(population, team_size, "A")["steady_discovery_kbps"]
                steady = metrics(population, team_size, variant)["steady_discovery_kbps"]
                change = ""
                if baseline > 0:
                    direction = "reduction" if steady <= baseline else "increase"
                    change = f" ({abs(baseline-steady)/baseline*100:.1f}% {direction})"
                lines.append(f"  Steady discovery: {baseline:.2f} to {steady:.2f} kb/s{change}.")
                if base > value and steady > baseline:
                    seconds = (base-value)*8/1000/(steady-baseline)
                    lines.append(f"  The lower startup cost is overtaken after approximately "
                                 f"{seconds:.1f} additional steady seconds.")
                elif value > base and baseline > steady:
                    seconds = (value-base)*8/1000/(baseline-steady)
                    lines.append(f"  The higher startup cost is recovered after approximately "
                                 f"{seconds:.1f} additional steady seconds.")
    if manifest.get("capture_accounting_version") == 2:
        lines += [
            "",
            "These time estimates compare median startup-plus-activation discovery bytes with "
            "median steady discovery rates, assuming those steady conditions persist. They are "
            "not an observed population crossover or an RF-link performance prediction. Only "
            "the tested populations establish which design wins at those points.",
        ]
    lines += [
        "", "## Resources and delivery", "",
        "| Platforms | Team size | Design | Worker CPU seconds / steady window | Sum worker peak RSS MiB | Max pose callback latency ms | Missing | Unintended |",
        "|---:|---:|---|---:|---:|---:|---:|---:|",
    ]
    for population, team_size in scenarios:
        for variant in manifest["variants"]:
            m = metrics(population, team_size, variant)
            lines.append(
                f"| {population} | {team_size} | {variant} | {m['stable_worker_cpu_seconds']:.2f} | "
                f"{m['worker_peak_rss_mib_sum']:.1f} | {m['pose_max_latency_ms']:.1f} | "
                f"{m['missing']:.0f} | {m['unintended']:.0f} |")
    lines += [
        "",
        f"Audited totals: {retained['totals']['audited_sends']:,} sends, "
        f"{retained['totals']['destination_receives']:,} expected destination receives, "
        f"{retained['totals']['missing']} missing, {retained['totals']['unintended']} unintended.",
        "",
        "## Per-run results", "",
        "| Platforms / team size / design / repeat | Cumulative discovery KiB | Steady discovery avg kb/s | Discovery max kb/s | Total steady kb/s |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda x: (x["platforms"], x["team_size"], x["variant"], x["repetition"])):
        lines.append(
            f"| {r['platforms']}/{r['team_size']}/{r['variant']}/{r['repetition']} | "
            f"{r['convergence_discovery_bytes']/1024:.1f} | {r['steady_discovery_kbps']:.2f} | "
            f"{r['steady_discovery_peak_kbps']:.2f} | {r['steady_total_kbps']:.2f} |")
    lines += ["", "## Limits", ""]
    lines.extend(f"- {limit}" for limit in limits)
    lines += [
        "",
        "Do not project loopback peak rates onto a 50-500 kb/s RF link. A burst can queue "
        "or disrupt discovery on that link even when this unthrottled test completes cleanly.",
        "Real-router integration, independently calibrated nested-schema shapes, late join/restart, "
        "1268-byte MTU, and EMANE rate/loss limits remain separate verification gates.",
        "The JSON also retains GUID-to-node/DP maps and all capture-window summaries, "
        "including class bytes, per-DP transmission bytes, and writer-entity frame totals.",
        "",
    ]
    if "calibration" in schema:
        lines += [
            "Historical schema-size API: [DDS TypeCode C API](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/api/connext_dds/api_c/group__DDSTypeCodeModule.html). "
            "The historical helper measured a v1 proxy, not actual v2 discovery traffic.",
        ]
    else:
        lines += [
            "Sample sizes are measured with `DynamicData.to_cdr_buffer()`; "
            "discovery totals come from captured traffic, not a TypeObject-size estimate.",
        ]
    if manifest["seconds"] < 150:
        lines += ["", "**Smoke-only windows; periodic discovery savings are not established.**"]
    output.with_suffix(".md").write_text("\n".join(lines) + "\n")
    return retained


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write(args.source, args.output)
    print(args.output.with_suffix(".md"))
    print(args.output.with_suffix(".json"))
