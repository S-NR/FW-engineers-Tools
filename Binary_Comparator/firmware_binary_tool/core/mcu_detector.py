"""Heuristic identification of the target microcontroller.

A firmware image does not store its part number, but builds leave clues:

* **Named evidence** (high confidence): device header / startup file / linker
  script names in ELF debug information or linker map files
  (``stm32f411xe.h``, ``STM32F411RETX_FLASH.ld``, ``p32mx575f512l.h``) and
  compiler options recorded by GCC (``-mcpu=cortex-m4 -mfpu=fpv4-sp-d16``).
* **Structural evidence** (family level, low/medium confidence): the memory
  layout (STM32 flash at 0x08000000, PIC32 boot flash and configuration
  words), a Cortex-M vector table (initial stack pointer -> RAM size, number
  of vectors -> interrupt count), the flash extent, ESP32 / AVR signatures.

The result always lists its evidence. It is a best guess, not a guarantee.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

HIGH, MEDIUM, LOW = "high", "medium", "low"
_RANK = {HIGH: 0, MEDIUM: 1, LOW: 2}


@dataclass
class Evidence:
    source: str  # where it was found: "ELF debug info", "linker map", "vector table", ...
    finding: str
    confidence: str


@dataclass
class McuIdentification:
    device: str | None = None  # exact part if known, e.g. "STM32F411RET"
    family: str | None = None  # e.g. "STM32F4 (STM32F411xE)"
    vendor: str | None = None
    core: str | None = None  # e.g. "ARM Cortex-M4 (FPU: fpv4-sp-d16)"
    confidence: str = LOW
    details: dict[str, str] = field(default_factory=dict)  # decoded part number / layout facts
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return bool(self.device or self.family or self.core)

    def short(self) -> str:
        if not self.known:
            return "unknown (no identifying evidence)"
        name = self.device or self.family or "unknown device"
        core = f" — {self.core}" if self.core else ""
        return f"{name}{core}  [{self.confidence} confidence]"

    def same_target_as(self, other: "McuIdentification") -> bool | None:
        """True = same device, False = definitely different, None = compatible but not provable.

        A family-level result (e.g. "PIC32MX3xx-7xx") is compatible with a device inside that family
        (PIC32MX575F512L); only conflicting identifications return False.
        """
        a, b = (self.device or self.family), (other.device or other.family)
        if not a or not b:
            return None
        if self.device and other.device:
            return self.device.upper() == other.device.upper()
        if _vendor_group(a) != _vendor_group(b):
            return False
        for x, y in ((self, other), (other, self)):  # device vs. family range
            if x.device and y.family and not y.device:
                return None if _in_family(x.device, y.family) else False
        fa, fb = (self.family or "").split(" ")[0].upper(), (other.family or "").split(" ")[0].upper()
        if fa == fb:
            return True
        return None if fa.startswith(fb) or fb.startswith(fa) else False


def _vendor_group(name: str) -> str:
    n = name.upper()
    for prefix in ("PIC32", "STM32", "ESP32", "RP20", "NRF5", "AVR", "ATMEGA", "ATTINY", "SAM", "LPC", "GD32", "AT32"):
        if n.startswith(prefix) or f" {prefix}" in n:
            return {"ATMEGA": "AVR", "ATTINY": "AVR"}.get(prefix, prefix)
    return n[:5]


def _in_family(device: str, family: str) -> bool:
    """Is *device* (e.g. PIC32MX575F512L / STM32F411RET) plausibly a member of *family*?"""
    d, f = device.upper(), family.upper()
    m = re.match(r"PIC32(M[XZKM])(\d)XX-(\d)XX", f) or re.match(r"PIC32(M[XZKM])(\d)XX/(\d)XX", f)
    if m:
        dm = re.match(r"PIC32(M[XZKM])(\d)", d)
        return bool(dm and dm.group(1) == m.group(1) and int(m.group(2)) <= int(dm.group(2)) <= int(m.group(3)))
    if "OR COMPATIBLE" in f:  # structural STM32-style guess
        return d.startswith(("STM32", "GD32", "AT32", "APM32"))
    head = f.split(" ")[0]
    return d.startswith(head)


# ------------------------------------------------------------------ STM32 decoding
_STM32_FLASH = {"4": "16 KB", "6": "32 KB", "8": "64 KB", "B": "128 KB", "Z": "192 KB", "C": "256 KB",
                "D": "384 KB", "E": "512 KB", "F": "768 KB", "G": "1 MB", "H": "1.5 MB", "I": "2 MB"}
_STM32_PINS = {"F": 20, "G": 28, "K": 32, "T": 36, "S": 44, "C": 48, "U": 63, "R": 64, "J": 72, "M": 80, "O": 90,
               "V": 100, "Q": 132, "Z": 144, "A": 169, "I": 176, "B": 208, "N": 216, "X": 256}
_STM32_PKG = {"T": "LQFP", "H": "BGA", "U": "UFQFPN", "Y": "WLCSP", "P": "TSSOP", "I": "UFBGA", "J": "UFBGA",
              "K": "UFBGA", "M": "SO"}
_STM32_CORE = {
    "F0": "ARM Cortex-M0", "G0": "ARM Cortex-M0+", "L0": "ARM Cortex-M0+", "C0": "ARM Cortex-M0+",
    "F1": "ARM Cortex-M3", "F2": "ARM Cortex-M3", "L1": "ARM Cortex-M3",
    "F3": "ARM Cortex-M4F", "F4": "ARM Cortex-M4F", "G4": "ARM Cortex-M4F", "L4": "ARM Cortex-M4F",
    "WB": "ARM Cortex-M4F + M0+", "WL": "ARM Cortex-M4 + M0+",
    "F7": "ARM Cortex-M7F", "H7": "ARM Cortex-M7F (some parts + M4)",
    "L5": "ARM Cortex-M33", "U5": "ARM Cortex-M33", "H5": "ARM Cortex-M33", "U0": "ARM Cortex-M0+",
}

_STM32_TOKEN = re.compile(rb"(?i)stm32([a-z]{1,2}\d{1,3}[0-9a-z]{0,8})")
_STM32_FULL = re.compile(r"^([A-Z]{1,2})(\d{2,3})([A-WYZ])([0-9A-Z])([A-Z])?(\d|X)?$")  # F411 R E T 6 ('x' = any)
_STM32_LINE = re.compile(r"^([A-Z]{1,2})(\d{2,3})X([0-9A-WYZ])$")  # F411 x E  (device header)
_STM32_LINE_ANY = re.compile(r"^([A-Z]{1,2})(\d{2,3})XX$")  # F407xx (device header without flash code)
_STM32_FAMILY = re.compile(r"^([A-Z]{1,2})(\d)XX$")  # F4xx


def _stm32_series(prefix: str, digits: str) -> str:
    return f"{prefix}{digits[0]}"


def _decode_stm32(tokens: Counter) -> tuple[str | None, str | None, dict, str | None]:
    """(device, family, details, core) from stm32 tokens."""
    full = line = line_any = fam = None
    for tok, _ in tokens.most_common():
        t = tok.upper().removesuffix("_FLASH").removesuffix("_RAM")
        m = _STM32_FULL.match(t)
        if m and not full and not t.endswith("XX"):
            full = m
            continue
        m = _STM32_LINE.match(t)
        if m and not line:
            line = m
            continue
        m = _STM32_LINE_ANY.match(t)
        if m and not line_any:
            line_any = m
            continue
        m = _STM32_FAMILY.match(t)
        if m and not fam:
            fam = m
    details: dict[str, str] = {}
    device = family = core = None
    if full:
        p, d, pins, flash, pkg = full.group(1), full.group(2), full.group(3), full.group(4), full.group(5)
        device = f"STM32{p}{d}{pins}{flash}{pkg or ''}"
        series = _stm32_series(p, d)
        family = f"STM32{series} (STM32{p}{d}x{flash})"
        details["Flash (from part number)"] = _STM32_FLASH.get(flash, "?")
        if pins in _STM32_PINS:
            details["Pins (from part number)"] = str(_STM32_PINS[pins])
        if pkg and pkg in _STM32_PKG:
            details["Package (from part number)"] = _STM32_PKG[pkg]
        core = _STM32_CORE.get(series)
    elif line:
        p, d, flash = line.group(1), line.group(2), line.group(3)
        series = _stm32_series(p, d)
        family = f"STM32{series} (STM32{p}{d}x{flash})"
        details["Flash (from device header)"] = _STM32_FLASH.get(flash, "?")
        core = _STM32_CORE.get(series)
    elif line_any:
        p, d = line_any.group(1), line_any.group(2)
        series = _stm32_series(p, d)
        family = f"STM32{series} (STM32{p}{d}xx)"
        core = _STM32_CORE.get(series)
    elif fam:
        series = f"{fam.group(1)}{fam.group(2)}"
        family = f"STM32{series} series"
        core = _STM32_CORE.get(series)
    return device, family, details, core


# ------------------------------------------------------------------ PIC32 decoding
_PIC32_TOKEN = re.compile(rb"(?i)(?<![0-9a-z])(?:pic|p)?32(m[xzkm])(\d{3})([fh])(\d{3})([a-z])(?![0-9a-z])")
_PIC32_PINS = {"B": 28, "C": 36, "D": 44, "H": 64, "L": 100, "M": 124, "E": 64, "G": 100, "J": 144, "K": 144}
_PIC32_CORE = {"MX": "MIPS32 M4K", "MZ": "MIPS32 microAptiv (M5150 / M-class)", "MK": "MIPS32 microAptiv",
               "MM": "MIPS32 microAptiv UC"}


def _decode_pic32(tokens: Counter) -> tuple[str | None, str | None, dict, str | None]:
    if not tokens:
        return None, None, {}, None
    tok = tokens.most_common(1)[0][0]
    m = re.match(r"(?i)(?:pic|p)?32(m[xzkm])(\d{3})([fh])(\d{3})([a-z])", tok)
    fam, num, mem_type, flash_kb, pin = (m.group(1).upper(), m.group(2), m.group(3).upper(), m.group(4),
                                         m.group(5).upper())
    device = f"PIC32{fam}{num}{mem_type}{flash_kb}{pin}"
    details = {"Program flash (from part number)": f"{int(flash_kb)} KB"}
    if pin in _PIC32_PINS:
        details["Pins (from part number)"] = str(_PIC32_PINS[pin])
    return device, f"PIC32{fam}", details, _PIC32_CORE.get(fam)


# ------------------------------------------------------------------ other vendors (name only)
_OTHER = [
    (re.compile(rb"(?i)\bnrf5[1-4]\d{2,3}(?:_xx[a-z]{2})?"), "Nordic", "ARM Cortex-M4F / M33 (nRF5x)"),
    (re.compile(rb"(?i)\b(?:at)?sam[a-z]\d{2}[a-z]\d{2}[a-z]?\b"), "Microchip (Atmel) SAM", None),
    (re.compile(rb"(?i)\blpc\d{3,4}[a-z0-9]*"), "NXP LPC", None),
    (re.compile(rb"(?i)\bgd32[a-z]\d{3}[a-z0-9]*"), "GigaDevice GD32", None),
    (re.compile(rb"(?i)\bat32[a-z]\d{3}[a-z0-9]*"), "Artery AT32", None),
    (re.compile(rb"(?i)\brp2040\b"), "Raspberry Pi RP2040", "ARM Cortex-M0+ (dual core)"),
    (re.compile(rb"(?i)\brp2350\b"), "Raspberry Pi RP2350", "ARM Cortex-M33 / Hazard3 RISC-V"),
    (re.compile(rb"(?i)\besp32(?:-?[schp]\d)?\b"), "Espressif ESP32", None),
    (re.compile(rb"(?i)\batmega\d{2,4}[a-z]{0,3}\b"), "Microchip AVR (megaAVR)", "AVR 8-bit"),
    (re.compile(rb"(?i)\battiny\d{1,4}[a-z]{0,3}\b"), "Microchip AVR (tinyAVR)", "AVR 8-bit"),
    (re.compile(rb"(?i)\btm4c\d{3}[a-z0-9]+"), "TI Tiva C", "ARM Cortex-M4F"),
    (re.compile(rb"(?i)\bmsp430[a-z]{1,2}\d{3,4}[a-z0-9]*"), "TI MSP430", "MSP430 16-bit"),
    (re.compile(rb"(?i)\bef[mr]32[a-z]{2}\d{2,3}[a-z0-9]*"), "Silicon Labs EFM32/EFR32", None),
    (re.compile(rb"(?i)\br7fa\d[a-z]\d{1,2}[a-z0-9]*"), "Renesas RA", None),
]

_MCPU = re.compile(rb"-mcpu=([\w.+-]+)")
_MFPU = re.compile(rb"-mfpu=([\w.+-]+)")
_MPROC = re.compile(rb"-mprocessor=([\w.+-]+)")
_ARM_ATTR_CPU = re.compile(rb"(?i)(cortex-m\d+(?:plus|\+)?|cortex-a\d+|cortex-r\d+)")

_SOURCE_LABEL = {"elf": "ELF (debug info / strings)", "map": "linker map", "image": "firmware data strings",
                 "symbols": "symbol file"}


def _scan_names(blob: bytes) -> dict[str, Counter]:
    out: dict[str, Counter] = {"stm32": Counter(), "pic32": Counter()}
    for m in _STM32_TOKEN.finditer(blob):
        tok = m.group(0).decode("latin1")
        if re.match(r"(?i)stm32cube|stm32[a-z]*ide|stm32_?prog", tok):
            continue
        out["stm32"][tok[5:]] += 1
    for m in _PIC32_TOKEN.finditer(blob):
        out["pic32"][m.group(0).decode("latin1")] += 1
    for rx, vendor, _ in _OTHER:
        c = Counter(m.group(0).decode("latin1").upper() for m in rx.finditer(blob))
        if c:
            out[vendor] = c
    return out


# ------------------------------------------------------------------ structural analysis
def _read_word(image, addr: int) -> int | None:
    vals = image.read(addr, 4)
    if any(v is None for v in vals):
        return None
    return int.from_bytes(bytes(vals), "little")


def _cortex_vector_table(image, base: int) -> tuple[int, int, int] | None:
    """(initial SP, reset handler, vector count) if a Cortex-M vector table starts at *base*."""
    sp, reset = _read_word(image, base), _read_word(image, base + 4)
    if sp is None or reset is None:
        return None
    lo, hi = image.address_range
    ram_like = 0x10000000 <= sp <= 0x3FFFFFFF and sp % 4 == 0
    if not (ram_like and reset & 1 and lo <= (reset & ~1) <= hi):
        return None
    count = 2
    for i in range(2, 512):
        w = _read_word(image, base + 4 * i)
        if w is None:
            break
        if w == 0 or (w & 1 and lo <= (w & ~1) <= hi):
            count += 1
            continue
        break
    while count > 2 and _read_word(image, base + 4 * (count - 1)) == 0:  # trailing padding is not a vector
        count -= 1
    return sp, reset & ~1, count


def _structure(image, ident: McuIdentification) -> tuple[str | None, str | None]:
    """Adds layout evidence; returns (family hint, core hint)."""
    rng = image.address_range
    if rng is None:
        return None, None
    lo, hi = rng
    fam = core = None
    cov = image.coverage

    # ESP32 application / bootloader image header
    first = image.byte_at(lo)
    if first == 0xE9 and lo in (0, 0x1000, 0x10000):
        chip = _read_word(image, lo + 12)
        names = {0: "ESP32", 2: "ESP32-S2", 5: "ESP32-C3", 9: "ESP32-S3", 12: "ESP32-C2", 13: "ESP32-C6",
                 16: "ESP32-H2", 18: "ESP32-P4"}
        if chip is not None and (chip & 0xFFFF) in names:
            fam = names[chip & 0xFFFF]
            ident.evidence.append(Evidence("image header", f"ESP image header (magic 0xE9), chip id "
                                                           f"{chip & 0xFFFF} = {fam}", MEDIUM))
            return fam, None

    # PIC32 (physical or KSEG addresses)
    def in_any(a, b):
        return cov.overlap(a, b) > 0

    if in_any(0x1D000000, 0x1E000000) or in_any(0x9D000000, 0x9E000000) or in_any(0x1FC00000, 0x1FC80000) \
            or in_any(0xBFC00000, 0xBFC80000):
        fam = "PIC32"
        core = "MIPS32"
        flash_top = max((e for s, e in cov if 0x1D000000 <= s < 0x1E000000 or 0x9D000000 <= s < 0x9E000000),
                        default=None)
        ident.evidence.append(Evidence("memory layout", "program flash at 0x1D000000 / boot flash at 0x1FC00000 "
                                                        "(PIC32 memory map)", MEDIUM))
        if in_any(0x1FC02FF0, 0x1FC03000) or in_any(0xBFC02FF0, 0xBFC03000):
            fam = "PIC32MX3xx-7xx"
            ident.evidence.append(Evidence("configuration words", "DEVCFG0..3 at 0x1FC02FF0-0x1FC02FFF "
                                                                  "(12 KB boot flash: PIC32MX3xx/4xx/5xx/6xx/7xx)",
                                           MEDIUM))
        elif in_any(0x1FC00BF0, 0x1FC00C00) or in_any(0xBFC00BF0, 0xBFC00C00):
            fam = "PIC32MX1xx/2xx"
            ident.evidence.append(Evidence("configuration words", "DEVCFG at 0x1FC00BF0 (3 KB boot flash: "
                                                                  "PIC32MX1xx/2xx)", MEDIUM))
        elif in_any(0x1FC0FF00, 0x1FC10000) or in_any(0x1FC4FF00, 0x1FC50000):
            fam = "PIC32MZ"
            ident.evidence.append(Evidence("configuration words", "configuration area at 0x1FC0FFxx (PIC32MZ)",
                                           MEDIUM))
        if flash_top is not None:
            used = (flash_top & 0x1FFFFFFF) - 0x1D000000
            ident.details["Program flash used up to"] = f"0x{0x1D000000 + used - 1:08X} (≥ {used // 1024 + 1} KB)"
        return fam, core

    # Cortex-M vector table at the start of the image (or after a 256-byte RP2040 boot2)
    for base in (lo, lo + 0x100):
        vt = _cortex_vector_table(image, base)
        if vt is None:
            continue
        sp, reset, count = vt
        core = "ARM Cortex-M"
        ident.evidence.append(Evidence("vector table", f"Cortex-M vector table at 0x{base:08X}: initial SP "
                                                       f"0x{sp:08X}, reset handler 0x{reset:08X}, {count} vectors"
                                                       + (f" ({count - 16} interrupts)" if count > 16 else ""),
                                       MEDIUM))
        ident.details["Vector table"] = f"{count} entries" + (f" ({count - 16} IRQs)" if count > 16 else "")
        if 0x20000000 <= sp <= 0x20200000:
            ident.details["RAM (from initial stack pointer)"] = f"≥ {(sp - 0x20000000) // 1024} KB at 0x20000000"
        if base == lo + 0x100 and 0x10000000 <= lo < 0x10001000:
            fam = "RP2040 (XIP flash with 256-byte boot2)"
        break

    if 0x08000000 <= lo < 0x08200000:
        fam = fam or "STM32 or compatible (GD32 / AT32 / APM32)"
        where = "at the start of" if lo == 0x08000000 else f"{(lo - 0x08000000) // 1024} KB into"
        ident.evidence.append(Evidence("memory layout", f"code starts at 0x{lo:08X}, {where} the STM32-style main "
                                                        "flash region (0x08000000)", LOW))
        if lo > 0x08000000:
            ident.details["Image start"] = (f"0x{lo:08X} - {(lo - 0x08000000) // 1024} KB into flash "
                                            "(application behind a bootloader?)")
    elif 0x00400000 <= lo < 0x00500000 and core:
        fam = fam or "Microchip SAM E/S/V / SAM4 (flash at 0x00400000)"
        ident.evidence.append(Evidence("memory layout", "code starts at 0x00400000 (SAM E/S/V flash)", LOW))
    elif lo == 0 and core is None and _read_word(image, 0) is not None:
        w0 = _read_word(image, 0)
        if (w0 & 0xFE0F) == 0x940C and (_read_word(image, 4) or 0) & 0xFE0F == 0x940C:
            fam, core = "AVR (megaAVR)", "AVR 8-bit"
            ident.evidence.append(Evidence("vector table", "AVR JMP interrupt vector table at 0x0000", MEDIUM))
    if fam is None and core:
        ident.details["Flash used"] = f"0x{lo:08X} - 0x{hi:08X} ({(hi - lo + 1) // 1024} KB span)"
    return fam, core


# ------------------------------------------------------------------ main entry
def detect_mcu(image, extra_files: list[str] | tuple = ()) -> McuIdentification:
    """Identify the controller of *image*. *extra_files* are symbol files (ELF / .map) of the same build."""
    ident = McuIdentification()
    sources: list[tuple[str, bytes]] = []
    if image.elf is not None:
        sources.append(("elf", image.elf.raw))
    for path in extra_files or ():
        try:
            with open(path, "rb") as f:
                blob = f.read()
        except OSError:
            continue
        sources.append(("elf" if blob[:4] == b"\x7fELF" else "map", blob))
    if image.elf is None:  # strings inside the firmware data itself (e.g. a device name in a USB descriptor)
        sources.append(("image", b"\n".join(bytes(s.data) for s in image.segments)))

    best: tuple | None = None  # (rank, device, family, details, core, vendor, source)
    core_flags: list[str] = []
    for kind, blob in sources:
        names = _scan_names(blob)
        conf = HIGH if kind in ("elf", "map") else MEDIUM
        label = _SOURCE_LABEL.get(kind, kind)
        if names["stm32"]:
            dev, fam, det, core = _decode_stm32(names["stm32"])
            if dev or fam:
                toks = ", ".join(f"{t}" for t, _ in names["stm32"].most_common(4))
                ident.evidence.append(Evidence(label, f"STM32 names: {toks}", conf if (dev or "(" in (fam or ""))
                                               else MEDIUM))
                cand = (_RANK[conf] * 2 + (0 if dev else 1), dev, fam, det, core, "STMicroelectronics", label)
                best = min(best, cand, key=lambda x: x[0]) if best else cand
        if names["pic32"]:
            dev, fam, det, core = _decode_pic32(names["pic32"])
            ident.evidence.append(Evidence(label, f"PIC32 part name: {names['pic32'].most_common(1)[0][0]}", conf))
            cand = (_RANK[conf] * 2, dev, fam, det, core, "Microchip", label)
            best = min(best, cand, key=lambda x: x[0]) if best else cand
        for rx, vendor, vcore in _OTHER:
            if vendor in names:
                tok = names[vendor].most_common(1)[0][0]
                ident.evidence.append(Evidence(label, f"{vendor} name: {tok}", conf))
                cand = (_RANK[conf] * 2 + 1, tok, vendor, {}, vcore, vendor.split(" ")[0], label)
                best = min(best, cand, key=lambda x: x[0]) if best else cand
        if kind in ("elf",):
            cpu = [m.group(1).decode() for m in _MCPU.finditer(blob)]
            fpu = [m.group(1).decode() for m in _MFPU.finditer(blob)]
            proc = [m.group(1).decode() for m in _MPROC.finditer(blob)]
            if cpu:
                c = Counter(cpu).most_common(1)[0][0]
                f = Counter(fpu).most_common(1)[0][0] if fpu else None
                pretty = f"Cortex-{c.split('-', 1)[1].upper()}" if c.lower().startswith("cortex-") else c
                core_flags.append(f"ARM {pretty}" + (f" (FPU: {f})" if f and f != "none" else ""))
                ident.evidence.append(Evidence(label, f"compiler options: -mcpu={c}" + (f" -mfpu={f}" if f else ""),
                                               HIGH))
            if proc:
                ident.evidence.append(Evidence(label, f"compiler option: -mprocessor={proc[0]}", HIGH))
            elif not cpu:
                m = _ARM_ATTR_CPU.search(blob)
                if m:
                    name = m.group(1).decode()
                    core_flags.append("ARM Cortex-" + name.split("-", 1)[1].upper())

    if image.elf is not None:
        ident.details["ELF machine"] = f"{image.elf.machine}, ELF{image.elf.elf_class}, {image.elf.endian}-endian"

    fam_hint, core_hint = _structure(image, ident)

    if best is not None:
        _, ident.device, ident.family, det, core, ident.vendor, _ = best
        ident.details.update(det)
        ident.core = core_flags[0] if core_flags else core
        ident.confidence = HIGH if best[0] <= 1 else MEDIUM
    else:
        ident.family = fam_hint
        ident.core = core_flags[0] if core_flags else core_hint
        if fam_hint and fam_hint.startswith(("PIC32", "STM32")):
            ident.vendor = "Microchip" if fam_hint.startswith("PIC32") else "STMicroelectronics (or compatible)"
        ident.confidence = MEDIUM if core_flags or (fam_hint and fam_hint.startswith(("PIC32MX", "PIC32MZ", "ESP"))) \
            else LOW

    # consistency checks
    if ident.family and fam_hint and ident.family.startswith("STM32") and fam_hint.startswith("PIC32"):
        ident.evidence.append(Evidence("consistency", "WARNING: name says STM32 but memory layout is PIC32", LOW))
    if ident.family and fam_hint and ident.family.startswith("PIC32") and "STM32" in fam_hint:
        ident.evidence.append(Evidence("consistency", "WARNING: name says PIC32 but memory layout is STM32-like", LOW))
    flash = ident.details.get("Flash (from part number)") or ident.details.get("Flash (from device header)") \
        or ident.details.get("Program flash (from part number)")
    parts = flash.split() if flash else []
    if len(parts) == 2 and image.address_range:
        num, unit = parts
        limit = float(num) * {"MB": 1024, "KB": 1}.get(unit, 1) * 1024
        flash_bytes = image.coverage.overlap(0x08000000, 0x09000000) or \
            image.coverage.overlap(0x1D000000, 0x1E000000)
        if flash_bytes and flash_bytes > limit:
            ident.evidence.append(Evidence("consistency", f"WARNING: image uses {flash_bytes // 1024} KB of flash, "
                                                          f"more than the {flash} of the identified part", LOW))
    ident.evidence.sort(key=lambda e: _RANK[e.confidence])
    return ident
