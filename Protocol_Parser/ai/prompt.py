"""Build a provider-neutral prompt describing a decoded packet.

An LLM backend can send this text to any model. The prompt pins the model to
the deterministic decode and asks it to label every inference.
"""
from __future__ import annotations

from core.decoder import DecodeResult

__all__ = ["SYSTEM_PROMPT", "build_prompt"]

SYSTEM_PROMPT = (
    "You are assisting an embedded firmware engineer. You are given the output of a deterministic "
    "packet decoder. Treat the decoded values, CRC results and errors as ground truth and never "
    "contradict them. Do not invent meanings for fields marked 'Unknown / Undefined'. Prefix every "
    "statement that is not directly stated in the decode with 'AI inference:'. Be concise."
)


def build_prompt(result: DecodeResult, direction: str = "", context: str = "") -> str:
    parts = ["Decoded packet (deterministic decoder output):", "", result.to_text(), ""]
    if direction:
        parts.append(f"Direction: {direction}")
    if context:
        parts.append(f"Engineer's notes: {context}")
    parts += [
        "",
        "Explain: what kind of frame this appears to be, what the payload means, whether it is valid, "
        "and anything suspicious. Structure the answer with headings: Frame type, Address, Command/Data, "
        "Integrity, Overall.",
    ]
    return "\n".join(parts)
