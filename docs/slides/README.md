# RTI-branded slide decks

| Deck | Main decision | Source | PDF |
|---|---|---|---|
| WAN architecture and QoS | Why the qualified Routing Service design chose three delivery policies, platform-selected receive filters, and shared WAN pacing | [Editable PowerPoint](wan-architecture-and-qos.pptx) | [PDF](wan-architecture-and-qos.pdf) |
| Bandwidth Optimization | Send less through audience/rate selection; separate tested discovery, delivery and compression strategies from unqualified options | [Markdown](bandwidth-optimization.md) | [PDF](bandwidth-optimization.pdf) |
| Adaptive runtime tuning | RTT and protocol pressure drive bounded policy messages; a qualified actuator applies supported live settings | [Markdown](adaptive-runtime-tuning.md) | [PDF](adaptive-runtime-tuning.pdf) |
| Startup optimization | Suppress application startup requests; spread gateway launches and discovery responses over one second | [Markdown](startup-optimization.md) | [PDF](startup-optimization.pdf) |
| DDIL recovery | Active-reader repair succeeds; inactivity/restart and unread-sample expiry can lose deletions | [Markdown](ddil-recovery.md) | [PDF](ddil-recovery.pdf) |
| WAN schema propagation options | Two architectures: generated typed unions versus fixed binary envelopes using local payload types; tested versus source-inspected | [Markdown](wan-schema-propagation-options.md) | [PDF](wan-schema-propagation-options.pdf) |
| Event / State / Telemetry | Three-topic WAN names and delivery contracts: required occurrences, current instance state and fresh observations | [Markdown](event-state-telemetry.md) | [PDF](event-state-telemetry.pdf) |
| Telemetry: typed or catalog-driven | One-slide payload comparison; State catalog drives application-managed Telemetry filters | [Markdown](telemetry-typed-or-catalog.md) | [PDF](telemetry-typed-or-catalog.pdf) |
| Link health | Measure presence, RTT, and delivery pressure separately | [Markdown](connext-ddil-link-health.md) | [PDF](connext-ddil-link-health.pdf) |
| Connext Edge Mesh Overview | System diagram, three WAN delivery rules, contact report outage spike, sample scheduling versus four network priority tiers, typed versus binary payloads, LAN auto-QoS versus explicit WAN QoS, isolation, filtering, discovery, and link health | [Markdown](connext-edge-mesh-overview.md) | [PDF](connext-edge-mesh-overview.pdf) · [PowerPoint](connext-edge-mesh-overview.pptx) |
| Discovery strategies | Four mesh-radio steps plus the measured LBED rejection: receiver filtering works, but unwanted data still crosses the WAN | [Markdown](dds-discovery-strategies.md) | [PDF](dds-discovery-strategies.pdf) |
| System presence and liveliness | Compare writer liveliness, participant discovery, and periodic application health in Connext 7.7 | [Markdown](system-presence-liveliness.md) | [PDF](system-presence-liveliness.pdf) |

The twenty-one-slide Bandwidth Optimization deck also has a
[PowerPoint export](bandwidth-optimization.pptx).
It consolidates repository evidence for data filtering, source-side rate reduction,
three-family delivery, shared pacing, discovery isolation, startup spreading,
binary envelopes and native compression. Data filtering distinguishes reader
configuration from actual sender-side execution, with measured transmission
counts and the current ACT workload's remaining byte-qualification gap.
It explicitly rejects the tested LBED and bzip2 configurations and separates
current implementation, source-only qualification, isolated experiments and the
adaptive-control proposal. Synthetic compression percentages are not fleet-wide
savings. Speaker notes retain source citations, conditions and limits.
The PowerPoint contains rendered slides; edit the Markdown source.

The eleven-slide adaptive runtime tuning deck also has a
[PowerPoint export](adaptive-runtime-tuning.pptx).
It outlines runtime-capable options, a proposed local policy/status topic loop,
RTT and protocol-pressure thresholds, hysteresis, safe expiry and qualification.
Native Connext capabilities are separated from unverified Routing Service actuator
access. It preserves required Event delivery and the three-topic WAN contract.
This is a design proposal, not an implemented or deployed adaptive controller.
The final slide contains key takeaways; speaker notes preserve API references and
integration limits. Edit the Markdown source.

The eight-slide startup optimization deck also has a
[PowerPoint export](startup-optimization.pptx) and a detailed
[configuration/qualification record](../startup-optimization.md).
It separates proven application-reader suppression from unresolved discovery
suppression, shows one-second gateway slots and 0–1 second discovery response
windows, and records the native seven-participant capture.
The final slide contains key takeaways. This is a source-only change, not a
deployment or radio-bandwidth guarantee. Edit the Markdown source.

The fourteen-slide DDIL recovery deck also has a
[PowerPoint export](ddil-recovery.pptx) and a detailed
[findings record](../instance-state-through-ddil.md).
It explicitly traces ContactReport instance recovery through the shared WAN State
topic and separates the actual processor WAN tests from native-reader cache controls,
and positive recovery results from the two reproduced recovery limitations.
Two ISC slides distinguish normal reliable repair from same-writer state recovery,
retained-state requirements and the historical data-less ALIVE relay limitation.
The current processor's ContactReport tests are not presented as ISC qualification.
The startup ACKNACK experiment is a separate protocol result, not a fix for
restart recovery. Measurements are from 2026-11-19; local reports/captures were
deleted by the requested 2026-11-20 debug cleanup and were not regenerated.
The PowerPoint contains rendered slide images; edit the Markdown source.
Speaker notes preserve test conditions, observation windows and limitations.

The schema-propagation comparison also has a
[PowerPoint export](wan-schema-propagation-options.pptx) and a detailed
[workflow/options record](../wan-schema-propagation-options.md).
It is one slide comparing typed unions (runtime discovery or preloaded XML)
with fixed envelopes carrying binary payloads whose types are available locally.
Binary means no WAN application type graph, not no outer DDS schema.
The one-Event-union spikes are distinguished from Zenoh source evidence and the
untested Connext binary path, proposed setup tooling, three-family priority
integration and unmeasured WAN metadata cost. Periodic is
the discussion's name for the current Telemetry family, not a deployed rename.
The PowerPoint contains a rendered slide image; edit the Markdown source.

The telemetry comparison also has a [PowerPoint export](telemetry-typed-or-catalog.pptx).
Both options retain best-effort, freshness-first delivery. The catalog option
uses State for definitions/inventory and application-managed filters on Telemetry
identifiers, not an automatic cross-topic join. Neither option is selected or
implemented by the slide.

The Event / State / Telemetry slide also has a [PowerPoint export](event-state-telemetry.pptx).
Its names match the main router's three-topic WAN contract. The rename preserves
existing payloads and policies; transient local remains a candidate for State
bootstrap, not a finalized QoS policy. Speaker notes retain
the lifecycle, restart and classification qualifications.

The system-presence deck also has a [PowerPoint export](system-presence-liveliness.pptx).
Its six plain-language slides focus on platform-level presence: SPDP2 maintains
the discovery database and cleanup; RouterHealth supplies periodic metadata and
application notifications. All wire traffic must be validated by its purpose and
sender/receiver roles, including discovery, writer liveliness, health, application
messages, acknowledgments, and repairs. The comparison retains the Connext 7.7
finite-writer-liveliness restriction on writer-side filtering.
The PowerPoint contains rendered slide images; edit the Markdown source to change
content. Speaker notes retain technical caveats, shared-Status implementation
details, and evidence links. This is not a fresh live-system or wire-traffic check.
Render its PDF using the shared renderer below. To export PowerPoint with the same
installed Marp image, use `--pptx` instead of `--pdf` with the same theme, local-file,
read-only source mount, and disposable output-directory options in `render.py`.

The discovery-strategies deck is a separate, simplified mesh-radio walkthrough.
Its five slides include the 2026-11-20 Routing Service comparison: SPDP2 + LBED
supports dynamic receiver filters but transmits all offered samples, unlike the
SEDP control. LBED is therefore rejected for our current WAN audience-filtering
requirements. Speaker notes link the [measured spike](../../spikes/routing_service_lbed/README.md)
and distinguish working binary-envelope forwarding from missing sender-side
suppression. No production discovery change is implied.
Its first slide uses a block diagram matching the edge-mesh overview:
gray node groups, blue routers, and an orange shared WAN block.
Its type-propagation reduction is explicitly scoped to Connext versions before 7.7;
WAN consumers must already have compatible types, and dynamic type discovery still needs propagation.
Background references: [pre-7.7 type representation (7.3)](https://community.rti.com/static/documentation/connext-dds/7.3.0/doc/manuals/connext_dds_professional/extensible_types_guide/extensible_types/Type_Representation.htm),
[discovery peer configuration](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/ConfigPeersListUsed_inDiscov.htm),
and [CDS introduction](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/addon_products/cloud_discovery_service/introduction.html).
The [C2 bootstrap report](../cpp_router/c2-peer-bootstrap-report.md) retains the detailed
application-directory evidence. The simplified deck does not claim measured radio bandwidth savings.
The Connext Edge Mesh Overview deck contains a current-system block diagram, the scope matrix,
a comparison of route-based exposure and reader-driven interest, a discovery-options comparison,
and a simple link-health summary adapted from the link-health deck.
Its PDF and PowerPoint use a 3:4 portrait layout, a portrait architecture diagram,
and reserved footer/logo space. Layout overrides are local to this deck.
Each uses the [shared RTI theme](rti-theme.css):
white content pages, blue title pages where included, a consistent footer/logo, and orange
takeaway markers. Comparison tables distinguish the benefit from the cost;
tested, documented, and untested capabilities are labeled separately.

## Branding

Applied from the supplied internal RTI brand guide, pages 6-9 and 15:

- RTI Blue `#004C97`, Orange `#ED8B00`, and Gray `#63666A`.
- Original corporate logo geometry, with the orange dot preserved.
  [Blue artwork](assets/rti-logo.svg) and the guide's
  [white-on-blue variant](assets/rti-logo-white.svg) include the required clear
  space. The symbol is not stretched or used on a patterned background.
- Avenir Next is the presentation font specified by the guide.
  The theme requests it first, then Arial / Liberation Sans as explicit fallbacks.

**Font limitation:** the current Marp container does not have a licensed Avenir
Next installation. Generated PDFs use the available Arial-compatible Liberation
Sans fallback. No proprietary font was extracted from the guide or bundled here.
Exact Avenir Next rendering requires that licensed font in the rendering environment.

The internal guide itself is not copied into the slide assets. Only the approved
logo artwork and relevant presentation styling are applied.

## Rebuild and inspect

Prerequisites: Docker, the official `marpteam/marp-cli:latest` image, and Poppler
tools (`pdftotext`, `pdftoppm`). Pillow is used only for optional overview images.
The renderer resolves the image to its local immutable ID before running it.

```bash
python3 docs/slides/render.py

# Optional visual-review images; use a fresh disposable directory.
python3 docs/slides/render.py --preview-dir /tmp/rti-slides-review
```

The renderer discovers Marp Markdown sources, applies the shared theme, and checks
every PDF page for out-of-page text before replacing its output. Containers have
no network access; sources are mounted read-only and output staging is disposable.
Local file access is enabled only to load the theme's local logo assets.

For VS Code Marp preview, register `docs/slides/rti-theme.css` as a custom theme in
the extension's theme settings. PDF rendering should still be visually reviewed:
text-bounds checks alone do not establish readability, clear space, or contrast.

## Engineering comparison: catalog vs types (2026-11-11)

[Editable PowerPoint](catalog-vs-types-2026-11-11.pptx) |
[PDF](catalog-vs-types-2026-11-11.pdf)

This concise 14-slide review compares typed messages and catalog-driven
point/key/value records as clean-sheet DDS designs. It does not award advantages
for existing code, deployment history or migration effort. It covers selectable
units, schemas, consistency, efficiency, audience routing, and a possible
Event / Status / StatusDictionary layout.

The revision incorporates the discussion: Event transient-local durability is a
requirement; dictionary definitions and status values have separate bootstrap
needs; commands retain structured semantics; batching with retained replay remains
a qualification question, not a claimed documented defect.

Source citations and PowerPoint speaker notes distinguish requirements, proposals
and documented semantics. No application, QoS or live-mesh changes were made.

This standalone PowerPoint uses a neutral engineering theme, not the RTI-branded
Marp theme above. Its PDF was exported with LibreOffice; it is not rebuilt by
`render.py`.
