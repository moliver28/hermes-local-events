"""Gaps + adaptive learning: RRULE expansion, series linking, conditions
(rain/AQI/alerts), metrics rates, catalog trends, trend-boosted wildcard,
facet evidence decay, agent-content ingestion, user source registration.
All offline: canned payloads, fake clients, monkeypatched probes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from core import profile as P
from core.dedup import dedupe
from core.feedback import metrics
from core.geo import air_quality, conditions_line, nws_alerts
from core.models import Energy, Event, Feedback, Household, now_utc
from core.rank import Query, rank, wildcard_pick
from core.runtime import LocalRuntime
from core.store import Store
from core.trends import catalog_stats, flaky_organizers
from ingest.rss_ics import IcsConnector
from tools import HANDLERS

ICS_RRULE = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:story-1
SUMMARY:Baby Story Time
DTSTART:20261019T101500
DURATION:PT30M
RRULE:FREQ=WEEKLY;COUNT=6
END:VEVENT
END:VCALENDAR"""

ICS_PLAIN = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:one-off-9
SUMMARY:Author Talk
DTSTART:20261020T180000
DTEND:20261020T193000
END:VEVENT
END:VCALENDAR"""


def _q():
    now = now_utc()
    return Query(start=now, end=now + timedelta(days=10), energy=Energy.NORMAL)


def _ranked(store, q, hh):
    return rank(store.events_between(q.start.isoformat(), q.end.isoformat()), q, hh, store)


# ------------------------------------------------------------------ A1 series


def test_ics_rrule_expands_instances_with_series_id():
    out = IcsConnector().parse(ICS_RRULE, {"id": "ics_test", "url": "https://x.example/feed.ics"})
    assert len(out) == 6
    assert all(e.series_id == "ics_test:story-1" for e in out)
    ids = {e.source_event_id for e in out}
    assert len(ids) == 6  # per-occurrence identity — dedup can't collapse them
    starts = sorted(e.start for e in out)
    assert len({s.date() for s in starts}) == 6


def test_ics_without_rrule_unchanged():
    out = IcsConnector().parse(ICS_PLAIN, {"id": "ics_test"})
    assert len(out) == 1
    assert out[0].series_id is None
    assert out[0].source_event_id == "one-off-9"


def test_link_series_same_title_same_venue(store, mk_event, mk_venue):
    v = mk_venue(id="ven_series")
    a = mk_event(title="Farmers Market", venue_id=v.id, start=now_utc() + timedelta(days=2))
    b = mk_event(title="Farmers  Market!", venue_id=v.id, start=now_utc() + timedelta(days=9))
    merged = dedupe([a, b])
    assert all(e.series_id for e in merged)
    assert merged[0].series_id == merged[1].series_id


def test_link_series_never_without_venue_or_across_venues(store, mk_event, mk_venue):
    v1, v2 = mk_venue(id="va"), mk_venue(id="vb")
    a = mk_event(title="Market Day", venue_id=v1.id, start=now_utc() + timedelta(days=2))
    b = mk_event(title="Market Day", venue_id=v2.id, start=now_utc() + timedelta(days=9))
    c = mk_event(title="Market Day", start=now_utc() + timedelta(days=16))
    merged = dedupe([a, b, c])
    assert all(e.series_id is None for e in merged)


# --------------------------------------------------------------- A2 conditions


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _Client:
    def __init__(self, payload):
        self.p = payload

    def get(self, url, params=None, headers=None):
        return _Resp(self.p)

    def close(self):
        pass


def test_air_quality_nearest_hour():
    payload = {
        "hourly": {
            "time": ["2026-10-19T09:00", "2026-10-19T10:00", "2026-10-19T11:00"],
            "us_aqi_pm2_5": [None, 88, 140],
        }
    }
    assert air_quality(47.3, -122.5, "2026-10-19T10:45:00+00:00", client=_Client(payload)) == 140.0
    assert air_quality(47.3, -122.5, "2026-10-19T10:15:00+00:00", client=_Client(payload)) == 88.0


def test_nws_alerts_filters_and_caps():
    payload = {
        "features": [
            {"properties": {"event": "Wind Advisory", "severity": "Moderate", "status": "Actual"}},
            {"properties": {"event": "Flood Watch", "severity": "Minor", "status": "Actual"}},
            {"properties": {"event": "Ice Storm Warning", "severity": "Extreme", "status": "Actual"}},
            {"properties": {"event": "Test", "severity": "Severe", "status": "Exercise"}},
        ]
    }
    alerts = nws_alerts(47.3, -122.5, client=_Client(payload))
    assert [a["event"] for a in alerts] == ["Wind Advisory", "Ice Storm Warning"]


def test_conditions_line_thresholds():
    assert "70% rain" in conditions_line({"rain_pct": 70, "aqi": None, "alerts": []})
    line = conditions_line({"rain_pct": 10, "aqi": 180, "alerts": []})
    assert "unhealthy" in line
    assert "sensitive" in conditions_line({"rain_pct": 10, "aqi": 120, "alerts": []})
    assert conditions_line({"rain_pct": 10, "aqi": 40, "alerts": []}) is None
    assert "⚠️" in conditions_line({"rain_pct": None, "aqi": None, "alerts": [{"event": "Wind Advisory"}]})


def test_digest_conditions_fn(store, mk_event, hh):
    soon = now_utc() + timedelta(days=3)
    ev = mk_event(title="Rainy hike", kid="verified_yes", start=soon)
    ev.effort.indoor_outdoor = "outdoor"
    store.upsert_event(ev)
    window = (now_utc(), now_utc() + timedelta(days=10))

    def _digest(conditions_fn):
        from core.digest import build_digest

        return build_digest(hh, store, window=window, conditions_fn=conditions_fn)

    base = _digest(None)
    again = _digest(None)
    assert base["text"] == again["text"]  # deterministic without conditions
    assert "80% rain" not in base["text"]
    wet = _digest(lambda ev, s: {"rain_pct": 80, "aqi": None, "alerts": []})
    assert "80% rain" in wet["text"]


# ---------------------------------------------------------------- A3 metrics


def test_metrics_rates(store, mk_event):
    ev = mk_event()
    store.add_feedback(Feedback(event_id=ev.id, signal="went", effort_match="right_effort"))
    store.add_feedback(Feedback(event_id=ev.id, signal="went", effort_match="too_much"))
    store.add_feedback(Feedback(event_id=ev.id, signal="interested", band="tired", mood="tired"))
    store.add_feedback(Feedback(event_id=ev.id, signal="interested", band="normal", mood="tired"))
    m = metrics(store)
    assert m["effort_match_rate"] == 0.5
    assert m["band_mood_match_rate"] == 0.5


def test_metrics_rates_none_without_data(store):
    assert metrics(store)["effort_match_rate"] is None
    assert metrics(store)["band_mood_match_rate"] is None


# ----------------------------------------------------------------- A4 trends


def test_catalog_stats_trending_and_flaky(store, mk_event):
    soon = now_utc() + timedelta(days=3)
    mid = now_utc() + timedelta(days=45)
    for i in range(6):
        mk_event(title=f"Story {i}", category="story", start=soon + timedelta(hours=i))
    for i in range(3):
        mk_event(title=f"Festival {i}", category="festival", start=mid + timedelta(hours=i))
    for i in range(4):
        mk_event(
            title=f"Flaky show {i}",
            category="other",
            organizer="Flaky Co",
            start=soon + timedelta(hours=i),
            status="cancelled" if i < 2 else "postponed" if i == 2 else "scheduled",
        )
    stats = catalog_stats(store)
    # story: 6/7 of the 30-day window vs 6/10 of the 90-day baseline
    assert stats["trending"]["story"] == 1.43
    assert "festival" not in stats["trending"]  # nothing starting in the near window
    assert stats["organizers"]["Flaky Co"]["flaky"] is True
    assert flaky_organizers(store) == {"Flaky Co"}


def test_wildcard_trend_boost(monkeypatch, store, mk_event, hh):
    for i in range(6):
        mk_event(
            title=f"E{i}",
            start=now_utc() + timedelta(days=i + 1),
            category="zoo" if i in (1, 5) else "library",
        )
    scored = _ranked(store, _q(), hh)
    pool_cats = {s.event.category for s in scored[3:]}
    assert pool_cats == {"library", "zoo"}  # both categories reachable by the wildcard
    monkeypatch.setattr(P, "thompson_sample", lambda hh, keys, rng: 0.5)
    plain = wildcard_pick(scored, hh, "s")
    boosted = wildcard_pick(scored, hh, "s", trends={"zoo": 3.0})
    assert plain.event.category == "library"
    assert boosted.event.category == "zoo"
    assert boosted.reasons[0] == "wildcard — trending in your area"
    plain2 = wildcard_pick(scored, hh, "s")
    assert plain2.reasons[0] == "wildcard — exploring outside your usual"


def test_flaky_organizer_reason_annotated(store, mk_event):
    soon = now_utc() + timedelta(days=2)
    mk_event(title="A show", kid="verified_yes", organizer="NoShow Arts", start=soon)
    mk_event(title="B show", kid="verified_yes", organizer="NoShow Arts", start=soon + timedelta(days=1))
    mk_event(
        title="C show",
        kid="verified_yes",
        organizer="NoShow Arts",
        start=soon + timedelta(days=2),
        status="cancelled",
    )
    scored = _ranked(store, _q(), Household())
    assert any("cancellations" in r for r in scored[0].reasons)


# -------------------------------------------------------------- B facet decay


def test_facet_evidence_decays_toward_prior():
    hh = Household()
    t0 = datetime(2026, 6, 1, tzinfo=UTC)
    P.update_facets(hh, ["cat:zoo"], Feedback(event_id="e", signal="went"), when=t0)
    fresh_a, fresh_b = P._decayed_facet(hh, "cat:zoo", when=t0)
    assert (fresh_a, fresh_b) == (P.ALPHA0 + 2.0, P.BETA0)
    old_a, old_b = P._decayed_facet(hh, "cat:zoo", when=t0 + timedelta(days=120))
    assert old_a == P.ALPHA0 + 1.0  # one halflife → evidence halves toward prior
    assert old_b == P.BETA0


def test_facet_without_timestamp_passthrough():
    hh = Household()
    hh.facet_alpha["x"] = 10.0
    assert P._decayed_facet(hh, "x") == (10.0, P.BETA0)


# ------------------------------------------- C agent content + user sources


JSONLD_PAGE = """<html><head><script type="application/ld+json">
{"@type":"Event","name":"Zoo Day","startDate":"2026-10-25T10:00:00+00:00",
 "location":{"name":"Point Defiance Zoo","geo":{"latitude":47.3,"longitude":-122.55}}}
</script></head><body>zoo</body></html>"""


def _rt(tmp_path):
    return LocalRuntime(data_dir=tmp_path)


def test_ingest_content_jsonld_enters_pipeline(tmp_path):
    rt = _rt(tmp_path)
    out = json.loads(
        HANDLERS["events_ingest_content"]({"content": JSONLD_PAGE, "source_id": "agent_zoo"}, rt=rt)
    )
    assert out["ok"] and out["new_events"] == 1 and out["venues_upserted"] == 1
    store = Store(rt.db_path)
    ev = next(iter(store.search_text("Zoo Day")))
    assert ev.provenance[0]["method"] == "agent-browser"
    assert store.get_venue(ev.venue_id).name == "Point Defiance Zoo"
    store.close()


def test_ingest_content_rejects_private_url(tmp_path):
    rt = _rt(tmp_path)
    out = json.loads(
        HANDLERS["events_ingest_content"](
            {"content": JSONLD_PAGE, "url": "http://127.0.0.1:8080/events"}, rt=rt
        )
    )
    assert not out["ok"] and "rejected" in out["error"]


def test_ingest_content_ics(tmp_path):
    rt = _rt(tmp_path)
    out = json.loads(
        HANDLERS["events_ingest_content"]({"content": ICS_RRULE, "source_id": "agent_ics"}, rt=rt)
    )
    assert out["ok"] and out["parsed"] == 6


def test_sources_register_and_forget(tmp_path, monkeypatch):
    import tools

    rt = _rt(tmp_path)
    monkeypatch.setattr(
        tools,
        "safe_probe",
        lambda url, timeout=15.0: {"ok": True, "status": 200, "text": "<rss><channel>events</channel>"},
    )
    out = json.loads(
        HANDLERS["events_sources"](
            {"action": "register", "url": "https://feeds.example.com/city-events/rss", "name": "City Events"},
            rt=rt,
        )
    )
    assert out["ok"] and out["registered"]["via"] == "http" and out["registered"]["connector"] == "rss"
    listing = json.loads(HANDLERS["events_sources"]({"action": "list"}, rt=rt))
    assert any(s["id"] == out["registered"]["id"] and s["via"] == "http" for s in listing["sources"])
    # refresh merges it
    from ingest import runner

    assert [s["id"] for s in runner.load_user_sources(rt)] == [out["registered"]["id"]]
    gone = json.loads(
        HANDLERS["events_sources"]({"action": "forget", "source_id": out["registered"]["id"]}, rt=rt)
    )
    assert gone["ok"] and gone["remaining_user_sources"] == 0
    assert runner.load_user_sources(rt) == []


def test_sources_register_rejects_bogus_host(tmp_path, monkeypatch):
    import tools

    rt = _rt(tmp_path)
    monkeypatch.setattr(
        tools,
        "safe_probe",
        lambda url, timeout=15.0: {"ok": False, "error": "host resolves to a private/blocked address"},
    )
    out = json.loads(
        HANDLERS["events_sources"]({"action": "register", "url": "https://internal.example/feed"}, rt=rt)
    )
    assert not out["ok"] and "rejected" in out["error"]


def test_sources_register_browser_walled_ok(tmp_path, monkeypatch):
    import tools

    rt = _rt(tmp_path)
    monkeypatch.setattr(
        tools, "safe_probe", lambda url, timeout=15.0: {"ok": False, "error": "fetch failed: ConnectError"}
    )
    out = json.loads(
        HANDLERS["events_sources"](
            {"action": "register", "url": "https://walled.example.com/calendar", "connector": "html_list"},
            rt=rt,
        )
    )
    assert out["ok"] and out["registered"]["via"] == "browser"


def test_conditions_tool(tmp_path, monkeypatch):
    rt = _rt(tmp_path)
    store = Store(rt.db_path)
    from core.models import Venue

    v = Venue(id="ven_c1", name="Point Defiance Zoo", lat=47.3, lon=-122.55, venue_type="zoo")
    store.upsert_venue(v)
    ev = Event(
        title="Zoo morning",
        source_id="t",
        source_event_id="zoo-1",
        start=now_utc() + timedelta(days=2),
        venue_id=v.id,
        kid="verified_yes",
    )
    store.upsert_event(ev)
    store.close()
    import core.geo

    monkeypatch.setattr(
        core.geo,
        "conditions",
        lambda lat, lon, when_iso, client=None: {"rain_pct": 65, "aqi": 45, "alerts": []},
    )
    out = json.loads(HANDLERS["events_conditions"]({"event_ref": ev.id}, rt=rt))
    assert out["ok"] and "65% rain" in out["line"]
