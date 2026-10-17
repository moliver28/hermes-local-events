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
                "like": {
                    "type": "string",
                    "description": (
                        "Event id or digest pick number — return events most similar to it "
                        "(semantic; requires the optional model2vec extra)."
                    ),
                },
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
                "max_total": {
                    "type": "number",
                    "description": (
                        "Hard cap on the party-total cost (per-person prices × your party, kid "
                        "discounts applied). Events with unknown prices are excluded under a cap."
                    ),
                },
                "budget": {
                    "type": "number",
                    "description": (
                        "Override the budget center for this search (party total). Without it, the "
                        "mood's budget applies: tired ≈ 0.6×, normal 1×, adventurous ≈ 1.8×, escape ≈ 3× "
                        "the household baseline, rescaled by the last-two-weeks spend trend."
                    ),
                },
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
    "events_conditions": {
        "name": "events_conditions",
        "description": (
            "Best-effort conditions for one event: rain % at start (Open-Meteo), US AQI / "
            "wildfire smoke (Open-Meteo air quality), and active NWS alerts. Each field is "
            "None when unavailable (offline-safe). Use for morning-of checks and outdoor picks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_ref": {"type": "string", "description": "Event id or digest pick number."},
            },
            "required": ["event_ref"],
        },
    },
    "events_ingest_content": {
        "name": "events_ingest_content",
        "description": (
            "Ingest page content YOU fetched (web_extract, browser_exec, or pasted text) so "
            "agent-found events enter the same pipeline as scraped ones. Pass the raw HTML, "
            "JSON-LD, ICS, or RSS text plus its source URL. Never fetch through this tool — "
            "fetch with your web tools first, then hand the body here."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Raw page body / JSON-LD / ICS / RSS text."},
                "url": {
                    "type": "string",
                    "description": "Source URL the content came from (http/s, public).",
                },
                "source_id": {
                    "type": "string",
                    "description": "Stable source label; defaults to 'agent'. Use the registered source id when known.",
                },
                "format": {
                    "type": "string",
                    "enum": ["auto", "jsonld", "html", "ics", "rss"],
                    "description": "Default auto-detects.",
                },
                "selectors": {
                    "type": "object",
                    "description": "Optional html_list selector overrides: item, title, link, date, description, venue_name, venue_type, category.",
                },
            },
            "required": ["content"],
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
        "description": (
            "List configured sources (shipped + user-registered), validate a candidate URL, "
            "register a discovered source (onboarding/source discovery), or forget one. "
            "register: JS-walled sites may fail the plain probe but still register with via=browser."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "validate", "register", "forget"],
                    "description": "Default list.",
                },
                "url": {
                    "type": "string",
                    "description": "validate/register: the source URL (http/s, public host).",
                },
                "source_id": {
                    "type": "string",
                    "description": "register/forget: stable id; register derives one from the URL when omitted.",
                },
                "name": {"type": "string", "description": "register: display name."},
                "connector": {
                    "type": "string",
                    "enum": ["rss", "ics", "jsonld", "html_list", "tacomaweekly", "ticketmaster"],
                    "description": "register: override; default is auto-suggested from the probe.",
                },
                "cadence_hours": {"type": "number", "description": "register: refresh cadence (default 24)."},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "register: tags (default ['user']).",
                },
            },
        },
    },
    "events_budget": {
        "name": "events_budget",
        "description": (
            "Show the household money model: baseline per-outing budget, per-mood budgets "
            "(wiped/solid/adventure/escape), kid pricing rules, and the rolling spend trend from "
            "attended outings. 'too pricey' feedback tightens a mood's budget; splurges loosen it."
        ),
        "parameters": {"type": "object", "properties": {}},
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
