"""Feedback ledger → profile updates. Receipts and calendar confirms are
high-weight positives only (never negatives when absent)."""

from __future__ import annotations

from datetime import datetime

from . import profile as P
from .models import Event, Feedback, Household
from .store import Store

VALID_SIGNALS = {
    "interested",
    "not_interested",
    "went",
    "dismissed",
    "too_far",
    "too_pricey",
    "too_much_effort",
}


def record_feedback(
    hh: Household,
    store: Store,
    event_id: str,
    signal: str,
    effort_match: str | None = None,
    mood: str | None = None,
    note: str = "",
    band: str | None = None,
) -> dict:
    if signal not in VALID_SIGNALS:
        return {"ok": False, "error": f"signal must be one of {sorted(VALID_SIGNALS)}"}
    ev = store.get_event(event_id)
    if not ev:
        return {"ok": False, "error": f"unknown event {event_id}"}
    fb = Feedback(
        event_id=event_id, signal=signal, effort_match=effort_match, mood=mood, band=band, note=note
    )
    store.add_feedback(fb)

    from .effort import effort_score
    from .models import now_utc

    venue = store.get_venue(ev.venue_id) if ev.venue_id else None
    computed = effort_score(ev, hh, venue, None, "unknown", now_utc())
    keys = P.facet_keys(ev, computed.effort_band, P.price_band_for(ev), P.timeslot_for(ev))
    P.update_facets(hh, keys, fb)
    P.update_momentum(hh, ev.category, fb.is_positive)
    from .cost import breakdown as cost_breakdown

    party_total = cost_breakdown(ev, hh).total
    drive = computed.drive_minutes
    if (
        signal in ("went", "too_far", "too_much_effort")
        and drive is None
        and venue
        and venue.lat is not None
        and hh.home_lat is not None
    ):
        from .geo import estimate_drive_minutes, haversine_km

        drive = estimate_drive_minutes(haversine_km(hh.home_lat, hh.home_lon, venue.lat, venue.lon))
    P.update_tolerance(hh, fb, drive, computed.effort_band, party_total=party_total)
    store.kv_set("household", hh.model_dump_json())
    return {"ok": True, "signal": signal, "event": ev.title}


def confirm_attendance(
    hh: Household, store: Store, event_id: str, effort_match: str | None = None, note: str = ""
) -> dict:
    """Calendar-confirmed or user-confirmed attendance — strongest signal."""
    return record_feedback(hh, store, event_id, "went", effort_match=effort_match, note=note)


def recap_items(store: Store, now: datetime | None = None) -> list[Event]:
    now = now or datetime.now()
    from .models import now_utc

    items = store.pending_recap(now_utc().isoformat())
    return [ev for _, ev in items]


def record_backfill(hh: Household, store: Store, event: Event) -> dict:
    """Location-confirmed attendance backfill (Google Timeline import)."""
    fb = Feedback(event_id=event.id, signal="went", note="location-confirmed (timeline import)")
    store.add_feedback(fb)
    keys = P.facet_keys(event, None, P.price_band_for(event), P.timeslot_for(event))
    P.update_facets(hh, keys, fb)
    P.update_momentum(hh, event.category, True)
    return {"ok": True, "event": event.title}


def metrics(store: Store) -> dict:
    rows = store.conn.execute("SELECT signal, COUNT(*) c FROM feedback GROUP BY signal").fetchall()
    counts = {r["signal"]: r["c"] for r in rows}
    delivered = sum(
        counts.get(s, 0)
        for s in (
            "interested",
            "not_interested",
            "went",
            "dismissed",
            "too_far",
            "too_pricey",
            "too_much_effort",
        )
    )
    engaged = sum(counts.get(s, 0) for s in ("interested", "went"))
    dismissed = sum(
        counts.get(s, 0) for s in ("not_interested", "dismissed", "too_far", "too_pricey", "too_much_effort")
    )
    detail = store.conn.execute("SELECT signal, effort_match, mood, band FROM feedback").fetchall()
    rated = [r for r in detail if r["signal"] == "went" and r["effort_match"]]
    effort_match_rate = (
        round(sum(1 for r in rated if r["effort_match"] == "right_effort") / len(rated), 3) if rated else None
    )
    banded = [r for r in detail if r["signal"] in ("went", "interested") and r["band"] and r["mood"]]
    band_mood_match_rate = (
        round(sum(1 for r in banded if r["band"] == r["mood"]) / len(banded), 3) if banded else None
    )
    return {
        "feedback_total": delivered,
        "engagement_rate": round(engaged / delivered, 3) if delivered else None,
        "dismiss_rate": round(dismissed / delivered, 3) if delivered else None,
        "effort_match_rate": effort_match_rate,
        "band_mood_match_rate": band_mood_match_rate,
        "went": counts.get("went", 0),
        "signals": counts,
        "alarm_dismiss_rate_gt": 0.15,
    }
