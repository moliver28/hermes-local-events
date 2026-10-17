"""Shared fixtures: tmp store, household, event factory. All offline."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.models import Event, Household, Venue
from core.store import Store


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture()
def hh() -> Household:
    return Household(
        home_lat=47.2529,
        home_lon=-122.4443,
        home_label="South Tacoma",
        kid_ages=[3, 6],
        nap_windows=[["12:30", "14:30"]],
        has_dog=True,
        dog_size="medium",
    )


def when(days_ahead: int, hour: int = 10) -> datetime:
    """A fixed upcoming Saturday-ish datetime (UTC)."""
    base = datetime(2026, 10, 17, hour, 0, tzinfo=UTC)  # a Saturday
    from datetime import timedelta

    return base + timedelta(days=days_ahead)


@pytest.fixture()
def mk_event(store):
    def _mk(**kw) -> Event:
        defaults = dict(
            title="Library story time",
            source_id="test_source",
            source_event_id=kw.get("title", "ev").replace(" ", "-").lower(),
            start=when(3, 10),
            end=when(3, 11),
            category="library",
            kid="verified_yes",
            is_free=True,
        )
        defaults.update(kw)
        ev = Event(**defaults)
        store.upsert_event(ev)
        return ev

    return _mk


@pytest.fixture()
def mk_venue(store):
    def _mk(**kw) -> Venue:
        defaults = dict(
            id="ven_test_1",
            name="Test Library",
            lat=47.26,
            lon=-122.45,
            venue_type="library",
            indoor_outdoor="indoor",
        )
        defaults.update(kw)
        v = Venue(**defaults)
        store.upsert_venue(v)
        return v

    return _mk
