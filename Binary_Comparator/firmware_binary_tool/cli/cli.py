"""Command-line interface.

    firmware-diff old.bin new.bin [--regions regions.json] [--ignore expected.json]
                  [--report report.html] [--csv changes.csv] [--base 0x08000000]

Exit codes:
    0  no unexpected differences (identical, or only configured expected changes)
    1  unexpected differences found
    2  error (bad arguments, unreadable/malformed file, invalid configuration)
"""

from __future__ import annotations

import argparse
import json
import sys

from .. import APP_NAME, __version__
from ..core import checksum as checksum_mod
from ..core.analysis import V_UNEXPECTED
from ..core.block_detector import sort_blocks
from ..core.formatting import addr_width, fmt_addr, fmt_int, fmt_offset, fmt_size, parse_int
from ..core.metadata_detector import DISCLAIMER
from ..core.region_manager import ProjectConfig
from ..core.session import load_and_compare
from ..errors import FirmwareToolError

EXIT_OK, EXIT_DIFF, EXIT_ERROR = 0, 1, 2


def _int_arg(text: str) -> int:
    try:
        return parse_int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="firmware-diff",
        description=f"{APP_NAME} - compare two firmware images (.bin, Intel .hex, .elf).",
        epilog="Exit status: 0 = no unexpected differences, 1 = unexpected differences, 2 = error.",
    )
    p.add_argument("old", help="Firmware A (old) file")
    p.add_argument("new", help="Firmware B (new) file")
    p.add_argument("--regions", metavar="JSON", help="Region / project configuration file")
    p.add_argument("--ignore", "--expected", dest="ignore", metavar="JSON",
                   help="Expected-difference areas (e.g. version, timestamp, CRC)")
    p.add_argument("--report", metavar="HTML", help="Write an HTML report")
    p.add_argument("--csv", metavar="CSV", help="Write changed blocks as CSV")
    p.add_argument("--json", metavar="JSON", help="Write a machine-readable summary as JSON ('-' for stdout)")
    p.add_argument("--base", type=_int_arg, metavar="ADDR",
                   help="MCU address of file offset 0 for raw .bin files (both A and B)")
    p.add_argument("--base-a", type=_int_arg, metavar="ADDR", help="Base address for Firmware A (.bin)")
    p.add_argument("--base-b", type=_int_arg, metavar="ADDR", help="Base address for Firmware B (.bin)")
    p.add_argument("--format-a", default="auto", choices=["auto", "bin", "hex", "elf"])
    p.add_argument("--format-b", default="auto", choices=["auto", "bin", "hex", "elf"])
    p.add_argument("--elf-vaddr", action="store_true",
                   help="Use ELF virtual addresses (VMA) instead of load addresses (LMA)")
    p.add_argument("--merge-gap", type=_int_arg, metavar="N",
                   help="Merge changed runs separated by <= N unchanged bytes into one block")
    p.add_argument("--fill", type=_int_arg, metavar="BYTE",
                   help="Compare bytes present in only one image against this value (e.g. 0xFF for erased "
                        "flash) instead of reporting them all as differences")
    p.add_argument("--blocks", type=int, default=20, metavar="N",
                   help="Number of changed blocks to list on the console (default 20, 0 = none, -1 = all)")
    p.add_argument("--sort", choices=["address", "size", "region"], default="address", help="Block listing order")
    p.add_argument("--checksum", action="append", metavar="ALG",
                   choices=checksum_mod.algorithm_names(),
                   help=f"Also compute a checksum of each image's data ({', '.join(checksum_mod.algorithm_names())}); "
                        "may be repeated")
    p.add_argument("--symbols-a", metavar="FILE",
                   help="ELF or GNU ld .map file of the old build (shows which functions/modules changed)")
    p.add_argument("--symbols-b", metavar="FILE", help="ELF or GNU ld .map file of the new build")
    p.add_argument("--functions", type=int, default=25, metavar="N",
                   help="Number of changed functions/variables to list (default 25, 0 = none, -1 = all)")
    p.add_argument("--show-relocated", action="store_true",
                   help="Also list functions that only moved / had call targets or pointers updated")
    p.add_argument("--functions-csv", metavar="CSV", help="Write the function/variable comparison as CSV")
    p.add_argument("--controller-details", dest="verbose_controller", action="store_true",
                   help="Show the evidence used to identify the controller")
    p.add_argument("--no-metadata", action="store_true", help="Skip heuristic metadata detection output")
    p.add_argument("-q", "--quiet", action="store_true", help="Only print the verdict")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _load_config(args) -> ProjectConfig:
    cfg = ProjectConfig.load(args.regions) if args.regions else ProjectConfig()
    if args.ignore:
        cfg = cfg.merged_with_expected(ProjectConfig.load(args.ignore))
    if args.merge_gap is not None:
        cfg.merge_gap = args.merge_gap
    if args.base is not None:
        cfg.base_address = args.base
    if args.fill is not None:
        cfg.gap_fill = args.fill
    return cfg


def run(argv: list[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)

    def say(text: str = "") -> None:
        print(text, file=out)

    try:
        cfg = _load_config(args)
        warnings = cfg.check()
        session = load_and_compare(args.old, args.new, cfg, fmt_a=args.format_a, fmt_b=args.format_b,
                                   base_a=args.base_a, base_b=args.base_b, elf_use_vaddr=args.elf_vaddr,
                                   symbols_a=args.symbols_a, symbols_b=args.symbols_b)
    except FirmwareToolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        an, diff = session.analysis, session.diff
        a, b = session.image_a, session.image_b
        w = addr_width(diff.union.last)
        if args.quiet:
            say(an.verdict)
        else:
            _print_console(session, args, say, warnings, w)
        if args.report:
            from ..reports.html_report import write_html_report
            write_html_report(session, args.report)
            if not args.quiet:
                say(f"\nReport generated:\n{args.report}")
        if args.csv:
            from ..reports.csv_report import write_blocks_csv
            n = write_blocks_csv(session, args.csv)
            if not args.quiet:
                say(f"\nCSV generated ({n} blocks):\n{args.csv}")
        if args.functions_csv:
            from ..reports.csv_report import write_symbols_csv
            if session.features is None:
                print("WARNING: --functions-csv ignored: no symbol information (load an ELF or use "
                      "--symbols-a/--symbols-b)", file=sys.stderr)
            else:
                n = write_symbols_csv(session, args.functions_csv)
                if not args.quiet:
                    say(f"\nFunction CSV generated ({n} symbols):\n{args.functions_csv}")
        if args.json:
            data = json.dumps(summary_dict(session), indent=2)
            if args.json == "-":
                say(data)
            else:
                with open(args.json, "w", encoding="utf-8") as f:
                    f.write(data + "\n")
        return EXIT_DIFF if an.verdict == V_UNEXPECTED else EXIT_OK
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        session.close()


def _print_console(session, args, say, warnings, w) -> None:
    an, diff, cfg = session.analysis, session.diff, session.config
    a, b = session.image_a, session.image_b
    say("Firmware Binary Comparison")
    say("==========================")
    say()
    say(f"Old: {a.name}  [{a.format.upper()}]")
    say(f"New: {b.name}  [{b.format.upper()}]")
    say()
    say("Size:")
    say(f"Old: {a.file_size}")
    say(f"New: {b.file_size}")
    say()
    say("SHA-256:")
    say(f"Old: {a.sha256}")
    say(f"New: {b.sha256}")
    say()
    say("Controller (heuristic):")
    for img, tag in ((a, "Old"), (b, "New")):
        say(f"{tag}: {img.mcu.short() if img.mcu else 'unknown'}")
        if img.mcu and args.verbose_controller:
            for key, val in img.mcu.details.items():
                say(f"       {key}: {val}")
            for ev in img.mcu.evidence:
                say(f"       [{ev.confidence}] {ev.source}: {ev.finding}")
    if session.controller_mismatch:
        say("WARNING: Firmware A and B appear to target DIFFERENT controllers!")
    say()
    for img, tag in ((a, "Old"), (b, "New")):
        rng = img.address_range
        say(f"Address range {tag}: " + (f"{fmt_addr(rng[0], w)} - {fmt_addr(rng[1], w)}" if rng else "(no data)"))
        for warn in img.warnings:
            say(f"  WARNING ({tag}): {warn}")
    for warn in warnings:
        say(f"CONFIG WARNING: {warn}")
    say()
    if diff.fill is not None:
        say(f"Gap fill:       0x{diff.fill:02X} (bytes present in only one image are compared against this value)")
    say(f"Total compared: {diff.total_compared:,} bytes")
    if diff.only_a_bytes or diff.only_b_bytes:
        say(f"Only in old:    {diff.only_a_bytes:,} bytes")
        say(f"Only in new:    {diff.only_b_bytes:,} bytes")
    say(f"Changed bytes:  {diff.diff_bytes:,}")
    say(f"Changed:        {diff.changed_percent:.2f}%")
    say(f"Changed blocks: {len(an.blocks):,}")
    lb = an.largest_block
    if lb:
        say(f"Largest block:  {lb.size:,} bytes at {fmt_addr(lb.start, w)}")
        say(f"First diff:     {fmt_addr(diff.first_diff, w)} (file offset old {fmt_offset(a.file_offset_of(diff.first_diff))}, "
            f"new {fmt_offset(b.file_offset_of(diff.first_diff))})")
        say(f"Last diff:      {fmt_addr(diff.last_diff, w)}")
    if cfg.expected:
        say(f"Expected changes:   {an.expected_bytes:,} bytes")
    say(f"Unexpected changes: {an.unexpected_bytes:,} bytes")

    if an.region_results:
        say()
        name_w = max(len(r.region.name) for r in an.region_results)
        for r in an.region_results:
            say(f"{r.region.name + ':':<{name_w + 1}} {r.status:<16} {fmt_size(r.size):>9}  "
                f"{r.changed_bytes:>10,} bytes  {r.changed_percent:6.2f}%")
        if an.outside_changed:
            say(f"(outside regions): {an.outside_changed:,} changed bytes")

    if cfg.expected:
        say()
        say("Expected areas:")
        for er in an.expected_results:
            say(f"  {er.area.name}: {er.status} ({er.changed_bytes:,} bytes)")

    if args.blocks and an.blocks:
        blocks = sort_blocks(an.blocks, args.sort)
        shown = blocks if args.blocks < 0 else blocks[:args.blocks]
        say()
        say(f"Changed blocks ({len(shown)} of {len(blocks)}, sorted by {args.sort}):")
        has_syms = session.symbols_a is not None or session.symbols_b is not None
        say(f"  {'#':>5}  {'Start':<{w + 2}}  {'End':<{w + 2}}  {'Size':>10}  {'Class':<10}  {'Region':<14}"
            + ("  Symbol" if has_syms else ""))
        for blk in shown:
            sym = f"  {session.symbol_label(blk.start)}" if has_syms else ""
            say(f"  {blk.index:>5}  {fmt_addr(blk.start, w)}  {fmt_addr(blk.last, w)}  {blk.size:>10,}  "
                f"{blk.classification:<10}  {blk.region_label:<14}{sym}")

    _print_features(session, args, say)

    if not args.no_metadata and session.metadata_diff:
        found = [m for m in session.metadata_diff if m.status != "NOT FOUND"]
        if found:
            say()
            say("Detected metadata (heuristic - not guaranteed):")
            for m in found:
                say(f"  {m.category}: {', '.join(m.values_a) or '-'}  ->  {', '.join(m.values_b) or '-'}  [{m.status}]")

    if session.elf is not None:
        changed = session.elf.changed_sections
        say()
        say(f"ELF sections: {len(session.elf.sections)} compared, {len(changed)} changed")
        for s in changed:
            say(f"  {s.name:<20} {s.status:<8} diff {s.diff_bytes:,} bytes  {'; '.join(s.notes)}")
    elif session.elf_error:
        say(f"WARNING: {session.elf_error}")

    if args.checksum:
        say()
        say("Checksums (computed over image data in address order; gaps skipped):")
        for alg in args.checksum:
            ca = checksum_mod.checksum_image(alg, a)
            cb = checksum_mod.checksum_image(alg, b)
            say(f"  {alg}: old {ca}  new {cb}  {'SAME' if ca == cb else 'DIFFERENT'}")

    say()
    say(f"Result: {an.verdict}")


def _print_features(session, args, say) -> None:
    if args.functions == 0:
        return
    say()
    fr = session.features
    if fr is None:
        say(session.features_error or "Changed functions: no symbol information. Compare ELF files or pass "
                                      "--symbols-a/--symbols-b (ELF or GNU ld .map) to see which functions and "
                                      "modules changed.")
        return
    say(f"Changed functions / variables  (symbols: {fr.source_a} -> {fr.source_b})")
    say("-" * 60)
    for note in fr.notes:
        say(f"NOTE: {note}")
    say(fr.headline())
    if fr.modules:
        say()
        say("By module:")
        mw = max(len(m.module) for m in fr.modules)
        for m in fr.modules:
            parts = [f"{n} {lbl}" for n, lbl in ((m.modified, "modified"), (m.added, "added"),
                                                 (m.removed, "removed")) if n]
            tag = "  [expected only]" if m.expected_only else ""
            say(f"  {m.module:<{mw}}  {', '.join(parts):<34} {m.changed_bytes:>8,} bytes{tag}")
    changed = fr.changed
    if changed:
        shown = changed if args.functions < 0 else changed[:args.functions]
        say()
        nw = max(len(c.name) for c in shown)
        say(f"  {'Status':<9} {'Kind':<6} {'Symbol':<{nw}}  {'Module':<14} {'Changed':>8}  Notes")
        for c in shown:
            notes = list(c.notes)
            if c.expected_only:
                notes.insert(0, "expected change")
            say(f"  {c.status:<9} {c.kind:<6} {c.name:<{nw}}  {c.module or '-':<14} {c.changed_bytes:>8,}  "
                f"{'; '.join(notes)}")
        if len(shown) < len(changed):
            say(f"  ... {len(changed) - len(shown)} more (use --functions -1 or --functions-csv)")
    elif fr.side_effects:
        say("\nNo function or variable content was edited.")
    if fr.uncertain and args.functions:
        say()
        say("Likely relocated (only address references differ, some to unnamed data - not verifiable):")
        shown_u = fr.uncertain if args.functions < 0 else fr.uncertain[:args.functions]
        nw = max(len(c.name) for c in shown_u)
        for c in shown_u:
            say(f"  {c.kind:<6} {c.name:<{nw}}  {c.module or '-':<14} {c.unverified_bytes:>5} bytes unverified")
        if len(shown_u) < len(fr.uncertain):
            say(f"  ... {len(fr.uncertain) - len(shown_u)} more")
    side = fr.side_effects
    if side and args.show_relocated:
        say()
        say("Side effects only (code/data moved, call targets / pointers updated):")
        nw = max(len(c.name) for c in side)
        for c in side:
            say(f"  {c.status:<9} {c.kind:<6} {c.name:<{nw}}  {c.module or '-':<14} {'; '.join(c.notes)}")
    elif side:
        say(f"\n{len(side)} symbol(s) only moved or had call targets / pointers updated (side effects of the "
            "change, not edits) - list them with --show-relocated.")
    if fr.unattributed_bytes:
        say(f"\n{fr.unattributed_bytes:,} changed bytes are not inside any known symbol "
            "(padding, literal pools, unnamed data).")


def summary_dict(session) -> dict:
    an, diff = session.analysis, session.diff

    def img(i):
        rng = i.address_range
        m = i.mcu
        return {"file": i.path, "format": i.format, "size": i.file_size, "sha256": i.sha256,
                "controller": None if m is None else {
                    "device": m.device, "family": m.family, "vendor": m.vendor, "core": m.core,
                    "confidence": m.confidence, "details": m.details,
                    "evidence": [{"source": e.source, "finding": e.finding, "confidence": e.confidence}
                                 for e in m.evidence]},
                "address_range": [rng[0], rng[1]] if rng else None, "warnings": i.warnings}

    return {
        "tool": f"{APP_NAME} {__version__}",
        "timestamp": session.timestamp.isoformat(),
        "verdict": an.verdict,
        "firmware_a": img(session.image_a),
        "firmware_b": img(session.image_b),
        "total_compared": diff.total_compared,
        "changed_bytes": diff.diff_bytes,
        "only_in_a": diff.only_a_bytes,
        "only_in_b": diff.only_b_bytes,
        "changed_percent": round(diff.changed_percent, 4),
        "first_diff": diff.first_diff,
        "last_diff": diff.last_diff,
        "changed_blocks": len(an.blocks),
        "expected_bytes": an.expected_bytes,
        "unexpected_bytes": an.unexpected_bytes,
        "regions": [{"name": r.region.name, "status": r.status, "changed_bytes": r.changed_bytes,
                     "expected_bytes": r.expected_bytes, "size": r.size} for r in an.region_results],
        "metadata": [{"item": m.category, "a": m.values_a, "b": m.values_b, "status": m.status}
                     for m in session.metadata_diff],
        "metadata_disclaimer": DISCLAIMER,
        "features": None if session.features is None else {
            "symbols_a": session.features.source_a,
            "symbols_b": session.features.source_b,
            "unattributed_bytes": session.features.unattributed_bytes,
            "relocated": session.features.count("RELOCATED"),
            "moved": session.features.count("MOVED"),
            "notes": session.features.notes,
            "modules": [{"module": m.module, "modified": m.modified, "added": m.added, "removed": m.removed,
                         "moved": m.moved, "changed_bytes": m.changed_bytes, "expected_only": m.expected_only,
                         "symbols": m.symbols} for m in session.features.modules],
            "symbols": [{"name": c.name, "kind": c.kind, "module": c.module, "status": c.status,
                         "addr_a": c.addr_a, "addr_b": c.addr_b, "size_a": c.size_a, "size_b": c.size_b,
                         "changed_bytes": c.changed_bytes, "expected_only": c.expected_only, "notes": c.notes}
                        for c in session.features.changed],
        },
    }


def main(argv: list[str] | None = None) -> int:
    return run(argv)


def main_entry() -> None:
    sys.exit(main())


if __name__ == "__main__":
    main_entry()
