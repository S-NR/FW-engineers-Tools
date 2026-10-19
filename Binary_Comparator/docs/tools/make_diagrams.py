"""Draw the architecture and data-flow diagrams used by the design document and the presentation."""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(__file__), "..", "images")
INK, MUTED, LINE = "#1f2933", "#52606d", "#9aa5b1"


def font(size, bold=False):
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"), "arial.ttf"):
        try:
            return ImageFont.truetype(os.path.join("C:/Windows/Fonts", name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def box(d, xy, title, lines=(), fill="#ffffff", outline="#c3ccd6", title_color=INK, radius=14):
    d.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=2)
    x0, y0, x1, _ = xy
    d.text(((x0 + x1) // 2, y0 + 16), title, font=font(24, True), fill=title_color, anchor="mt")
    for i, ln in enumerate(lines):
        d.text(((x0 + x1) // 2, y0 + 54 + i * 27), ln, font=font(19), fill=MUTED, anchor="mt")


def arrow(d, x0, y0, x1, y1, color=LINE):
    d.line((x0, y0, x1, y1), fill=color, width=4)
    d.polygon([(x1, y1), (x1 - 9, y1 - 16), (x1 + 9, y1 - 16)] if y1 > y0 else
              [(x1, y1), (x1 - 16, y1 - 9), (x1 - 16, y1 + 9)], fill=color)


def architecture():
    w, h = 1800, 1060
    img = Image.new("RGB", (w, h), "#f4f6f9")
    d = ImageDraw.Draw(img)
    d.text((60, 36), "Architecture — layers and modules", font=font(34, True), fill=INK)
    d.text((60, 82), "The comparison engine is independent of the GUI; GUI, CLI and tests all use the same core API.",
           font=font(21), fill=MUTED)
    # front ends
    box(d, (60, 140, 620, 300), "GUI  (Tkinter)", ["main_window · file_panel · comparison_view",
                                                   "features_view · hex_viewer · memory_map"], fill="#e8f0fe",
        outline="#2563eb", title_color="#1d4ed8")
    box(d, (660, 140, 1140, 300), "CLI  (firmware-diff)", ["argparse · console output",
                                                           "exit codes for CI"], fill="#e8f0fe", outline="#2563eb",
        title_color="#1d4ed8")
    box(d, (1180, 140, 1740, 300), "Reports", ["html_report · csv_report", "JSON summary"], fill="#f3e8ff",
        outline="#7c3aed", title_color="#6d28d9")
    # session
    box(d, (60, 360, 1740, 470), "core.session  —  load → compare → analyse → symbols / features / controller",
        fill="#ffffff", outline="#1e293b")
    for x in (340, 900, 1460):
        arrow(d, x, 300, x, 360)
    # core modules
    core = [
        ("binary_reader", ["format detection", "SHA-256 · image model"]),
        ("binary_comparator", ["chunked hierarchical", "byte comparison"]),
        ("analysis", ["blocks · regions", "expected / unexpected"]),
        ("feature_diff", ["functions · modules", "relocation-aware"]),
        ("mcu_detector", ["controller", "identification"]),
        ("metadata · checksum", ["version / date / git", "CRC-32 / CRC-16 / SHA"]),
    ]
    cw = (1680 - 5 * 20) // 6
    for i, (title, lines) in enumerate(core):
        x0 = 60 + i * (cw + 20)
        box(d, (x0, 530, x0 + cw, 680), title, lines, fill="#ffffff", outline="#94a3b8")
        arrow(d, x0 + cw // 2, 470, x0 + cw // 2, 530)
    d.text((60, 692), "core/  ·  intervals (address sets) · image (segments, address ↔ file offset) · block_detector · "
                      "region_manager · symbols · elf_compare · memory_map_model", font=font(19), fill=MUTED)
    # formats
    fmts = [("bin_parser", ["memory-mapped, read-only"]), ("hex_parser", ["Intel HEX, checksums"]),
            ("elf_parser", ["ELF32/64, LMA/VMA, PIC32"]), ("symbols (map)", ["GNU ld / XC32 map files"])]
    fw = (1680 - 3 * 20) // 4
    for i, (title, lines) in enumerate(fmts):
        x0 = 60 + i * (fw + 20)
        box(d, (x0, 760, x0 + fw, 880), title, lines, fill="#ecfdf5", outline="#15803d", title_color="#166534")
    d.text((60, 900), "formats/  ·  every parser validates its input and raises a clear error instead of crashing",
           font=font(19), fill=MUTED)
    d.rounded_rectangle((60, 950, 1740, 1020), radius=12, fill="#1e293b")
    d.text((900, 985), "Firmware files (.bin · .hex · .elf)  +  optional ELF / .map symbol files  +  project JSON",
           font=font(22, True), fill="#f1f5f9", anchor="mm")
    img.save(os.path.join(OUT, "00_architecture.png"))


def pipeline():
    w, h = 1800, 430
    img = Image.new("RGB", (w, h), "#f4f6f9")
    d = ImageDraw.Draw(img)
    d.text((60, 30), "Comparison pipeline", font=font(34, True), fill=INK)
    steps = [("Load", ["parse + validate", "SHA-256 · controller"]),
             ("Compare", ["union of addresses", "byte runs (IntervalSet)"]),
             ("Analyse", ["blocks · regions", "expected areas · verdict"]),
             ("Functions", ["symbols A / B", "modified vs relocated"]),
             ("Present", ["GUI tabs · hex viewer", "HTML / CSV / JSON / CLI"])]
    sw = (1680 - 4 * 40) // 5
    for i, (title, lines) in enumerate(steps):
        x0 = 60 + i * (sw + 40)
        box(d, (x0, 110, x0 + sw, 300), f"{i + 1}. {title}", lines, fill="#ffffff",
            outline="#2563eb" if i < 4 else "#7c3aed")
        if i:
            arrow(d, x0 - 38, 205, x0 - 4, 205, color="#2563eb")
    d.text((60, 340), "Step 2 runs once (about 0.1 s for a 64 MB image); steps 3-4 re-run instantly when regions, expected "
                      "areas or symbol files change.", font=font(21), fill=MUTED)
    img.save(os.path.join(OUT, "00_pipeline.png"))


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    architecture()
    pipeline()
    print("diagrams written")
