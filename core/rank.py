"""Eligibility, scoring, and slate construction.

Digest side is pure exploitation on the posterior lower bound; exploration
lives in one labeled wildcard slot in search (never the digest). MMR-lite with
fixed λ and hard caps per venue/organizer/day keeps slates from collapsing."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime

from . import profile as P
from .models import (
    Audience,
    EffortComputed,
    Energy,
    Event,
    EventStatus,
    Household,
    Party,
    Suitability,
    Venue,
    now_utc,
)
from .store import Store

WEIGHTS = {"pref": 0.50, "effort_fit": 0.30, "freshness": 0.10, "novelty": 0.10}
MMR_LAMBDA = 0.30
FRESH_DAYS = 14.0


@dataclass
class Query:
    start: datetime
    end: datetime
    party: Party = Party.FAMILY
    energy: Energy = Energy.NORMAL
    strict_kid: bool = True
    strict_dog: bool = False
    max_results: int = 20
    free_only: bool = False
    text: str = ""
    home: tuple[float, float] | None = None


@dataclass
class Scored:
    event: Event
    venue: Venue | None
    computed: EffortComputed
    score: float
    pref: float
    effort_fit: float
    reasons: list[str] = field(default_factory=list)
    components: dict = field(default_factory=dict)


def is_eligible(ev: Event, venue: Venue | None, q: Query, hh: Household) -> tuple[bool, str]:
    if ev.status != EventStatus.SCHEDULED:
        return False, f"status={ev.status.value}"
    if not ev.start or not (q.start <= ev.start <= q.end):
        return False, "outside window"
    if q.free_only and not (ev.is_free or (ev.price_min or 0) == 0):
        return False, "not free"
    if q.party == Party.ADULTS:
        # kid-targeted events (verified kid-yes, not explicitly neutral all-ages)
        # don't belong on a date-night/adults search
        if ev.kid == Suitability.VERIFIED_YES and ev.audience != Audience.ALL_AGES:
            return False, "kid-targeted"
    else:
        if q.strict_kid and ev.kid not in (Suitability.VERIFIED_YES, Suitability.LIKELY):
            return False, f"kid={ev.kid.value}"
        if hh.kid_ages and ev.age_min and ev.age_min > min(hh.kid_ages):
            return False, f"age_min={ev.age_min}"
    if q.party == Party.FAMILY and q.strict_dog and hh.has_dog:
        if ev.dog == Suitability.VERIFIED_NO:
            return False, "no dogs"
    preset = _preset(hh, q.energy)
    if venue and venue.lat is not None and q.home:
        from .geo import estimate_drive_minutes, haversine_km

        km = haversine_km(q.home[0], q.home[1], venue.lat, venue.lon)
        mins = P.accept_drive(hh, q.energy.value, estimate_drive_minutes(km))  # rough prefilter
        if mins < 0.02:
            return False, "beyond drive tolerance"
    if preset.get("max_intensity") is not None and ev.effort.physical_intensity is not None:
        if ev.effort.physical_intensity > preset["max_intensity"] + 0.25:
            return False, "too intense for mood"
    if preset.get("overnight_ok") is None and ev.effort.overnight:
        return False, "overnight"
    return True, ""


def _preset(hh: Household, energy: Energy) -> dict:
    from .effort import energy_preset

    return energy_preset(hh, energy)


def score_event(
    ev: Event,
    venue: Venue | None,
    computed: EffortComputed,
    q: Query,
    hh: Household,
    seen_categories: int = 0,
    weights: dict | None = None,
) -> Scored:
    w = weights or WEIGHTS
    keys = P.facet_keys(ev, computed.effort_band, P.price_band_for(ev), P.timeslot_for(ev))
    if keys:
        prefs = [P.facet_score(hh, k) for k in keys]
        pref = sum(prefs) / len(prefs)
        pref += 0.08 * P.momentum_for(hh, ev.category)
    else:
        pref = P.posterior_lower_bound(P.ALPHA0, P.BETA0)

    dtol_center = hh.drive_tolerance.get(q.energy.value, 45.0)
    drive_fit = (
        1.0
        if computed.drive_minutes is None
        else (1.0 / (1.0 + ((computed.drive_minutes - dtol_center * 0.5) / max(12.0, dtol_center))))
    )
    dur_fit = 1.0
    if ev.effort.duration_hours is not None:
        dur_fit = P.accept_duration(hh, q.energy.value, ev.effort.duration_hours)
    intensity = ev.effort.physical_intensity
    mood_ok = 1.0
    if energy_is_low(q.energy) and intensity is not None:
        mood_ok = max(0.0, 1.0 - intensity)
    effort_fit = 0.5 * drive_fit + 0.3 * dur_fit + 0.2 * mood_ok

    freshness = 1.0
    if ev.first_seen:
        from .models import now_utc

        age_days = (now_utc() - datetime.fromisoformat(ev.first_seen)).days
        freshness = max(0.0, 1.0 - age_days / FRESH_DAYS)

    novel = 1.0 if (venue and venue.visit_count == 0) else 0.0
    novel += 0.5 if seen_categories == 0 else 0.0

    unknown_pen = 0.0
    if (
        ev.kid == Suitability.UNKNOWN
        or ev.dog == Suitability.UNKNOWN
        or computed.band_state.value == "unknown"
    ):
        unknown_pen = 0.15

    score = (
        w["pref"] * pref
        + w["effort_fit"] * effort_fit
        + w["freshness"] * freshness
        + w["novelty"] * min(1.0, novel)
        - unknown_pen
    )
    reasons = _reasons(ev, venue, computed, pref, freshness, min(1.0, novel))
    components = {
        "pref": round(pref, 3),
        "effort_fit": round(effort_fit, 3),
        "freshness": round(freshness, 3),
        "novelty": round(min(1.0, novel), 3),
        "unknown_pen": unknown_pen,
        "drive_minutes": round(computed.drive_minutes, 0) if computed.drive_minutes is not None else None,
        "effort_band": computed.effort_band,
    }
    return Scored(
        event=ev,
        venue=venue,
        computed=computed,
        score=score,
        pref=pref,
        effort_fit=effort_fit,
        reasons=reasons,
        components=components,
    )


def energy_is_low(energy: Energy) -> bool:
    return energy == Energy.TIRED


def _reasons(
    ev: Event,
    venue: Venue | None,
    computed: EffortComputed,
    pref: float,
    freshness: float,
    novelty: float,
) -> list[str]:
    out = []
    if pref > 0.62:
        out.append("matches your patterns")
    if novelty >= 0.9:
        out.append("venue you've never tried" if venue and venue.visit_count == 0 else "new category for you")
    if freshness >= 0.9:
        out.append("just added")
    if computed.drive_minutes is not None:
        out.append(f"{computed.drive_minutes:.0f}-min drive")
    if ev.kid == Suitability.VERIFIED_YES:
        out.append("kid-friendly (verified)")
    if ev.dog == Suitability.VERIFIED_YES:
        out.append("dogs ok (verified)")
    if ev.audience == Audience.VERIFIED_ADULT:
        out.append("adults-only (verified)")
    if ev.effort.indoor_outdoor == "outdoor":
        out.append("outdoor — check rain swap")
    return out[:3]


def rank(events: list[Event], q: Query, hh: Household, store: Store) -> list[Scored]:
    eligible: list[Scored] = []
    seen_cats: dict[str, int] = {}
    for ev in events:
        venue = store.get_venue(ev.venue_id) if ev.venue_id else None
        ok, _ = is_eligible(ev, venue, q, hh)
        if not ok:
            continue
        from .effort import effort_score

        drive_min, drive_state = None, "unknown"
        if venue and venue.lat is not None and q.home:
            from .geo import drive_minutes as _dm
            from .models import EffortState

            drive_min, drive_state = _dm(q.home, (venue.lat, venue.lon))
            drive_state = EffortState(drive_state)
        computed = effort_score(ev, hh, venue, drive_min, drive_state, now_utc())
        s = score_event(ev, venue, computed, q, hh, seen_categories=seen_cats.get(ev.category, 0))
        seen_cats[ev.category] = seen_cats.get(ev.category, 0) + 1
        eligible.append(s)
    eligible.sort(key=lambda s: -s.score)
    return eligible


def select_slate(scored: list[Scored], k: int, per_day_cap: int = 1) -> list[Scored]:
    """Greedy calibrated-MMR-lite: relevance first, then bounded redundancy trade."""
    chosen: list[Scored] = []
    day_count: dict[str, int] = {}
    venue_count: dict[str, int] = {}
    org_count: dict[str, int] = {}
    pool = list(scored)
    while pool and len(chosen) < k:
        best, best_val = None, -1e9
        for s in pool:
            red = max((_sim(s, c) for c in chosen), default=0.0)
            val = (1 - MMR_LAMBDA) * s.score - MMR_LAMBDA * red
            day = s.event.start.strftime("%Y-%m-%d") if s.event.start else "?"
            if day_count.get(day, 0) >= per_day_cap:
                continue
            if (
                venue_count.get(s.event.venue_id or "", 0) >= 2
                or org_count.get(s.event.organizer or "", 0) >= 2
            ):
                continue
            if val > best_val:
                best, best_val = s, val
        if best is None:
            break
        chosen.append(best)
        pool.remove(best)
        day = best.event.start.strftime("%Y-%m-%d") if best.event.start else "?"
        day_count[day] = day_count.get(day, 0) + 1
        venue_count[best.event.venue_id or ""] = venue_count.get(best.event.venue_id or "", 0) + 1
        org_count[best.event.organizer or ""] = org_count.get(best.event.organizer or "", 0) + 1
    return chosen


def _sim(a: Scored, b: Scored) -> float:
    if a.event.id == b.event.id:
        return 1.0
    if a.event.venue_id and a.event.venue_id == b.event.venue_id:
        return 0.9
    if a.event.organizer and a.event.organizer == b.event.organizer:
        return 0.8
    sem = _semantic_sim(a, b)
    if sem >= SEMANTIC_SIM_LEVEL:
        return 0.6
    same_day = a.event.start and b.event.start and a.event.start.date() == b.event.start.date()
    if a.event.category == b.event.category and same_day:
        return 0.5
    if same_day:
        return 0.3
    return 0.0


SEMANTIC_SIM_LEVEL = 0.85


def _semantic_sim(a: Scored, b: Scored) -> float:
    """Cosine over stored event embeddings (populated only when the optional
    model2vec extra is installed; 0.0 otherwise — behavior unchanged)."""
    if not (a.event.embedding and b.event.embedding):
        return 0.0
    from .store import _blob_to_vec, _cosine

    va, vb = _blob_to_vec(a.event.embedding), _blob_to_vec(b.event.embedding)
    if not va or not vb:
        return 0.0
    return _cosine(va, vb)


WILDCARD_TOP = 3


def wildcard_pick(scored: list[Scored], hh: Household, seed: str) -> Scored | None:
    """The one labeled exploration slot, ranked 4+ so it never displaces the
    honest top-3. Selection is a Thompson draw over the facet posteriors
    (instead of the lower bound), so high-uncertainty options sometimes win.
    Search only — the digest never calls this."""
    if len(scored) <= WILDCARD_TOP:
        return None
    rng = random.Random(seed)
    best, best_draw = None, -1.0
    for s in scored[WILDCARD_TOP:]:
        keys = P.facet_keys(
            s.event, s.computed.effort_band, P.price_band_for(s.event), P.timeslot_for(s.event)
        )
        draw = P.thompson_sample(hh, keys, rng) + 0.08 * P.momentum_for(hh, s.event.category)
        if draw > best_draw:
            best, best_draw = s, draw
    if best is None:
        return None
    best.reasons = ["wildcard — exploring outside your usual", *best.reasons][:3]
    return best
