"""Dedup: same occurrence merges across sources; different occurrences stay."""

from __future__ import annotations

from core.dedup import dedupe
from tests.conftest import when


def _ev(title, start, source="a", sid=None, venue_id=None):
    from core.models import Event

    return Event(
        title=title,
        source_id=source,
        source_event_id=sid or title.lower(),
        start=start,
        end=start,
        venue_id=venue_id,
        provenance=[
            {
                "source_id": source,
                "source_event_id": sid or title.lower(),
                "source_url": f"https://x.test/{source}",
            }
        ],
    )


def test_same_occurrence_from_two_sources_merges():
    t = when(2)
    a = _ev("Tacoma Nature Walk", t, source="src_a", venue_id="ven_x")
    b = _ev(
        "tacoma  nature  walk!",
        t + __import__("datetime").timedelta(minutes=10),
        source="src_b",
        venue_id="ven_x",
    )
    out = dedupe([a, b])
    assert len(out) == 1
    assert len(out[0].provenance) == 2


def test_different_times_same_title_stay_separate():
    t = when(2)
    a = _ev("Farmers Market", t)
    b = _ev("Farmers Market", when(9))
    assert len(dedupe([a, b])) == 2


def test_verified_no_is_sticky_in_merge():
    t = when(2)
    from core.models import Suitability

    a = _ev("Big Concert", t)
    a.dog = Suitability.VERIFIED_NO
    b = _ev("Big Concert", t + __import__("datetime").timedelta(minutes=5), source="b")
    b.dog = Suitability.VERIFIED_YES
    out = dedupe([a, b])
    assert out[0].dog == Suitability.VERIFIED_NO


def test_all_unknown_starts_do_not_crash():
    """Regression: refresh crashed with NoneType < NoneType when the first
    merged event had no start time (e.g. ParentMap listicles without dates).
    Startless events must dedupe and sort last, not raise."""
    from core.models import Event

    a = _ev("Family fun this weekend", None)
    b = _ev("Family fun this weekend", None, source="b")
    out = dedupe([a, b])
    assert len(out) >= 1
    assert all(e.start is None for e in out)
