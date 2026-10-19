"""High-level comparison session used by both the CLI and the GUI."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from .analysis import Analysis, analyze
from .binary_comparator import DiffResult, compare_images
from .binary_reader import load_firmware
from .elf_compare import ElfComparison, compare_elf
from .feature_diff import FeatureReport, compare_features, uses_vma
from .image import FirmwareImage
from .metadata_detector import MetadataDiff, MetadataItem, compare_metadata, detect_metadata
from .region_manager import ProjectConfig
from .symbols import SymbolTable, from_elf, load_symbol_table


@dataclass
class ComparisonSession:
    image_a: FirmwareImage
    image_b: FirmwareImage
    config: ProjectConfig
    diff: DiffResult
    analysis: Analysis
    metadata_a: list[MetadataItem] = field(default_factory=list)
    metadata_b: list[MetadataItem] = field(default_factory=list)
    metadata_diff: list[MetadataDiff] = field(default_factory=list)
    elf: ElfComparison | None = None
    elf_error: str | None = None
    symbols_a: SymbolTable | None = None
    symbols_b: SymbolTable | None = None
    features: FeatureReport | None = None
    features_error: str | None = None
    timestamp: _dt.datetime = field(default_factory=lambda: _dt.datetime.now().astimezone())

    def reanalyze(self, config: ProjectConfig) -> Analysis:
        """Re-run region/expected-area analysis without re-comparing bytes.

        A change of ``gap_fill`` requires a full re-comparison (build_session).
        """
        self.analysis = analyze(self.diff, config)
        self.config = config
        self.update_features()
        return self.analysis

    def set_symbols(self, table_a: SymbolTable | None, table_b: SymbolTable | None) -> None:
        from .binary_reader import identify_with_symbols
        for img, old, new in ((self.image_a, self.symbols_a, table_a), (self.image_b, self.symbols_b, table_b)):
            if getattr(old, "path", None) != getattr(new, "path", None) or img.mcu is None:
                identify_with_symbols(img, getattr(new, "path", None))
        self.symbols_a, self.symbols_b = table_a, table_b
        self.update_features()

    @property
    def controller_mismatch(self) -> bool:
        """True when A and B were identified as different controllers."""
        a, b = self.image_a.mcu, self.image_b.mcu
        return bool(a and b and a.same_target_as(b) is False)

    def update_features(self) -> None:
        self.features_error = None
        try:
            self.features = compare_features(self, self.symbols_a, self.symbols_b)
        except Exception as exc:  # auxiliary analysis must never break the comparison
            self.features = None
            self.features_error = f"Function/module analysis failed: {exc}"

    def symbol_label(self, addr: int) -> str:
        """'function+0xOFF (module)' for an image address, '' if unknown. Prefers the new build (B)."""
        for table, img in ((self.symbols_b, self.image_b), (self.symbols_a, self.image_a)):
            if table is not None:
                s = table.symbol_at(addr, uses_vma(img))
                if s is not None:
                    off = addr - s.addr(uses_vma(img))
                    return f"{s.name}+0x{off:X}" + (f" ({s.module})" if s.module else "")
        return ""

    def close(self) -> None:
        self.image_a.close()
        self.image_b.close()


def default_symbols(image: FirmwareImage) -> SymbolTable | None:
    """Symbols embedded in an ELF image (None for BIN/HEX)."""
    if image.elf is None:
        return None
    table = from_elf(image.elf, f"{image.name} (ELF)")
    return table if len(table) else None


def build_session(image_a: FirmwareImage, image_b: FirmwareImage, config: ProjectConfig,
                  progress=None, cancel=None, detect_meta: bool = True,
                  symbols_a: SymbolTable | None = None, symbols_b: SymbolTable | None = None) -> ComparisonSession:
    config.check()  # fail early on invalid regions
    diff = compare_images(image_a, image_b, progress, cancel, fill=config.gap_fill)
    session = ComparisonSession(image_a, image_b, config, diff, analyze(diff, config))
    if detect_meta:
        session.metadata_a = detect_metadata(image_a)
        session.metadata_b = detect_metadata(image_b)
        session.metadata_diff = compare_metadata(session.metadata_a, session.metadata_b)
    if image_a.elf is not None and image_b.elf is not None:
        try:
            session.elf = compare_elf(image_a.elf, image_b.elf)
        except Exception as exc:  # ELF comparison is auxiliary; never break the main comparison
            session.elf_error = f"ELF section comparison failed: {exc}"
    session.set_symbols(symbols_a or default_symbols(image_a), symbols_b or default_symbols(image_b))
    return session


def load_and_compare(path_a: str, path_b: str, config: ProjectConfig | None = None, *,
                     fmt_a: str = "auto", fmt_b: str = "auto", base_a: int | None = None,
                     base_b: int | None = None, elf_use_vaddr: bool = False,
                     symbols_a: str | None = None, symbols_b: str | None = None,
                     progress=None, cancel=None) -> ComparisonSession:
    """*symbols_a/b* are optional paths to an ELF or GNU ld .map file of each build."""
    config = config or ProjectConfig()
    default_base = config.base_address or 0
    table_a = load_symbol_table(symbols_a) if symbols_a else None
    table_b = load_symbol_table(symbols_b) if symbols_b else None
    img_a = load_firmware(path_a, fmt_a, default_base if base_a is None else base_a, elf_use_vaddr)
    try:
        img_b = load_firmware(path_b, fmt_b, default_base if base_b is None else base_b, elf_use_vaddr)
    except Exception:
        img_a.close()
        raise
    try:
        return build_session(img_a, img_b, config, progress, cancel, symbols_a=table_a, symbols_b=table_b)
    except Exception:
        img_a.close()
        img_b.close()
        raise
