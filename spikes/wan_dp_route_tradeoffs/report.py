#!/usr/bin/env python3
"""Write compact retained results without copying raw captures or sample ledgers."""

import argparse
import hashlib
import json
from pathlib import Path
import statistics
from spike import validate_capture_integrity, validate_provenance


def metrics(result):
    idle = result["capture"]["windows"]["idle"]
    checkpoints = result["checkpoints"]["idle"]
    discovery_entries = sum(
        len(dp["publications"]) + len(dp["subscriptions"])
        for node in checkpoints.values() for dp in node["participants"].values())
    checks = {name: value for name, value in result["events"].items()
              if isinstance(value, dict) and "unintended" in value}
    return {
        "variant": result["variant"], "repetition": result["repetition"],
        "execution_provenance": result.get("execution_provenance"),
        "capture_integrity": result["capture"].get("integrity"),
        "wan_dp_count": sum(len(node["participants"]) for node in checkpoints.values()),
        "local_endpoint_count": sum(len(node["endpoints"]) for node in checkpoints.values()),
        "observed_remote_metadata_entries": discovery_entries,
        "metadata_inventory_complete": False,
        "out_of_team_metadata_visible": result["events"]["out_of_team_metadata_visible"],
        "idle_discovery_kbps": idle["discovery_tx_Bps"] * 8 / 1000,
        "idle_discovery_peak_kbps": idle["discovery_peak_1s_Bps"] * 8 / 1000,
        "idle_discovery_bytes_per_nominal_30s_cycle": idle["discovery_tx_Bps"] * 30,
        "idle_total_kbps": idle["total_tx_Bps"] * 8 / 1000,
        "loaded_total_kbps": result["capture"]["windows"]["loaded"]["total_tx_Bps"] * 8 / 1000,
        "startup_bytes": result["capture"]["windows"]["startup"]["total_bytes"],
        "pair_open_bytes": result["capture"]["windows"]["pair_open"]["total_bytes"],
        "pair_close_bytes": result["capture"]["windows"]["pair_close"]["total_bytes"],
        "team_change_bytes": result["capture"]["windows"]["team_change"]["total_bytes"],
        "restart_bytes": result["capture"]["windows"]["restart"]["total_bytes"],
        "post_pair_commands_match_poll_ms": result["events"]["pair_matches_s"] * 1000,
        "post_team_commands_match_poll_ms": result["events"]["team_matches_s"] * 1000,
        "initial_match_poll_ms": result["events"]["initial_matches_s"] * 1000,
        "restart_match_poll_ms": result["events"]["restart_matches_s"] * 1000,
        "idle_worker_cpu_seconds": sum(result["events"]["idle_cpu_s"].values()),
        "worker_peak_rss_kib_sum": sum(node["max_rss_kib"] for node in checkpoints.values()),
        "audited_test_sends": sum(check["sent"] for check in checks.values()),
        "audited_test_destination_receives": sum(check["destination_receives"] for check in checks.values()),
        "unintended_test_deliveries": sum(check["unintended"] for check in checks.values()),
        "idle_per_dp_total_bytes": {
            result["guid_map"][prefix]: count
            for prefix, count in idle["per_dp_bytes"].items()
        },
        "idle_writer_entity_set_frame_bytes": idle["writer_entity_set_frame_bytes"],
    }


def aggregate(rows):
    grouped = {}
    for variant in sorted({row["variant"] for row in rows}):
        runs = [r for r in rows if r["variant"] == variant]
        numeric = {
            key: {"median": statistics.median(r[key] for r in runs),
                  "min": min(r[key] for r in runs), "max": max(r[key] for r in runs)}
            for key, value in runs[0].items()
            if isinstance(value, (float, int)) and not isinstance(value, bool)
            and key != "repetition"
        }
        grouped[variant] = dict(repetitions=len(runs), metrics=numeric,
                               out_of_team_metadata_visible=all(
                                   r["out_of_team_metadata_visible"] for r in runs))
    return grouped


def report(source, output):
    results = json.loads((source / "results.json").read_text())
    manifest = json.loads((source / "manifest.json").read_text())
    for result in results:
        if "execution_provenance_version" in manifest:
            if manifest["execution_provenance_version"] != 1:
                raise ValueError("unsupported execution provenance version")
            validate_provenance(result.get("execution_provenance"), manifest)
        if "capture_integrity_version" in manifest:
            if manifest["capture_integrity_version"] != 1:
                raise ValueError("unsupported capture integrity version")
            validate_capture_integrity(result["capture"].get("integrity"), result["capture"])
    rows = [metrics(r) for r in results]
    grouped = aggregate(rows)
    if len(results) != manifest["settings"]["repetitions"] * len(
            manifest["settings"]["variants"] if "balanced" not in manifest["settings"]["variants"]
            else "ABCD"):
        raise ValueError("result count does not match manifest")
    if any(r["unintended_test_deliveries"] for r in rows):
        raise ValueError("audience isolation failed")
    full_windows = all(r["idle_seconds"] >= 150 for r in results)
    retained = dict(
        manifest=manifest, full_five_cycle_windows=full_windows,
        execution_provenance_verified=manifest.get("execution_provenance_version") == 1,
        capture_integrity_verified=manifest.get("capture_integrity_version") == 1,
        source_sha256=manifest.get("source_hashes")
        if manifest.get("execution_provenance_version") == 1 else None,
        report_generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        runs=rows, aggregate=grouped,
        semantic_limitations=[
            "DDS mechanism spike, not production router or EMANE validation",
            "No proof that the live discovery spike is an assertion or response",
            "Same synthetic type on every topic; production type-metadata cost not represented",
            "Post-command match-poll timings exclude command application/entity creation",
            "Event-window bytes include ongoing infrastructure traffic and validation sends",
            "Remote metadata entries summed across observer DPs, not unique system endpoints",
            "Two-DP builtin-reader metadata counts varied across identical runs; treat counts "
            "as incomplete observed inventories, not authoritative endpoint-cardinality comparisons",
            "30-second cycle bytes are normalized from the whole idle window",
            "Concurrent replicas can influence scheduling, CPU, and one-second peak alignment",
        ],
    )
    if not retained["execution_provenance_verified"]:
        retained["semantic_limitations"].append(
            "Historical execution provenance is unverified; present-day worker/QoS hashes "
            "are not substituted for missing execution-time evidence")
    if not retained["capture_integrity_verified"]:
        retained["semantic_limitations"].append(
            "Historical capture drop/completion evidence is unavailable; capture completeness "
            "cannot be retrospectively certified from reconciled bytes alone")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(json.dumps(retained, indent=2) + "\n")
    lines = [
        "# WAN DP / on-demand route spike results",
        "",
        "Stage 1: Connext DDS mechanism comparison on isolated loopback.",
        "No live mesh configuration or router runtime changes.",
        "",
        f"Repetitions per variant: {manifest['settings']['repetitions']}. "
        f"Idle window: {manifest['settings']['idle_seconds']} seconds. "
        f"Concurrent repetitions: {manifest['settings']['concurrent_repetitions']}.",
        "",
        "A: two WAN DPs, eager endpoints; B: one WAN DP, eager endpoints; "
        "C: one WAN DP, on-demand pair endpoints; D: two WAN DPs, on-demand pair endpoints.",
        "",
        "## Aggregate comparison",
        "",
        "Rates are median kb/s (bits), summed TX once per captured transmission. "
        "Peaks are medians of full one-second per-run maxima.",
        "",
        "| Variant | WAN DPs | Local endpoints at idle | Discovery avg kb/s | Discovery peak kb/s | Discovery bytes / nominal 30 s | Total idle kb/s | Observed metadata entries (incomplete) | Out-of-team metadata | Unintended deliveries |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    def median(variant, name):
        return grouped[variant]["metrics"][name]["median"]

    for variant in grouped:
        lines.append(
            f"| {variant} | {median(variant, 'wan_dp_count'):.0f} | "
            f"{median(variant, 'local_endpoint_count'):.0f} | "
            f"{median(variant, 'idle_discovery_kbps'):.2f} | "
            f"{median(variant, 'idle_discovery_peak_kbps'):.2f} | "
            f"{median(variant, 'idle_discovery_bytes_per_nominal_30s_cycle'):.0f} | "
            f"{median(variant, 'idle_total_kbps'):.2f} | "
            f"{median(variant, 'observed_remote_metadata_entries'):.0f} | "
            f"{'yes' if grouped[variant]['out_of_team_metadata_visible'] else 'no'} | "
            f"{median(variant, 'unintended_test_deliveries'):.0f} |")
    lines += ["", "## Paired consolidation comparisons", ""]
    for before, after in (("A", "B"), ("D", "C")):
        base = median(before, "idle_discovery_kbps")
        value = median(after, "idle_discovery_kbps")
        if base == 0:
            lines.append(f"- {before} to {after}: no baseline discovery rate; percentage unavailable.")
        else:
            lines.append(f"- {before} to {after}: median average discovery TX "
                         f"{base:.2f} to {value:.2f} kb/s "
                         f"({(base-value)/base*100:.1f}% reduction).")
    lines += [
        "", "## Individual runs", "",
        "| Variant / repetition | Discovery avg kb/s | Discovery peak kb/s | Idle total kb/s | Startup bytes | Pair-open bytes | Team-change bytes | Restart bytes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(rows, key=lambda r: (r["variant"], r["repetition"])):
        lines.append(
            f"| {row['variant']}/{row['repetition']} | {row['idle_discovery_kbps']:.2f} | "
            f"{row['idle_discovery_peak_kbps']:.2f} | {row['idle_total_kbps']:.2f} | "
            f"{row['startup_bytes']} | {row['pair_open_bytes']} | "
            f"{row['team_change_bytes']} | {row['restart_bytes']} |")
    lines += [
        "", "## Correctness and tradeoffs", "",
        "- Targeted commands, cross-team navigation, team audience changes, explicit "
        "pair-route delivery, route teardown, and restart delivery passed exact recipient checks.",
        "- Health and APPLICATION_AUTO probe receipts were validated during stable idle; "
        "the dedicated probe was retained identically in every variant.",
        "- In C/D, dormant pair readers/writers were absent at idle and removed on closure.",
        "- In A/D, P0 did not discover P2's team writer on its main participant. "
        "In B/C it did, even though endpoint partitions prevented unintended team-data delivery.",
        "- On-demand endpoints reduce dormant endpoint inventory, but enabling them incurs "
        "discovery churn. Event bytes and CPU/RSS are retained in the JSON.",
        "- Builtin-reader metadata counts varied in the two-DP runs (including an A-run "
        "outlier). Cardinality measurement is not accepted as complete; the independently "
        "checked out-of-team writer visibility and exact recipient tests remain valid.",
        "", "## Limits and next gates", "",
    ]
    lines.extend(f"- {limitation}" for limitation in retained["semantic_limitations"])
    lines += [
        "- Unicast bootstrap peer probing covers IDs 0-31 on localhost; topology/fanout "
        "differs from the live EMANE mesh. Do not extrapolate absolute rates to its 209 kb/s spike.",
        "- Representative ACT type metadata, 6/12-platform scale, full command-to-first-delivery "
        "latency, real-router configuration integration, and RF loss experiments remain pending.",
        "- No production migration is recommended solely from this Stage 1 result.",
        "",
        "See the sibling JSON for per-run ranges, source/config hashes, and per-DP bytes.",
    ]
    if not full_windows:
        lines += ["", "**Smoke-only results: these windows do not cover five assertion periods.**"]
    output.with_suffix(".md").write_text("\n".join(lines) + "\n")
    return retained


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path,
                        default=Path("debug/test_reports/wan_dp_route_tradeoffs_20261106"))
    args = parser.parse_args()
    report(args.source, args.output)
    print(args.output.with_suffix(".md"))
    print(args.output.with_suffix(".json"))


if __name__ == "__main__":
    main()
