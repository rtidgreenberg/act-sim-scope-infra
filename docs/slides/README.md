# RTI-branded slide decks

| Deck | Main decision | Source | PDF |
|---|---|---|---|
| Link health | Measure presence, RTT, and delivery pressure separately | [Markdown](connext-ddil-link-health.md) | [PDF](connext-ddil-link-health.pdf) |
| Connext Edge Mesh Overview | Five slides: system diagram, isolation scope/runtime control, data-filtering strategies, discovery options, and DDIL link-health measurements | [Markdown](connext-edge-mesh-overview.md) | [PDF](connext-edge-mesh-overview.pdf) |
| Discovery strategies | Choose multicast, unicast, C2 directory, or CDS for operational needs | [Markdown](dds-discovery-strategies.md) | [PDF](dds-discovery-strategies.pdf) |

The discovery-strategies deck remains a separate, detailed reference.
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
