"""Central configuration. Everything tunable that isn't a scoring weight lives here."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("SCRAPE_TEST_DATA", PROJECT_ROOT / "data"))
DB_PATH = DATA_DIR / "scrape_test.db"
WEB_DIR = PROJECT_ROOT / "web"

# Identify ourselves honestly rather than impersonating a browser where we don't need to.
# YC's robots.txt allows /companies/<slug>/jobs; we stay well inside it.
CONTACT = os.environ.get("SCRAPE_TEST_CONTACT", "scrape-test (personal research tool)")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 +{CONTACT}"
)

HTTP_TIMEOUT = 20.0
HTTP_RETRIES = 3
MAX_BODY_BYTES = 2_000_000  # cap homepage reads; some marketing sites are enormous

# Per-host minimum seconds between requests. GDELT explicitly asks for 1 req / 5s.
HOST_THROTTLE: dict[str, float] = {
    "api.gdeltproject.org": 5.0,
    "www.ycombinator.com": 0.4,
    "news.google.com": 0.5,
}
DEFAULT_THROTTLE = 0.0

# Cache lifetimes (seconds)
TTL_DIRECTORY = 24 * 3600
TTL_JOBS = 12 * 3600
TTL_FINGERPRINT = 7 * 24 * 3600
TTL_POSTS = 24 * 3600

YC_DIRECTORY_URL = "https://yc-oss.github.io/api/companies/all.json"
YC_COMPANY_URL = "https://www.ycombinator.com/companies/{slug}"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
