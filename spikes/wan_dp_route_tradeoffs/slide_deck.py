#!/usr/bin/env python3
"""Create an editable briefing deck from the retained mission smoke results."""

import argparse
import json
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Inches, Pt

from scale_report import setup_discovery_comparison, validate_rows


NAVY = "10213B"
INK = "15243C"
MUTED = "62718A"
PAPER = "F3F5F8"
WHITE = "FFFFFF"
TEAL = "087F78"
MINT = "8BE4D9"
AMBER = "F2AE49"
BLUE = "3C638C"
LINE = "E2E8F0"
WIDTH, HEIGHT = 13.333333, 7.5
REFERENCE = (
    "https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/"
    "connext_dds_professional/extensible_types_guide/extensible_types/Type_Representation.htm"
)


def text(slide, x, y, w, h, value, size=18, color=INK, bold=False):
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.margin_left = frame.margin_right = Inches(0)
    frame.margin_top = frame.margin_bottom = Inches(0)
    for index, line in enumerate(value.split("\n")):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.text = line
        paragraph.font.name = "Arial"
        paragraph.font.size = Pt(size)
        paragraph.font.bold = bold
        paragraph.font.color.rgb = RGBColor.from_string(color)
        paragraph.space_after = Pt(8)
        paragraph.line_spacing = 1.08
    return shape


def box(slide, x, y, w, h, color, outline=None, rounded=True):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(color)
    if outline:
        shape.line.color.rgb = RGBColor.from_string(outline)
    else:
        shape.line.fill.background()
    if rounded:
        shape.adjustments[0] = 0.12
    return shape


def slide(prs, title, label, dark=False):
    item = prs.slides.add_slide(prs.slide_layouts[6])
    item.background.fill.solid()
    item.background.fill.fore_color.rgb = RGBColor.from_string(NAVY if dark else PAPER)
    text(item, .58, .35, 8.6, .24, "ACT / WAN ARCHITECTURE BRIEF", 10,
         MINT if dark else MUTED, True)
    box(item, 10.55, .30, 2.18, .37, TEAL if dark else "E0F0EA")
    text(item, 10.70, .36, 1.92, .23, label, 9.5, WHITE if dark else TEAL, True)
    text(item, .58, .98, 12.0, 1.45, title, 32, WHITE if dark else INK, True)
    text(item, .58, 7.10, 10.4, .20, "Connext 7.7  |  14 mission types  |  07 November 2026",
         9, "B8CBDF" if dark else MUTED)
    text(item, 12.05, 7.08, .6, .24, f"{len(prs.slides):02d}", 10,
         "B8CBDF" if dark else MUTED, True)
    return item


def notes(item, value):
    frame = item.notes_slide.notes_text_frame
    if frame is None:
        raise RuntimeError("speaker-notes text frame is unavailable")
    frame.text = value


def card(item, x, title, body, eyebrow="", color=WHITE, w=3.96):
    box(item, x, 2.04, w, 3.95, color, LINE)
    if eyebrow:
        text(item, x+.22, 2.29, w-.44, .30, eyebrow, 11, TEAL, True)
    text(item, x+.22, 2.85, w-.44, .78, title, 23, INK, True)
    text(item, x+.22, 3.66, w-.44, 2.12, body, 16, MUTED)


def banner(item, value, color="E0F0EA"):
    box(item, .58, 6.25, 12.16, .55, color)
    text(item, .80, 6.38, 11.72, .32, value, 15, INK, True)


def build(data, output):
    validate_rows(data["runs"], data["manifest"])
    if (data["manifest"]["populations"] != [2] or data["manifest"]["variants"] != "ABC"
            or data["manifest"]["repetitions"] != 1 or data["full_windows"]):
        raise ValueError("briefing requires the current two-platform mission smoke comparison")
    if "sizing" not in data["manifest"]["schema_manifest"]:
        raise ValueError("superseded TypeObject-calibrated results cannot be used as current evidence")
    verified = data["type_discovery_verification"]
    if (not verified["type_lookup_enabled"]
            or verified["qos_source_sha256"] != data["manifest"]["source_hashes"]["qos.xml"]
            or verified["type_object_max_serialized_length"] != "LENGTH_AUTO"
            or verified["type_code_max_serialized_length"] != 0):
        raise ValueError("verified discovery configuration does not match the smoke measurement")
    cases = {r["variant"]: r for r in data["runs"]}
    difference, _ = setup_discovery_comparison(cases)
    comparison = ("Shared eager: percentage undefined (zero A baseline)" if difference is None else
                  "Shared eager: equal setup discovery" if difference == 0 else
                  f"Shared eager: {abs(difference):.1f}% {'less' if difference < 0 else 'more'} setup discovery")
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(WIDTH), Inches(HEIGHT)
    prs.core_properties.title = "When should we split WAN participants?"
    prs.core_properties.subject = "TypeObject v2, mission endpoint discovery, and the crossover test"
    prs.core_properties.author = "ACT Engineering"

    item = slide(prs, "When should we split\nWAN participants?", "DECISION BRIEF", dark=True)
    text(item, .66, 3.12, 8.3, 1.45,
         "TypeObject v2 makes type discovery lighter.\nTeam scope determines how far endpoint metadata travels.",
         23, "C5D5E6")
    box(item, .66, 5.05, 8.48, .82, "17434D")
    text(item, .91, 5.23, 7.98, .45,
         "There is no measured platform-count crossover yet.", 22, WHITE, True)
    box(item, 9.70, 3.0, 2.55, 2.85, "17434D")
    text(item, 10.05, 3.32, 1.9, .7, "14", 48, MINT, True)
    text(item, 10.05, 4.33, 1.9, 1.05, "mission types\nsame payload ranges\nthree architectures", 15, WHITE)
    notes(item, "This deck separates measured two-platform smoke evidence from an illustrative "
          "14-advertised-endpoint model and a proposed scale campaign. Fourteen types/topics do not "
          "necessarily mean fourteen reader/writer endpoints. The older v1-proxy-calibrated "
          "full-sweep results are not used as current mission measurements.")

    item = slide(prs, "What v2 changes - and what it does not", "VERIFIED + DOCUMENTED")
    box(item, .58, 2.05, 5.95, 3.95, WHITE, LINE)
    box(item, 6.78, 2.05, 5.95, 3.95, WHITE, LINE)
    text(item, .84, 2.32, 5.40, .45, "TYPE DEFINITIONS", 13, TEAL, True)
    text(item, .84, 3.02, 5.40, 2.62,
         "Endpoint discovery advertises type hashes.\nUnknown definitions can be fetched via TypeLookup.\n"
         "Resolved definitions can be reused within a participant.\n"
         "14 x N endpoints does not imply 14 x N full type transfers.", 18)
    text(item, 7.04, 2.32, 5.40, .45, "ENDPOINT + PARTICIPANT DISCOVERY", 13, TEAL, True)
    text(item, 7.04, 3.02, 5.40, 2.62,
         "Writer/reader metadata is still discovered.\nBroader visibility increases fanout and state.\n"
         "Extra DPs add participant overhead and separate caches.\n"
         "Audience partitions do not guarantee hidden endpoint metadata.", 18)
    banner(item, "Current profile: TypeLookup enabled; type size AUTO; legacy TypeCode size 0; request filter '*'.")
    notes(item, "Official RTI 7.7 guide, section 5.1.1: " + REFERENCE +
          "\nInstalled SDK QosProvider inspected Tradeoffs::scale_participant. Resource limits: "
          "type_object_max_serialized_length=LENGTH_AUTO, type_code_max_serialized_length=0. "
          "TypeLookup channel enabled and request_types_filter='*'. This is configuration evidence, "
          "not packet-by-packet proof of the wire representation or a separate TypeLookup byte total.")

    item = slide(prs, "Three options, the same required mission flows", "ARCHITECTURE OPTIONS")
    card(item, .58, "Split DPs", "A position DP plus an operational DP per platform.\n"
         "Team discovery can be scoped, if configured and verified.\n"
         "More participant overhead.", "A / EAGER ENDPOINTS")
    card(item, 4.69, "One shared DP", "Fewer participants and one shared type cache per platform.\n"
         "Operational endpoints exist before assignment.\n"
         "Broader endpoint visibility.", "B / EAGER ENDPOINTS", "F3FCF8")
    card(item, 8.80, "One DP, staged", "Only core endpoints exist initially.\n"
         "Other endpoints appear after team assignment.\n"
         "Discovery moves into activation.", "C / ON-DEMAND ENDPOINTS")
    banner(item, "All options preserve squad-wide pose, addressed commands, health, and covered contact delivery.")
    notes(item, "In the current rig, total WAN DP counts at two platforms plus control are 5 for A "
          "and 3 for B/C. A/B begin with 52 endpoints; C begins with 14 and reaches 52. The "
          "two-platform smoke does not prove out-of-team metadata isolation. A split only helps "
          "visibility if participant discovery really is scoped; two broadly connected DPs are "
          "not sufficient by themselves.")

    item = slide(prs, "What we actually measured", "MEASURED / 2 PLATFORMS")
    text(item, .68, 1.97, 11.9, .43, "Startup + activation discovery, whole-mesh outbound KiB", 18, MUTED)
    maximum = max(r["convergence_discovery_bytes"] for r in cases.values())
    box(item, 2.60, 2.69, .18, .18, BLUE, rounded=False)
    text(item, 2.88, 2.64, 1.62, .30, "Startup", 12, MUTED)
    box(item, 4.46, 2.69, .18, .18, AMBER, rounded=False)
    text(item, 4.74, 2.64, 3.3, .30, "Activation after assignment", 12, MUTED)
    for index, variant in enumerate("ABC"):
        r = cases[variant]
        y = 3.15 + index*.70
        text(item, .68, y-.04, 1.75, .49,
             {"A": "A / Split", "B": "B / Shared", "C": "C / Staged"}[variant], 19, INK, True)
        box(item, 2.60, y, 8.26, .34, "E6ECF3", rounded=False)
        start_width = 8.26*r["startup_discovery_bytes"]/(maximum or 1)
        activation_width = 8.26*r["activation_discovery_bytes"]/(maximum or 1)
        if start_width:
            box(item, 2.60, y, start_width, .34, BLUE, rounded=False)
        if activation_width:
            box(item, 2.60+start_width, y, activation_width, .34, AMBER, rounded=False)
        text(item, 11.10, y-.05, 1.50, .52,
             f"{r['convergence_discovery_bytes']/1024:.1f}", 24, TEAL, True)
    text(item, .68, 5.48, 6.0, .45, comparison, 20, TEAL if difference is not None and difference < 0 else INK, True)
    text(item, 7.01, 5.44, 5.54, .53,
         f"{data['totals']['destination_receives']:,} receives; "
         f"{data['totals']['missing']} missing / {data['totals']['unintended']} unintended", 18, INK, True)
    banner(item, "One run/design. Three-second steady windows. No periodic-discovery or RF conclusion.", "FFF1D9")
    notes(item, "Source: debug/test_reports/wan_dp_mission_types_smoke_20261107.json. "
          "One control plus two platforms; A/B/C single repetitions. Fourteen replacement synthetic "
          "mission types, not padded for TypeObject size. Audits cover 631 eligible sends and 841 "
          "destination receives, excluding the final two seconds of each phase for delivery grace. "
          "Current measurements do not establish the larger-scale crossover.")

    item = slide(prs, "Define the model before counting endpoints", "ILLUSTRATIVE / NOT BYTES")
    card(item, .58, "14 endpoints", "E = 14 advertised reader/writer endpoints per platform.\n"
         "This is an illustration, not the actual 14-topic inventory.", "TOTAL / E")
    card(item, 4.69, "2 global + 12 team", "G = 2 squad-wide endpoints.\n"
         "E - G = 12 team-only endpoints.\n"
         "Global position still reaches the squad.", "AUDIENCE / G")
    card(item, 8.80, "Teams of 3", "T = 3 platforms/team.\n"
         "Split discovery is confined to those teams.\n"
         "Control and protocol costs are omitted.", "TEAM SIZE / T")
    banner(item, "Count remote endpoint relationships per platform - not packets, type transfers, or airtime.")
    notes(item, "The formulas on the next slide assume homogeneous platforms, equal teams of three, "
          "two globally visible advertised endpoints, twelve team-only advertised endpoints, and "
          "successful participant discovery scoping. They omit control endpoints, peer topology, "
          "type lookup traffic, assertions, reliability overhead, churn, and all radio overhead. "
          "The actual spike has 14 application topic/type identities plus health reusing Speed, "
          "with 52 active endpoints across control and two platforms.")

    item = slide(prs, "Scope savings grow when the squad spans teams", "ILLUSTRATIVE / NOT MEASURED")
    text(item, .67, 1.96, 12, .4, "Remote endpoint records per platform | E = 14, G = 2, T = 3", 18, MUTED)
    box(item, 2.60, 2.51, .18, .18, BLUE, rounded=False)
    text(item, 2.88, 2.46, 2.72, .30, "One shared DP", 12, MUTED)
    box(item, 5.82, 2.51, .18, .18, TEAL, rounded=False)
    text(item, 6.10, 2.46, 4.46, .30, "Split, team-scoped DPs", 12, MUTED)
    populations = (3, 6, 12, 24)
    for index, population in enumerate(populations):
        common = 14*(population-1)
        scoped = 2*(population-1)+12*(3-1)
        y = 3.02+index*.66
        text(item, .69, y, 1.68, .5, f"{population} platforms", 16, INK, True)
        for offset, value, color in ((0, common, BLUE), (.24, scoped, TEAL)):
            width = 8.14*value/322
            box(item, 2.60, y+offset, width, .18, color, rounded=False)
            text(item, 2.72+width, y+offset-.04, .85, .28, str(value), 12, color, True)
    text(item, .69, 5.84, 11.96, .32,
         "Shared: E(N - 1)     |     Split: G(N - 1) + (E - G)(T - 1)", 17, INK, True)
    banner(item, "At 12 platforms: 154 vs 46 records (~70% fewer). This is NOT a 70% bandwidth result.")
    notes(item, "Exact illustrative values: N=3 -> 28/28; N=6 -> 70/34; N=12 -> 154/46; "
          "N=24 -> 322/70. The chart is relationship cardinality per platform, not total network "
          "transmissions, RSS, or serialized bytes. Larger N does not prove a performance crossover. "
          "If all endpoints are squad-wide, or if T=N, there is no audience reduction from this split.")

    item = slide(prs, "The crossover is a cost balance, not a magic N", "DECISION RULE", dark=True)
    box(item, .66, 2.15, 5.84, 3.56, "17374A")
    box(item, 6.76, 2.15, 5.84, 3.56, "17374A")
    text(item, .94, 2.48, 5.29, .39, "SPLIT BENEFIT", 13, MINT, True)
    text(item, .94, 3.15, 5.29, 2.11,
         "Avoided out-of-team endpoint advertisements\n"
         "Less irrelevant discovery state\n"
         "Smaller activation/rejoin fanout", 24, WHITE)
    text(item, 7.04, 2.48, 5.29, .39, "SPLIT COST", 13, MINT, True)
    text(item, 7.04, 3.15, 5.29, 2.11,
         "Extra participant discovery/assertions\n"
         "Separate type caches and possible lookups\n"
         "More resources and configuration", 24, WHITE)
    text(item, .84, 6.10, 11.96, .57,
         "Compare cold-start, mission churn, and steady operation separately.", 23, MINT, True)
    notes(item, "Split when the measured avoided discovery costs outweigh added DP costs, or when "
          "the scoped design is needed to satisfy discovery convergence/resource constraints. "
          "Use identical offered load and data audiences. Global pose fanout is not removed by "
          "putting it on a separate participant. Stable teams with many team-only endpoints give "
          "the largest potential scoping benefit. Broad request_types_filter='*' may fetch unknown "
          "types even without existing local topic matches; production filtering needs its own test.")

    item = slide(prs, "What we need to test it properly", "INPUTS + READY TOOLING")
    box(item, .58, 2.05, 5.95, 3.95, WHITE, LINE)
    box(item, 6.78, 2.05, 5.95, 3.95, WHITE, LINE)
    text(item, .85, 2.31, 5.40, .40, "MISSION / NETWORK INPUTS", 13, TEAL, True)
    text(item, .85, 3.03, 5.40, 2.59,
         "1. Team sizes and team-change frequency\n"
         "2. Squad-wide vs team-only topic audiences\n"
         "3. WAN rate, loss, latency, and shared/per-node budget\n"
         "4. Acceptable activation time and required delivery", 18)
    text(item, 7.04, 2.31, 5.40, .40, "ALREADY AVAILABLE", 13, TEAL, True)
    text(item, 7.04, 3.03, 5.40, 2.59,
         "Connext 7.7 and fourteen mission-size schemas\n"
         "Isolated UDP workers and packet capture\n"
         "Sequence-matched delivery audits\n"
         "A/B/C harness, reports, and runtime cleanup", 18)
    banner(item, "Before performance claims: verify scoped discovery visibility and the real endpoint inventory.")
    notes(item, "The mission owner needs to choose audience mapping and target communications "
          "conditions, not provide new DDS tooling. Nominal teams of three and a 2-global/12-team "
          "endpoint split are illustrative defaults only. The actual 14-topic scenario must count "
          "its concrete writers/readers. The current benchmark is unthrottled loopback; it does "
          "not establish a reliable activation SLA on an RF network.")

    item = slide(prs, "The campaign that finds the actual break-even", "PROPOSED / NOT RUN")
    card(item, .58, "Scale + scope", "2 / 6 / 12 / 24 platforms.\n"
         "Single-team and multiple-team cases.\n"
         "Same audiences, endpoint inventory, and QoS across A/B/C.", "01 / EXTEND THE HARNESS")
    card(item, 4.69, "Events + wire", "Cold start, assignment, enable, team change, late join, restart.\n"
         "Separate SEDP from TypeLookup.\n"
         "Measure totals, peaks, convergence, CPU/RSS.", "02 / REPEAT + AUDIT")
    card(item, 8.80, "Rate + loss", "Use identical link constraints.\n"
         "Verify intended delivery and recovery.\n"
         "Confirm the winner with actual routers and isolated EMANE.", "03 / TEST THE BOTTLENECK")
    banner(item, "Use repeated 150-second steady windows. The 24-platform case requires a harness extension.")
    notes(item, "Suggested first campaign: one-team and teams-of-three layouts, A/B/C, repeated "
          "steady windows of at least 150 seconds, event windows, and exact recipients. Do not "
          "choose a winner based only on endpoint cardinality. The current runner supports at "
          "most 12 platforms and probes participant IDs 0-31; 24 platforms with two DPs requires "
          "49 WAN DPs including control. Increase the isolated peer-probe range consistently "
          "across variants and keep DDS domains <=232. Verify scoped participant/endpoint "
          "visibility before interpreting bytes. The production mesh must remain unchanged.")

    item = slide(prs, "Recommendation: measure the scope advantage", "TAKEAWAY", dark=True)
    text(item, .76, 2.32, 11.8, 1.62,
         "Start testing at 6 platforms in two teams.\n"
         "Treat 12 and 24 as scale candidates - not proven thresholds.", 29, WHITE, True)
    text(item, .76, 4.25, 11.5, 1.38,
         "Keep one DP as the simpler baseline.\n"
         "Choose split DPs when measured scope savings or convergence requirements justify them.",
         23, "C5D5E6")
    box(item, .76, 6.02, 11.80, .67, "17434D")
    text(item, 1.02, 6.16, 11.26, .39,
         "Platform count amplifies the cost. Team discovery scope determines the opportunity.",
         20, MINT, True)
    notes(item, "Six platforms in two three-platform teams is the first useful candidate in the "
          "illustrative model, not a measured performance crossover. At two platforms the "
          "current smoke measured lower cumulative setup discovery for one DP. New 6/12/24 "
          "mission-schema data and constrained-link verification are still required. "
          "Sources: debug/test_reports/wan_dp_mission_types_smoke_20261107.json; "
          "spikes/wan_dp_route_tradeoffs/mission_types.xml; " + REFERENCE)

    output.parent.mkdir(parents=True, exist_ok=True)
    prs.save(output)
    return len(prs.slides)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    count = build(json.loads(args.source.read_text()), args.output)
    print(f"{args.output}: {count} slides")
