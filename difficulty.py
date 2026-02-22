"""
Card difficulty detection using FSRS memory_state.

is_difficult(card, cfg) -> bool
difficulty_label(card)   -> str

Config keys (from addon config.json):
  difficulty_min_reps          int   min reviews before flagging  (default 5)
  difficulty_fsrs_d_threshold  float FSRS D threshold 1-10       (default 6.0)
"""
from __future__ import annotations

_DEFAULT_MIN_REPS = 5
_DEFAULT_D_THRESHOLD = 6.0


def is_difficult(card, cfg: dict | None = None) -> bool:
    cfg = cfg or {}
    min_reps = cfg.get("difficulty_min_reps", _DEFAULT_MIN_REPS)
    d_threshold = cfg.get("difficulty_fsrs_d_threshold", _DEFAULT_D_THRESHOLD)

    if card.reps < min_reps:
        return False
    ms = getattr(card, "memory_state", None)
    if ms is None:
        return False
    return ms.difficulty >= d_threshold


def difficulty_label(card) -> str:
    """Short human-readable label, e.g. 'D 7.2/10 · 8 reviews'."""
    ms = getattr(card, "memory_state", None)
    if ms is None:
        return ""
    return f"D {ms.difficulty:.1f}/10 · {card.reps} reviews"
