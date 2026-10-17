"""Effort engine — the first-class dimension must compute, band, and summarize."""

from __future__ import annotations

from core.effort import effort_score, effort_summary, infer_intensity, nap_overlap_minutes, static_effort
from core.models import EffortState, Event, Household, Venue, now_utc


def test_intensity_keywords():
    assert infer_intensity("Family story time at the library")[0] == 0.05
    assert infer_intensity("Guided hike through the gorge")[0] == 0.60  # nature walk hits first
    assert infer_intensity("Beer tasting night")[0] == 0.15
    v, state = infer_intensity("Something entirely unclassifiable")
    assert v is None and state == EffortState.UNKNOWN


def test_static_effort_fills_fields(mk_venue):
    venue = mk_venue(venue_type="zoo")
    ev = Event(
        title="Zoo Boo trick or treat", description="Costume parade outdoors", start=now_utc(), end=now_utc()
    )
    static_effort(ev, venue)
    assert ev.effort.duration_hours is not None
    assert ev.effort.indoor_outdoor == "mixed"
    assert ev.effort.physical_intensity is not None
    assert ev.effort.overnight is False


def test_band_relative_to_tolerance():
    hh_far = Household(drive_tolerance={"normal": 45.0})
    hh_near = Household(drive_tolerance={"normal": 20.0})
    ev = Event(title="X", start=now_utc(), end=now_utc())
    venue = Venue(id="v", name="V", lat=47.3, lon=-122.5)
    far = effort_score(ev, hh_far, venue, 30.0, EffortState.COMPUTED)
    near = effort_score(ev, hh_near, venue, 30.0, EffortState.COMPUTED)
    assert near.effort_score > far.effort_score  # same drive is harder for a tight household
    assert far.effort_band in ("low", "medium", "high")


def test_nap_overlap():
    from datetime import datetime

    start = datetime(2026, 10, 17, 13, 0)  # 1pm, inside 12:30-14:30 nap
    end = datetime(2026, 10, 17, 15, 0)
    assert nap_overlap_minutes(start, end, [["12:30", "14:30"]]) == 90.0


def test_overnight_forces_high():
    hh = Household()
    ev = Event(
        title="Coast camping weekend", start=now_utc(), end=now_utc(), **{"effort": {"overnight": True}}
    )
    assert ev.effort.overnight
    res = effort_score(ev, hh, None, 160.0, EffortState.COMPUTED)
    assert res.effort_score >= 80.0
    assert res.effort_band == "high"


def test_unknown_when_no_data():
    hh = Household()
    ev = Event(title="Mystery event")
    res = effort_score(ev, hh, None, None, EffortState.UNKNOWN)
    assert res.band_state == EffortState.UNKNOWN


def test_effort_summary_plain_talk(mk_event):
    ev = mk_event()
    ev.effort.duration_hours = 1.0
    ev.effort.physical_intensity = 0.05
    s = effort_summary(ev, None)
    assert "1h" in s and "sit-back" in s
