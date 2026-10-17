"""Tool handlers. Contract per Hermes plugin guide: (args: dict, **kwargs) -> str,
ALWAYS return a JSON string, NEVER raise. Thin adapters over the core engine."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from core import digest as digest_mod
from core import feedback as feedback_mod
from core.calendar import events_to_ics
from core.email_ingest import extract_candidates, extract_receipt
from core.locations import analyze as locations_analyze
from core.locations import apply_import, cleanup_imported_files
from core.models import Energy, Household, Party, now_utc
from core.net import safe_probe
from core.rank import Query, rank
from core.runtime import home_coord
from core.share import invite_card
from core.store import Store
from ingest import runner as ingest_runner


def _store(rt) -> Store:
    return Store(rt.db_path)


def _household(rt, store: Store) -> Household:
    raw = store.kv_get("household")
    if raw:
        try:
            hh = Household.model_validate_json(raw)
        except Exception:
            hh = Household()
    else:
        hh = Household()
    # settings are the source of truth for declared facts; kv holds learned state
    lat, lon = home_coord(rt)
    if lat is not None:
        hh.home_lat, hh.home_lon = lat, lon
    hh.home_label = rt.get_setting("home_label", hh.home_label) or hh.home_label
    ages = rt.get_setting("kid_ages", "")
    if isinstance(ages, str) and ages.strip():
        hh.kid_ages = [int(a) for a in ages.replace(",", " ").split() if a.strip().isdigit()]
    hh.has_dog = bool(rt.get_setting("has_dog", hh.has_dog))
    hh.dog_size = str(rt.get_setting("dog_size", hh.dog_size))
    hh.transport_mode = str(rt.get_setting("transport_mode", hh.transport_mode))
    try:
        hh.budget_per_outing = float(rt.get_setting("budget_per_outing", hh.budget_per_outing))
    except (TypeError, ValueError):
        pass
    return hh


def _resolve_ref(store: Store, rt, ref: str) -> str | None:
    """Accept full ids or digest pick numbers ('#2') via the last-digest kv note."""
    if not ref:
        return None
    ref = str(ref).strip()
    if ref.startswith("#") or ref.isdigit():
        picks = _last_picks(store)
        n = int(ref.lstrip("#"))
        for p in picks:
            if p["n"] == n:
                return p["event_id"]
        return None
    return ref if store.get_event(ref) else None


def _last_picks(store: Store) -> list[dict]:
    try:
        return json.loads(store.kv_get("last_digest_picks") or "[]")
    except json.JSONDecodeError:
        return []


# --------------------------------------------------------------------- tools


def events_refresh(args: dict, rt=None, **kwargs) -> str:
    store = _store(rt)
    try:
        report = ingest_runner.refresh(store, rt, [args["source_id"]] if args.get("source_id") else None)
        return json.dumps(
            {
                "ok": True,
                "ran": [r if isinstance(r, dict) else vars(r) for r in report.ran],
                "skipped": report.skipped,
                "new_events": report.total_new,
                "canonical_events": report.total_merged,
                "total_in_store": store.count(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


def events_search(args: dict, rt=None, **kwargs) -> str:
    store = _store(rt)
    hh = _household(rt, store)
    now = now_utc()
    q = Query(
        start=now,
        end=now + timedelta(days=float(args.get("days_ahead", 7))),
        party=Party(args.get("party", "family")),
        energy=Energy(args.get("energy", "normal")),
        strict_kid=not args.get("include_kid_unknown", False),
        free_only=bool(args.get("free_only", False)),
        text=args.get("query", ""),
        home=(hh.home_lat, hh.home_lon) if hh.home_lat is not None else None,
        max_results=int(args.get("max_results", 10)),
    )
    events = store.events_between(q.start.isoformat(), q.end.isoformat())
    if q.text:
        fts = {e.id for e in store.search_text(q.text)}
        events = [e for e in events if e.id in fts] or store.search_text(q.text)
    scored = rank(events, q, hh, store)
    out = []
    for s in scored[: q.max_results]:
        from core.effort import effort_summary

        out.append(
            {
                "id": s.event.id,
                "title": s.event.title,
                "start": s.event.start.isoformat() if s.event.start else None,
                "url": s.event.url,
                "effort": effort_summary(s.event, s.computed),
                "effort_band": s.computed.effort_band,
                "kid": s.event.kid.value,
                "dog": s.event.dog.value,
                "audience": s.event.audience.value,
                "score": round(s.score, 3),
                "why": s.reasons,
            }
        )
    unknown_kid = sum(1 for e in events if e.kid.value == "unknown")
    return json.dumps(
        {
            "ok": True,
            "count": len(out),
            "events": out,
            "excluded_kid_unknown": unknown_kid if q.strict_kid else 0,
            "note": "pass include_kid_unknown=true to see unchecked events"
            if unknown_kid and q.strict_kid
            else "",
        }
    )


def events_digest(args: dict, rt=None, **kwargs) -> str:
    store = _store(rt)
    hh = _household(rt, store)
    party = Party(args.get("party", "family"))
    result = digest_mod.build_digest(hh, store, party=party)
    store.kv_set("last_digest_picks", json.dumps(result["picks"]))
    store.kv_set("last_digest_at", now_utc().isoformat())
    return json.dumps({"ok": True, **result})


def events_feedback(args: dict, rt=None, **kwargs) -> str:
    store = _store(rt)
    hh = _household(rt, store)
    eid = _resolve_ref(store, rt, args.get("event_ref", ""))
    if not eid:
        return json.dumps({"ok": False, "error": "unknown event_ref; give an event id or digest pick number"})
    result = feedback_mod.record_feedback(
        hh,
        store,
        eid,
        args.get("signal", ""),
        effort_match=args.get("effort_match"),
        mood=args.get("mood"),
        note=args.get("note", ""),
    )
    return json.dumps(result)


def events_calendar_ics(args: dict, rt=None, **kwargs) -> str:
    store = _store(rt)
    events = []
    for ref in args.get("event_ids", []):
        eid = _resolve_ref(store, rt, str(ref))
        if eid:
            ev = store.get_event(eid)
            if ev:
                events.append(ev)
    if not events:
        return json.dumps({"ok": False, "error": "no valid event_ids"})
    return json.dumps({"ok": True, "ics": events_to_ics(events)})


def events_ingest_email(args: dict, rt=None, **kwargs) -> str:
    subject, body = args.get("subject", ""), args.get("body", "")
    kind = args.get("kind", "auto")
    receipt = extract_receipt(subject, body) if kind in ("auto", "receipt") else None
    if receipt:
        return json.dumps(
            {
                "ok": True,
                "kind": "receipt",
                "receipt": receipt.__dict__,
                "hint": "match title to an event, then events_feedback signal=went",
            }
        )
    candidates = extract_candidates(subject, body) if kind in ("auto", "newsletter") else []
    return json.dumps(
        {
            "ok": True,
            "kind": "newsletter",
            "candidates": [c.__dict__ for c in candidates],
            "hint": "verify before surfacing; these are raw candidates",
        }
    )


def events_sources(args: dict, rt=None, **kwargs) -> str:
    sources = ingest_runner.load_sources()
    if args.get("action") == "validate":
        probe = safe_probe(args.get("url", ""))
        if not probe.get("ok"):
            return json.dumps({"ok": False, "url": args.get("url", ""), "error": probe.get("error")})
        text = probe["text"].lower()
        probes = {
            "http_status": probe["status"],
            "jsonld": ("application/ld+json" in text or "application/ld+json" in probe["text"].lower())
            and '"event"' in text,
            "rss": ("<rss" in text[:500]) or ("<feed" in text[:500]),
            "ics": "begin:vcalendar" in text[:2000],
            "list_html": any(sel in text for sel in ("<article", 'class="event', "calendar")),
        }
        suggested = (
            "jsonld"
            if probes["jsonld"]
            else "ics"
            if probes["ics"]
            else "rss"
            if probes["rss"]
            else "html_list"
            if probes["list_html"]
            else None
        )
        return json.dumps(
            {"ok": True, "url": args.get("url", ""), "probes": probes, "suggested_connector": suggested}
        )
    try:
        last = json.loads(_store(rt).kv_get("last_refresh") or "{}")
    except json.JSONDecodeError:
        last = {}
    return json.dumps(
        {
            "ok": True,
            "sources": [
                {
                    "id": s.get("id"),
                    "name": s.get("name"),
                    "connector": s.get("connector"),
                    "enabled": s.get("enabled", True),
                    "tags": s.get("tags", []),
                    "last_refresh": last.get(s.get("id")),
                }
                for s in sources
            ],
        }
    )


def events_share(args: dict, rt=None, **kwargs) -> str:
    store = _store(rt)
    eid = _resolve_ref(store, rt, str(args.get("event_id", "")))
    ev = store.get_event(eid) if eid else None
    if not ev:
        return json.dumps({"ok": False, "error": "unknown event_id"})
    return json.dumps({"ok": True, "card": invite_card(ev)})


def events_import_locations(args: dict, rt=None, **kwargs) -> str:
    store = _store(rt)
    hh = _household(rt, store)
    path = args.get("path", "auto")
    if path in ("auto", ""):
        path = rt.imports_dir
        if not any(Path(path).iterdir()):
            return json.dumps(
                {"ok": False, "error": f"drop your Takeout zip into {path} (or pass an explicit path)"}
            )
    try:
        analysis = locations_analyze(path, store)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"parse failed: {exc}"})
    if not args.get("confirm"):
        return json.dumps(
            {
                "ok": True,
                "stage": "analysis",
                **_location_summary(analysis, hh),
                "next": "re-run with confirm=true to apply (imported copy is deleted)",
            }
        )
    applied = apply_import(hh, store, analysis)
    try:
        cleaned = cleanup_imported_files(path)
    except Exception:
        cleaned = 0
    return json.dumps(
        {
            "ok": True,
            "stage": "applied",
            **_location_summary(analysis, hh),
            "applied": applied,
            "cleaned_paths": cleaned,
        }
    )


def _location_summary(analysis: dict, hh: Household) -> dict:
    home = analysis.get("home_guess") or {}
    return {
        "visits_found": analysis.get("visits", 0),
        "distinct_places": analysis.get("places", 0),
        "drives_found": analysis.get("drives", 0),
        "median_drive_minutes": analysis.get("median_drive_min"),
        "home_guess": f"{home.get('lat'):.3f}, {home.get('lon'):.3f} ({home.get('visits')} visits)"
        if home
        else None,
        "events_matched_as_attended": len(analysis.get("event_matches", [])),
        "matched_titles": [m["title"] for m in analysis.get("event_matches", [])[:5]],
    }


def events_forget_locations(args: dict, rt=None, **kwargs) -> str:
    store = _store(rt)
    store.clear_location_data()
    hh = _household(rt, store)
    store.kv_set("household", hh.model_dump_json())
    return json.dumps({"ok": True, "forgotten": "visit counts + drive histogram cleared; feedback retained"})


HANDLERS = {
    "events_refresh": events_refresh,
    "events_search": events_search,
    "events_digest": events_digest,
    "events_feedback": events_feedback,
    "events_calendar_ics": events_calendar_ics,
    "events_ingest_email": events_ingest_email,
    "events_sources": events_sources,
    "events_share": events_share,
    "events_import_locations": events_import_locations,
    "events_forget_locations": events_forget_locations,
}
