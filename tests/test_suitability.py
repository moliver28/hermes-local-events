"""Four-state suitability: rules produce verified_* only from explicit text;
LLM assist fills unknowns; verified_no is sticky in merges."""

from __future__ import annotations

from core.models import Audience, Event, Suitability, Venue
from core.suitability import classify


def _llm(responses: dict):
    calls = []

    def fake(system, user):
        calls.append(user)
        return responses

    return fake, calls


def test_dog_positive_rule():
    ev = Event(title="Yappy hour", description="Leashed dogs welcome on the patio")
    classify(ev, None)
    assert ev.dog == Suitability.VERIFIED_YES


def test_no_dogs_rule():
    ev = Event(title="Wine tasting", description="Sorry, no dogs allowed inside")
    classify(ev, Venue(id="v", name="Winery", venue_type="brewery"))
    assert ev.dog == Suitability.VERIFIED_NO


def test_adults_only_verified():
    ev = Event(title="Comedy night", description="21+ adults only, tickets $20")
    classify(ev, Venue(id="v", name="Club", venue_type="comedy_club"))
    assert ev.audience == Audience.VERIFIED_ADULT
    assert ev.kid == Suitability.VERIFIED_NO


def test_adults_leaning_venue_prior():
    ev = Event(title="Trivia night", description="Teams of four")
    classify(ev, Venue(id="v", name="Taproom", venue_type="brewery"))
    assert ev.audience == Audience.ADULTS_LEANING


def test_source_default_kid_by_construction():
    ev = Event(title="Pumpkin patch day", description="")
    classify(ev, None, source_defaults={"kid": "verified_yes"})
    assert ev.kid == Suitability.VERIFIED_YES


def test_llm_assist_fills_unknown_with_evidence():
    ev = Event(title="Harvest festival", description="Live music and food trucks")
    llm, calls = _llm({"dog": "likely", "quote": "outdoor lawn area"})
    classify(ev, Venue(id="v", name="Fairgrounds", venue_type="park"), llm=llm)
    assert ev.dog == Suitability.LIKELY
    assert calls and "Harvest festival" in calls[0]


def test_llm_unknown_stays_unknown():
    ev = Event(title="Quiet craft circle", description="")
    llm, _ = _llm({"kid": "unknown", "dog": "unknown", "audience": "unknown"})
    classify(ev, None, llm=llm)
    assert ev.kid == Suitability.UNKNOWN
    assert ev.audience == Audience.UNKNOWN
