"""Journal replay evaluation. For every logged feedback event, recompute its
score percentile within that day's eligible pool under a given weight set.

BIASED BY DESIGN: we only log events that were actually served, so this is a
regression check for scorer-weight changes (did attended picks keep scoring
high? did dismissed picks keep scoring low?), not an unbiased policy estimate —
no IPS / counterfactual claims are made (docs/SPEC.md 'dropped' table)."""

from __future__ import annotations

from datetime import timedelta

from .models import Energy, Household, Party, now_utc
from .rank import Query, is_eligible, score_event
from .store import Store

POSITIVE = ("went", "interested")
NEGATIVE = ("not_interested", "dismissed", "too_far", "too_pricey", "too_much_effort")


def _percentile(target: float, scores: list[float]) -> float:
    """1 = top of the pool, 0 = bottom. Mid-rank for ties; a single-item pool is top."""
    n = len(scores)
    if n <= 1:
        return 1.0
    below = sum(1 for s in scores if s < target)
    ties = sum(1 for s in scores if s == target) - 1  # don't tie with yourself
    return (below + 0.5 * ties) / (n - 1)


def _pool_scores(store: Store, hh: Household, day, weights: dict | None) -> dict[str, float]:
    """Score every eligible event on `day` under `weights`. Pool queries use a
    permissive family/normal filter (the historical query isn't logged)."""
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    q = Query(
        start=start,
        end=end,
        party=Party.FAMILY,
        energy=Energy.NORMAL,
        strict_kid=False,
        strict_dog=False,
        home=(hh.home_lat, hh.home_lon) if hh.home_lat is not None else None,
        max_results=1000,
    )
    from .effort import effort_score

    out: dict[str, float] = {}
    for ev in store.events_between(start.isoformat(), end.isoformat()):
        venue = store.get_venue(ev.venue_id) if ev.venue_id else None
        ok, _ = is_eligible(ev, venue, q, hh)
        if not ok:
            continue
        drive_min, drive_state = None, "unknown"
        if venue and venue.lat is not None and q.home:
            from .geo import drive_minutes as _dm
            from .models import EffortState

            drive_min, drive_state = _dm(q.home, (venue.lat, venue.lon))
            drive_state = EffortState(drive_state)
        computed = effort_score(ev, hh, venue, drive_min, drive_state, now_utc())
        out[ev.id] = score_event(ev, venue, computed, q, hh, weights=weights).score
    return out


def replay(store: Store, hh: Household, weights_a: dict, weights_b: dict | None = None) -> dict:
    """Replay the whole feedback journal. Returns mean score percentiles for
    positive vs negative signals under each weight set, plus the A/B delta."""
    rows = store.conn.execute("SELECT event_id, signal FROM feedback ORDER BY ts").fetchall()
    pct: dict[str, dict[str, list[float]]] = {"a": {"pos": [], "neg": []}, "b": {"pos": [], "neg": []}}
    pools_a: dict = {}
    pools_b: dict = {}
    counted = 0
    for row in rows:
        ev = store.get_event(row["event_id"])
        if not ev or not ev.start:
            continue
        day = ev.start.date()
        if day not in pools_a:
            pools_a[day] = _pool_scores(store, hh, ev.start, weights_a)
        scores_a = pools_a[day]
        if ev.id not in scores_a:
            continue  # no longer eligible as scored today — skip, don't fabricate
        bucket = "pos" if row["signal"] in POSITIVE else "neg" if row["signal"] in NEGATIVE else None
        if bucket is None:
            continue
        counted += 1
        pct["a"][bucket].append(_percentile(scores_a[ev.id], list(scores_a.values())))
        if weights_b is not None:
            if day not in pools_b:
                pools_b[day] = _pool_scores(store, hh, ev.start, weights_b)
            scores_b = pools_b[day]
            if ev.id in scores_b:
                pct["b"][bucket].append(_percentile(scores_b[ev.id], list(scores_b.values())))

    def _mean(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 3) if xs else None

    result = {
        "scored_feedback": counted,
        "logged_bias": "replay covers served events only — regression check, not an unbiased estimate",
        "weights_a": weights_a,
        "positive_mean_percentile_a": _mean(pct["a"]["pos"]),
        "negative_mean_percentile_a": _mean(pct["a"]["neg"]),
    }
    if weights_b is not None:
        result["weights_b"] = weights_b
        result["positive_mean_percentile_b"] = _mean(pct["b"]["pos"])
        result["negative_mean_percentile_b"] = _mean(pct["b"]["neg"])
        pa, pb = _mean(pct["a"]["pos"]), _mean(pct["b"]["pos"])
        result["positive_delta_b_minus_a"] = round(pb - pa, 3) if pa is not None and pb is not None else None
    return result
