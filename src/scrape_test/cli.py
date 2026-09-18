"""Command line entry points."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .db import init_db, session
from .http import make_client
from .scoring import RULES_VERSION, compute_score
from .scoring.score import rescore_all, store_score
from .yc.directory import company_dict, refresh_directory, resolve
from .yc.fingerprint import get_fingerprint
from .yc.jobs import get_jobs, stack_from_jobs


async def _cmd_refresh(args: argparse.Namespace) -> int:
    init_db()
    async with make_client() as client:
        with session() as conn:
            n = await refresh_directory(conn, client, force=args.force)
    print(f"directory: {n} companies")
    return 0


async def _cmd_lookup(args: argparse.Namespace) -> int:
    init_db()
    async with make_client() as client:
        with session() as conn:
            await refresh_directory(conn, client)
            row, suggestions = resolve(conn, args.company)
            if row is None:
                print(f"{args.company!r} not found in the YC directory.")
                if suggestions:
                    print("did you mean:", ", ".join(suggestions))
                return 1
            c = company_dict(row)
            jobs, _ = await get_jobs(conn, client, c["id"], c["slug"], force=args.refresh)
            fp, _ = await get_fingerprint(conn, client, c["id"], c["website"], force=args.refresh)
            result = compute_score(c, jobs, fp)
            store_score(conn, c["id"], result)

    if args.json:
        print(
            json.dumps(
                {
                    "company": c,
                    "jobs": jobs,
                    "stack": stack_from_jobs(jobs),
                    "fingerprint": fp,
                    "score": result.as_dict(),
                },
                indent=2,
                default=str,
            )
        )
        return 0

    print(f"\n{c['name']}  ({c['batch']}, {c['status']}, team {c['team_size']})")
    print(f"  {c['one_liner'] or ''}")
    print(f"\n  Atlassian fit: {result.total:.1f}/100   confidence: {result.confidence}")
    for s in result.signals:
        if abs(s.points) > 0.01:
            print(f"    {s.points:+6.1f}  {s.label:22} {s.reason}")
    stack = stack_from_jobs(jobs)
    if stack:
        print("\n  Stack from job postings:")
        print("    " + ", ".join(f"{s['skill']} ({s['mentions']})" for s in stack))
    detected = (fp or {}).get("detected") or {}
    if detected:
        print("\n  Detected on website:")
        for cat, items in sorted(detected.items()):
            rendered = ", ".join(f"{i['product']} [{i['confidence']}]" for i in items)
            print(f"    {cat:16} {rendered}")
    if jobs:
        print(f"\n  Open roles ({len(jobs)}):")
        for j in jobs:
            role = (j["pretty_role"] or "?")[:12]
            title = (j["title"] or "")[:52]
            print(f"    {role:12} {title:52} {j['salary_range'] or ''}")
    return 0


def _cmd_rescore(args: argparse.Namespace) -> int:
    """Recompute all cached scores with current weights. Pure local work."""
    init_db()
    with session() as conn:
        updated = rescore_all(conn)
    print(f"rescored {updated} companies with rules {RULES_VERSION}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("scrape_test.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scrape-test", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("refresh", help="pull the YC company directory into SQLite")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=_cmd_refresh, is_async=True)

    p = sub.add_parser("lookup", help="look up one company")
    p.add_argument("company")
    p.add_argument("--refresh", action="store_true", help="bypass cache")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_lookup, is_async=True)

    p = sub.add_parser("rescore", help="recompute cached scores after editing weights")
    p.set_defaults(fn=_cmd_rescore, is_async=False)

    p = sub.add_parser("serve", help="run the web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(fn=_cmd_serve, is_async=False)

    args = parser.parse_args(argv)
    if args.is_async:
        return asyncio.run(args.fn(args))
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
