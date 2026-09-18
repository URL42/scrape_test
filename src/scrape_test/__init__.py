"""YC prospecting + current-events scraper."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

__version__ = "0.1.0"

# Load .env here, not in config.py. This module is the only thing guaranteed to run on
# every import path - `scrape_test.brief` and the `llm` package never import config, so
# loading there meant secrets silently failed to load for CLI and library callers while
# appearing to work through the API (which imports config for WEB_DIR).
# override=False so a real environment variable always beats the file.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
