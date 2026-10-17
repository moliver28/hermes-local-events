"""Cost as a first-class dimension: party math, mood budgets, spend trend,
learned tolerance, rank/feedback integration."""

from __future__ import annotations

from datetime import timedelta

import pytest

from core import cost as C
from core import profile as P
from core.effort import effort_score, effort_summary
from core.feedback import record_feedback
from core.models import EffortComputed, EffortState, Energy, Event, Feedback, Household, Party, now_utc
from core.rank import Query, rank, score_event


def _q(energy=Energy.NORMAL, **kw):
    now = now_utc()
    return Query(start=now, end=now + timedelta(days=10), energy=energy, **kw)


def _rank_all(store, q, hh):
    return rank(store.events_between(q.start.isoformat(), q.end.isoformat()), q, hh, store)


# ------------------------------------------------------------------ party math


def test_breakdown_family_kid_discounts(mk_event, hh):
    hh.kid_ages = [3, 6]
    hh.kid_free_under = 4  # the 3-year-old is free
    ev = mk_event(title="Aquarium", is_free=False, price_min=10.0, price_max=20.0)
    b = C.breakdown(ev, hh)
    assert b.known and not b.is_free
    assert b.per_adult == 10.0
    assert b.typical == 25.0  # 2×10 + 0 (under-4 free) + 5 (half price)
    assert b.total == 50.0  # conservative: price_max basis
    assert b.per_person == 25.0 / 4
    assert b.party_size == 4


def test_breakdown_free_and_unknown(mk_event, hh):
    b = C.breakdown(mk_event(title="Park day", is_free=True), hh)
    assert b.is_free and b.known and b.total == 0.0
    b2 = C.breakdown(mk_event(title="Mystery gala", is_free=False), hh)
    assert not b2.known and b2.total is None and b2.per_person is None


def test_breakdown_adults_party_ignores_kids(mk_event, hh):
    ev = mk_event(title="Comedy night", is_free=False, price_min=15.0)
    b = C.breakdown(ev, hh, Party.ADULTS)
    assert b.party_size == 2 and b.total == 30.0 and b.per_kid is None


def test_cost_line_plain_talk(mk_event, hh):
    assert C.cost_line(C.breakdown(mk_event(is_free=True), hh)) == "free"
    priced = C.breakdown(mk_event(is_free=False, price_min=10.0, price_max=20.0), hh)
    assert "/person" in C.cost_line(priced) and "for the party" in C.cost_line(priced)
    assert C.cost_line(C.breakdown(mk_event(is_free=False), hh)) == "price unknown"


# ------------------------------------------------------------------- budgets


def test_budget_mood_scaling_and_learning():
    hh = Household(budget_per_outing=60.0)
    assert C.budget_for(hh, "tired") == pytest.approx(36.0)
    assert C.budget_for(hh, "normal") == pytest.approx(60.0)
    assert C.budget_for(hh, "adventurous") == pytest.approx(108.0)
    assert C.budget_for(hh, "escape") == pytest.approx(180.0)
    hh.budget_tolerance["tired"] = 25.0  # learned curve beats the derived one
    assert C.budget_for(hh, "tired") == 25.0


def test_accept_cost_logistic():
    hh = Household(budget_per_outing=60.0)
    assert C.accept_cost(hh, "normal", 0.0) > 0.9
    assert C.accept_cost(hh, "normal", 60.0) == pytest.approx(0.5)
    assert C.accept_cost(hh, "normal", 300.0) < 0.05
    # the same total is more acceptable in a richer mood
    assert C.accept_cost(hh, "escape", 150.0) > C.accept_cost(hh, "tired", 150.0)
    # trend factor rescales the center: splurgy stretch → stricter acceptance
    assert C.accept_cost(hh, "normal", 70.0, factor=0.7) < C.accept_cost(hh, "normal", 70.0)


def test_tolerance_too_pricey_tightens():
    hh = Household(budget_per_outing=60.0)
    mu = C.budget_for(hh, "tired")  # 36
    C.update_tolerance(hh, "too_pricey", 80.0, "tired")
    assert hh.budget_tolerance["tired"] == pytest.approx(mu - 0.25 * (80 - mu))
    for _ in range(20):  # asymptotes at the floor, never below 30% of baseline
        C.update_tolerance(hh, "too_pricey", 80.0, "tired")
    assert hh.budget_tolerance["tired"] >= max(10.0, 60.0 * 0.3) - 0.01


def test_tolerance_went_past_center_loosens():
    hh = Household(budget_per_outing=60.0)
    C.update_tolerance(hh, "went", 100.0, "normal")
    assert hh.budget_tolerance["normal"] == 80.0  # halfway toward the observed total
    hh2 = Household(budget_per_outing=60.0)
    C.update_tolerance(hh2, "went", 30.0, "normal")  # under budget → no curve change
    assert "normal" not in hh2.budget_tolerance
    C.update_tolerance(hh2, "too_pricey", None, "normal")  # unknown price → no-op
    assert not hh2.budget_tolerance


# --------------------------------------------------------------------- trend


def test_trend_neutral_without_history(store, hh):
    t = C.trend(store, hh)
    assert t["outings"] == 0 and t["factor"] == 1.0 and t["avg_per_outing"] is None


def test_trend_splurgy_stretch_tightens(store, mk_event):
    hh = Household(budget_per_outing=60.0)  # no kids: party = 2 adults
    for i in range(3):
        ev = mk_event(title=f"Pricey {i}", is_free=False, price_min=40.0)
        store.add_feedback(Feedback(event_id=ev.id, signal="went"))
    t = C.trend(store, hh)
    assert t["outings"] == 3
    assert t["avg_per_outing"] == pytest.approx(80.0)  # 2 × $40
    assert t["factor"] == pytest.approx(1.0 + (60.0 - 80.0) / 60.0, abs=1e-3)


def test_trend_frugal_stretch_capped(store, mk_event):
    hh = Household(budget_per_outing=60.0)
    ev = mk_event(title="Free win")
    store.add_feedback(Feedback(event_id=ev.id, signal="went"))
    assert C.trend(store, hh)["factor"] == C.TREND_MAX


# ---------------------------------------------------------------------- rank


def test_rank_cost_components_and_tired_budget(store, mk_event, hh):
    mk_event(title="Cheap", is_free=False, price_min=5.0)
    mk_event(title="Pricey", is_free=False, price_min=80.0)
    q = _q(energy=Energy.TIRED)
    scored = _rank_all(store, q, hh)
    comp = {s.event.title: s.components for s in scored}
    assert comp["Cheap"]["budget_effective"] == pytest.approx(60.0 * 0.6)
    assert comp["Pricey"]["cost_fit"] < comp["Cheap"]["cost_fit"]
    assert [s.event.title for s in scored].index("Cheap") < [s.event.title for s in scored].index("Pricey")


def test_same_total_fits_richer_mood(mk_event, hh):
    ev = mk_event(is_free=False, price_min=60.0)
    tired = score_event(ev, None, EffortComputed(), _q(energy=Energy.TIRED), hh)
    escape = score_event(ev, None, EffortComputed(), _q(energy=Energy.ESCAPE), hh)
    assert escape.components["cost_fit"] > tired.components["cost_fit"]


def test_unknown_price_neutral_never_free(mk_event, hh):
    s = score_event(mk_event(title="Mystery", is_free=False), None, EffortComputed(), _q(), hh)
    assert s.components["cost_known"] is False
    assert s.components["cost_fit"] == 0.5


def test_max_total_hard_cap(store, mk_event, hh):
    mk_event(title="Within", is_free=False, price_min=10.0)
    mk_event(title="Over", is_free=False, price_min=50.0, price_max=80.0)
    mk_event(title="Mystery", is_free=False)  # price unknown → excluded under a cap
    q = _q(max_total=40.0)
    assert [s.event.title for s in _rank_all(store, q, hh)] == ["Within"]


def test_rank_applies_spend_trend(store, mk_event, hh):
    for i in range(3):
        ev = mk_event(title=f"Old spend {i}", is_free=False, price_min=80.0)
        store.add_feedback(Feedback(event_id=ev.id, signal="went"))
    mk_event(title="Fresh", is_free=False, price_min=5.0)
    comp = {s.event.title: s.components for s in _rank_all(store, _q(), hh)}
    assert comp["Fresh"]["trend_factor"] < 1.0
    assert comp["Fresh"]["budget_effective"] < 60.0


def test_budget_overrun_leads_reasons(store, mk_event, hh):
    s = score_event(mk_event(is_free=False, price_min=200.0), None, EffortComputed(), _q(), hh)
    assert any("budget" in r for r in s.reasons)
    assert s.reasons[0].startswith("over your")


# ------------------------------------------------------------------ feedback


def test_too_pricey_feedback_updates_budget(store, mk_event, hh):
    ev = mk_event(is_free=False, price_min=80.0, price_max=90.0)
    assert record_feedback(hh, store, ev.id, "too_pricey", mood="tired")["ok"]
    assert hh.budget_tolerance["tired"] < C.budget_for(Household(), "tired")


def test_price_band_unknown_is_not_free(mk_event):
    assert P.price_band_for(mk_event(is_free=False)) == "unknown"
    assert P.price_band_for(mk_event(is_free=True)) == "free"


# -------------------------------------------------------------------- effort


def test_effort_cost_component_engages():
    hh = Household()
    now = now_utc()
    priced = Event(title="A", start=now, end=now, is_free=False, price_min=100.0)
    free = Event(title="B", start=now, end=now, is_free=True)
    assert (
        effort_score(priced, hh, None, None, EffortState.UNKNOWN).effort_score
        > effort_score(free, hh, None, None, EffortState.UNKNOWN).effort_score
    )


def test_effort_summary_money_line(mk_event, hh):
    line = effort_summary(mk_event(is_free=False, price_min=10.0, price_max=20.0), None, hh)
    assert "/person" in line and "for the party" in line
