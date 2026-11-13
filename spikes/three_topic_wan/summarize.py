#!/usr/bin/env python3
"""Retain per-trial tradeoffs and slide-ready tables without hiding failed gates."""

import argparse
from collections import defaultdict
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import statistics

from topics import TRADEOFF_CASES

CASE_LABELS = {
    "baseline": "3 topics; unbanded scheduling control",
    "none_all": "3 topics; unbanded KEEP_ALL control",
    "bands_last": "3 topics; banded retention control",
    "mixed_last": "2 topics; lossy shared Event KEEP_LAST(1) negative control",
    "mixed_all": "2 topics; KEEP_ALL; default suppression",
    "split_keep_all": "3 topics; KEEP_ALL; default suppression",
    "bands_all": "3 topics; contact KEEP_LAST(1); default suppression",
    "mixed_no_suppression": "2 topics; KEEP_ALL; zero suppression",
    "bands_no_suppression": "3 topics; contact KEEP_LAST(1); zero contact suppression",
}


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def health_gaps(events, start, end):
    arrivals = sorted(event["at"] for event in events if start <= event["at"] <= end)
    boundaries = [start, *arrivals, end]
    return [b - a for a, b in zip(boundaries, boundaries[1:])]


def convergence_phase(result):
    if not result["outage"]:
        return "not_applicable"
    delay = result["first_recovery_convergence_s"]
    if delay is None:
        return "not_observed"
    return ("producing" if result["outage_finished"] + delay < result["stopped_producing"]
            else "drain")


def validate_layout(result):
    case = result["effective_qos"]["comparison_case"]
    if case not in TRADEOFF_CASES:
        return
    inventory = result["effective_qos"]["endpoint_inventory"]
    topics = {"Event", "Status"} if case.startswith("mixed") else {"Event", "Contacts", "Status"}
    expected = {node + "/" + topic for node in ("Source", "Receiver") for topic in topics}
    if any(set(inventory[kind]) != expected for kind in ("writers", "readers")):
        raise AssertionError(f"Asymmetric endpoint comparison: {case}")
    shared_flow = result["effective_qos"]["flow_controller"]
    for name in expected:
        writer, reader = inventory["writers"][name], inventory["readers"][name]
        topic = name.split("/")[1]
        if writer["topic"] != topic or reader["topic"] != topic:
            raise AssertionError(f"Incorrect physical topic: {case}/{name}")
        if writer["matched_readers"] != 1 or reader["matched_writers"] != 1:
            raise AssertionError(f"Local or missing destination: {case}/{name}")
        if topic == "Status":
            if (writer["reliability"] != "ReliabilityKind.BEST_EFFORT"
                    or reader["reliability"] != "ReliabilityKind.BEST_EFFORT"
                    or writer["history"] != "HistoryKind.KEEP_LAST"
                    or reader["history"] != "HistoryKind.KEEP_LAST"
                    or writer["flow_controller"] == shared_flow):
                raise AssertionError(f"Status policy changed: {case}/{name}")
            continue
        history = ("HistoryKind.KEEP_LAST" if topic == "Contacts" and case != "split_keep_all"
                   else "HistoryKind.KEEP_ALL")
        if (writer["history"] != history or reader["history"] != "HistoryKind.KEEP_ALL"
                or writer["reliability"] != "ReliabilityKind.RELIABLE"
                or reader["reliability"] != "ReliabilityKind.RELIABLE"
                or writer["flow_controller"] != shared_flow or writer["channel_count"] != 2):
            raise AssertionError(f"Incorrect retention or scheduling: {case}/{name}")
        zero = (case == "mixed_no_suppression"
                or case == "bands_no_suppression" and topic == "Contacts")
        if reader["heartbeat_suppression_s"] != (0 if zero else 0.0625):
            raise AssertionError(f"Incorrect suppression: {case}/{name}")


def summary(result):
    validate_layout(result)
    outage = result["outage"]
    restored = result["outage_finished"]
    metrics = result["metrics"]
    stop = result["stopped_producing"]
    commands = result["commands"]
    fresh = [event["rtt_s"] * 1000 for event in commands
             if not outage or event["sent"] >= restored]
    steady = [event["rtt_s"] * 1000 for event in commands
              if not outage or event["sent"] < result["outage_started"]]
    health = [event for event in result["health"] if not outage or event["at"] >= restored]
    health_age = [event["age_s"] * 1000 for event in health]
    intervals = ([(result["started"], result["outage_started"]), (restored, stop)]
                 if outage else [(result["started"], stop)])
    gaps = [gap for start, end in intervals for gap in health_gaps(result["health"], start, end)]
    delivery = result["event_delivery"]
    discrepancies = {name: len(values) for name, values in result["final_discrepancies"].items()}
    event_errors = sum(len(delivery[name]) for name in (
        "missing_commands", "duplicate_commands", "duplicate_acks", "unexpected_commands", "unexpected_acks"))
    missing_acks = len(result["missing_command_acks"])
    correct = (not event_errors and not missing_acks and not any(discrepancies.values())
               and metrics[-1]["unacked"] == 0
               and (not result["write_history"]["required"]
                    or result["write_history"]["expected"] == result["write_history"]["observed"]))
    qos = result["effective_qos"]
    peak_rss = max(sample["rss_kib"] for sample in metrics)
    backlog = [event["received"] - restored for event in commands
               if outage and result["outage_started"] <= event["sent"] < restored]
    source_final = {name: values for name, values in metrics[-1]["writer_metrics"].items()
                    if name.startswith("Source/")}
    startup = recovery_wire = total_wire = discovery = mixed = other = prefixless = None
    if "wire" in result:
        phases = result["wire"]["all_rtps"]["phases"]
        startup = phases["startup"]["udp_bytes"]
        categories = phases["startup"]["categories"]
        discovery = categories.get("discovery", {}).get("udp_bytes", 0)
        mixed = categories.get("mixed_builtin_user", {}).get("udp_bytes", 0)
        other = categories.get("other", {}).get("udp_bytes", 0)
        prefixless = categories.get("prefixless_rtps", {}).get("udp_bytes", 0)
        total_wire = sum(values["udp_bytes"] for phase, values in phases.items() if phase != "startup")
        recovery_wire = phases.get("recovery_producing", {}).get("udp_bytes", 0) if outage else None
    visible_deletions, deletion_delays = 0, []
    by_key = defaultdict(list)
    for event in result["observations"]:
        by_key[event["key"]].append(event)
    for operation in result["operation_events"]:
        if operation["operation"] != "dispose":
            continue
        rows = by_key[operation["key"]]
        before = [row for row in rows if row["at"] < operation["at"]]
        if not before or before[-1]["state"] != "alive":
            continue
        visible_deletions += 1
        after = [row["at"] - operation["at"] for row in rows
                 if row["at"] >= operation["at"] and row["state"] == "disposed"]
        if after:
            deletion_delays.append(min(after))
    return {
        "case": qos["comparison_case"], "scenario": "outage" if outage else "healthy",
        "contract": result["contract"], "passed": result["passed"], "errors": result["errors"],
        "correctness_passed": bool(correct),
        "measurement_valid": result["scheduler_lag_max_s"] <= 0.05,
        "recovery_s": result["first_recovery_convergence_s"],
        "convergence_phase": convergence_phase(result),
        "fresh_rtt_max_s": max(fresh) / 1000 if fresh else None,
        "fresh_rtt_p95_ms": percentile(fresh, 0.95),
        "steady_rtt_p95_ms": percentile(steady, 0.95),
        "outage_ack_drain_s": max(backlog) if backlog else None,
        "commands_sent": delivery["commands_sent"],
        "commands_acknowledged": len(commands), "missing_acks": missing_acks,
        "required_event_errors": event_errors,
        "expected_writes": result["write_history"]["expected"],
        "observed_writes": result["write_history"]["observed"],
        "contact_history_required": result["write_history"]["required"],
        "scheduler_lag_max_s": result["scheduler_lag_max_s"],
        "final_discrepancies": discrepancies, "final_unacked": metrics[-1]["unacked"],
        "health_age_max_ms": max(health_age) if health_age else None,
        "health_age_p95_ms": percentile(health_age, 0.95),
        "health_first_after_restore_s": health[0]["at"] - restored if outage and health else None,
        "health_gap_max_outside_blackout_s": max(gaps),
        "health_gaps_over_2s_outside_blackout": sum(gap > 2 for gap in gaps),
        "health_deadline_misses_total": metrics[-1]["health_deadline_misses"],
        "health_received": len(result["health"]), "health_sent": len(result["health_sends"]),
        "writers_per_node": len(qos["endpoint_inventory"]["writers"]) // 2,
        "readers_per_node": len(qos["endpoint_inventory"]["readers"]) // 2,
        "endpoint_rss_delta_kib": qos["rss_after_endpoints_kib"] - qos["rss_before_endpoints_kib"],
        "rss_peak_kib": peak_rss,
        "rss_workload_growth_kib": peak_rss - qos["rss_after_endpoints_kib"],
        "cache_samples_peak": max(sum(values["cache_samples"] for values in sample["writer_metrics"].values())
                                  for sample in metrics),
        "inactive_readers_max": max(sum(values["inactive_readers"] for values in sample["writer_metrics"].values())
                                    for sample in metrics),
        "source_repair_bytes": sum(values["pulled_bytes"] for values in source_final.values()),
        "source_push_bytes": sum(values["pushed_bytes"] for values in source_final.values()),
        "source_nacks": sum(values["nacks"] for values in source_final.values()),
        "cpu_seconds": result["cpu_seconds"],
        "startup_udp_bytes": startup, "startup_discovery_udp_bytes": discovery,
        "startup_mixed_builtin_user_udp_bytes": mixed, "startup_other_udp_bytes": other,
        "startup_prefixless_rtps_udp_bytes": prefixless,
        "workload_udp_bytes": total_wire, "recovery_producing_udp_bytes": recovery_wire,
        "visible_deletions": visible_deletions,
        "visible_deletion_notifications": len(deletion_delays),
        "visible_deletion_delay_max_s": max(deletion_delays) if deletion_delays else None,
    }


def range_text(rows, field, scale=1):
    values = [row[field] * scale for row in rows if row[field] is not None]
    if not values:
        return "not measured"
    return f"{statistics.median(values):.3f} [{min(values):.3f}-{max(values):.3f}]"


def write_report(comparison, output=None, csv_path=None):
    comparison = Path(comparison)
    output = Path(output) if output else comparison / "summary.md"
    csv_path = Path(csv_path) if csv_path else comparison / "trials.csv"
    manifest = json.loads((comparison / "manifest.json").read_text())
    rows = json.loads((comparison / "comparison.json").read_text())
    if not rows:
        raise ValueError("Comparison has no completed trials")
    policies = set()
    for row in rows:
        raw = json.loads((Path(row["report"]) / "results.json").read_text())
        if len(raw) != 1:
            raise ValueError(f"Ambiguous trial results: {row['report']}")
        validate_layout(raw[0])
        for name, writer in raw[0]["effective_qos"]["endpoint_inventory"]["writers"].items():
            policies.add((row["case"], name, writer["history"], writer["channel_count"],
                          writer["flow_controller"]))
        phase = convergence_phase(raw[0])
        if "convergence_phase" in row and row["convergence_phase"] != phase:
            raise AssertionError(f"Convergence phase mismatch: {row['report']}")
        row["convergence_phase"] = phase
    fields = list(rows[0])
    output.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value
                         for key, value in row.items()} for row in rows)

    def link(path):
        return os.path.relpath(path, output.parent)

    args = manifest["arguments"]
    cases = list(dict.fromkeys(row["case"] for row in rows))
    comparisons = []
    for first, second, explanation in (
            ("mixed_all", "split_keep_all",
             "same KEEP_ALL retention and reader timing; isolates adding the Contacts pair "
             "and splitting transmission queues."),
            ("split_keep_all", "bands_all",
             "same three-topic topology and timing; isolates contact-writer KEEP_LAST(1) coalescing."),
            ("mixed_all", "mixed_no_suppression", "suppression treatment within two topics."),
            ("bands_all", "bands_no_suppression", "suppression treatment within three topics.")):
        if first in cases and second in cases:
            comparisons.append(f"- `{first}` versus `{second}`: {explanation}")
    lines = [
        "# WAN topic tradeoffs: retained comparison",
        "",
        "**Transport spike only. No dev-router integration or live-mesh changes.**",
        "",
        f"- Source commit: `{manifest['source_commit']}`; dirty inputs are archived and individually hashed.",
        f"- Derived report generator SHA-256: `{hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}`. "
        "Convergence-phase labels are derived from original trial markers; frozen measurement inputs "
        "are not changed by report regeneration.",
        f"- Image: `{manifest['image']}`.",
        f"- Started: {manifest['started_at_utc']}; completed: {manifest['completed_at_utc']}.",
        f"- Host: {manifest['host_cpu_count']} CPUs; {manifest['host_scope']}. "
        "Host load averages at each trial boundary are retained in the CSV.",
        f"- Execution: {manifest['execution']}. Cases within this suite never overlap.",
        f"- Requested repetitions: {args['repeats']}; completed trial rows: {len(rows)}.",
        f"- Outage: {args['outage_seconds']} s; baseline: {args['baseline']} s; continued production: "
        f"{args['recovery']} s; then up to 30 s drain.",
        "- Workload: 50-ms ticks, one create, three updates and one dispose per tick; commands/ACKs "
        "and health once per second; one never-disposed stationary contact.",
        "- Two synthetic nodes each own one writer and reader per topic, with two-sided self-ignore "
        "and exactly one remote match. Reverse Contacts/Status endpoints are idle.",
        "- Scheduling and retention vary by case. The native writer inventory below records "
        "the executed histories, channel counts and flow controllers; original trial JSON retains "
        "repair limits and reader timing.",
        "- Status remains best effort and outside that controller. Routine periodic Status competition "
        "and actual router STALE/DEAD transitions are **not tested**.",
        "",
        "## Comparisons and causal limits",
        "",
        *comparisons,
        "- Single-variable causal claims above apply only to those executed case pairs. "
        "Other cases are scheduling/retention controls, not a matched lossless-baseline matrix.",
        "- In two topics, the contact-carrying reader also carries commands/ACKs; zero suppression "
        "there applies to the whole Event reader. Three topics can change Contacts independently. "
        "That coupling is documented, not hidden as a perfectly identical per-reader treatment.",
        "- A lossy shared Event KEEP_LAST(1) writer is a negative control, not an eligible "
        "lossless two-topic baseline. Its failed gates remain visible.",
        "",
        "## Executed native writer policies",
        "",
        "Each distinct recorded policy is shown, including changes across repetitions.",
        "",
        "| Case | Native writer | History | Channels | Flow controller |",
        "|---|---|---|---:|---|",
        *(f"| `{case}` | {name} | {history} | {channels} | {flow} |"
          for case, name, history, channels, flow in sorted(policies)),
        "",
        "## Outage results for slides",
        "",
        "Values are **median [minimum-maximum] across trials**, not confidence intervals. "
        "Timing tables retain failed and scheduler-invalid trials; check the validity column.",
        "",
        "| Case | Trials / valid | Correct final state + all required events | Recovery (s) | "
        "Converged while producing | Fresh RTT p95 (ms) | Worst fresh RTT (ms) | Acceptance passes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case in cases:
        trials = [row for row in rows if row["case"] == case and row["scenario"] == "outage"]
        if trials:
            lines.append(
                f"| {CASE_LABELS.get(case, case)} | {len(trials)} / "
                f"{sum(row['measurement_valid'] for row in trials)} | "
                f"{sum(row['correctness_passed'] for row in trials)}/{len(trials)} | "
                f"{range_text(trials, 'recovery_s')} | "
                f"{sum(row['convergence_phase'] == 'producing' for row in trials)}/{len(trials)} | "
                f"{range_text(trials, 'fresh_rtt_p95_ms')} | "
                f"{range_text(trials, 'fresh_rtt_max_s', 1000)} | "
                f"{sum(row['passed'] for row in trials)}/{len(trials)} |")
    lines += [
        "",
        "Required-event gates reject missing/duplicate/unexpected commands or ACKs. Acceptance also "
        "requires contact recovery within 10 s, worst fresh command RTT within 2 s, drained reliable "
        "queues, correct final contact state and scheduler lag at most 50 ms.",
        "",
        "## Resource and wire costs",
        "",
        "Outage trials only; median [minimum-maximum]. KiB = 1024 bytes.",
        "",
        "| Case | Writers / readers per node | Startup discovery UDP (KiB) | "
        "Endpoint RSS increase (KiB) | Peak benchmark RSS (KiB) | "
        "Source native repair bytes (KiB) | Both-node recovery UDP (KiB) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for case in cases:
        trials = [row for row in rows if row["case"] == case and row["scenario"] == "outage"]
        if trials:
            lines.append(
                f"| {CASE_LABELS.get(case, case)} | {trials[0]['writers_per_node']} / "
                f"{trials[0]['readers_per_node']} | "
                f"{range_text(trials, 'startup_discovery_udp_bytes', 1 / 1024)} | "
                f"{range_text(trials, 'endpoint_rss_delta_kib')} | "
                f"{range_text(trials, 'rss_peak_kib')} | "
                f"{range_text(trials, 'source_repair_bytes', 1 / 1024)} | "
                f"{range_text(trials, 'recovery_producing_udp_bytes', 1 / 1024)} |")
    lines += [
        "",
        "- RSS is the Python/native benchmark process, including type loading and retained oracle/"
        "observation records; it is **not isolated DDS memory or production-router memory**. Endpoint "
        "RSS increase is measured before workload keys are created. Capture subprocess memory is excluded.",
        "- Source repair bytes come from native pulled-sample counters, not total packet bytes.",
        "- UDP totals include both owned participants, user data/repairs, health and recognized protocol "
        "traffic. Recovery UDP uses the common continued-production interval, not unequal drain durations.",
        "- Startup discovery counts recognized builtin-only RTPS packets before workload start. Mixed "
        "builtin/user, prefixless RTPS and unknown/vendor packet bytes are separate CSV fields; initial user state is "
        "excluded from this discovery column. It is not RF overhead or fleet-scale discovery qualification.",
        "",
        "## Health, deletion and healthy controls",
        "",
        "| Case | Scenario | Trials | RTT p95 (ms) | Health age p95 (ms) | "
        "Largest health gap outside blackout (s) | Gaps >2 s, summed | Final ghosts, summed |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for case in cases:
        for scenario in ("healthy", "outage"):
            trials = [row for row in rows if row["case"] == case and row["scenario"] == scenario]
            if trials:
                lines.append(
                    f"| {CASE_LABELS.get(case, case)} | {scenario} | {len(trials)} | "
                    f"{range_text(trials, 'fresh_rtt_p95_ms')} | "
                    f"{range_text(trials, 'health_age_p95_ms')} | "
                    f"{range_text(trials, 'health_gap_max_outside_blackout_s')} | "
                    f"{sum(row['health_gaps_over_2s_outside_blackout'] for row in trials)} | "
                    f"{sum(row['final_discrepancies']['ghosts'] for row in trials)} |")
    lines += [
        "",
        "- Health gaps include interval boundaries and exclude the intentional blackout. A gap >2 s "
        "is a freshness warning, **not a measured false router STALE transition**.",
        "- Correct final state includes disposed contacts; a never-seen disposed key may remain absent "
        "under latest-state semantics. The CSV separately records notification delays for contacts "
        "visible as ALIVE when disposed. Those delays can include the intentional outage.",
        "- Recovery is first current-state convergence, checked on receptions with a periodic fallback. "
        "It may occur while production continues or during the subsequent quiet drain; the table and "
        "CSV distinguish those phases. With 40 s of recovery production, longer recovery times include "
        "post-production drain. Final-state correctness is separate. It is not a sustained "
        "freshness guarantee, router restart/late-joiner test or bounded-admission qualification.",
        "- Small samples on a shared VM do not establish statistical significance. Endpoint count and "
        "independent retention are structural benefits/costs; measured speed rankings are conditional.",
        "",
    ]
    outage_groups = {case: [row for row in rows if row["case"] == case and row["scenario"] == "outage"]
                     for case in CASE_LABELS}
    if all(outage_groups.values()):
        two = outage_groups["mixed_all"]
        split = outage_groups["split_keep_all"]
        latest = outage_groups["bands_all"]
        candidate = outage_groups["bands_no_suppression"]
        lines += [
            "## Slide-ready takeaways",
            "",
            f"- **The extra topic alone is not a demonstrated recovery-speed improvement:** "
            f"two-topic KEEP_ALL {range_text(two, 'recovery_s')} s versus three-topic KEEP_ALL "
            f"{range_text(split, 'recovery_s')} s.",
            f"- **Independent contact retention is the important benefit:** three-topic contact "
            f"KEEP_LAST(1) reaches {range_text(latest, 'recovery_s')} s with default suppression, "
            "while Event stays KEEP_ALL. Those recoveries can occur before production stops.",
            f"- **Zero suppression remains a tuning candidate, not a stable optimum:** "
            f"{range_text(candidate, 'recovery_s')} s; its fastest and slowest trials are both retained.",
            "- **Cost:** DDS writer/reader objects increase from 2/2 to 3/3 per node. The tables "
            "record discovery and process-memory costs; counts exclude internal per-channel RTPS streams.",
            f"- **Correctness:** {sum(row['correctness_passed'] for row in rows)}/{len(rows)} trial "
            "rows preserve required events and correct final state. Performance gates remain separate.",
            "",
        ]
    lines += ["## Every failed gate", ""]
    failures = [row for row in rows if row["errors"]]
    lines.extend(f"- Repeat {row['repeat']}, `{row['case']}`, {row['scenario']}: "
                 + "; ".join(row["errors"]) for row in failures)
    if not failures:
        lines.append("- No executed gate failed.")
    lines += [
        "",
        "## Reusable evidence",
        "",
        f"- [Per-trial CSV]({link(csv_path)}) for slide tables/charts.",
        f"- [Machine-readable trial results]({link(comparison / 'comparison.json')}).",
        f"- [Frozen manifest]({link(comparison / 'manifest.json')}) and "
        f"[exact compressed inputs]({link(comparison / 'inputs.tar.gz')}).",
        "- Each result links its original JSON, JUnit and closed-capture location through its manifest.",
        "",
    ]
    if (comparison / "repair_trace.json").exists():
        lines.insert(-1, f"- [Offline repair-request timelines]({link(comparison / 'repair_trace.json')}) "
                     "(first 30 s after restoration, not the full KEEP_ALL drain).")
    if (comparison / "analysis_manifest.json").exists():
        lines.insert(-1, f"- [Derived-analysis source hashes]({link(comparison / 'analysis_manifest.json')}).")
    output.write_text("\n".join(lines))
    return output, csv_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args()
    for path in write_report(args.comparison, args.output, args.csv):
        print(path)


if __name__ == "__main__":
    main()
