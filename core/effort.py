"""Effort engine — the system's first-class dimension.

Static components (ingest): duration, physical intensity, overnight, indoor/outdoor.
Dynamic components (query): drive time, nap overlap, planning lead, then a
party-relative effort_score/band. Bands are *relative to tolerance curves*:
the same event is "low" for eager adults and "high" with a nap-time toddler.
"""

from __future__ import annotations

import re
from datetime import datetime

from .models import EffortComputed, EffortState, Energy, Event, Household, Venue, now_utc

try:  # yaml only needed where taxonomy loads; keep import local-friendly
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


def load_taxonomy(path=None) -> dict:
    import pathlib

    p = (
        pathlib.Path(path)
        if path
        else pathlib.Path(__file__).resolve().parent.parent / "data" / "taxonomy.yaml"
    )
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


_TAX = None


def tax() -> dict:
    global _TAX
    if _TAX is None:
        _TAX = load_taxonomy()
    return _TAX


# ---------------------------------------------------------------- static side


def infer_intensity(text: str, venue_type: str = "") -> tuple[float | None, EffortState]:
    text_l = (text or "").lower()
    for row in tax()["intensity_keywords"]:
        if re.search(row["re"], text_l):
            return float(row["v"]), EffortState.COMPUTED
    prior = tax()["venue_type_priors"].get(venue_type)
    if prior:
        return float(prior[0]), EffortState.LIKELY
    return None, EffortState.UNKNOWN


def infer_overnight(text: str, duration_hours: float | None) -> bool:
    if re.search(tax()["overnight_keywords"], (text or "").lower()):
        return True
    return bool(duration_hours and duration_hours >= 8)


def infer_indoor_outdoor(venue_type: str, text: str = "") -> str:
    prior = tax()["venue_type_priors"].get(venue_type)
    if prior:
        return prior[1]
    t = (text or "").lower()
    if "outdoor" in t or "outside" in t:
        return "outdoor"
    if "indoor" in t:
        return "indoor"
    return "unknown"


def infer_stroller(text: str) -> bool | None:
    if re.search(tax()["stroller_keywords"], (text or "").lower()):
        return True
    return None


def static_effort(event: Event, venue: Venue | None) -> Event:
    """Fill event.effort static fields in place (ingest step)."""
    e = event.effort
    if event.start and event.end:
        e.duration_hours = round((event.end - event.start).total_seconds() / 3600, 2)
    venue_type = (venue.venue_type if venue else "") or ""
    text = f"{event.title} {event.description} {venue.name if venue else ''}"
    intensity, state = infer_intensity(text, venue_type)
    e.physical_intensity, e.intensity_state = intensity, state
    e.overnight = infer_overnight(text, e.duration_hours)
    e.indoor_outdoor = infer_indoor_outdoor(venue_type, text)
    e.stroller_friendly = infer_stroller(text)
    return event


# --------------------------------------------------------------- dynamic side


def nap_overlap_minutes(start: datetime, end: datetime | None, nap_windows: list[list[str]]) -> float:
    """Minutes of the event overlapping declared nap windows (same-day, HH:MM)."""
    if not nap_windows or not start:
        return 0.0
    total = 0.0
    end = end or start
    for win in nap_windows:
        try:
            h1, m1 = (int(x) for x in win[0].split(":"))
            h2, m2 = (int(x) for x in win[1].split(":"))
        except (ValueError, IndexError):
            continue
        ns = start.replace(hour=h1, minute=m1, second=0, microsecond=0)
        ne = start.replace(hour=h2, minute=m2, second=0, microsecond=0)
        overlap = (min(end, ne) - max(start, ns)).total_seconds()
        total += max(0.0, overlap) / 60.0
    return round(total, 1)


def kid_logistics_penalty(event: Event, hh: Household) -> float:
    """0..1 — nap overlap dominates; very young kids add baseline friction."""
    if not hh.kid_ages:
        return 0.0
    pen = 0.0
    if event.start:
        end = event.end
        if hh.nap_windows:
            ov = nap_overlap_minutes(event.start, end, hh.nap_windows)
            pen += min(1.0, ov / 60.0)
        youngest = min(hh.kid_ages)
        pen += 0.25 if youngest <= 2 else 0.10 if youngest <= 4 else 0.0
    return min(1.0, pen)


def planning_lead_penalty(days: float | None, booking_required: bool) -> float:
    """0..1 — too-little-lead is effortful (scramble), too-much-lead for escape needs booking."""
    if days is None:
        return 0.5 if booking_required else 0.2
    if booking_required and days < 3:
        return 0.9
    if days < 1:
        return 0.7
    if days > 60 and booking_required:
        return 0.5
    return 0.05


def cost_per_party(event: Event, hh: Household) -> float:
    adults = 2
    kids = len(hh.kid_ages)
    n = adults + kids
    base = event.price_min if event.price_min is not None else 0.0
    return base * n


def effort_score(
    event: Event,
    hh: Household,
    venue: Venue | None,
    drive_minutes: float | None,
    drive_state: EffortState,
    now: datetime | None = None,
) -> EffortComputed:
    """Weighted 0..100, party-relative. State = computed only if drive + duration known."""
    now = now or now_utc()
    w = tax()["effort_weights"]
    e = event.effort

    drive_n = cost_n = dur_n = 0.5
    mood = Energy.ESCAPE if e.overnight else Energy.NORMAL
    dtol = hh.drive_tolerance.get(mood.value, 45.0)
    if drive_minutes is not None:
        drive_n = min(1.0, drive_minutes / max(15.0, dtol * 2.0))
    if e.duration_hours is not None:
        dur_tol = hh.duration_tolerance.get(mood.value, 3.5)
        dur_n = min(1.0, e.duration_hours / max(1.0, dur_tol * 2.0))
    if e.cost_max is not None or e.cost_min is not None:
        cpp = cost_per_party(event, hh)
        cost_n = min(1.0, cpp / max(10.0, hh.budget_per_outing * 2.0))
    intensity = e.physical_intensity if e.physical_intensity is not None else 0.5
    kid_pen = kid_logistics_penalty(event, hh)
    lead_days = ((event.start - now).total_seconds() / 86400.0) if event.start else None
    booking = e.overnight or (event.price_max or 0) >= 40
    lead_pen = planning_lead_penalty(lead_days, booking)

    score = 100.0 * (
        w["drive"] * drive_n
        + w["duration"] * dur_n
        + w["intensity"] * float(intensity)
        + w["cost"] * cost_n
        + w["kid_logistics"] * kid_pen
        + w["lead"] * lead_pen
    )
    if e.overnight:
        score = max(score, 80.0)

    thresholds = tax()["band_thresholds"]
    band = "low" if score < thresholds["low"] else "high" if score > thresholds["high"] else "medium"

    drive_known = drive_minutes is not None
    dur_known = e.duration_hours is not None
    if drive_known and dur_known:
        state = EffortState.COMPUTED
    elif drive_known or dur_known:
        state = EffortState.LIKELY
    else:
        state = EffortState.UNKNOWN

    return EffortComputed(
        drive_minutes=drive_minutes,
        drive_state=drive_state,
        kid_nap_overlap_min=nap_overlap_minutes(event.start, event.end, hh.nap_windows)
        if hh.nap_windows
        else 0.0,
        planning_lead_days=round(lead_days, 1) if lead_days is not None else None,
        booking_required=booking,
        effort_score=round(score, 1),
        effort_band=band,
        band_state=state,
    )


def energy_preset(hh: Household, energy: Energy) -> dict:
    """Query-time targets per mood, derived from tolerance curves."""
    dt = hh.drive_tolerance
    presets = {
        Energy.TIRED: {"max_drive": dt.get("tired", 25.0), "max_intensity": 0.35, "max_hours": 2.5},
        Energy.NORMAL: {"max_drive": dt.get("normal", 45.0), "max_intensity": 0.60, "max_hours": 4.0},
        Energy.ADVENTUROUS: {
            "max_drive": dt.get("adventurous", 80.0),
            "max_intensity": 1.01,
            "max_hours": 8.0,
        },
        Energy.ESCAPE: {
            "max_drive": dt.get("escape", 160.0),
            "max_intensity": 1.01,
            "max_hours": 999.0,
            "overnight_ok": True,
        },
    }
    return presets[energy]


def effort_summary(event: Event, computed: EffortComputed | None) -> str:
    """Plain-talk effort line for digests: '18-min drive · 2h · stroll-paced · $free'."""
    parts = []
    c = computed
    if c and c.drive_minutes is not None:
        parts.append(f"{c.drive_minutes:.0f}-min drive")
    dur = event.effort.duration_hours
    if dur:
        parts.append(f"{dur:g}h" if dur < 10 else "all day" if dur < 12 else "overnight")
    it = event.effort.physical_intensity
    if it is not None:
        parts.append(
            "sit-back"
            if it < 0.2
            else "stroll-paced"
            if it < 0.45
            else "active"
            if it < 0.75
            else "hard-charging"
        )
    if event.is_free or (event.price_max == 0):
        parts.append("free")
    elif event.price_min is not None:
        top = event.price_max if event.price_max is not None else event.price_min
        parts.append(f"${event.price_min:g}–{top:g}")
    return " · ".join(parts) if parts else "effort unknown"
