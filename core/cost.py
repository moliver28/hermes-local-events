"""Cost engine — the money twin of the effort engine.

Ingest stores per-person prices (price_min/price_max); query time turns them
into a party total with kid discounts applied. Fit to budget is mood-relative
(tired = cheap + close, escape = splurge OK) via a learned per-mood tolerance
curve, exactly parallel to drive/duration tolerance, and trend-relative: the
last two weeks of attended outings rescale the effective budget, so a splurgy
stretch tightens and a frugal one earns some headroom. Budget checks use the
conservative (price_max-based) total — never surprise a family at the gate."""

from __future__ import annotations

import math

from pydantic import BaseModel

from .models import Event, Household, Party

MOOD_BUDGET_FACTORS = {"tired": 0.6, "normal": 1.0, "adventurous": 1.8, "escape": 3.0}
TREND_WINDOW_DAYS = 14.0
TREND_MIN, TREND_MAX = 0.6, 1.5


class CostBreakdown(BaseModel):
    known: bool  # any price signal at all (is_free or a price field)
    is_free: bool = False
    per_adult: float | None = None  # typical (price_min-based) adult admission
    per_kid: float | None = None  # kid admission after household discounts
    per_person: float | None = None  # party-weighted typical average
    total: float | None = None  # conservative party total (price_max-based)
    typical: float | None = None  # midpoint-ish party total (display)
    party_size: int = 0


def party_size(hh: Household, party: Party = Party.FAMILY) -> int:
    return 2 if party == Party.ADULTS else 2 + len(hh.kid_ages)


def _kid_price(unit: float, age: int, hh: Household) -> float:
    if age < hh.kid_free_under:
        return 0.0
    return unit * hh.kid_price_ratio


def breakdown(event: Event, hh: Household, party: Party = Party.FAMILY) -> CostBreakdown:
    n = party_size(hh, party)
    if event.is_free:
        return CostBreakdown(
            known=True,
            is_free=True,
            per_adult=0.0,
            per_kid=0.0,
            per_person=0.0,
            total=0.0,
            typical=0.0,
            party_size=n,
        )
    if event.price_min is None and event.price_max is None:
        return CostBreakdown(known=False, party_size=n)
    low = event.price_min if event.price_min is not None else event.price_max
    high = event.price_max if event.price_max is not None else event.price_min
    kid_ages = [] if party == Party.ADULTS else hh.kid_ages
    kids_low = sum(_kid_price(low, a, hh) for a in kid_ages)
    kids_high = sum(_kid_price(high, a, hh) for a in kid_ages)
    typical = 2 * low + kids_low
    total = 2 * high + kids_high
    return CostBreakdown(
        known=True,
        per_adult=round(low, 2),
        per_kid=round(kids_low / len(kid_ages), 2) if kid_ages else None,
        per_person=round(typical / n, 2),
        total=round(total, 2),
        typical=round(typical, 2),
        party_size=n,
    )


# ------------------------------------------------------------------ budgets


def budget_for(hh: Household, mood: str) -> float:
    """Party-total budget for a mood: learned curve if present, else derived
    from the declared baseline."""
    learned = hh.budget_tolerance.get(mood)
    if learned:
        return learned
    return hh.budget_per_outing * MOOD_BUDGET_FACTORS.get(mood, 1.0)


def trend(store, hh: Household, now=None, days: float = TREND_WINDOW_DAYS) -> dict:
    """Rolling spend from the went-ledger. factor < 1 means you've been
    splurging (tighten), > 1 means a frugal stretch (headroom)."""
    events = store.recent_attended(days=days)
    baseline = budget_for(hh, "normal")
    total = 0.0
    outings = 0
    for ev in events:
        b = breakdown(ev, hh)
        if b.known and not b.is_free:
            total += b.total or 0.0
        outings += 1
    if not outings or baseline <= 0:
        factor = 1.0
    else:
        avg = total / outings
        factor = max(TREND_MIN, min(TREND_MAX, 1.0 + (baseline - avg) / baseline))
    return {
        "window_days": days,
        "outings": outings,
        "recent_total": round(total, 2),
        "avg_per_outing": round(total / outings, 2) if outings else None,
        "baseline": baseline,
        "factor": round(factor, 3),
    }


def effective_budget(hh: Household, mood: str, factor: float = 1.0) -> float:
    return budget_for(hh, mood) * factor


def accept_cost(
    hh: Household, mood: str, total: float, factor: float = 1.0, center: float | None = None
) -> float:
    """Logistic acceptance centered at the mood's effective budget (or an
    explicit per-query center)."""
    c = center if center is not None else effective_budget(hh, mood, factor)
    width = max(10.0, c * 0.35)
    return 1.0 / (1.0 + math.exp((total - c) / width))


def update_tolerance(hh: Household, signal: str, total: float | None, mood: str | None) -> None:
    """too_pricey tightens the curve at that mood (a quarter of the way down
    from the complained-about total); attendance past the center loosens it
    halfway toward the observed total (mirrors drive/duration)."""
    if total is None or not mood:
        return
    mu = budget_for(hh, mood)
    floor = max(10.0, hh.budget_per_outing * 0.3)
    if signal == "too_pricey":
        if total > mu:
            hh.budget_tolerance[mood] = round(max(floor, mu - 0.25 * (total - mu)), 2)
        else:
            hh.budget_tolerance[mood] = round(max(floor, 0.8 * mu + 0.2 * total), 2)
    elif signal == "went" and total > mu:
        hh.budget_tolerance[mood] = round(mu + 0.5 * (total - mu), 2)


def cost_line(b: CostBreakdown) -> str:
    """Plain-talk money line: '$free' · '$10/person · ~$35 for the party'."""
    if not b.known:
        return "price unknown"
    if b.is_free:
        return "free"
    return f"${b.per_person:g}/person · ~${b.total:g} for the party"
