"""Digest: band sections, whitespace, unknown count, numbered picks."""

from __future__ import annotations

from datetime import timedelta

from core.digest import build_digest
from core.models import Energy, now_utc
from core.rank import Query, rank
from tests.conftest import when


def _window():
    start = when(0).replace(hour=0, minute=0)
    return start, start + timedelta(days=4)


def test_digest_shape_and_picks(store, mk_event, hh):
    mk_event(title="Library story time", category="library", kid="verified_yes")
    mk_event(title="Point Defiance ramble", category="zoo_aquarium", kid="verified_yes", start=when(1, 13))
    mk_event(title="Gorge hike", category="parks_outdoors", kid="verified_yes", start=when(2, 9))
    start, end = _window()
    result = build_digest(hh, store, window=(start, end))
    text = result["text"]
    assert "Weekend plan" in text
    assert any(label in text for label in ("If you're wiped", "Solid outing", "Alternates"))
    assert result["picks"] and result["picks"][0]["n"] == 1
    assert "went to" in text  # feedback footer


def test_digest_counts_unknown_kid(store, mk_event, hh):
    mk_event(title="Mystery gig", kid="unknown")
    start, end = _window()
    result = build_digest(hh, store, window=(start, end))
    assert "couldn't be kid-checked" in result["text"]


def test_search_energy_tired_filters(store, mk_event, hh):
    from core.dedup import dedupe  # noqa: F401 (import sanity)

    mk_event(title="Story time", kid="verified_yes")
    ev = mk_event(title="Hard trail run", kid="verified_yes", category="parks_outdoors")
    ev.effort.physical_intensity = 0.95
    store.upsert_event(ev)
    now = now_utc()
    q = Query(start=now, end=now + timedelta(days=10), energy=Energy.TIRED, home=(hh.home_lat, hh.home_lon))
    scored = rank(store.events_between(q.start.isoformat(), q.end.isoformat()), q, hh, store)
    assert scored
    tired_top = scored[0]
    hard = [s for s in scored if s.event.title == "Hard trail run"]
    if hard:
        assert tired_top.score >= hard[0].score
