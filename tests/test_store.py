"""Store round-trips: events, venues, feedback, FTS, embeds, location aggs."""

from __future__ import annotations

from core.models import Feedback
from core.store import vec_to_blob


def test_event_roundtrip(store, mk_event):
    ev = mk_event(price_min=5, price_max=10, organizer="Parks")
    got = store.get_event(ev.id)
    assert got.title == ev.title
    assert got.price_min == 5
    assert got.organizer == "Parks"
    assert got.kid.value == "verified_yes"


def test_fts_search(store, mk_event):
    mk_event(title="Zoo Boo costume parade")
    mk_event(title="Quiet book club")
    hits = store.search_text("zoo boo")
    assert len(hits) == 1 and hits[0].title.startswith("Zoo")


def test_feedback_and_pending_recap(store, mk_event):
    ev = mk_event()
    store.add_feedback(Feedback(event_id=ev.id, signal="interested"))
    past = [e for _, e in store.pending_recap("2099-01-01T00:00:00+00:00")]
    assert ev.id in [e.id for e in past]
    store.add_feedback(Feedback(event_id=ev.id, signal="went"))
    assert store.pending_recap("2099-01-01T00:00:00+00:00") == []


def test_drive_hist_and_visits(store):
    store.bump_drive(21.0)
    store.bump_drive(19.4)
    hist = store.drive_histogram()
    assert hist.get("20") == 2
    store.upsert_visit("47.26_-122.45", 47.26, -122.45, overnight=True)
    store.upsert_visit("47.26_-122.45", 47.26, -122.45)
    tops = store.top_places()
    assert tops[0]["visit_count"] == 2
    store.clear_location_data()
    assert not store.has_location_data()


def test_cosine_neighbors(store, mk_event):
    ev = mk_event()
    ev.embedding = vec_to_blob([1.0, 0.0, 0.0])
    store.upsert_event(ev)
    neighbors = store.cosine_neighbors([0.9, 0.1, 0.0])
    assert neighbors and neighbors[0][1] > 0.99
