"""Offline, rule-based analyzer used when no AI backend is configured.

It restates decoded facts in a structured way and adds a small number of
clearly labelled, conservative inferences. It never invents field meanings.
"""
from __future__ import annotations

from ai.base import AIAnalyzer, AnalysisReport, AnalysisSection
from core.comparison import text_fields
from core.decoder import DecodeResult

__all__ = ["HeuristicAnalyzer"]


class HeuristicAnalyzer(AIAnalyzer):
    name = "Offline heuristic analyzer (no AI provider configured)"
    is_ai = False

    def analyze(self, result: DecodeResult, direction: str = "") -> AnalysisReport:
        report = AnalysisReport(self.name, is_ai=False)

        facts = [f"Protocol: {result.protocol_name}", f"Length: {len(result.data)} bytes",
                 f"Decoder status: {result.status}"]
        for f in result.fields:
            if not f.defined:
                facts.append(f"Offset {f.offset_text}: Unknown / Undefined ({f.length} byte(s))")
                continue
            line = f"{f.name}: {f.display}"
            if f.interpretation and f.interpretation != f.display:
                line += f"  ({f.interpretation})"
            facts.append(line)
            for b in f.bits:
                facts.append(f"    {b.bit_label} {b.name} = {b.value} ({b.interpretation})")
        report.sections.append(AnalysisSection("Decoded fields", facts))

        if result.checks:
            lines = [f"{c.kind} ({c.algorithm}): {'valid' if c.valid else 'INVALID'}" for c in result.checks]
            report.sections.append(AnalysisSection("Integrity", lines))
        if result.errors or result.warnings:
            report.sections.append(AnalysisSection(
                "Problems", [f"Error: {e}" for e in result.errors] + [f"Warning: {w}" for w in result.warnings]))

        inferences: list[str] = []
        texts = text_fields(result)
        if texts:
            kind = {"TX": "command", "RX": "response"}.get(direction.upper(), "command or response")
            for name, text in texts:
                inferences.append(f"Possible interpretation: '{name}' carries an ASCII {kind}: \"{text}\"")
            if direction.upper() == "TX":
                inferences.append("Possible interpretation: packet appears to be a command frame.")
            elif direction.upper() == "RX":
                inferences.append("Possible interpretation: packet appears to be a response frame.")
        for f in result.fields:
            if f.defined and f.interpretation.lower().startswith("exception"):
                inferences.append(f"Possible interpretation: '{f.name}' indicates an error/exception reply.")
        undefined = [f for f in result.fields if not f.defined]
        if undefined:
            inferences.append(
                f"{len(undefined)} region(s) are not covered by the definition - the protocol definition "
                f"may be incomplete or the packet malformed.")
        if result.ok:
            overall = "Structurally valid packet according to the selected protocol definition."
        elif result.status.endswith("ERROR"):
            overall = "Structure decodes, but the integrity check failed - possible transmission error " \
                      "or wrong CRC/checksum configuration."
        else:
            overall = "Packet does not match the selected protocol definition (see Problems)."
        inferences.append(f"Overall: {overall}")
        report.sections.append(AnalysisSection("Interpretation", inferences, inferred=True))
        return report
