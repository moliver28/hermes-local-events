"""Connectors against recorded fixtures (offline) + store round-trips."""

from __future__ import annotations

from datetime import datetime

import pytest

import ingest.base as base
from ingest.html_list import HtmlListConnector, TacomaWeeklyConnector
from ingest.rss_ics import IcsConnector, RssConnector
from ingest.schemaorg import JsonLdConnector

JSONLD_HTML = """
<html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Event", "name": "Zoo Boo",
 "startDate": "2026-10-24T10:00:00-07:00", "endDate": "2026-10-24T17:00:00-07:00",
 "location": {"@type": "Place", "name": "Point Defiance Zoo",
              "geo": {"latitude": 47.3167, "longitude": -122.5561},
              "address": {"streetAddress": "5400 N Pearl St"}},
 "offers": {"price": "5", "priceCurrency": "USD"},
 "organizer": {"name": "PDZA"}}
</script>
</head><body><a href="/events/zoo-boo">link</a></body></html>
"""

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Events</title>
<item><title>Story time</title><link>https://x/1</link>
  <pubDate>Tue, 20 Oct 2026 10:30:00 -0700</pubDate>
  <category>story_time</category>
  <description>Songs and rhymes for toddlers</description></item>
<item><title>Guided nature walk</title><link>https://x/2</link>
  <pubDate>Sat, 24 Oct 2026 09:00:00 -0700</pubDate>
  <category>hike</category></item>
</channel></rss>
"""

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:abc123
SUMMARY: Toddler play group
DTSTART:20261021T101500
DTEND:20261021T113000
DESCRIPTION:Free play for littles
URL:https://lib/abc
END:VEVENT
END:VCALENDAR
"""

TW_HTML = """
<html><body>
<p>Saturday, Oct 24 — Arts month continues</p>
<a href="https://tacomaweekly.com/event-a">Metropolitan Arts running</a>
<p>Join local artists Saturday October 24 for open studios across the district, 10am to 4pm. Family friendly and free.</p>
</body></html>
"""


@pytest.fixture()
def _patch_fetch(monkeypatch):
    registry = {}

    def set_response(url_substr, body):
        registry[url_substr] = body

    def fake_fetch(url, client=None, timeout=20.0):
        for substr, body in registry.items():
            if substr in url:
                return body
        return None

    import ingest.html_list as html_mod
    import ingest.schemaorg as jsonld_mod

    monkeypatch.setattr(base, "fetch", fake_fetch)
    monkeypatch.setattr(jsonld_mod, "fetch", fake_fetch)
    monkeypatch.setattr(html_mod, "fetch", fake_fetch)
    monkeypatch.setattr(JsonLdConnector, "_follow_detail_links", lambda self, *a, **k: [])
    return set_response


def test_jsonld_connector(_patch_fetch):
    _patch_fetch("pdza", JSONLD_HTML)
    events = JsonLdConnector().fetch(
        {"id": "pdza", "urls": ["https://www.pdza.org/events/"], "venue_type": "zoo", "follow_links": False}
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.title == "Zoo Boo"
    assert ev.start.hour == 10
    assert ev.price_min == 5.0
    assert ev.category == "other"  # cfg category absent → about absent → other


def test_rss_connector(_patch_fetch):
    _patch_fetch("x/feed", RSS)
    events = RssConnector().fetch({"id": "southsoundtalk", "url": "https://x/feed"})
    titles = [e.title for e in events]
    assert "Story time" in titles
    walk = [e for e in events if e.title == "Guided nature walk"][0]
    assert walk.category == "parks_outdoors"
    assert walk.start.year == 2026


def test_ics_connector(_patch_fetch):
    _patch_fetch("lib/ics", ICS)
    events = IcsConnector().fetch({"id": "tacoma_public_library", "url": "https://lib/ics"})
    assert len(events) == 1
    ev = events[0]
    assert ev.title == "Toddler play group"
    assert ev.source_event_id == "abc123"
    assert isinstance(ev.start, datetime)


def test_tacomaweekly_connector(_patch_fetch):
    _patch_fetch("tacomaweekly", TW_HTML)
    events = TacomaWeeklyConnector().fetch(
        {"id": "tacomaweekly", "url": "https://www.tacomaweekly.com/index216.htm"}
    )
    assert events, "should scrape the linked title"
    ev = events[0]
    assert "Metropolitan Arts" in ev.title
    assert ev.start is not None and ev.start.month == 10


def test_html_list_generic(_patch_fetch):
    _patch_fetch(
        "parentmap",
        """
    <html><body><article><h2><a href="/e1">Pumpkin patch outing</a></h2>
    <p>Sat Oct 24, 10am. Family friendly.</p></article></body></html>""",
    )
    events = HtmlListConnector().fetch(
        {
            "id": "parentmap",
            "url": "https://www.parentmap.com/calendar",
            "item": "article",
            "title": "h2 a",
            "link": "a",
        }
    )
    assert len(events) == 1
    assert events[0].title == "Pumpkin patch outing"
