"""LLM-facing tool schemas (JSON schema dicts; see Hermes plugin guide)."""

SCHEMAS = {
    "events_refresh": {
        "name": "events_refresh",
        "description": (
            "Fetch new events from configured sources (polite, cadence-limited). "
            "Run before digests/search if data may be stale. Returns per-source counts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": "Optional: refresh only this source id (see events_sources).",
                },
            },
        },
    },
    "events_search": {
        "name": "events_search",
        "description": (
            "Search ranked local events. Energy is first-class: tired = low effort nearby, "
            "normal, adventurous, escape (overnight trips). Party=family applies kid/dog "
            "filters; party=adults surfaces date-night/friends options."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free text (optional)."},
                "days_ahead": {"type": "number", "description": "Look-ahead window in days (default 7)."},
                "energy": {
                    "type": "string",
                    "enum": ["tired", "normal", "adventurous", "escape"],
                    "description": "How much effort the household has. Default normal.",
                },
                "party": {"type": "string", "enum": ["family", "adults"], "description": "Default family."},
                "include_kid_unknown": {
                    "type": "boolean",
                    "description": "Include kid-suitability-unknown events (default false).",
                },
                "free_only": {"type": "boolean", "description": "Only free events."},
                "max_results": {"type": "number", "description": "Default 10."},
            },
        },
    },
    "events_digest": {
        "name": "events_digest",
        "description": (
            "Build the plan-first weekend digest: one anchor per energy band "
            "(wiped / solid / adventure), alternates, whitespace note, rain swaps, "
            "numbered picks for one-line feedback. Run events_refresh first if stale."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "party": {"type": "string", "enum": ["family", "adults"], "description": "Default family."},
            },
        },
    },
    "events_feedback": {
        "name": "events_feedback",
        "description": (
            "Record feedback that teaches the ranker and tolerance curves. "
            "signals: interested | not_interested | went | dismissed | too_far | too_pricey | too_much_effort. "
            "effort_match: right_effort | too_much | too_little. mood: tired|normal|adventurous|escape."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_ref": {
                    "type": "string",
                    "description": "Event id, or the digest pick number like '#2' if you tracked it.",
                },
                "signal": {
                    "type": "string",
                    "enum": [
                        "interested",
                        "not_interested",
                        "went",
                        "dismissed",
                        "too_far",
                        "too_pricey",
                        "too_much_effort",
                    ],
                },
                "effort_match": {"type": "string", "enum": ["right_effort", "too_much", "too_little"]},
                "mood": {"type": "string", "enum": ["tired", "normal", "adventurous", "escape"]},
                "note": {"type": "string"},
            },
            "required": ["event_ref", "signal"],
        },
    },
    "events_calendar_ics": {
        "name": "events_calendar_ics",
        "description": "Generate an ICS file content for chosen event ids (hand to Hermes calendar skill, or save as .ics).",
        "parameters": {
            "type": "object",
            "properties": {"event_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["event_ids"],
        },
    },
    "events_ingest_email": {
        "name": "events_ingest_email",
        "description": (
            "Mine one email's text for event candidates (newsletters) or ticket receipts "
            "(commitment signals). Pass subject+body text; nothing else is stored."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["auto", "newsletter", "receipt"],
                    "description": "Default auto-detect.",
                },
            },
            "required": ["subject", "body"],
        },
    },
    "events_sources": {
        "name": "events_sources",
        "description": "List configured sources (id, name, connector, enabled, last refresh) or validate a candidate source URL.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "validate"], "description": "Default list."},
                "url": {
                    "type": "string",
                    "description": "validate: probe a URL for JSON-LD/RSS/ICS/list-HTML.",
                },
            },
        },
    },
    "events_share": {
        "name": "events_share",
        "description": "Copy-paste invite card for one event (forward to friends in any chat).",
        "parameters": {
            "type": "object",
            "properties": {"event_id": {"type": "string"}},
            "required": ["event_id"],
        },
    },
    "events_import_locations": {
        "name": "events_import_locations",
        "description": (
            "One-time Google Maps Timeline (Takeout) import. Pass 1: analyze a zip/folder/file "
            "path and return a plain-language summary. confirm=true applies: seeds drive "
            "tolerances, confirms home, backfills attended events. Raw GPS points are never stored."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to Takeout zip / Timeline.json / folder, or 'auto' for the imports folder.",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "false (default) = analyze only; true = apply + delete imported copy.",
                },
            },
        },
    },
    "events_forget_locations": {
        "name": "events_forget_locations",
        "description": "Delete all derived location data (visit counts, drive histogram, home-from-timeline). Feedback stays.",
        "parameters": {"type": "object", "properties": {}},
    },
}
