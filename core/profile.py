"""Preference learning, sized for 5–50 feedback events/month:
Beta-Binomial facet posteriors (pure stdlib math), category momentum, and
logistic tolerance curves per mood. Posteriors auto-shrink to the prior below
~5 observations; no decay on time-of-day (confounded with category)."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from .models import Event, Feedback, Household

ALPHA0, BETA0 = 2.0, 3.0  # mild neutral prior
SIGNAL_WEIGHTS = {"went": 2.0, "interested": 1.0}  # failures get 1.0
MOMENTUM_HALFLIFE_DAYS = 60.0
FACET_HALFLIFE_DAYS = 120.0  # slow forgetting: stale evidence shrinks toward the prior


def facet_keys(
    event: Event, effort_band: str | None = None, price_band: str | None = None, timeslot: str | None = None
) -> list[str]:
    keys = [f"cat:{event.category}"]
    if event.subcategory:
        keys.append(f"sub:{event.category}:{event.subcategory}")
    if event.venue_id:
        keys.append(f"venue:{event.venue_id}")
    if event.organizer:
        keys.append(f"org:{event.organizer}")
    if effort_band:
        keys.append(f"effort:{effort_band}")
    if price_band:
        keys.append(f"price:{price_band}")
    if timeslot:
        keys.append(f"slot:{timeslot}")
    return keys


def price_band_for(event: Event) -> str:
    from .effort import tax

    if event.is_free:
        return "free"
    if event.price_min is None and event.price_max is None:
        return "unknown"  # price not stated — never bookkeep it as free
    t = tax()["price_bands"]
    p = event.price_min if event.price_min is not None else event.price_max
    if p <= 0:
        return "free"
    for name in ("cheap", "moderate", "pricey"):
        if p <= t[name]:
            return name
    return "pricey"


def timeslot_for(event: Event) -> str:
    if not event.start:
        return "unknown"
    h = event.start.hour
    return "morning" if h < 12 else "afternoon" if h < 17 else "evening"


# ------------------------------------------------------------------ posterior


def beta_posterior(alpha: float, beta: float) -> tuple[float, float]:
    """(posterior mean, std) of Beta(alpha, beta)."""
    n = alpha + beta
    mean = alpha / n
    var = (alpha * beta) / (n * n * (n + 1.0))
    return mean, math.sqrt(var)


def posterior_lower_bound(alpha: float, beta: float) -> float:
    mean, sd = beta_posterior(alpha, beta)
    return max(0.0, mean - 1.96 * sd)


def update_facets(hh: Household, keys: list[str], fb: Feedback, when: datetime | None = None) -> None:
    when = when or datetime.now(UTC)
    w = SIGNAL_WEIGHTS.get(fb.signal, 1.0)
    for k in keys:
        if fb.is_positive:
            hh.facet_alpha[k] = hh.facet_alpha.get(k, ALPHA0) + w
            hh.facet_beta[k] = hh.facet_beta.get(k, BETA0)
        else:
            hh.facet_alpha[k] = hh.facet_alpha.get(k, ALPHA0)
            hh.facet_beta[k] = hh.facet_beta.get(k, BETA0) + w
        hh.facet_ts[k] = when.isoformat()


def _decayed_facet(hh: Household, key: str, when: datetime | None = None) -> tuple[float, float]:
    """Facet (α, β) decayed toward the prior at the 120-day evidence halflife.
    Untimestamped keys (pre-decay-clock data) pass through undecayed."""
    a = hh.facet_alpha.get(key, ALPHA0)
    b = hh.facet_beta.get(key, BETA0)
    ts = hh.facet_ts.get(key)
    if ts and (a != ALPHA0 or b != BETA0):
        try:
            elapsed = max(
                0.0, ((when or datetime.now(UTC)) - datetime.fromisoformat(ts)).total_seconds() / 86400.0
            )
        except ValueError:
            return a, b
        f = 0.5 ** (elapsed / FACET_HALFLIFE_DAYS)
        a = ALPHA0 + (a - ALPHA0) * f
        b = BETA0 + (b - BETA0) * f
    return a, b


def facet_score(hh: Household, key: str) -> float:
    a, b = _decayed_facet(hh, key)
    return posterior_lower_bound(a, b)


# ------------------------------------------------------------------- momentum


def update_momentum(hh: Household, category: str, positive: bool, when: datetime | None = None) -> None:
    when = when or datetime.now(UTC)
    key = f"mom:{category}"
    old = decayed_momentum(hh, category, when)
    hh.category_momentum[key] = old + (0.2 if positive else -0.15)
    hh.category_momentum_ts[key] = when.isoformat()


def decayed_momentum(hh: Household, category: str, when: datetime | None = None) -> float:
    """Momentum for a category decayed to `when` at the 60-day halflife.
    Entries without timestamps (pre-decay-clock data) pass through undecayed."""
    when = when or datetime.now(UTC)
    key = f"mom:{category}"
    old = hh.category_momentum.get(key, 0.0)
    ts = hh.category_momentum_ts.get(key)
    if old and ts:
        try:
            elapsed = max(0.0, (when - datetime.fromisoformat(ts)).total_seconds() / 86400.0)
        except ValueError:
            return old
        old *= math.exp(-(math.log(2) / MOMENTUM_HALFLIFE_DAYS) * elapsed)
    return old


def momentum_for(hh: Household, category: str) -> float:
    return max(-1.0, min(1.0, decayed_momentum(hh, category)))


def thompson_sample(hh: Household, keys: list[str], rng) -> float:
    """One posterior draw per facet, averaged — the exploration analogue of
    facet_score's lower-bound mean. Uses decayed posteriors, so stale tastes
    lose exploration priority too. rng is a random.Random instance."""
    if not keys:
        return rng.betavariate(ALPHA0, BETA0)
    draws = [rng.betavariate(*_decayed_facet(hh, k)) for k in keys]
    return sum(draws) / len(draws)


# ----------------------------------------------------------- tolerance curves


def accept_drive(hh: Household, mood: str, minutes: float) -> float:
    """Logistic acceptance centered at the mood's tolerance."""
    mu = hh.drive_tolerance.get(mood, 45.0)
    return 1.0 / (1.0 + math.exp((minutes - mu) / 12.0))


def accept_duration(hh: Household, mood: str, hours: float) -> float:
    mu = hh.duration_tolerance.get(mood, 3.5)
    return 1.0 / (1.0 + math.exp((hours - mu) / 1.2))


def update_tolerance(
    hh: Household,
    fb: Feedback,
    drive_minutes: float | None,
    effort_band: str | None,
    party_total: float | None = None,
) -> None:
    """too_far/too_much_effort tighten the drive curve at that mood; too_pricey
    tightens the money curve; attendance past any center loosens it (halfway
    toward the observed value)."""
    from .cost import update_tolerance as update_cost_tolerance

    mood = fb.mood or "normal"
    if fb.signal in ("too_far", "too_much_effort") and drive_minutes:
        mu = hh.drive_tolerance.get(mood, 45.0)
        hh.drive_tolerance[mood] = max(10.0, 0.8 * mu + 0.2 * drive_minutes * 0.5)
    elif fb.signal == "went" and drive_minutes:
        mu = hh.drive_tolerance.get(mood, 45.0)
        if drive_minutes > mu:
            hh.drive_tolerance[mood] = round(mu + 0.5 * (drive_minutes - mu), 1)
    if fb.effort_match == "too_much":
        mu = hh.duration_tolerance.get(mood, 3.5)
        hh.duration_tolerance[mood] = max(1.0, 0.85 * mu)
    elif fb.effort_match == "right_effort":
        pass  # right effort is rewarded through facet posteriors, not curve inflation
    update_cost_tolerance(hh, fb.signal, party_total, mood)


def seed_from_drive_histogram(hh: Household, histogram: dict[str, int]) -> None:
    """histogram: {"15": 42, "20": 37, ...} minutes-bucket → count of real drives.
    Normal-mood tolerance := median observed drive; others scale from it."""
    total = sum(histogram.values())
    if not total:
        return
    median_bucket, acc = "45", 0
    for bucket in sorted(histogram, key=lambda k: float(k)):
        acc += histogram[bucket]
        if acc >= total / 2:
            median_bucket = bucket
            break
    median = float(median_bucket)
    hh.drive_tolerance["normal"] = round(median, 0)
    hh.drive_tolerance["tired"] = round(max(10.0, median * 0.6), 0)
    hh.drive_tolerance["adventurous"] = round(max(45.0, median * 1.8), 0)
    hh.drive_tolerance["escape"] = round(max(90.0, median * 3.2), 0)


def seed_interests_from_top_places(hh: Household, top_categories: list[str]) -> None:
    for c in top_categories:
        if c not in hh.interests:
            hh.interests.append(c)
