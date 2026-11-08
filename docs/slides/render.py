#!/usr/bin/env python3
"""Render every Marp deck with the shared RTI theme and check PDF text bounds."""

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET


SLIDES = Path(__file__).resolve().parent


def validate_pdf(pdf):
    xml = subprocess.check_output(["pdftotext", "-bbox", str(pdf), "-"], text=True)
    root = ET.fromstring(xml)
    namespace = {"h": "http://www.w3.org/1999/xhtml"}
    pages = root.findall(".//h:page", namespace)
    if not pages:
        raise ValueError(f"{pdf.name}: no pages")
    for number, page in enumerate(pages, 1):
        width, height = float(page.attrib["width"]), float(page.attrib["height"])
        words = page.findall(".//h:word", namespace)
        if not words:
            raise ValueError(f"{pdf.name}, slide {number}: no text")
        overflow = [
            word.text for word in words
            if float(word.attrib["xMin"]) < 0 or float(word.attrib["yMin"]) < 0
            or float(word.attrib["xMax"]) > width or float(word.attrib["yMax"]) > height
        ]
        if overflow:
            raise ValueError(f"{pdf.name}, slide {number}: clipped text {overflow}")
    return len(pages)


def preview(pdf, directory, page_count):
    from PIL import Image, ImageDraw

    prefix = directory / pdf.stem
    subprocess.run(["pdftoppm", "-scale-to", "1280", "-png", str(pdf), str(prefix)],
                   check=True)
    images = sorted(directory.glob(f"{pdf.stem}-*.png"))
    if len(images) != page_count:
        raise ValueError(f"{pdf.name}: preview count differs from PDF pages")
    columns, tile_width, tile_height = 3, 426, 264
    rows = (page_count + columns - 1) // columns
    sheet = Image.new("RGB", (columns * tile_width, rows * tile_height), "white")
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(images):
        with Image.open(path) as image:
            image.thumbnail((tile_width - 12, tile_height - 30))
            x, y = (index % columns) * tile_width, (index // columns) * tile_height
            sheet.paste(image, (x + 6, y + 24))
            draw.text((x + 8, y + 4), f"Slide {index + 1}", fill="#004c97")
    sheet.save(directory / f"{pdf.stem}-overview.png")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview-dir", type=Path)
    args = parser.parse_args()
    if args.preview_dir:
        args.preview_dir.mkdir(parents=True, exist_ok=True)
    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", "marpteam/marp-cli:latest"],
        text=True).strip()
    decks = [
        path for path in sorted(SLIDES.glob("*.md"))
        if re.search(r"(?m)^marp:\s*true\s*$", path.read_text())
    ]
    if not decks:
        raise ValueError("no Marp decks found")
    with tempfile.TemporaryDirectory(prefix="rti-slide-render-") as temporary:
        output = Path(temporary)
        output.chmod(0o777)
        for source in decks:
            subprocess.run([
                "docker", "run", "--rm", "--network", "none",
                "-v", f"{SLIDES}:/slides:ro", "-v", f"{output}:/output",
                image, "--pdf", "--html", "--allow-local-files",
                "--theme-set", "/slides/rti-theme.css",
                "--output", f"/output/{source.stem}.pdf", "--", f"/slides/{source.name}",
            ], check=True)
            rendered = output / f"{source.stem}.pdf"
            page_count = validate_pdf(rendered)
            pdf = source.with_suffix(".pdf")
            shutil.copyfile(rendered, pdf)
            print(f"PASS {pdf.name}: {page_count} slides, no out-of-page text", flush=True)
            if args.preview_dir:
                preview(pdf, args.preview_dir, page_count)


if __name__ == "__main__":
    main()
