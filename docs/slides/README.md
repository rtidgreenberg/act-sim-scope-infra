# RTI-branded slide decks

| Deck | Main decision | Source | PDF |
|---|---|---|---|
| Link health | Measure presence, RTT, and delivery pressure separately | [Markdown](connext-ddil-link-health.md) | [PDF](connext-ddil-link-health.pdf) |
| Connext Edge Mesh Overview | Five slides: system diagram, isolation scope/runtime control, data-filtering strategies, discovery options, and DDIL link-health measurements | [Markdown](connext-edge-mesh-overview.md) | [PDF](connext-edge-mesh-overview.pdf) |
| Discovery strategies | Four steps for mesh radio: router isolation, smaller discovery announcements, multicast vs. unicast, and peer setup | [Markdown](dds-discovery-strategies.md) | [PDF](dds-discovery-strategies.pdf) |
| System presence and liveliness | Compare writer liveliness, participant discovery, and periodic application health in Connext 7.7 | [Markdown](system-presence-liveliness.md) | [PDF](system-presence-liveliness.pdf) |

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
