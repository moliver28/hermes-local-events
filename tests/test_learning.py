"""Learning-layer upgrades: Thompson wildcard, elapsed-time momentum decay,
score-component reasons, LLM listwise rerank, journal replay, semantic sim."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from core import embed as embed_mod
from core import eval as eval_mod
from core import profile as P
from core.models import EffortComputed, Energy, Event, Household, now_utc
from core.rank import Scored, _sim, score_event, wildcard_pick
from core.rerank import rerank_slate
from core.store import vec_to_blob
from tests.conftest import when


def _q(window_days=10):
    from core.rank import Query

    now = now_utc()
    return Query(start=now, end=now + timedelta(days=window_days), energy=Energy.NORMAL)


# ------------------------------------------------------------------- wildcard


def test_wildcard_deterministic_and_labeled(store, mk_event, hh):
    for i in range(5):
        mk_event(title=f"Event {i}", start=when(i + 1, 10))
    q = _q()
    scored = rank_all(store, q, hh)
    w1 = wildcard_pick(scored, hh, seed="2026-10-17")
    w2 = wildcard_pick(scored, hh, seed="2026-10-17")
    assert w1 is not None and w2.event.id == w1.event.id
    assert w1.event.id not in [s.event.id for s in scored[:3]]  # never displaces honest top-3
    assert w1.reasons[0].startswith("wildcard")


def test_wildcard_needs_pool(store, mk_event, hh):
    mk_event(title="Only one")
    assert wildcard_pick(rank_all(store, _q(), hh), hh, "s") is None


def rank_all(store, q, hh):
    from core.rank import rank

    return rank(store.events_between(q.start.isoformat(), q.end.isoformat()), q, hh, store)


# ------------------------------------------------------------------- momentum


def test_momentum_decays_by_elapsed_time():
    hh = Household()
    t0 = datetime(2026, 8, 1, tzinfo=UTC)
    P.update_momentum(hh, "library", True, when=t0)
    assert hh.category_momentum["mom:library"] == 0.2
    # 60 days later at the 60-day halflife → half
    decayed = P.decayed_momentum(hh, "library", when=t0 + timedelta(days=60))
    assert 0.09 < decayed < 0.11


def test_momentum_without_timestamp_passes_through():
    hh = Household()
    hh.category_momentum["mom:x"] = 0.5  # pre-decay-clock data
    assert P.decayed_momentum(hh, "x") == 0.5


def test_momentum_update_chains_decay():
    hh = Household()
    t0 = datetime(2026, 6, 1, tzinfo=UTC)
    P.update_momentum(hh, "zoo", True, when=t0)  # 0.2
    P.update_momentum(hh, "zoo", True, when=t0 + timedelta(days=60))  # 0.1 + 0.2
    assert abs(hh.category_momentum["mom:zoo"] - 0.3) < 1e-9


# ----------------------------------------------------------------- components


def test_components_and_novelty_reason(store, mk_event, mk_venue):
    venue = mk_venue()
    ev = mk_event(venue_id=venue.id, organizer="Parks Dept")  # organizer no longer blocks novelty
    s = score_event(ev, venue, EffortComputed(), _q(), Household())
    assert set(s.components) >= {"pref", "effort_fit", "freshness", "novelty", "unknown_pen"}
    assert s.components["novelty"] == 1.0
    assert "venue you've never tried" in s.reasons


# --------------------------------------------------------------------- rerank


def _scored(title, day=1):
    ev = Event(title=title, start=when(day, 10), category="library", kid="verified_yes")
    return Scored(event=ev, venue=None, computed=EffortComputed(), score=1.0, pref=0.5, effort_fit=0.5)


def test_rerank_reorders_within_section_only():
    a, b, c = _scored("A"), _scored("B"), _scored("C")
    sections = {"band1": [a, b], "band2": [c]}

    def llm(system, user):  # band2's pick first overall — it can only move within band2
        return {
            "order": [
                {"id": c.event.id, "reason": "r3"},
                {"id": b.event.id, "reason": "r2"},
                {"id": a.event.id, "reason": "r1"},
            ]
        }

    out = rerank_slate(sections, llm, random.Random("s"))
    assert [s.event.title for s in out["band1"]] == ["B", "A"]
    assert [s.event.title for s in out["band2"]] == ["C"]
    assert out["band1"][0].reasons[0] == "r2"


def test_rerank_fail_closed():
    a, b = _scored("A"), _scored("B")
    sections = {"band1": [a, b]}
    base = [s.event.title for s in sections["band1"]]
    cases = [
        None,
        "not a dict",
        {"order": [{"id": a.event.id, "reason": "dropped b"}]},  # missing id
        {"order": [{"id": a.event.id}, {"id": a.event.id}]},  # duplicate
        {"order": [{"id": "ghost", "reason": "x"}, {"id": b.event.id}]},  # unknown id
    ]
    for bad in cases:
        assert [s.event.title for s in rerank_slate(sections, bad, random.Random("s"))["band1"]] == base
    assert [s.event.title for s in rerank_slate(sections, None, random.Random("s"))["band1"]] == base


# --------------------------------------------------------------------- replay


def test_replay_percentiles_and_ab(store, mk_event, mk_venue, hh):
    near = mk_venue(id="ven_near", lat=47.26, lon=-122.45)
    far = mk_venue(id="ven_far", lat=47.55, lon=-122.70)
    store.conn.execute("UPDATE venues SET visit_count=5 WHERE id='ven_near'")
    store.conn.commit()
    good = mk_event(title="Near win", venue_id=near.id, start=when(2, 10))
    far_ev = mk_event(title="Far one", venue_id=far.id, start=when(2, 10), kid="verified_yes")
    from core.models import Feedback

    store.add_feedback(Feedback(event_id=good.id, signal="went"))
    store.add_feedback(Feedback(event_id=far_ev.id, signal="too_far"))
    weights_b = {"pref": 0.0, "effort_fit": 0.0, "freshness": 0.0, "novelty": 1.0}
    result = eval_mod.replay(store, hh, eval_mod_weights(), weights_b)
    assert result["scored_feedback"] == 2
    assert result["positive_mean_percentile_a"] == 1.0  # near event tops its day pool
    assert result["negative_mean_percentile_a"] == 0.0
    assert result["positive_delta_b_minus_a"] is not None
    assert -1.0 <= result["positive_delta_b_minus_a"] <= 1.0


def eval_mod_weights():
    from core.rank import WEIGHTS

    return dict(WEIGHTS)


# ------------------------------------------------------------ semantic sim


def _with_vec(s: Scored, vec: list[float]) -> Scored:
    s.event.embedding = vec_to_blob(vec)
    return s


def test_semantic_similarity_level():
    a = _with_vec(_scored("A", 1), [1.0, 0.0, 0.0])
    b = _with_vec(_scored("B", 2), [0.95, 0.31, 0.0])  # cosine ≈ 0.95 with A
    assert _sim(a, b) == 0.6
    c = _with_vec(_scored("C", 3), [0.0, 1.0, 0.0])  # orthogonal → no semantic signal
    assert _sim(a, c) == 0.0


def test_sim_without_embeddings_unchanged():
    a, b = _scored("A", 1), _scored("B", 1)
    assert _sim(a, b) == 0.5  # same day, same category — the old rule


# ------------------------------------------------------------------- embed


def test_embed_fail_soft(monkeypatch):
    def boom():
        raise ImportError("model2vec not installed")

    monkeypatch.setattr(embed_mod, "_model", boom)
    assert embed_mod.embed("anything") is None
    assert embed_mod.embed("") is None


def test_event_embedding_text_includes_venue(mk_event, mk_venue):
    v = mk_venue()
    ev = mk_event(venue_id=v.id)
    text = embed_mod.event_text(ev, v)
    assert "Library story time" in text and "Test Library" in text
