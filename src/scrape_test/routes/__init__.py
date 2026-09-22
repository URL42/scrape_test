"""HTTP routes, split by what they are about.

They were one 482-line module, which made it hard to see where anything lived. Each
router owns one area and shares only the HTTP client and background-task handles, via
`deps`.
"""

from __future__ import annotations

from . import company, digest, scan, search
from .deps import client, running, set_client

__all__ = ["client", "company", "digest", "running", "scan", "search", "set_client"]
