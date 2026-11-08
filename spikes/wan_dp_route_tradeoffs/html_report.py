#!/usr/bin/env python3
"""Build a self-contained comparison report from retained measurements."""

import argparse
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path

from scale_report import LABELS, setup_discovery_comparison, validate_rows


STYLE = """
:root{--ink:#15243c;--muted:#62718a;--paper:#f3f5f8;--line:#e2e8f0;--teal:#087f78;--amber:#f2ae49}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
a{color:var(--teal);text-underline-offset:3px}button{font:inherit}main{max-width:1180px;margin:auto;padding:28px 24px 48px}
.masthead{display:flex;justify-content:space-between;gap:16px;align-items:center;margin:0 4px 22px;font-size:12px;color:var(--muted)}
.brand{font-weight:800;letter-spacing:.13em;color:var(--ink)}.hero{position:relative;overflow:hidden;border-radius:24px;padding:44px 46px;background:radial-gradient(ellipse at 90% 20%,#125b68 0%,transparent 58%),#10213b;color:#fff}
.hero .eyebrow{color:#8be4d9}.eyebrow{text-transform:uppercase;letter-spacing:.15em;font-weight:750;font-size:11px}
h1{font-size:clamp(34px,5vw,55px);line-height:1.08;letter-spacing:-.045em;margin:18px 0 20px;max-width:720px}h2{font-size:24px;letter-spacing:-.025em;margin:0 0 7px}h3{font-size:17px;line-height:1.4;margin:0 0 10px}
.hero p{max-width:690px;color:#c4d3e4;margin:0}.hero strong{color:white}.pills{display:flex;gap:8px;flex-wrap:wrap;margin-top:24px}.pill{border:1px solid #ffffff30;border-radius:30px;padding:5px 12px;font-size:12px;color:#deecf5}
.actions{display:flex;gap:12px;align-items:center;margin-top:28px;flex-wrap:wrap}.button{display:inline-block;border:0;border-radius:9px;background:#c2f5e7;color:#153d3c;padding:10px 16px;font-weight:750;text-decoration:none;cursor:pointer}.button.secondary{background:#ffffff12;color:#eef7ff;border:1px solid #ffffff30}
.notice{border:1px solid #ecd8b5;border-left:4px solid var(--amber);border-radius:10px;background:#fff9ed;padding:14px 18px;margin:20px 0;color:#65502f;font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:22px 0 34px}.kpi{background:#fff;border:1px solid var(--line);border-radius:14px;padding:22px}.kpi .value{font-size:33px;font-weight:800;letter-spacing:-.04em;line-height:1.2}.kpi .label{color:var(--muted);font-size:12px;margin-top:8px}.green{color:var(--teal)}
section{margin:34px 0}.section-head{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:18px}.section-head p,.subtle{color:var(--muted);margin:0;font-size:13px}.tag{white-space:nowrap;color:var(--teal);background:#e1f3ef;border-radius:6px;padding:5px 9px;font-size:11px;font-weight:750}
.panel{background:#fff;border:1px solid var(--line);border-radius:16px;padding:25px}.legend{display:flex;gap:20px;font-size:12px;color:var(--muted);margin-bottom:22px;flex-wrap:wrap}.key:before{content:"";display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:7px;background:#3c638c}.key.activation:before{background:var(--amber)}
.chart-row{display:grid;grid-template-columns:185px 1fr 90px;gap:18px;align-items:center;margin:22px 0}.chart-name{font-size:13px;font-weight:750}.chart-name small{display:block;font-weight:400;color:var(--muted);font-size:11px}.track{display:flex;height:28px;border-radius:5px;overflow:hidden;background:#eef2f7}.startup{background:#3c638c}.activation-bar{background:var(--amber)}.amount{text-align:right;font-weight:800;font-variant-numeric:tabular-nums;font-size:15px}.amount small{font-size:10px;font-weight:500;color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:18px}.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:24px}.card.best{border-color:#8bc7b9;background:#f5fcf9}.card .eyebrow{color:var(--muted);margin-bottom:12px}.statline{display:flex;justify-content:space-between;border-top:1px solid var(--line);padding-top:11px;margin-top:11px;font-size:12px;color:var(--muted)}.statline b{color:var(--ink)}.card p{font-size:13px;color:var(--muted);margin:0 0 16px}.callout{margin-top:18px;background:#e5f3ef;padding:17px 21px;border-radius:10px;color:#24483f;font-size:14px}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:18px}.steps{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:15px 0}.step{background:#eef3f8;border-radius:8px;padding:10px 12px;font-weight:750;font-size:13px}.step.teal{background:#def2eb;color:#156c5e}.arrow{color:var(--muted)}.checklist{list-style:none;padding:0;margin:16px 0 0}.checklist li{border-top:1px solid var(--line);padding:10px 0;font-size:13px}.checklist b{display:inline-block;color:var(--teal);margin-right:7px}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:white}table{border-collapse:collapse;width:100%;font-size:12px}th,td{text-align:left;padding:12px 16px;border-bottom:1px solid var(--line)}th{color:var(--muted);background:#f8fafc;font-size:10px;text-transform:uppercase;letter-spacing:.06em}tr:last-child td{border-bottom:0}td.number{text-align:right;font-variant-numeric:tabular-nums}code{font-size:11px;background:#eef2f6;padding:2px 5px;border-radius:4px;overflow-wrap:anywhere}
.proof{border-left:3px solid #31a895;padding-left:15px;margin-top:18px;font-size:13px}.fine{font-size:11px;color:var(--muted);margin:12px 0 0}details{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px 22px;margin-top:20px}summary{font-weight:750;cursor:pointer;font-size:14px}details p{font-size:13px;color:var(--muted)}
footer{border-top:1px solid var(--line);padding-top:20px;margin-top:38px;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;font-size:11px;color:var(--muted)}.source-links{display:flex;gap:16px;flex-wrap:wrap}
@media(max-width:760px){main{padding:16px}.masthead{align-items:start}.hero{padding:30px 24px}.kpis{grid-template-columns:repeat(2,1fr);gap:10px}.kpi{padding:18px}.cards,.two-col{grid-template-columns:1fr}.chart-row{grid-template-columns:1fr 75px;gap:8px}.chart-name{grid-column:1/-1}.section-head{display:block}.tag{display:inline-block;margin-top:10px}.panel{padding:20px}.table-wrap{max-width:100%}th,td{padding:10px}.track{height:24px}}
@media print{body{background:#fff;font-size:11px}main{max-width:none;padding:0}.hero{background:#10213b!important;color:white!important;padding:25px;print-color-adjust:exact}.actions,.masthead{display:none}h1{font-size:32px}.kpis{margin:16px 0}.kpi{padding:12px}.kpi .value{font-size:26px}section{margin:22px 0}.panel,.card,.kpi,.notice{break-inside:avoid}details{display:none}.track,.startup,.activation-bar,.card.best{print-color-adjust:exact}footer{font-size:9px}}
"""


def build(current, historical=None):
    validate_rows(current["runs"], current["manifest"])
    if current["manifest"]["populations"] != [2] or current["manifest"]["variants"] != "ABC":
        raise ValueError("this report layout requires the two-platform A/B/C mission comparison")
    if current["manifest"]["repetitions"] != 1 or current["full_windows"]:
        raise ValueError("this smoke report requires one repetition and short windows")
    if "sizing" not in current["manifest"]["schema_manifest"]:
        raise ValueError("current report must use the replacement mission schema model")
    verification = current["type_discovery_verification"]
    if (verification["qos_source_sha256"] != current["manifest"]["source_hashes"]["qos.xml"]
            or not verification["type_lookup_enabled"]
            or verification["type_object_max_serialized_length"] != "LENGTH_AUTO"
            or verification["type_code_max_serialized_length"] != 0
            or verification["request_types_filter"] != "*"):
        raise ValueError("verified type-discovery configuration does not match this report")
    cases = {r["variant"]: r for r in current["runs"]}
    totals = current["totals"]
    validated = current["sdk_sample_validation"]
    expected_types = {t["type"] for t in current["manifest"]["schema_manifest"]["types"]}
    if len(validated) != 14 or {t["type"] for t in validated} != expected_types:
        raise ValueError("all fourteen SDK sample-size validations are required")
    difference, winners = setup_discovery_comparison(cases)
    shared = cases["B"]["convergence_discovery_bytes"]
    if difference is not None:
        percentage = f"{abs(difference):.1f}%"
        change = "less" if difference < 0 else "more" if difference > 0 else "unchanged"
        comparison = (f"Shared eager design B recorded {percentage} {change} setup discovery than A."
                      if difference else "Shared eager design B and split design A recorded equal setup discovery.")
        kpi_label = f"{change.capitalize()} setup discovery &middot; B versus A"
        kpi_class = "green" if difference < 0 else ""
    else:
        percentage, kpi_label, kpi_class = "N/A", "B versus A &middot; zero baseline", ""
        comparison = (f"Design A recorded zero setup discovery; B recorded {shared / 1024:.1f} KiB. "
                      "A percentage comparison is undefined.")
    max_total = max(r["convergence_discovery_bytes"] for r in cases.values())
    chart_scale = max_total if max_total else 1
    charts, cards = [], []
    descriptions = {
        "A": ("Split participants", "A position DP plus an operational DP per platform. "
              "Operational endpoints already exist before team assignment."),
        "B": ("Shared participant", "One WAN DP per platform. Operational endpoints "
              "are already present; partition audiences govern data delivery."),
        "C": ("Shared, staged topics", "One WAN DP per platform. Non-core endpoints "
              "are created after assignment, reducing the initial inventory."),
    }
    for variant in "ABC":
        r = cases[variant]
        startup = r["startup_discovery_bytes"] / 1024
        activation = r["activation_discovery_bytes"] / 1024
        total = r["convergence_discovery_bytes"] / 1024
        label, description = descriptions[variant]
        charts.append(f"""
        <div class="chart-row">
          <div class="chart-name">{variant} &middot; {label}<small>{escape(LABELS[variant])}</small></div>
          <div class="track" role="img" aria-label="{variant}: startup {startup:.1f} KiB, activation {activation:.1f} KiB">
            <div class="startup" style="width:{r['startup_discovery_bytes']/chart_scale*100:.4f}%"></div>
            <div class="activation-bar" style="width:{r['activation_discovery_bytes']/chart_scale*100:.4f}%"></div>
          </div><div class="amount">{total:.1f}<small> KiB</small></div>
        </div>""")
        cards.append(f"""
        <article class="card {'best' if variant in winners else ''}">
          <div class="eyebrow">Design {variant}{' &middot; ' + ('tied for ' if len(winners) > 1 else '') + 'lowest observed setup traffic' if variant in winners else ''}</div>
          <h3>{label}</h3><p>{description}</p>
          <div class="statline"><span>Total WAN participants</span><b>{r['wan_dps']}</b></div>
          <div class="statline"><span>Endpoints, before &rarr; after</span><b>{r['preassignment_endpoints']} &rarr; {r['active_endpoints']}</b></div>
          <div class="statline"><span>Startup / activation KiB</span><b>{startup:.1f} / {activation:.1f}</b></div>
          <div class="statline"><span>Enable to control-reader matches</span><b>{r['enable_to_all_control_matches_ms']:.1f} ms</b></div>
        </article>""")
    samples = []
    rates = {t["type"]: t["rate_hz"] for t in current["manifest"]["schema_manifest"]["types"]}
    for item in validated:
        low, high = item["minimum_target"], item["maximum_target"]
        actual_low, actual_high = item["measured_minimum"], item["measured_maximum"]
        if (not low <= actual_low <= high or not low <= actual_high <= high
                or abs(actual_low - low) > 3 or abs(actual_high - high) > 3):
            raise ValueError(f"sample bounds failed: {item['type']}")
        rate = f"{rates[item['type']]:g} Hz" if rates[item["type"]] else "Sparse / event"
        samples.append(
            f"<tr><td>{escape(item['type'].removeprefix('Scale'))}</td>"
            f"<td class='number'>{low:,} &ndash; {high:,}</td>"
            f"<td class='number'>{actual_low:,} / {actual_high:,}</td><td>{rate}</td></tr>")
    history = ""
    if historical is not None:
        if "calibration" not in historical["manifest"]["schema_manifest"]:
            raise ValueError("historical appendix requires the original calibrated schema model")
        rows = []
        for population in historical["manifest"]["populations"]:
            metrics = [historical["aggregate"][f"{population}:{v}"]["metrics"] for v in "ABC"]
            values = [m["convergence_discovery_bytes"]["median"] / 1024 for m in metrics]
            rows.append(f"<tr><td>{population}</td>" + "".join(
                f"<td class='number'>{value:,.1f}</td>" for value in values) + "</tr>")
        history = f"""
        <details id="historical"><summary>Historical full-sweep appendix &mdash; different schemas</summary>
          <div class="notice"><strong>Not measurements of the current mission types.</strong>
          These 24 runs used the superseded optional-field, TypeObject-v1-proxy-calibrated model.
          Their discovery-byte results must not be carried forward to the replacement workload.</div>
          <p>One control plus 2/7/8/12 platforms; two repetitions; 150-second steady windows.
          Below: median startup + activation discovery, whole-mesh KiB.</p>
          <div class="table-wrap"><table><thead><tr><th>Platforms</th><th>A: split</th><th>B: shared eager</th><th>C: shared staged</th></tr></thead>
          <tbody>{''.join(rows)}</tbody></table></div>
          <p><a href="wan_dp_fourteen_type_scale_20261106.md">Historical report</a>
          &middot; <a href="wan_dp_fourteen_type_scale_20261106.json">Historical data</a></p>
        </details>"""
    observed_at = datetime.fromtimestamp(
        current["manifest"]["started_at"], timezone.utc).strftime("%d %b %Y &middot; %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%2310213b'/%3E%3Ccircle cx='16' cy='16' r='7' fill='%238be4d9'/%3E%3C/svg%3E"><title>WAN Mission Topic Comparison</title><style>{STYLE}</style></head>
<body><main>
<div class="masthead"><span class="brand">ACT / ENGINEERING FIELD NOTES</span><span>{observed_at}</span></div>
<header class="hero">
  <div class="eyebrow">WAN participants &amp; mission topic discovery</div>
  <h1>One mission workload.<br>Three discovery designs.</h1>
  <p>Three architectures, the same mission traffic. <strong>{comparison}</strong>
  These are observations from the two-platform smoke test.
  Delaying topics reduced initial inventory, but moved discovery into assignment.</p>
  <div class="pills"><span class="pill">Connext 7.7</span><span class="pill">1 control + 2 platforms</span>
  <span class="pill">14 synthetic mission types</span><span class="pill">Isolated loopback</span></div>
  <div class="actions"><a class="button" href="#comparison">Explore comparison &darr;</a>
  <button class="button secondary" type="button" onclick="window.print()">Print / save PDF</button></div>
</header>
<div class="notice"><strong>Smoke evidence, not a scale verdict.</strong>
One run per design with three-second steady windows. Startup/activation bytes and delivery
checks are useful observations; periodic discovery savings, multi-team scale, and RF tolerance
are not established. The live mesh was not changed.</div>
<div class="kpis">
  <div class="kpi"><div class="value {kpi_class}">{percentage}</div><div class="label">{kpi_label}</div></div>
  <div class="kpi"><div class="value">{totals['audited_sends']:,}</div><div class="label">Eligible audited sends</div></div>
  <div class="kpi"><div class="value">{totals['destination_receives']:,}</div><div class="label">Expected destination receives</div></div>
  <div class="kpi"><div class="value green">{totals['missing']} / {totals['unintended']}</div><div class="label">Missing / unintended deliveries</div></div>
</div>
<section id="comparison">
  <div class="section-head"><div><h2>Where discovery happens</h2><p>Whole-mesh outbound frame bytes, counted once. Startup + team/topic activation.</p></div><span class="tag">KiB = bytes / 1024</span></div>
  <div class="panel"><div class="legend"><span class="key">Startup</span><span class="key activation">Activation after assignment</span></div>{''.join(charts)}
  <p class="fine">Bars compare total discovery, not pure TypeObject bytes. Each transmission is counted once; TX and RX are not added.</p></div>
  <div class="cards">{''.join(cards)}</div>
  <div class="callout"><strong>The tradeoff:</strong> staged creation takes the initial endpoint count
  from 52 to 14, but spends {cases['C']['activation_discovery_bytes']/1024:.1f} KiB discovering the newly enabled endpoints.
  It postpones discovery; it does not eliminate it.</div>
</section>
<section class="two-col">
  <article class="panel"><div class="eyebrow">Preserved behavior</div><h2>The mission still flows</h2>
    <ul class="checklist"><li><b>PASS</b> Squad-wide pose delivery at 1 Hz</li>
    <li><b>PASS</b> Addressed command and health paths</li><li><b>PASS</b> Covered contact-recipient rules</li>
    <li><b>PASS</b> All designs reach 52 active endpoints</li></ul>
    <p class="fine">Audit counts exclude the final two seconds of each phase for delivery grace.
    The two-platform baseline does not establish cross-team metadata isolation.</p>
  </article>
  <article class="panel"><div class="eyebrow">Endpoint lifetime</div><h2>Same final working set</h2>
    <p class="subtle">Shared, eager: endpoints exist before assignment.</p>
    <div class="steps"><span class="step">52 endpoints</span><span class="arrow">&rarr;</span><span class="step teal">52 active</span></div>
    <p class="subtle">Shared, staged: only core endpoints exist initially.</p>
    <div class="steps"><span class="step">14 core</span><span class="arrow">+ 38</span><span class="step teal">52 active</span></div>
    <p class="fine">Partitions select data audiences. A shared, broadly discovered participant
    should not be treated as proof that team endpoint metadata stays hidden.</p>
  </article>
</section>
<section id="types">
  <div class="section-head"><div><h2>Mission-sized, not metadata-padded</h2><p>Fourteen distinct schemas with mission fields, audit keys, and bounded payloads.</p></div><span class="tag">SDK-validated CDR sizes</span></div>
  <div class="table-wrap"><table><thead><tr><th>Topic / type</th><th>Target sample range &middot; B</th><th>Measured min / max &middot; B</th><th>Periodic rate profile</th></tr></thead>
  <tbody>{''.join(samples)}</tbody></table></div>
  <div class="proof"><strong>All fourteen min/max checks passed with installed Connext 7.7.</strong>
  CDR alignment stays inside the supplied range and within three bytes of the target.
  No UMAA definitions, TypeObject-size targets, or artificial optional-member banks.
  Health is an additional topic reusing the Speed schema.</div>
</section>
<section id="discovery" class="panel">
  <div class="eyebrow">TypeObject v2 &mdash; what is being counted?</div><h2>Hashes first. Definitions when needed.</h2>
  <p>For TypeObject v2, endpoint discovery advertises type hashes. Unknown definitions can be
  fetched through the builtin TypeLookup request/reply channel. A participant that already
  resolved that type does not need to fetch it again for every endpoint.</p>
  <p>This spike uses <code>request_types_filter = *</code>, so type requests are not restricted
  to existing local topic matches. The control constructed twelve operational readers from
  discovery-resolved types, not a local-type fallback.</p>
  <div class="proof"><strong>Configuration verified against the installed SDK:</strong>
  TypeLookup enabled; <code>type_object_max_serialized_length = LENGTH_AUTO</code>;
  <code>type_code_max_serialized_length = 0</code>.
  The captured discovery total includes builtin traffic; this report does not separately
  attribute bytes to SEDP metadata, TypeLookup definitions, or assertion responses.</div>
  <p class="fine">The effective profile was inspected after the smoke run; the QoS source is unchanged.
  Configuration evidence is not a packet-level verification of every wire representation.
  Reference: <a href="https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/extensible_types_guide/extensible_types/Type_Representation.htm">RTI 7.7 Type Representation, section 5.1.1</a>.</p>
</section>
<section id="next">
  <div class="section-head"><div><h2>What remains to prove</h2><p>The smoke test validates the workload. It does not choose the production architecture.</p></div></div>
  <div class="cards">
    <article class="card"><div class="eyebrow">01 / Scale</div><h3>7, 8, and 12 platforms</h3><p>Repeat 150-second windows and multiple runs with the replacement mission schemas. Measure setup bursts and long-run discovery separately.</p></article>
    <article class="card"><div class="eyebrow">02 / Attribution</div><h3>Endpoint versus type traffic</h3><p>Decode SEDP and TypeLookup separately, then test late join and restart. Establish which metadata costs are paid once and which are repeated.</p></article>
    <article class="card"><div class="eyebrow">03 / RF &amp; routers</div><h3>Constrained links</h3><p>Validate real routers, MTU, bandwidth, loss, and recovery. Loopback matching times and local callback delays are not RF round-trip latency.</p></article>
  </div>
</section>
{history}
<footer><span>Self-contained report &middot; no external scripts, fonts, or tracking</span>
  <div class="source-links"><a href="wan_dp_mission_types_smoke_20261107.md">Current methodology</a>
  <a href="wan_dp_mission_types_smoke_20261107.json">Current measurements</a>
  <a href="../../spikes/wan_dp_route_tradeoffs/mission_types.xml">Mission type catalog</a></div>
</footer>
</main></body></html>"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--historical", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    current = json.loads(args.source.read_text())
    historical = json.loads(args.historical.read_text()) if args.historical else None
    args.output.write_text(build(current, historical), encoding="utf-8")
    print(args.output)
