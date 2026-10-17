#!/usr/bin/env python
"""Standalone CLI — everything the plugin does, without Hermes running.

python cli.py refresh [--source ID]
python cli.py search [--energy tired|normal|adventurous|escape] [--party family|adults] [--days N] [--text Q]
python cli.py digest [--party family|adults]
python cli.py feedback <event_ref> <signal> [--effort right_effort|too_much|too_little] [--mood M]
python cli.py ics <event_id> [<event_id> ...]
python cli.py import-locations <path> [--confirm]
python cli.py sources [--validate URL]
python cli.py stats
"""

from __future__ import annotations

import argparse
import json
import sys

from core.runtime import LocalRuntime
from tools import HANDLERS


def main(argv: list[str] | None = None) -> int:
    rt = LocalRuntime()
    parser = argparse.ArgumentParser(prog="local-events")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("refresh")
    p.add_argument("--source", dest="source_id")

    p = sub.add_parser("search")
    p.add_argument("--energy", default="normal")
    p.add_argument("--party", default="family")
    p.add_argument("--days", type=float, default=7)
    p.add_argument("--text", default="")
    p.add_argument("--free-only", action="store_true")
    p.add_argument("--max", type=int, default=10)

    p = sub.add_parser("digest")
    p.add_argument("--party", default="family")

    p = sub.add_parser("feedback")
    p.add_argument("event_ref")
    p.add_argument("signal")
    p.add_argument("--effort", dest="effort_match")
    p.add_argument("--mood", dest="mood")

    p = sub.add_parser("ics")
    p.add_argument("event_ids", nargs="+")

    p = sub.add_parser("import-locations")
    p.add_argument("path", nargs="?", default="auto")
    p.add_argument("--confirm", action="store_true")

    p = sub.add_parser("sources")
    p.add_argument("--validate", dest="url")

    sub.add_parser("stats")

    args = parser.parse_args(argv)

    if args.cmd == "refresh":
        out = HANDLERS["events_refresh"]({"source_id": args.source_id}, rt=rt)
    elif args.cmd == "search":
        out = HANDLERS["events_search"](
            {
                "energy": args.energy,
                "party": args.party,
                "days_ahead": args.days,
                "query": args.text,
                "free_only": args.free_only,
                "max_results": args.max,
            },
            rt=rt,
        )
    elif args.cmd == "digest":
        out = HANDLERS["events_digest"]({"party": args.party}, rt=rt)
        data = json.loads(out)
        print(data.get("text", out))
        return 0
    elif args.cmd == "feedback":
        out = HANDLERS["events_feedback"](
            {
                "event_ref": args.event_ref,
                "signal": args.signal,
                "effort_match": args.effort,
                "mood": args.mood,
            },
            rt=rt,
        )
    elif args.cmd == "ics":
        out = HANDLERS["events_calendar_ics"]({"event_ids": args.event_ids}, rt=rt)
    elif args.cmd == "import-locations":
        out = HANDLERS["events_import_locations"]({"path": args.path, "confirm": args.confirm}, rt=rt)
    elif args.cmd == "sources":
        out = HANDLERS["events_sources"](
            {"action": "validate", "url": args.url} if args.url else {"action": "list"}, rt=rt
        )
    else:
        from core import feedback as fb
        from core.store import Store

        out = json.dumps(fb.metrics(Store(rt.db_path)), indent=2)

    data = json.loads(out)
    if args.cmd == "search" and data.get("ok"):
        print(json.dumps(data, indent=2)[:6000])
    else:
        print(out[:6000])
    return 0 if data.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
