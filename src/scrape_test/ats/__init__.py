"""Applicant tracking systems - the technographic source.

Public, documented job-board endpoints. No credentials, no bot-blocking, and vastly more
text than YC exposes: Stripe lists 3 roles on YC and 666 on Greenhouse.
"""

from __future__ import annotations

from .ashby import AshbyProvider
from .base import ATSProvider, ATSUnavailable, Posting, clean_html
from .discover import PROVIDERS, Board, discover_board, extract_tokens, fetch_board
from .greenhouse import GreenhouseProvider
from .lever import LeverProvider

__all__ = [
    "ATSProvider",
    "ATSUnavailable",
    "AshbyProvider",
    "Board",
    "GreenhouseProvider",
    "LeverProvider",
    "PROVIDERS",
    "Posting",
    "clean_html",
    "discover_board",
    "extract_tokens",
    "fetch_board",
]
