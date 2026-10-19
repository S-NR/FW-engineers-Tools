"""Build docs/Firmware_Binary_Comparison_Tool_Design_v1.0.docx (python-docx)."""

from __future__ import annotations

import os

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

DOCS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
IMG = os.path.join(DOCS, "images")
OUT = os.path.join(DOCS, "Firmware_Binary_Comparison_Tool_Design_v1.0.docx")

ACCENT = RGBColor(0x1D, 0x4E, 0xD8)
INK = RGBColor(0x1F, 0x29, 0x33)
MUTED = RGBColor(0x52, 0x60, 0x6D)

doc = Document()
_fig = [0]
_tab = [0]


# ------------------------------------------------------------------ helpers
def setup_styles():
    st = doc.styles
    st["Normal"].font.name = "Segoe UI"
    st["Normal"].font.size = Pt(10)
    st["Normal"]._element.rPr.rFonts.set(qn("w:eastAsia"), "Segoe UI")
    for name, size, color in (("Title", 26, INK), ("Heading 1", 16, ACCENT), ("Heading 2", 12.5, INK),
                              ("Heading 3", 11, INK)):
        s = st[name]
        s.font.name = "Segoe UI"
        s.font.size = Pt(size)
        s.font.color.rgb = color
        s.font.bold = True
    for sec in doc.sections:
        sec.left_margin = sec.right_margin = Cm(2.0)
        sec.top_margin = sec.bottom_margin = Cm(2.0)


def shade(cell, hex_color):
    tc_pr = cell._element.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def h(text, level=1):
    doc.add_heading(text, level=level)


def p(text, bold_prefix=None, italic=False, size=None, color=None):
    para = doc.add_paragraph()
    if bold_prefix:
        r = para.add_run(bold_prefix)
        r.bold = True
    r = para.add_run(text)
    r.italic = italic
    if size:
        r.font.size = Pt(size)
    if color:
        r.font.color.rgb = color
    para.paragraph_format.space_after = Pt(6)
    return para


def bullets(items, style="List Bullet"):
    for it in items:
        para = doc.add_paragraph(style=style)
        if isinstance(it, tuple):
            r = para.add_run(it[0])
            r.bold = True
            para.add_run(it[1])
        else:
            para.add_run(it)
        para.paragraph_format.space_after = Pt(2)


def code(text):
    para = doc.add_paragraph()
    r = para.add_run(text)
    r.font.name = "Consolas"
    r.font.size = Pt(8.5)
    para.paragraph_format.left_indent = Cm(0.5)
    para.paragraph_format.space_after = Pt(6)
    pPr = para._element.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), "F1F5F9")
    pPr.append(shd)


def table(headers, rows, widths=None, caption=None):
    if caption:
        _tab[0] += 1
        cp = doc.add_paragraph()
        r = cp.add_run(f"Table {_tab[0]}: {caption}")
        r.bold = True
        r.font.size = Pt(9)
        r.font.color.rgb = MUTED
        cp.paragraph_format.space_after = Pt(2)
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, head in enumerate(headers):
        c = t.rows[0].cells[i]
        c.text = ""
        r = c.paragraphs[0].add_run(head)
        r.bold = True
        r.font.size = Pt(9)
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        shade(c, "1E293B")
    for n, row in enumerate(rows):
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = ""
            r = cells[i].paragraphs[0].add_run(str(val))
            r.font.size = Pt(9)
            if n % 2:
                shade(cells[i], "F8FAFC")
    if widths:
        for row in t.rows:
            for i, wdt in enumerate(widths):
                row.cells[i].width = Cm(wdt)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def figure(name, caption, width_cm=16.5):
    path = os.path.join(IMG, name)
    if not os.path.isfile(path):
        p(f"[missing image {name}]", italic=True)
        return
    doc.add_picture(path, width=Cm(width_cm))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    _fig[0] += 1
    cp = doc.add_paragraph()
    cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = cp.add_run(f"Figure {_fig[0]}: {caption}")
    r.italic = True
    r.font.size = Pt(9)
    r.font.color.rgb = MUTED


# ------------------------------------------------------------------ content
def title_page():
    doc.add_paragraph().paragraph_format.space_after = Pt(60)
    t = doc.add_paragraph()
    r = t.add_run("Firmware Binary Comparison Tool")
    r.bold = True
    r.font.size = Pt(28)
    r.font.color.rgb = INK
    t2 = doc.add_paragraph()
    r = t2.add_run("Design Document  ·  Feature Specification")
    r.font.size = Pt(16)
    r.font.color.rgb = ACCENT
    doc.add_paragraph()
    table(["Item", "Value"], [
        ("Document version", "1.0"),
        ("Software version", "1.0.0 — frozen baseline"),
        ("Date", "16 October 2026"),
        ("Status", "Frozen — feature complete for v1.0.0"),
        ("Prepared by", "Vinaya Kumar"),
        ("Verification", "84 automated tests passing; validated on STM32F411 and PIC32MX575 production firmware"),
    ], widths=[5, 11])
    doc.add_paragraph()
    p("Revision history", bold_prefix="")
    table(["Version", "Date", "Description"], [
        ("1.0", "16 Oct 2026", "First release of the design document for the frozen v1.0.0 baseline."),
    ], widths=[2.5, 3, 10.5])
    doc.add_page_break()
    h("Contents", 1)
    for line in CONTENTS:
        para = doc.add_paragraph()
        indent = line.startswith("   ")
        r = para.add_run(line.strip())
        r.bold = not indent
        para.paragraph_format.left_indent = Cm(0.8 if indent else 0)
        para.paragraph_format.space_after = Pt(1 if indent else 3)
    doc.add_page_break()


CONTENTS = [
    "1. Introduction", "   1.1 Purpose · 1.2 Scope · 1.3 Intended audience · 1.4 Terms",
    "2. Feature overview and requirements traceability",
    "3. Architecture", "   3.1 Package structure · 3.2 Processing pipeline",
    "4. Data model", "   4.1 Firmware image · 4.2 Address sets · 4.3 Configuration file",
    "5. Comparison engine", "   5.1 Algorithm · 5.2 Gap-fill mode · 5.3 Results · 5.4 Performance",
    "6. Region, block and expected-difference analysis",
    "7. File format support",
    "8. Function- and module-level change analysis",
    "   8.1 Symbol sources · 8.2 Matching and statuses · 8.3 Relocation-aware comparison · 8.4 Validation",
    "9. Controller identification",
    "10. Metadata, checksums and ELF comparison",
    "11. Outputs: GUI, CLI and reports", "   11.1 GUI · 11.2 Command-line interface · 11.3 Reports",
    "12. Engineering requirements compliance",
    "13. Verification",
    "14. Build, deployment and baseline",
    "15. Known limitations and future work",
]


def section_intro():
    h("1. Introduction", 1)
    h("1.1 Purpose", 2)
    p("This document describes the design and the complete feature set of the Firmware Binary Comparison Tool, a "
      "desktop and command-line utility for embedded firmware engineers. It compares two firmware images and shows "
      "exactly what changed, where it changed, which functions and modules were affected, and whether each change "
      "was expected. It is the reference for the frozen v1.0.0 baseline.")
    h("1.2 Scope", 2)
    bullets([
        "Comparison of raw binary (.bin), Intel HEX (.hex/.ihex/.ihx) and ELF (.elf/.axf/.out) firmware images, in "
        "any combination, by MCU memory address.",
        "Region-based and expected-difference analysis, changed-block analysis and memory-map visualisation.",
        "Function- and module-level change analysis from ELF or GNU ld / XC32 linker map files, with relocation-aware "
        "comparison that separates real edits from address shifts.",
        "Heuristic controller identification, firmware metadata detection and checksums.",
        "Engineering reports (HTML, CSV, JSON), a command-line interface for CI and a Tkinter GUI.",
    ])
    h("1.3 Intended audience", 2)
    p("Embedded firmware engineers, test and release engineers, and maintainers of the tool.")
    h("1.4 Terms", 2)
    table(["Term", "Meaning"], [
        ("Address", "MCU memory address a byte is programmed to (e.g. 0x08012A40). All analysis uses addresses."),
        ("File offset", "Byte position inside the file on disk, always shown with a '+' prefix (+0x12A40). Intel HEX "
                        "has no byte-level file offset (shown as n/a)."),
        ("Base address", "For raw .bin files: the MCU address of file offset 0."),
        ("LMA / VMA", "Load (flash) address and virtual (run-time) address of ELF content. Images are compared at "
                      "the LMA by default."),
        ("Region", "Named, user-defined address range (Bootloader, Application, Calibration …)."),
        ("Expected area", "User-defined range that is allowed to change between builds (version, timestamp, CRC). "
                          "Nothing is assumed to be expected unless configured."),
        ("Changed block", "Contiguous run of differing bytes (optionally merged across small gaps)."),
        ("RELOCATED", "A function whose bytes differ only because call targets or pointers moved — a side effect, "
                      "not an edit."),
    ], widths=[3.5, 12.5])


def section_requirements():
    h("2. Feature overview and requirements traceability", 1)
    p("Table 1 maps every original requirement to its implementation. Features added during development are listed "
      "at the end.")
    table(["#", "Requirement", "Implementation", "Status"], [
        ("1", "File selection, drag-and-drop, file information",
         "Files tab: A/B cards with drop zones, SHA-256, format, address range, controller", "Done"),
        ("2", "Byte-by-byte comparison statistics", "core.binary_comparator + Summary tab / CLI", "Done"),
        ("3", "Hex comparison viewer with navigation and search", "gui.hex_viewer (virtualised)", "Done"),
        ("4", "Memory region configuration, reusable files", "core.region_manager + Configuration tab, JSON", "Done"),
        ("5", "Region-based comparison", "core.analysis + Regions tab / report", "Done"),
        ("6", "Changed block analysis, sorting", "core.block_detector + Changed Blocks tab", "Done"),
        ("7", "Binary / memory-map visualisation", "gui.memory_map + report SVG", "Done"),
        ("8", "Difference filters", "Hex viewer and block-table filters", "Done"),
        ("9", "Expected / ignored areas, EXPECTED vs UNEXPECTED", "core.analysis, verdict logic", "Done"),
        ("10", "Firmware metadata detection (heuristic)", "core.metadata_detector + Metadata tab", "Done"),
        ("11", "CRC-32 / CRC-16 / SHA-256", "core.checksum + Checksums tab / --checksum", "Done"),
        ("12", "ELF support: sections, symbols, build info", "formats.elf_parser + core.elf_compare + ELF tab",
         "Done"),
        ("13", "HTML report (10 sections) and CSV export", "reports.html_report / reports.csv_report", "Done"),
        ("14", "Command-line interface", "cli.cli (firmware-diff), exit codes", "Done"),
        ("15", "Python 3.11+, Tkinter, minimal dependencies, modular", "Standard library only", "Done"),
        ("16", "Engineering requirements (read-only, large files …)", "See section 13", "Done"),
        ("17", "Engineering-tool UX", "Tabbed GUI, header, tiles, tooltips", "Done"),
        ("+", "Function / module level changes", "core.symbols + core.feature_diff", "Added"),
        ("+", "Relocation-aware comparison (ARM Thumb-2, MIPS32)", "core.feature_diff", "Added"),
        ("+", "Controller identification", "core.mcu_detector", "Added"),
        ("+", "PIC32 KSEG ↔ physical address mapping", "formats.elf_parser, core.symbols", "Added"),
        ("+", "Gap-fill mode (sparse HEX vs full BIN)", "compare_images(fill=…)", "Added"),
        ("+", "Recent comparisons, remembered folders, tooltips", "gui.settings, gui.widgets", "Added"),
    ], widths=[0.8, 5.2, 7.5, 1.8], caption="Requirements traceability")


def section_architecture():
    h("3. Architecture", 1)
    p("The tool is a pure-Python 3.11 application with no mandatory third-party dependencies. It is split into "
      "layers so that the comparison engine can be used without the GUI — the CLI and the automated tests call the "
      "same API.")
    figure("00_architecture.png", "Layers and modules")
    h("3.1 Package structure", 2)
    table(["Package / module", "Responsibility"], [
        ("core/binary_reader.py", "Format detection (magic bytes, extension, content), SHA-256, loader dispatch, "
                                  "controller identification on load"),
        ("core/image.py", "FirmwareImage and Segment model; address ↔ file-offset mapping; reads, search, iteration"),
        ("core/intervals.py", "IntervalSet: sorted disjoint address ranges with O(log n) overlap queries"),
        ("core/binary_comparator.py", "Chunked hierarchical byte comparison over the union of address ranges"),
        ("core/block_detector.py", "Changed-block grouping (merge gap), per-block statistics, sorting"),
        ("core/region_manager.py", "Regions, expected areas, project configuration, validation, JSON I/O"),
        ("core/analysis.py", "Region results, expected/unexpected split, verdict"),
        ("core/symbols.py", "Symbol tables from ELF or GNU ld / XC32 map files, labels, aliases, LMA/VMA"),
        ("core/feature_diff.py", "Function / variable / module comparison, relocation-aware decoding"),
        ("core/mcu_detector.py", "Heuristic controller identification with evidence"),
        ("core/metadata_detector.py", "Heuristic version / date / time / git / product / board detection"),
        ("core/checksum.py", "CRC-32, CRC-16 variants, SHA-256 over files, images and ranges"),
        ("core/elf_compare.py", "ELF section and symbol comparison"),
        ("core/memory_map_model.py", "Data model for the map visualisation (GUI and report)"),
        ("core/session.py", "Orchestration: load → compare → analyse → metadata → ELF → features"),
        ("formats/", "bin_parser (mmap), hex_parser (+writer), elf_parser, elf_builder (tests/examples)"),
        ("reports/", "html_report, csv_report"),
        ("cli/cli.py", "firmware-diff command-line interface"),
        ("gui/", "main_window, file_panel, comparison_view, features_view, hex_viewer, hex_model, memory_map, "
                 "config_editor, details_view, widgets, theme, settings"),
    ], widths=[5, 11], caption="Modules")
    h("3.2 Processing pipeline", 2)
    figure("00_pipeline.png", "Comparison pipeline")
    p("The byte comparison runs once per file pair on a worker thread. Region, expected-area and symbol analysis are "
      "re-computed instantly when the configuration or symbol files change, without re-comparing bytes.")


def section_data_model():
    h("4. Data model", 1)
    h("4.1 Firmware image", 2)
    p("A FirmwareImage is a list of non-overlapping Segments. Each segment maps a range of MCU addresses to bytes "
      "and remembers its file offset when one exists:")
    bullets([("Raw BIN: ", "one segment at the configured base address; the file is memory-mapped read-only "
                           "(mmap ACCESS_READ), so large images are not copied into memory."),
             ("Intel HEX: ", "one segment per contiguous run of records; gaps are preserved, never filled."),
             ("ELF: ", "one segment per PT_LOAD program header at its load address (LMA). VMA mode is optional. "
                       "For PIC32 (MIPS32) KSEG0/KSEG1 addresses are converted to physical addresses so ELF, HEX and "
                       "programmer addresses agree.")])
    h("4.2 Address sets", 2)
    p("Every set of addresses — file coverage, differences, expected areas, regions — is an IntervalSet of sorted "
      "half-open ranges with a prefix-sum index. This keeps memory proportional to the number of changed runs, not "
      "changed bytes, and answers 'how many changed bytes lie in [s, e)' in O(log n). Intersection, subtraction and "
      "union are linear merges.")
    h("4.3 Configuration file", 2)
    p("Regions and expected areas are stored in one JSON project file (schema: firmware_binary_tool/config/"
      "schema.json). End addresses are inclusive; 'size' may replace 'end'; 'ignore' is an alias of 'expected'.")
    code('{\n  "name": "STM32F4 XC-200 motor controller",\n  "base_address": "0x08000000",\n  "merge_gap": 0,\n'
         '  "gap_fill": null,\n  "regions": [\n    { "name": "Bootloader",  "start": "0x08000000", "end": '
         '"0x08007FFF" },\n    { "name": "Application", "start": "0x08008000", "end": "0x0803FFFF" }\n  ],\n'
         '  "expected": [\n    { "name": "Version", "start": "0x08001000", "end": "0x0800101F" },\n'
         '    { "name": "CRC",     "start": "0x0807FFFC", "end": "0x0807FFFF" }\n  ]\n}')
    p("Validation reports errors (start after end, invalid numbers, duplicate names, out-of-range values) and "
      "warnings (overlapping regions).")


def section_engine():
    h("5. Comparison engine", 1)
    h("5.1 Algorithm", 2)
    bullets([
        "The comparison runs over the union of both images' address ranges. Addresses present in both are compared "
        "byte by byte; addresses present in only one image are reported as ONLY A / ONLY B differences, so a size "
        "change is never silently ignored.",
        "Data is processed in 64 KiB chunks. Identical chunks are skipped at memcmp speed; differing chunks are "
        "narrowed down hierarchically (4096 → 256 → 16 → 1 bytes) so only differing bytes are inspected "
        "individually.",
        "Differences are accumulated as runs and stored in an IntervalSet; adjacent runs across chunk and segment "
        "boundaries are merged.",
        "Progress reporting and cancellation are supported (worker thread in the GUI).",
    ])
    h("5.2 Gap-fill mode (optional)", 2)
    p("A sparse Intel HEX file omits erased flash pages, so it differs from a full BIN dump of the same build. With "
      "gap fill (e.g. 0xFF) bytes present in only one image are compared against the fill value instead. The mode is "
      "off by default and its state is shown in every summary and report.")
    h("5.3 Results", 2)
    table(["Statistic", "Definition"], [
        ("Total compared", "Bytes present in both images (plus one-sided bytes in gap-fill mode)"),
        ("Different bytes / Changed %", "All differing addresses / union of both images"),
        ("First / last difference", "Lowest / highest differing address, with file offsets of A and B"),
        ("Changed blocks / largest block", "Contiguous runs (merge gap optional)"),
        ("Only in A / only in B", "Bytes with data in one image only"),
        ("Expected / unexpected changes", "Differences inside / outside configured expected areas"),
        ("Verdict", "IDENTICAL, EXPECTED CHANGES ONLY or UNEXPECTED CHANGES"),
    ], widths=[5, 11], caption="Summary statistics")
    h("5.4 Performance (measured)", 2)
    table(["Case", "Time"], [
        ("Byte comparison, 64 MB image with 2,000 changed areas", "≈ 0.1 s"),
        ("Full session for the same images (hashing, metadata scan)", "≈ 2 s"),
        ("Every other byte differs in 4 MB (2 million blocks, pathological)", "≈ 9 s"),
        ("Real STM32F411 / PIC32MX575 production images", "< 0.1 s"),
    ], widths=[11, 5], caption="Performance")


def section_analysis():
    h("6. Region, block and expected-difference analysis", 1)
    bullets([
        ("Regions: ", "for every region the tool reports size, changed bytes, changed %, expected and unexpected "
                      "bytes, coverage of A and B, and a status: SAME, CHANGED, EXPECTED CHANGE or NOT PRESENT. "
                      "Changes outside all regions are reported in their own row."),
        ("Changed blocks: ", "start, end, size, changed bytes, expected/unexpected bytes, classification "
                             "(EXPECTED / UNEXPECTED / MIXED), regions, kind (modified / only in A / only in B), file "
                             "offsets and the containing function. Sorting by address, size, changed bytes or region; "
                             "filters by class, region and minimum size."),
        ("Expected areas: ", "a difference is EXPECTED only inside an area the user configured. Each area reports "
                             "CHANGED (EXPECTED), UNCHANGED or NOT PRESENT."),
        ("Memory map: ", "region tree, linear region bar, change strip and a clickable block grid over a compressed "
                         "address axis (gaps without data are omitted and marked)."),
    ])
    figure("07_regions.png", "Regions tab — per-region statistics with expected/unexpected split")
    figure("09_memory_map.png", "Memory map — region layout, change strip and block grid")


def section_formats():
    h("7. File format support", 1)
    table(["Format", "Support", "Validation / errors"], [
        ("Raw BIN", "Memory-mapped read-only; base address per file or from configuration",
         "Empty file → warning; never modified"),
        ("Intel HEX", "Records 00–05, extended segment / linear addressing, start addresses; HEX writer for examples",
         "Line-level errors for bad syntax, length, checksum, type; overlapping records rejected; missing EOF warned"),
        ("ELF", "ELF32/64, little/big endian; PT_LOAD segments at LMA (or VMA); sections, symbols, .comment, GNU "
                "build-id; PIC32 KSEG→physical", "Bounds-checked tables; truncated or invalid files give a clear "
                                                    "error"),
        ("GNU ld / XC32 map", "Input sections, symbols (incl. COMMON), modules, load addresses, linker labels; PIC32 "
                              "detection", "Rejects files without a memory-map section"),
    ], widths=[3, 7, 6], caption="Supported formats")
    p("Format detection: ELF by magic, Intel HEX by extension or text records, otherwise raw binary. The format can "
      "be forced per file.")


def section_features():
    h("8. Function- and module-level change analysis", 1)
    p("A binary does not know about 'features'. The closest reliable proxy is the symbol information of each build: "
      "functions, variables and the source module they come from.")
    h("8.1 Symbol sources", 2)
    bullets([("ELF: ", "symbol table, Thumb bit cleared, LMA computed from program headers, source file of local "
                       "symbols from STT_FILE entries, size-0 linker labels collected separately."),
             ("Linker map: ", "GNU ld and XC32 map files; gives the object file (module) of every symbol and the "
                              "load address of initialised data.")])
    h("8.2 Matching and statuses", 2)
    bullets([
        "Aliases (several names at one address) are grouped into one symbol.",
        "Symbols are matched by name and module; compiler-generated names that change between builds (C.55.31329, "
        "counter.1234) are paired by identical content or unique candidates.",
        "Each side's bytes are read at that side's own address and compared.",
    ])
    table(["Status", "Meaning", "Counted as edit"], [
        ("MODIFIED", "Content changed", "Yes"),
        ("ADDED / REMOVED", "Only in the new / old build", "Yes"),
        ("LIKELY RELOCATED", "Only address references differ, some to unnamed data — listed for review", "Review"),
        ("RELOCATED", "Only call targets / pointers changed because referenced code or data moved", "No"),
        ("MOVED", "Identical content at a new address", "No"),
        ("UNCHANGED / NOT IN IMAGE", "Identical / RAM-only (.bss)", "No"),
    ], widths=[4, 9, 3], caption="Function statuses")
    h("8.3 Relocation-aware comparison", 2)
    p("Removing or adding code shifts everything linked after it, which changes the bytes of many unedited "
      "functions. Every differing branch or pointer is therefore decoded in both builds and resolved to a "
      "build-independent key (symbol + offset, linker label, identical pointed-to content, or position relative to "
      "neighbouring symbols):")
    bullets([("ARM / Cortex-M: ", "Thumb-2 BL / BLX / B.W, ARM B / BL, literal-pool pointers."),
             ("MIPS32 / PIC32: ", "J / JAL, LUI + ADDIU / load / store address pairs with register tracking "
                                  "(including branch delay slots), $gp-relative small-data accesses.")])
    h("8.4 Validation on production firmware", 2)
    table(["Firmware", "Change", "Result"], [
        ("STM32F411 analysis board", "Watchdog commented out",
         "2 modified (peripheralInit, main), 6 removed (iwdgInit, HAL_IWDG_Init, iwdgRefresh, HAL_IWDG_Refresh, "
         "iwdgInstance, iwdgHandle); 762 side effects separated. Before relocation analysis: 350 'modified'."),
        ("PIC32MX575F512L system controller", "Release IR44 → IR45",
         "Modbus stack added, Chinese language data removed, 91 size-changed functions; unexplained bytes reduced "
         "from 420,619 to ~21–36 k after KSEG mapping."),
    ], widths=[4, 3.5, 8.5], caption="Real-firmware validation")
    figure("04_changed_functions.png", "Changed Functions — exactly the watchdog change in a 900-symbol firmware")


def section_controller():
    h("9. Controller identification", 1)
    p("The target controller is identified heuristically and every result lists its evidence with a confidence.")
    table(["Evidence", "Example", "Gives", "Confidence"], [
        ("ELF debug info / strings", "stm32f411xe.h, startup_stm32f411retx.s, p32mx575f512l.h", "Exact part",
         "High"),
        ("Linker map", "32MX575F512L (XC32)", "Exact part", "High"),
        ("Compiler options", "-mcpu=cortex-m4 -mfpu=fpv4-sp-d16", "Core, FPU", "High"),
        ("Memory layout", "0x08000000 flash; 0x1D000000 + 0x1FC00000", "Family", "Medium / low"),
        ("PIC32 configuration words", "0x1FC02FF0 / 0x1FC00BF0 / 0x1FC0FFxx", "MX3-7 / MX1-2 / MZ", "Medium"),
        ("Cortex-M vector table", "SP 0x20020000, 102 vectors", "RAM ≥ 128 KB, 86 IRQs", "Medium"),
        ("Image headers", "ESP 0xE9 + chip id, AVR JMP table", "ESP32 variant / AVR", "Medium"),
    ], widths=[3.8, 5.5, 3.5, 2.2], caption="Controller evidence")
    p("Part numbers are decoded (STM32F411RET → 512 KB flash, 64 pins, LQFP; PIC32MX575F512L → 512 KB, 100 pins). "
      "An application starting inside flash is flagged as likely behind a bootloader. A warning is shown when A and B "
      "target conflicting controllers; an exact part within a family-level result is treated as compatible.")
    figure("14_controller_pic32.png", "Files tab — PIC32 identification refined by the linker map")


def section_other_features():
    h("10. Metadata, checksums and ELF comparison", 1)
    bullets([
        ("Metadata (heuristic): ", "printable strings are matched against patterns for firmware version, build "
                                   "date / time (__DATE__ / ISO), git commit, product and board name, each with a "
                                   "confidence and address. The UI and reports state that detection is not "
                                   "guaranteed."),
        ("Checksums: ", "CRC-32 (ISO-HDLC), CRC-16/CCITT-FALSE, XMODEM, MODBUS, ARC and SHA-256 over the file, the "
                        "image data, a region or a custom range with gap fill. The tool never assumes the firmware's "
                        "own CRC algorithm."),
        ("ELF comparison: ", "sections (address, size, differing bytes, status, notes), build information (compiler, "
                             "build-id) and symbol changes (added / removed / moved / resized)."),
    ])
    figure("11_metadata.png", "Metadata tab with heuristic disclaimer", width_cm=15.5)


def section_outputs():
    h("11. Outputs: GUI, CLI and reports", 1)
    h("11.1 Graphical user interface", 2)
    table(["Tab", "Content"], [
        ("1 Files", "A/B cards (drop zone, format, base address, file details, controller), Compare / Swap, symbol "
                    "files, configuration, controller evidence"),
        ("2 Configuration", "Regions, expected areas, base address, merge gap, gap fill; load/save/import"),
        ("Summary", "Verdict banner, number tiles, details, 'What changed' function list"),
        ("Regions / Changed Blocks", "Per-region and per-block tables with filters, CSV export"),
        ("Changed Functions", "Modules, functions, real / likely relocated / side-effect filters"),
        ("Memory Map", "Region tree, region bar, change strip, clickable block grid"),
        ("Metadata / Checksums / ELF", "Heuristic metadata, checksum calculator, ELF sections and symbols"),
        ("Hex Comparison (window)", "Virtualised side-by-side or 1-byte table view; first/prev/next/last difference; "
                                    "go to address or file offset; hex search; filters; status bar with offsets, "
                                    "region, block and function"),
    ], widths=[4.5, 11.5], caption="GUI tabs")
    p("Usability: header with file names and verdict badge, colour coding (A blue, B purple), tooltips, helpful "
      "empty states, remembered folders and window size, File → Recent comparisons, Quick start and keyboard "
      "shortcuts, high-DPI awareness on Windows.")
    figure("03_summary_watchdog.png", "Summary tab")
    figure("05_hex_viewer.png", "Hex comparison viewer")
    h("11.2 Command-line interface", 2)
    table(["Option", "Purpose"], [
        ("--regions / --ignore", "Project / region file; extra expected areas"),
        ("--report / --csv / --json", "HTML report, changed-block CSV, JSON summary ('-' = stdout)"),
        ("--base / --base-a / --base-b", "Base address for raw .bin files"),
        ("--format-a / --format-b / --elf-vaddr", "Force format; use ELF VMA"),
        ("--fill / --merge-gap", "Gap-fill value; block merge gap"),
        ("--symbols-a / --symbols-b", "ELF or map file per build (function-level analysis)"),
        ("--functions / --show-relocated / --functions-csv", "Function listing, side effects, CSV"),
        ("--checksum ALG", "CRC-32, CRC-16 variants, SHA-256"),
        ("--controller-details / --no-metadata / -q", "Evidence output, skip metadata, verdict only"),
    ], widths=[6.5, 9.5], caption="Main CLI options")
    p("Exit codes: 0 = identical or only expected changes, 1 = unexpected changes, 2 = error — suitable as a CI "
      "gate.")
    figure("16_cli.png", "CLI output", width_cm=15)
    h("11.3 Reports", 2)
    p("The HTML report is self-contained (no external resources) and contains: firmware A and B information "
      "(including controller), SHA-256 hashes, overall summary with memory map, region comparison, changed blocks "
      "with function names, expected changes, unexpected changes, function and module changes (with side effects "
      "and likely-relocated sections), metadata differences, ELF comparison and the comparison timestamp. CSV exports "
      "cover changed blocks and function changes; JSON provides a machine-readable summary.")


def section_engineering():
    h("12. Engineering requirements compliance", 1)
    table(["Requirement", "How it is met"], [
        ("Never modify original files", "Files opened read-only; BIN mmap with ACCESS_READ; test verifies hash and "
                                        "modification time are unchanged"),
        ("Large images", "mmap, 64 KiB chunked hierarchical comparison, run-based storage, virtualised hex viewer, "
                         "capped tables with CSV for full lists"),
        ("File offset vs MCU address", "Separate columns everywhere; offsets prefixed '+', n/a for Intel HEX"),
        ("Never silently ignore differences", "One-sided bytes counted, outside-region changes listed, unattributed "
                                              "bytes reported, gap fill opt-in and always displayed"),
        ("Clear errors", "Typed exceptions with line numbers / offsets; GUI dialogs and status bar; CLI exit code 2"),
        ("Validate memory regions", "Range, number, duplicate-name checks; overlap warnings"),
        ("Different sizes, empty and malformed files", "ONLY A / ONLY B, empty-file warnings, parser validation"),
        ("Unit tests for the engine", "84 tests (section 13)"),
    ], widths=[5, 11], caption="Engineering requirements")


def section_testing():
    h("13. Verification", 1)
    table(["Test module", "Tests", "Coverage"], [
        ("test_intervals", "5", "Interval algebra checked against Python sets on random data"),
        ("test_comparator", "12", "Naive byte-by-byte reference on random data, chunk boundaries, sizes, empty, "
                                  "sparse, gap fill, progress / cancel"),
        ("test_blocks_regions", "15", "Merge gap, blocks across regions, expected vs unexpected, config validation"),
        ("test_formats", "17", "BIN, HEX (malformed, overlapping, extended addressing), ELF (truncated, VMA/LMA), "
                               "files never modified"),
        ("test_checksum_metadata", "7", "CRC catalogue check values, chunking, metadata heuristics"),
        ("test_features", "11", "Map parsing, function statuses, real Thumb-2 and MIPS code, aliases, labels, renamed "
                                "symbols, PIC32 KSEG"),
        ("test_mcu_detector", "8", "Part decoding, vector tables, PIC32 layout, mismatch rules"),
        ("test_reports_cli", "8", "HTML / CSV / JSON content, exit codes, hex row model"),
        ("test_gui_smoke", "1", "Drives the real GUI: load, compare, tabs, hex navigation, filters, search"),
    ], widths=[4.2, 1.6, 10.2], caption="Automated tests (84, all passing)")
    p("In addition the tool was validated manually on two production firmware pairs (section 8.4) and on the "
      "deterministic example builds in examples/.")


def section_build_limits():
    h("14. Build, deployment and baseline", 1)
    bullets([
        "Runtime: Python 3.11+ standard library; optional tkinterdnd2 for drag-and-drop.",
        "Run: python firmware_compare_gui.py (GUI), python firmware_diff.py (CLI), or pip install . for the "
        "firmware-diff / firmware-diff-gui commands.",
        "Standalone executables: build_windows.bat / build_linux.sh (PyInstaller).",
        "Frozen baseline: docs/FREEZE_v1.0.0.md lists the SHA-256 of every source, test and build file.",
        "Per-user settings: %APPDATA%\\FirmwareBinaryTool\\settings.json (Windows) or ~/.config/firmware_binary_tool "
        "(Linux).",
    ])
    h("15. Known limitations and future work", 1)
    bullets([
        "Instruction decoding covers ARM/Thumb-2 and MIPS32; MOVW/MOVT pairs, microMIPS/MIPS16e and other "
        "architectures (e.g. RISC-V) are not decoded — address-only changes there may appear as MODIFIED.",
        "Map parsing supports GNU ld and XC32; IAR and Keil map files are not supported (use their ELF output).",
        "Metadata and controller identification are heuristic and labelled as such.",
        "Intel HEX and ELF files are parsed into memory (raw BIN files are memory-mapped).",
        "Motorola S-record is not supported.",
        "Same-size MODIFIED functions can be side effects of header changes (e.g. struct layout) or compiler "
        "scheduling; the binary shows that code changed, not which source line.",
        "Candidate future work: S-record, IAR/Keil maps, RISC-V decoding, git integration, batch comparison.",
    ])


def main():
    setup_styles()
    title_page()
    section_intro()
    section_requirements()
    section_architecture()
    section_data_model()
    section_engine()
    section_analysis()
    section_formats()
    section_features()
    section_controller()
    section_other_features()
    section_outputs()
    section_engineering()
    section_testing()
    section_build_limits()
    doc.save(OUT)
    print("written", OUT)


if __name__ == "__main__":
    main()
