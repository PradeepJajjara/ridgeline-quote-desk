"""Post-extraction validation for LLM-extracted lines.

The source-text check applies to LLM lines only (Excel lines come straight from
cells). Each as_written must be found in the source text once whitespace and
case are normalised; if not, the line is flagged not_in_source and needs_review.
Nothing is ever guessed or repaired here - only flagged.
"""
from __future__ import annotations

import re


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def in_source_text(as_written: str, source_text: str) -> bool:
    """True if as_written appears in source_text (whitespace/case-insensitive)."""
    return _normalize(as_written) in _normalize(source_text)
