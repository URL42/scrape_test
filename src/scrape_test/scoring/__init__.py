"""Atlassian-fit scoring. Tune `rules.py`; `score.py` just applies it."""

from __future__ import annotations

from .rules import RULES_VERSION, WEIGHTS
from .score import ScoreResult, Signal, compute_score

__all__ = ["RULES_VERSION", "ScoreResult", "Signal", "WEIGHTS", "compute_score"]
