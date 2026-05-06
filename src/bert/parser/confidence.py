"""Confidence scoring helpers used by extractors.

We deliberately stay rule-based and deterministic. CI relies on the same
input bytes producing the same IR every time.
"""

from __future__ import annotations

import re

UUID_RE = re.compile(r"\b0x[0-9A-Fa-f]{4}\b")
HIGH_CONFIDENCE_KEYWORDS = ("shall", "mandatory", "is required to", "must")
MED_CONFIDENCE_KEYWORDS = ("should", "may be", "can be")
LOW_CONFIDENCE_KEYWORDS = ("optional", "if supported")


def score_requirement(text: str) -> tuple[float, bool]:
    """Return ``(confidence, needs_review)`` for a requirement-like sentence."""

    t = text.lower()
    hits_high = sum(1 for kw in HIGH_CONFIDENCE_KEYWORDS if kw in t)
    hits_med = sum(1 for kw in MED_CONFIDENCE_KEYWORDS if kw in t)
    hits_low = sum(1 for kw in LOW_CONFIDENCE_KEYWORDS if kw in t)
    if hits_high and not (hits_med or hits_low):
        return 0.95, False
    if hits_high and (hits_med or hits_low):
        return 0.6, True  # conflicting signals
    if hits_med:
        return 0.7, False
    if hits_low:
        return 0.85, False
    return 0.5, True
