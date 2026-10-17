"""Beta-Binomial posteriors, tolerance curves, and Timeline seeding."""

from __future__ import annotations

from core import profile as P
from core.models import Feedback, Household


def test_posterior_shrinks_to_prior_with_few_obs():
    hh = Household()
    fb = Feedback(event_id="e", signal="went")
    P.update_facets(hh, ["cat:library"], fb)
    a, b = hh.facet_alpha["cat:library"], hh.facet_beta["cat:library"]
    mean, _ = P.beta_posterior(a, b)
    assert 0.4 < mean < 0.6  # one went (w=2) barely moves the prior (2,3)
    # after many observations it should move decisively
    for _ in range(20):
        P.update_facets(hh, ["cat:library"], fb)
    mean2, _ = P.beta_posterior(hh.facet_alpha["cat:library"], hh.facet_beta["cat:library"])
    assert mean2 > 0.8


def test_negative_feedback_lowers_score():
    hh = Household()
    fb = Feedback(event_id="e", signal="dismissed")
    P.update_facets(hh, ["cat:sports"], fb)
    assert P.facet_score(hh, "cat:sports") < P.facet_score(Household(), "cat:sports")


def test_tolerance_tightens_on_too_far():
    hh = Household()
    P.update_tolerance(hh, Feedback(event_id="e", signal="too_far", mood="normal"), 60.0, "medium")
    assert hh.drive_tolerance["normal"] < 45.0


def test_tolerance_loosens_after_long_went():
    hh = Household()
    P.update_tolerance(hh, Feedback(event_id="e", signal="went", mood="normal"), 70.0, "medium")
    assert 45.0 < hh.drive_tolerance["normal"] <= 70.0


def test_drive_histogram_seeding():
    hh = Household()
    P.seed_from_drive_histogram(hh, {"10": 5, "15": 10, "20": 10, "45": 5})
    assert hh.drive_tolerance["normal"] == 15.0
    assert hh.drive_tolerance["tired"] < 15.0
    assert hh.drive_tolerance["adventurous"] > 30.0


def test_accept_drive_logistic():
    hh = Household()
    assert P.accept_drive(hh, "normal", 10.0) > P.accept_drive(hh, "normal", 90.0)
    assert P.accept_drive(hh, "tired", 200.0) < 0.05


def test_price_and_slot_bands():
    from core.models import Event, now_utc

    assert P.price_band_for(Event(title="x", price_min=0)) == "free"
    assert P.price_band_for(Event(title="x", price_min=15)) == "cheap"
    assert P.price_band_for(Event(title="x", price_min=45)) == "moderate"
    ev = Event(title="x", start=now_utc().replace(hour=19))
    assert P.timeslot_for(ev) == "evening"
