"""Optional analysis layer. The decoder never depends on this package.

To add an AI backend, subclass :class:`ai.base.AIAnalyzer`, use
:func:`ai.prompt.build_prompt` to describe the packet, and register the class
in ``ANALYZERS`` (selected by ``"ai_backend"`` in ``config/settings.json``).
"""
from __future__ import annotations

from ai.base import AIAnalyzer, AnalysisReport, AnalysisSection, AnalyzerUnavailable
from ai.heuristic import HeuristicAnalyzer

ANALYZERS: dict[str, type[AIAnalyzer]] = {
    "heuristic": HeuristicAnalyzer,
}


def get_analyzer(name: str | None = None) -> AIAnalyzer:
    """Return the configured analyzer, falling back to the offline heuristic one."""
    cls = ANALYZERS.get((name or "heuristic").lower(), HeuristicAnalyzer)
    analyzer = cls()
    return analyzer if analyzer.is_available() else HeuristicAnalyzer()


__all__ = ["AIAnalyzer", "AnalysisReport", "AnalysisSection", "AnalyzerUnavailable", "HeuristicAnalyzer",
           "ANALYZERS", "get_analyzer"]
