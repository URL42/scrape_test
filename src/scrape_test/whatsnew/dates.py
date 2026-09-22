"""Parse the published dates feeds actually emit, and turn them into an age.

Two shapes dominate: RFC 822 from RSS ("Wed, 29 Jul 2026 15:00:56 +0000") and ISO 8601
from sitemaps ("2026-08-26T14:28:35.452Z"). Everything else is treated as unknown rather
than guessed at, because a wrong date silently distorts the timing score.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

_ISO_CLEAN = re.compile(r"\.\d+(?=Z|[+-]\d{2}:?\d{2}|$)")


def parse_published(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None

    # RFC 822 / RSS
    if "," in text:
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            dt = None
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    # ISO 8601, with or without fractional seconds and a Z suffix
    candidate = _ISO_CLEAN.sub("", text).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def age_days(value: str | None, *, now: datetime | None = None) -> float | None:
    """Days since publication, or None when the date cannot be read."""
    dt = parse_published(value)
    if dt is None:
        return None
    reference = now or datetime.now(UTC)
    delta = (reference - dt).total_seconds() / 86400.0
    return max(0.0, delta)
