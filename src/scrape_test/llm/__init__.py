"""Pluggable LLM backends, mirroring the switchable news sources.

DeepSeek is the default: it is roughly an order of magnitude cheaper than Claude for this
workload, which matters when a brief is generated per prospect. Override with
SCRAPE_TEST_LLM=claude.
"""

from __future__ import annotations

import os

from .base import LLMProvider, LLMUnavailable, coerce, schema_instructions
from .claude import ClaudeProvider
from .deepseek import DeepSeekProvider

PROVIDERS: dict[str, LLMProvider] = {p.key: p for p in (DeepSeekProvider(), ClaudeProvider())}
DEFAULT_PROVIDER = "deepseek"


def get_provider(key: str | None = None) -> LLMProvider:
    name = key or os.environ.get("SCRAPE_TEST_LLM", DEFAULT_PROVIDER)
    if name not in PROVIDERS:
        raise LLMUnavailable(f"unknown LLM provider {name!r}; have {sorted(PROVIDERS)}")
    return PROVIDERS[name]


__all__ = [
    "DEFAULT_PROVIDER",
    "PROVIDERS",
    "ClaudeProvider",
    "DeepSeekProvider",
    "LLMProvider",
    "LLMUnavailable",
    "coerce",
    "get_provider",
    "schema_instructions",
]
