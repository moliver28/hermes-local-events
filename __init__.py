"""hermes-local-events — native Hermes Agent plugin.

Registers 12 tools, an /events slash command, a `local-events` CLI command,
four bundled skills (onboard / find-events / recap / adopt), and three
automation blueprints (Thursday digest, morning-of nudge, long-weekend prompt).
"""

from __future__ import annotations

import json

import sys
from pathlib import Path

_PLUGIN_ROOT = str(Path(__file__).resolve().parent)
if _PLUGIN_ROOT not in sys.path:
    # Package-mode loading (Hermes host): keep absolute imports valid
    # for schemas, tools, core.*, ingest.*, cli. Standalone mode already
    # has the repo root on sys.path via conftest.py / `python cli.py`.
    sys.path.insert(0, _PLUGIN_ROOT)

try:
    from schemas import SCHEMAS
    from tools import HANDLERS
except ImportError:
    from .schemas import SCHEMAS
    from .tools import HANDLERS

PLUGIN_NAME = "local-events"

AUTOMATION_BLUEPRINTS = [
    {
        "key": "thursday_digest",
        "title": "Weekend events digest",
        "description": "Plan-first weekend digest, organized by energy band, delivered Thursday evening.",
        "schedule_template": "{minute} {hour} * * 4",
        "prompt_template": (
            "Run events_refresh, then events_digest. Deliver the digest text as-is to me here. "
            "If fewer than 3 picks came back, say so honestly and suggest widening with "
            "events_search energy=adventurous."
        ),
        "slots": [
            {"key": "hour", "type": "time", "default": "20:00", "description": "Delivery time"},
            {"key": "minute", "type": "text", "default": "0", "hidden": True},
        ],
        "tags": ["events"],
    },
    {
        "key": "morning_nudge",
        "title": "Morning-of event nudge",
        "description": "Weather recheck, drive time, and what-to-bring for today's planned events.",
        "schedule_template": "30 8 * * 6,0",
        "prompt_template": (
            "Check today's calendar for events added from the events plugin. For each, run "
            "events_conditions on the event (rain %, air quality, alerts) and include the "
            "conditions line. Restate the effort line (drive, duration, what to bring for "
            "kids+dog). If AQI is 150+ or a severe alert is active, say plainly to skip "
            "strenuous outdoor plans and offer an indoor alternate via events_search energy=tired."
        ),
        "slots": [],
        "tags": ["events"],
    },
    {
        "key": "long_weekend_prompt",
        "title": "Long weekend escape prompt",
        "description": "Ahead of a 3-day weekend, offer escape-mode picks with booking lead times.",
        "schedule_template": "0 12 * * 1",
        "prompt_template": (
            "If a 3-day weekend is within 4 weeks, run events_search energy=escape days_ahead=35 "
            "and offer up to 3 escape options with booking-lead-time flags."
        ),
        "slots": [],
        "tags": ["events"],
    },
]


def register(ctx):
    """Entry point per Hermes plugin guide. Tools, slash command, CLI, skills,
    blueprints. Capability calls are guarded so older hosts load the tools."""
    try:
        from core.runtime import HermesRuntime
    except ImportError:  # loaded as a package by Hermes
        from .core.runtime import HermesRuntime

    rt = HermesRuntime(ctx)

    for name, handler in HANDLERS.items():
        ctx.register_tool(
            name=name,
            toolset="events",
            schema=SCHEMAS[name],
            handler=lambda args, _h=handler, _rt=rt, **kw: _h(args or {}, rt=_rt, **kw),
        )

    def _slash(args: str) -> str:
        args = (args or "").strip().lower()
        if args.startswith("digest") or not args:
            return json.loads(HANDLERS["events_digest"]({}, rt=rt)).get("text", "")
        if "tired" in args or "wiped" in args or "lazy" in args:
            return _fmt(json.loads(HANDLERS["events_search"]({"energy": "tired"}, rt=rt)))
        if "adventure" in args or "hike" in args or "big" in args:
            return _fmt(json.loads(HANDLERS["events_search"]({"energy": "adventurous"}, rt=rt)))
        if "adults" in args or "date night" in args:
            return _fmt(json.loads(HANDLERS["events_search"]({"party": "adults"}, rt=rt)))
        parts = args.split(maxsplit=1)
        days = 7
        if parts and parts[0].isdigit():
            days = int(parts[0])
        return _fmt(json.loads(HANDLERS["events_search"]({"days_ahead": days}, rt=rt)))

    try:
        ctx.register_command(
            name="events",
            handler=_slash,
            description="Weekend digest + event search (/events, /events tired, /events adults)",
        )
    except Exception:
        pass

    try:
        ctx.register_cli_command(
            name="local-events",
            help="Refresh, search, digest, and manage local events",
            handler=_cli_entry,
        )
    except Exception:
        pass

    for skill_name, skill_path in (
        ("local-events-onboard", "skills/onboard/SKILL.md"),
        ("local-events-find", "skills/find-events/SKILL.md"),
        ("local-events-recap", "skills/recap/SKILL.md"),
        ("local-events-adopt", "skills/adopt/SKILL.md"),
    ):
        fn = getattr(ctx, "register_skill", None)
        if callable(fn):
            try:
                fn(skill_name, str(__import__("pathlib").Path(__file__).parent / skill_path))
            except Exception:
                pass

    reg_bp = getattr(ctx, "register_automation_blueprint", None)
    if callable(reg_bp):
        for bp in AUTOMATION_BLUEPRINTS:
            try:
                reg_bp(f"{PLUGIN_NAME}:{bp['key']}", **{k: v for k, v in bp.items() if k != "key"})
            except Exception:
                pass


def _fmt(result: dict) -> str:
    if not result.get("ok"):
        return f"events: {result.get('error', 'error')}"
    lines = []
    for ev in result.get("events", []):
        line = f"• **{ev['title']}**"
        if ev.get("start"):
            line += f" — {ev['start'][:16].replace('T', ' ')}"
        line += f"\n  {ev.get('effort', '')} · kid:{ev.get('kid')} dog:{ev.get('dog')}"
        if ev.get("why"):
            line += f"\n  why: {', '.join(ev['why'])}"
        lines.append(line + f"\n  id: {ev['id']}")
    if result.get("excluded_kid_unknown"):
        lines.append(
            f"_{result['excluded_kid_unknown']} events skipped (kid-suitability unknown; "
            "ask for include_kid_unknown)_"
        )
    return "\n\n".join(lines) if lines else "Nothing found in that window — try a wider days_ahead."


def _cli_entry(args: str) -> str:
    """Inside-Hermes CLI passthrough; standalone use is `python cli.py`."""
    import contextlib
    import io

    try:
        from cli import main
    except ImportError:  # loaded as a package by Hermes
        from .cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main((args or "").split())
    return buf.getvalue() or "done"
