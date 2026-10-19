"""Provider-agnostic AI analysis interface.

The deterministic decoder is always the source of truth. An analyzer receives
a finished :class:`DecodeResult` and returns a report that keeps *facts*
(copied from the decoder) strictly apart from *inferences* (labelled).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from core.decoder import DecodeResult

__all__ = ["AnalysisSection", "AnalysisReport", "AIAnalyzer", "AnalyzerUnavailable"]


class AnalyzerUnavailable(RuntimeError):
    """The backend is not configured / reachable."""


@dataclass
class AnalysisSection:
    title: str
    lines: list[str]
    inferred: bool = False  # True = not verified by the deterministic decoder


@dataclass
class AnalysisReport:
    backend: str
    is_ai: bool  # True if produced by a language model
    sections: list[AnalysisSection] = field(default_factory=list)

    def to_text(self) -> str:
        label = "AI inference" if self.is_ai else "Heuristic inference"
        out = [f"Analysis backend: {self.backend}", ""]
        for s in self.sections:
            title = f"{s.title}  [{label} - not verified]" if s.inferred else f"{s.title}  [decoded]"
            out.append(title)
            out.extend(f"  {line}" for line in s.lines)
            out.append("")
        out.append("The deterministic decode table is the source of truth; inferences may be wrong.")
        return "\n".join(out)


class AIAnalyzer(ABC):
    """Implement this to plug in an AI backend (local model, cloud API, ...)."""

    name: str = "analyzer"
    is_ai: bool = True

    def is_available(self) -> bool:
        return True

    @abstractmethod
    def analyze(self, result: DecodeResult, direction: str = "") -> AnalysisReport:
        """Return a report for ``result``. May raise :class:`AnalyzerUnavailable`."""
