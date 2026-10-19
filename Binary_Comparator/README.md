# Firmware Binary Comparison Tool

A desktop and command-line utility for embedded firmware engineers that compares two
firmware images and shows **exactly what changed, where, and whether the change was expected**.

* Formats: raw **`.bin`**, **Intel HEX** (`.hex/.ihex/.ihx`), **ELF** (`.elf/.axf/.out`), and any mix of them.
* Comparison is done by **MCU memory address**, so a BIN dump can be compared with a HEX or ELF build.
* GUI: Tkinter, standard library only. Drag-and-drop works when `tkinterdnd2` is installed.
* **Which features changed:** with the ELF or GNU ld `.map` file of each build, the tool lists every
  modified, added, removed or moved function and variable, grouped by source module (e.g. `motor.o`).
* CLI: `firmware-diff old.bin new.bin --regions regions.json --report report.html`, with CI-friendly exit codes.
* The comparison engine (`firmware_binary_tool/core`) does not depend on the GUI. The CLI and the tests use it directly.

The tool opens firmware files **read-only** (raw BIN files are memory-mapped with `ACCESS_READ`) and never modifies them.

---

## Documentation (v1.0.0 frozen baseline)

| Document | Content |
|---|---|
| [docs/Firmware_Binary_Comparison_Tool_Design_v1.0.docx](docs/Firmware_Binary_Comparison_Tool_Design_v1.0.docx) | Design document: architecture, algorithms, all features, requirements traceability, verification, limitations |
| [docs/Firmware_Binary_Comparison_Tool_Demo_v1.0.pptx](docs/Firmware_Binary_Comparison_Tool_Demo_v1.0.pptx) | Demo presentation with screenshots and speaker notes |
| [docs/FREEZE_v1.0.0.md](docs/FREEZE_v1.0.0.md) | Frozen baseline record with SHA-256 of every source, test and build file |
| [docs/sample_comparison_report.html](docs/sample_comparison_report.html) | Example HTML report |

`docs/tools/` contains the scripts that regenerate the screenshots, diagrams, documents and freeze manifest.

## Quick start

```bash
# Python 3.11+ required. No mandatory third-party packages.
pip install -r requirements.txt          # optional: drag-and-drop support

python examples/generate_examples.py     # creates examples/firmware/*.bin|hex|elf

# GUI (files and configuration can be preloaded)
python firmware_compare_gui.py examples/firmware/FW_V1.2.3.bin examples/firmware/FW_V1.2.4.bin \
       --config examples/project_stm32.json

# CLI
python firmware_diff.py examples/firmware/FW_V1.2.3.bin examples/firmware/FW_V1.2.4.bin \
       --regions examples/regions_stm32.json --ignore examples/expected.json \
       --report comparison_report.html --csv changes.csv
```

You can also run `python -m firmware_binary_tool` (GUI) or `python -m firmware_binary_tool.cli` (CLI).
After `pip install .` the `firmware-diff` and `firmware-diff-gui` commands are available.

Linux needs Tk: `sudo apt install python3-tk` (Debian/Ubuntu) or `sudo dnf install python3-tkinter` (Fedora).
The CLI works without Tk.

---

## Key concepts

| Term | Meaning |
|---|---|
| **Address** | MCU memory address the byte is programmed to (e.g. `0x08012A40`). Every table, report and region uses addresses. |
| **File offset** | Byte position inside the file on disk. It is always shown with a `+` prefix (`+0x12A40`) so it cannot be confused with an address. Intel HEX has no byte-level file offset, so it shows `n/a`. |
| **Base address** | For raw `.bin` files only: the MCU address of file offset 0. Set it per file in the GUI, with `--base/--base-a/--base-b`, or as `base_address` in the config. HEX and ELF carry their own addresses. |
| **ELF addresses** | ELF data is placed at its **load address (LMA, `p_paddr`)**, which is where it lives in flash. Use `--elf-vaddr` or *Compare → ELF: use virtual addresses* to switch to the VMA. |
| **Only in A / only in B** | Bytes present in one image but not the other, for example after a size change or in sparse HEX gaps. These bytes **always count as differences** by default. |
| **Gap fill** (opt-in) | `--fill 0xFF` (or the setting on the Configuration tab) compares one-sided bytes with erased flash instead. This is useful when comparing a sparse HEX file with a full BIN dump. It is off by default and is shown in every summary and report. |
| **Changed block** | A contiguous run of differing bytes. `merge_gap` / `--merge-gap N` joins runs separated by at most N unchanged bytes. Each block then reports both its size and the number of bytes that actually changed. |
| **Expected change** | A difference inside an *expected / ignored area* that **you configured** (version, timestamp, CRC ...). Nothing is assumed: every difference outside these areas is **UNEXPECTED**. |

Overall verdicts are `IDENTICAL`, `EXPECTED CHANGES ONLY` and `UNEXPECTED CHANGES`.

---

## Which features changed? (function and module level)

A firmware binary contains no notion of "features". The closest reliable mapping is the **symbol information** of
each build: functions, variables, and the source module (object file) each one comes from.

| Symbol source | How to supply it | Module names |
|---|---|---|
| ELF of the build | automatic when you compare `.elf` files; for BIN/HEX, load it as the symbol file | local symbols only |
| GNU ld linker map (`-Wl,-Map=app.map`) | load it as the symbol file | every symbol |

GUI: **Changed Functions** tab → *Load ELF / .map…* for A and B (or `--symbols-a/--symbols-b` when launching).
CLI: `--symbols-a OLD.map --symbols-b NEW.map` (optionally `--functions-csv functions.csv`).

```
Changed functions / variables  (symbols: FW_V1.2.3.map (map) -> FW_V1.2.4.map (map))
12 modified, 2 added, 1 removed, 0 moved only

By module:
  diag.o     3 modified                              595 bytes
  motor.o    1 modified, 1 added                  12,312 bytes
  fault.o    1 added, 1 removed                    1,536 bytes
  ...
  version.o  3 modified                               18 bytes  [expected only]

  Status    Kind   Symbol               Module          Changed  Notes
  MODIFIED  FUNC   motor_control_loop   motor.o          12,312  size 12,032 -> 12,352 (+320 bytes)
  MODIFIED  FUNC   pid_update           pid.o               288  moved 0x8021000 -> 0x8021040
  ADDED     FUNC   fault_logger_v2      fault.o           1,024
  REMOVED   FUNC   fault_logger         fault.o             512
```

How it works:

* Symbols of A and B are matched by name (and module). Each side's bytes are read **at that side's own address**,
  so a function that only shifted because earlier code grew is reported as **MOVED**, not modified.
* Removing or adding code shifts everything linked after it. Many unedited functions then differ only because
  their `BL`/`B.W` call offsets, literal-pool pointers or pointer tables now point to the new addresses.
  The tool decodes every differing Thumb-2/ARM branch and 32-bit pointer in both builds. If both resolve to the
  same symbol + offset (or to identical content, e.g. the same string literal), the difference is an
  address-reference change. A function with only such differences is **RELOCATED**: a side effect, not an edit.
  Example: commenting out one watchdog call reports the edited caller as MODIFIED and the watchdog functions as
  REMOVED. Hundreds of other functions are counted as relocated/moved and kept out of the change list
  (`--show-relocated` lists them).
* Statuses: real changes are MODIFIED, ADDED and REMOVED. Side effects are RELOCATED and MOVED. The rest are
  UNCHANGED and NOT IN IMAGE (RAM-only `.bss` variables).
* The byte-level summary still counts every shifted byte. The function view is where real edits are
  separated from layout shifts.
* **Decoded references:**
  * **ARM / Cortex-M:** Thumb-2 `BL`/`BLX`/`B.W`, ARM `B`/`BL`, and literal-pool pointers.
  * **PIC32 / MIPS32:** `J`/`JAL` calls, `LUI`+`ADDIU`/load/store address pairs (base registers are tracked through
    the function, including branch delay slots), and `$gp`-relative small-data accesses.
* **Robust matching:**
  * Pointers to size-0 linker labels (`_end`, `__init_array_start`, `_gp`) are recognised.
  * Alias names at one address (e.g. XC32's several `_vfprintf_*` names) are treated as one symbol.
  * Compiler-generated names that change between builds (`C.55.31329` → `C.55.31491`, `counter.1234`) are paired.
  * Static functions without a symbol are identified by their position relative to the neighbouring symbols.
* **LIKELY RELOCATED:** all differences are decoded address references, but some point to unnamed data or code
  (e.g. `static` variables without a symbol), so equivalence cannot be proven. These are listed separately for
  review and are never mixed into the confirmed changes.
* **PIC32 addresses:** XC32 ELF files and map files use KSEG0/KSEG1 addresses (`0x9D000000`, `0xBFC00000`), while
  HEX files and programmers use physical addresses (`0x1D000000`, `0x1FC00000`). The tool converts automatically,
  so a HEX comparison with ELF or `.map` symbols lines up.
* **Not decoded:** `MOVW/MOVT` pairs, microMIPS / MIPS16e, and other architectures (e.g. RISC-V). Address-only
  changes in such code may still show as MODIFIED.
* **What MODIFIED can include:** a same-size MODIFIED function can still be a side effect of a change elsewhere.
  Examples are a struct field offset changed by a header edit, or code the compiler scheduled differently. The
  binary shows *that* the function's code changed, not *which source line* caused it.
* A modified symbol whose changes all fall inside configured expected areas is marked *expected*.
  The same applies to a whole module.
* Initialised RAM data (`.data`) is found at its flash load address (LMA). ARM Thumb function addresses are corrected.
* With symbols for only one build, the same memory layout is assumed for both. This is stated in the output.
* Changed bytes outside every known symbol (padding, literal pools) are counted separately, so nothing is hidden.
* The changed-block table, block CSV and hex viewer status bar also show the function each change falls in
  (e.g. `motor_control_loop+0xC0 (motor.o)`).
* Supported map format: GNU ld (GCC / arm-none-eabi). IAR and Keil map files are not supported yet. Use the ELF
  (`.out` / `.axf`) of those toolchains instead.

---

## Controller identification (heuristic)

A firmware image does not store its part number. The tool collects clues and lists every one with its confidence:

| Evidence | Example | Gives |
|---|---|---|
| ELF debug info / strings (high) | `stm32f411xe.h`, `startup_stm32f411retx.s`, `p32mx575f512l.h` | exact part or device line |
| Linker `.map` (high) | `32MX575F512L` (XC32) | exact part |
| Compiler options in ELF (high) | `-mcpu=cortex-m4 -mfpu=fpv4-sp-d16` | CPU core and FPU |
| Memory layout (medium/low) | flash at `0x08000000` (STM32-style); `0x1D000000` + boot flash `0x1FC00000` (PIC32) | family |
| PIC32 configuration words | DEVCFG at `0x1FC02FF0` / `0x1FC00BF0` / `0x1FC0FFxx` | PIC32MX3-7 / MX1-2 / MZ |
| Cortex-M vector table | initial SP `0x20020000`, 102 vectors | Cortex-M, RAM ≥ 128 KB, 86 IRQs |
| Image headers | ESP image magic `0xE9` + chip id; AVR `JMP` vector table | ESP32 variant / AVR |

**Part numbers are decoded.** `STM32F411RET` decodes to 512 KB flash, 64 pins and LQFP. `PIC32MX575F512L`
decodes to 512 KB flash and 100 pins.

**Typical results:**
* A plain BIN/HEX usually gives a family (e.g. *PIC32MX3xx-7xx*, *STM32 or compatible*).
* Adding the build's ELF or `.map` as a symbol file refines it to the exact part.
* An image starting inside flash (e.g. `0x08040000`) is flagged as a likely application behind a bootloader.

**Where it appears:** the *Controller* row of each file panel and the evidence table on the **Files** tab, the CLI
(`--controller-details` for the evidence), the JSON output and the HTML report.

**Mismatch warning:** if A and B identify as conflicting controllers, the tool warns. An exact part inside a
family-level result is treated as compatible.

## GUI overview

```
 A: AB FW/firmware.hex  →  B: AB FW - Watchdog Commented/firmware.hex      UNEXPECTED CHANGES   (always visible)
┌ 1. Files │ 2. Configuration │ Summary │ Region Comparison │ Changed Blocks │ Changed Functions │
│ Memory Map │ Metadata │ Checksums │ ELF ────────────────────────────────────────────────────────
│ (setup tabs)          (result tabs)
└ [Compare (F5)] [Open Hex Comparison] [HTML Report…] [CSV Export…]  status   progress [Cancel]
```

The window is split into setup tabs and result tabs. A header above the tabs always shows which files are
compared and the verdict. When both files have the same name, it adds their folder.

**Comfort features:**
* **Drop areas:** click anywhere on a drop area to browse; once loaded it shows the file name and folder.
* **Colour coding:** A is blue and B is purple throughout.
* **Header verdict:** a coloured badge (✔ identical / ● expected only / ✖ unexpected) is always visible.
* **Tooltips** explain inputs such as the base address and fill mode.
* **Empty tabs** say what to do next (e.g. *Configure regions…*).
* **Remembered between sessions:** last folder, window size, and **File → Recent comparisons** (which reloads
  files, symbol files and configuration). Settings live in `%APPDATA%\FirmwareBinaryTool\settings.json`
  (Windows) or `~/.config/firmware_binary_tool/` (Linux). Deleting the file just restores defaults.
* **Help menu:** *Quick start* and *Keyboard shortcuts*. Alt+1…7 switches tabs.
* **High-DPI:** text is crisp on high-DPI Windows displays.

* **1. Files**: Firmware A and B (drag-and-drop or Browse, format, `.bin` base address; file name, size, SHA-256,
  format, address range), the optional symbol files (ELF / `.map`), the active configuration, and **Compare**.
  The comparison runs automatically once both files are loaded, and the window then switches to **Summary**.
* **2. Configuration**: memory regions, expected-difference areas and comparison settings.
* **Summary**: verdict and key numbers, buttons to the detail tabs, and **What changed**. That is the list of
  functions and variables that really changed (modified / added / removed, plus *likely relocated* for review).
  Double-click a row to open it in the hex viewer.

* **Region Comparison** shows Region | Start | End | Size | Changed Bytes | Changed % | Expected | Unexpected | Coverage | Status.
  Changes outside every configured region get their own row. Double-click a region to inspect it.
* **Changed Blocks** lets you sort by address, size, changed bytes or region, and filter by class (unexpected/expected), by region and by size (> X bytes). It shows up to 20,000 rows; CSV export always contains every block.
* **Changed Functions** shows which functions, variables and modules changed (see above). Select a module to filter;
  double-click a function to open it in the hex viewer.
* **Memory Map** has a region tree (`├── Application [ CHANGED ]`), a linear region bar, a change strip and a clickable block grid. Gaps without data are omitted and marked with dashed lines. Click any cell to jump to it in the hex viewer.
* **Configuration**: add, edit, remove and reorder memory regions and expected areas. The end address can be entered as `+size`. You can also set the default BIN base address, merge gap and gap fill, and load or save project JSON files. Regions are validated: you get errors for bad ranges or duplicate names and warnings for overlaps.
* **Metadata** shows heuristic detection of firmware version, build date/time, git commit, product name and board name, each with a confidence level. **Detection is not guaranteed.**
* **Checksums**: CRC-32, CRC-16 (CCITT-FALSE / XMODEM / MODBUS / ARC) or SHA-256. You can checksum the whole file, the image data, a region, or a custom range with a gap fill byte. These values are informational and do **not** assume the firmware's own CRC algorithm.
* **ELF**: header and build information (compiler from `.comment`, GNU build-id), a per-section comparison (address, size, differing bytes) and symbol changes (added, removed, moved, resized).

### Hex Comparison viewer

A virtualised viewer that renders only the visible rows, so multi-megabyte images scroll instantly.

* Layouts: **1 byte/row table** (`Address | Offset A | Offset B | A | B | Status | Region`), or 8, 16 or 32 bytes per row side by side with ASCII.
* Highlighting: red = unexpected difference, amber = expected difference, blue/purple = only in A/B.
* Navigation: First / Previous / Next / Last difference (`n` / `p`), **Go to** an address or a file offset of A or B (`+0x...`), and **hex search** in A, B or either (`F3` / `Shift+F3`).
* Filters: show all, only differences, only unchanged, blocks larger than X bytes, and a single memory region.
* The status bar shows address, both bytes, both file offsets, status, region and block number.

---

## Command-line interface

```
firmware-diff OLD NEW [--regions JSON] [--ignore JSON] [--report HTML] [--csv CSV] [--json JSON|-]
              [--base ADDR] [--base-a ADDR] [--base-b ADDR] [--format-a/--format-b auto|bin|hex|elf]
              [--elf-vaddr] [--fill BYTE] [--merge-gap N] [--blocks N] [--sort address|size|region]
              [--checksum ALG ...] [--symbols-a FILE] [--symbols-b FILE] [--functions N]
              [--functions-csv CSV] [--no-metadata] [-q]
```

Exit status: **0** = identical or only expected changes, **1** = unexpected changes, **2** = error
(missing or malformed file, invalid configuration). This makes it easy to use as a CI gate.

Example output:

```
Firmware Binary Comparison
==========================

Old: FW_V1.2.3.bin  [BIN]
New: FW_V1.2.4.bin  [BIN]

Size:
Old: 786432
New: 786432
...
Total compared: 786,432 bytes
Changed bytes:  14,198
Changed:        1.81%
Changed blocks: 65
...
Bootloader:  EXPECTED CHANGE      32 KB          18 bytes    0.05%
Application: CHANGED             224 KB      13,544 bytes    5.90%
Calibration: EXPECTED CHANGE     256 KB           4 bytes    0.00%
Diagnostics: CHANGED             256 KB         632 bytes    0.24%
...
Result: UNEXPECTED CHANGES

Report generated:
comparison_report.html
```

---

## Configuration files

Regions and expected areas are stored in one JSON file. The format is documented in
[firmware_binary_tool/config/schema.json](firmware_binary_tool/config/schema.json).
End addresses are **inclusive**. You can give `size` instead of `end`, and `ignore` is accepted as an alias of `expected`.

```json
{
  "name": "STM32F4 XC-200 motor controller",
  "base_address": "0x08000000",
  "merge_gap": 0,
  "regions": [
    { "name": "Bootloader",  "start": "0x08000000", "end": "0x08007FFF" },
    { "name": "Application", "start": "0x08008000", "end": "0x0803FFFF" },
    { "name": "Calibration", "start": "0x08040000", "end": "0x0807FFFF" },
    { "name": "Diagnostics", "start": "0x08080000", "end": "0x080BFFFF" }
  ],
  "expected": [
    { "name": "Version",         "start": "0x08001000", "end": "0x0800101F" },
    { "name": "Build timestamp", "start": "0x08001020", "end": "0x0800103F" },
    { "name": "CRC",             "start": "0x0807FFFC", "end": "0x0807FFFF" }
  ]
}
```

Example files: [examples/regions_stm32.json](examples/regions_stm32.json),
[examples/expected.json](examples/expected.json) and [examples/project_stm32.json](examples/project_stm32.json) (both combined).
With the CLI, `--regions` loads a project file and `--ignore` adds further expected areas.

---

## Example test binaries

`python examples/generate_examples.py` deterministically creates the following in `examples/firmware/`:

| File | Purpose |
|---|---|
| `FW_V1.2.3.bin` / `FW_V1.2.4.bin` | 768 KB images at 0x08000000: version/timestamp/git strings, a 12 KB rewritten block, scattered patches, 1 KB of new code, diagnostics changes and a recomputed CRC |
| `FW_V1.2.3.hex` / `FW_V1.2.4.hex` | The same content as sparse Intel HEX (erased 4 KB pages omitted) |
| `FW_V1.2.3.elf` / `FW_V1.2.4.elf` | ELF32 ARM files with `.isr_vector .boot .text .rodata .data(LMA≠VMA) .bss .calibration .diagnostics` and symbols |
| `FW_V1.2.3.map` / `FW_V1.2.4.map` | GNU ld map files of the two builds (functions and source modules) |
| `FW_V1.2.4_truncated.bin` | A 512 KB image to demonstrate a size mismatch |
| `empty.bin`, `malformed.hex` | Error handling (empty file, HEX checksum error) |

Things to try:

```bash
cd examples/firmware
python ../../firmware_diff.py FW_V1.2.3.hex FW_V1.2.3.bin --base 0x08000000             # differences: HEX gaps vs 0xFF
python ../../firmware_diff.py FW_V1.2.3.hex FW_V1.2.3.bin --base 0x08000000 --fill 0xFF # IDENTICAL
python ../../firmware_diff.py FW_V1.2.3.elf FW_V1.2.4.elf --regions ../project_stm32.json   # + ELF sections
python ../../firmware_diff.py FW_V1.2.3.bin FW_V1.2.4.bin --merge-gap 16 --sort size
python ../../firmware_diff.py FW_V1.2.3.bin FW_V1.2.4.bin --regions ../project_stm32.json        --symbols-a FW_V1.2.3.map --symbols-b FW_V1.2.4.map                              # which functions changed
```

Because the example "code" is random data, a rewritten 12 KB area happens to contain bytes that still match.
This splits it into several blocks. Use `--merge-gap` to group them.

---

## Tests

```bash
python -m unittest discover -s tests -t .      # or: pytest
```

There are 84 tests covering:

* the comparison engine, checked against a naive byte-by-byte reference on random data, plus chunk-boundary runs, different sizes, empty images, sparse segments, gap fill, progress and cancel
* interval arithmetic
* blocks, merge gap, regions, and expected vs unexpected classification
* configuration validation and round trips
* BIN, HEX and ELF parsing, including malformed and truncated input, and a check that files are never modified
* CRC known-answer values
* metadata heuristics
* HTML and CSV reports
* CLI exit codes
* controller identification (part-number decoding, vector tables, PIC32 layout, mismatch rules)
* GNU ld map parsing and function/module-level change analysis (moved vs modified, added/removed, expected)
* the hex viewer row model
* a GUI smoke test that drives the real window. It is skipped without a display or when `FWCMP_SKIP_GUI_TESTS=1` is set.

---

## Building standalone executables

**Windows** (cmd or PowerShell, Python 3.11+ on PATH):

```bat
build_windows.bat
```

**Linux**:

```bash
sudo apt install python3-tk python3-venv     # once
./build_linux.sh
```

Both scripts install PyInstaller, run the unit tests and produce
`dist/FirmwareCompare/` (GUI) and `dist/firmware-diff/` (CLI).
macOS is not packaged yet. The code uses only portable Tk and standard-library APIs, so the same PyInstaller
commands are expected to work there.

---

## Project structure

```
firmware_binary_tool/
├── main.py / __main__.py      GUI entry point
├── errors.py                  exception types
├── core/                      GUI-independent engine
│   ├── binary_reader.py       format detection, hashing, loading
│   ├── image.py               FirmwareImage / Segment (address ↔ file offset)
│   ├── intervals.py           sorted interval sets (differences stored as runs)
│   ├── binary_comparator.py   chunked hierarchical byte comparison
│   ├── block_detector.py      changed-block grouping and sorting
│   ├── region_manager.py      regions, expected areas, project config + validation
│   ├── analysis.py            per-region / expected-area analysis, verdict
│   ├── checksum.py            CRC-32, CRC-16 variants, SHA-256
│   ├── metadata_detector.py   heuristic version/date/git/product/board detection
│   ├── elf_compare.py         ELF section and symbol comparison
│   ├── symbols.py             symbol tables from ELF or GNU ld .map files
│   ├── feature_diff.py        changed functions / variables / modules
│   ├── memory_map_model.py    data for the map visualisation (shared by GUI and report)
│   ├── session.py             load + compare + analyse (used by CLI and GUI)
│   └── formatting.py
├── formats/                   bin_parser, hex_parser (+writer), elf_parser, elf_builder (examples/tests)
├── reports/                   html_report.py, csv_report.py
├── cli/                       cli.py
├── gui/                       main_window, file_panel, comparison_view, hex_viewer, hex_model,
│                              memory_map, config_editor, details_view, features_view,
│                              widgets, theme
└── config/schema.json
tests/                         unittest suite
examples/                      configs + example generator
```

## Performance notes

Identical areas are skipped with `memcmp`-speed chunk comparisons. Differing chunks are narrowed down
hierarchically (4096 → 256 → 16 → 1 bytes), and differences are stored as runs rather than individual bytes.
On a typical desktop:

| Case | Time |
|---|---|
| Byte comparison of 64 MB with 2,000 changed areas | ~0.1 s |
| Full session for the same images (hashing and metadata scan included) | ~2 s |
| Pathological case: every other byte differs in 4 MB (2 million blocks) | ~9 s |

## Limitations

* Metadata detection is heuristic: it scans printable ASCII only and may miss values or report false positives.
* Checksums are informational. To verify an embedded CRC, select the algorithm and range your firmware actually uses.
* Intel HEX and ELF files are parsed into memory; only raw BIN files are memory-mapped.
* Motorola S-record files are not supported yet.
