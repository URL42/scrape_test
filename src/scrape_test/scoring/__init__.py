"""Atlassian-fit scoring. Tune `rules.py`; `score.py` just applies it."""

from __future__ import annotations

from .rules import RULES_VERSION, WEIGHTS
from .score import Signal, compute_score

__all__ = ["RULES_VERSION", "Signal", "WEIGHTS", "compute_score"]
