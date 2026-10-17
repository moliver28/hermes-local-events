"""Eligibility + slate construction: strict filters, caps, band structure."""

from __future__ import annotations

from datetime import timedelta

from core.models import Energy, EventStatus, Household, Party, Suitability, now_utc
from core.rank import Query, is_eligible, rank, select_slate
from tests.conftest import when


def _q(energy=Energy.NORMAL, party=Party.FAMILY, strict_kid=True, strict_dog=False):
    now = now_utc()
    return Query(
        start=now,
        end=now + timedelta(days=10),
        energy=energy,
        party=party,
        strict_kid=strict_kid,
        strict_dog=strict_dog,
    )


def test_cancelled_ineligible(store, mk_event):
    ev = mk_event(status=EventStatus.CANCELLED)
    ok, _ = is_eligible(ev, None, _q(), Household())
    assert not ok


def test_strict_kid_excludes_unknown(store, mk_event):
    """Kid-unknown passes a strict filter ONLY when no kids are declared
    (kid_ages empty = no kid filters, per plugin.yaml). Declared kids ->
    unknown never passes."""
    ev = mk_event(title="Mystery", kid="unknown")
    hh_kids = Household(kid_ages=[6])
    ok, reason = is_eligible(ev, None, _q(), hh_kids)
    assert not ok and "kid" in reason
    ok2, _ = is_eligible(ev, None, _q(strict_kid=False), hh_kids)
    assert ok2

    # No kids declared: unknown kid-suitability is not an exclusion.
    hh_none = Household()
    ok3, _ = is_eligible(ev, None, _q(), hh_none)
    assert ok3


def test_adults_party_excludes_kid_verified(store, mk_event):
    ev = mk_event(title="Kids story time", kid="verified_yes")
    ok, _ = is_eligible(ev, None, _q(party=Party.ADULTS, strict_kid=False), Household())
    assert not ok


def test_overnight_excluded_from_normal_energy(store, mk_event):
    ev = mk_event(title="Coast camping trip")
    ev.effort.overnight = True
    ok, reason = is_eligible(ev, None, _q(), Household())
    assert not ok and reason == "overnight"


def test_dog_verified_no_excluded_when_strict(store, mk_event, hh):
    ev = mk_event(title="No-dogs gala", dog="verified_no", kid="verified_yes")
    ok, reason = is_eligible(ev, None, _q(strict_dog=True), hh)
    assert not ok and reason == "no dogs"


def test_slate_day_cap(store, mk_event):
    mk_event(title="A", start=when(2, 10))
    mk_event(title="B", start=when(2, 14))
    mk_event(title="C", start=when(3, 10))
    q = _q()
    scored = rank(store.events_between(q.start.isoformat(), q.end.isoformat()), q, Household(), store)
    slate = select_slate(scored, k=3, per_day_cap=1)
    days = [s.event.start.date() for s in slate]
    assert len(days) == len(set(days))


def test_rank_respects_dog_filter(store, mk_event, hh):
    mk_event(title="Free library morning", kid="verified_yes")
    mk_event(title="No-dogs gala", dog="verified_no", kid="verified_yes")
    q = _q(strict_dog=True)
    scored = rank(store.events_between(q.start.isoformat(), q.end.isoformat()), q, hh, store)
    assert scored
    assert all(s.event.dog != Suitability.VERIFIED_NO for s in scored)
