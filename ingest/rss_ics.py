"""Generic RSS and ICS connectors — library event platforms (BiblioCommons,
Communico) and Luma public calendars expose these directly."""

from __future__ import annotations

from datetime import datetime

import feedparser
import httpx
from dateutil import parser as dtparse
from icalendar import Calendar

from core.models import Event

from .base import Connector, make_event


def _cat_from_tags(tags) -> str:
    text = " ".join((t.get("term") or "") for t in (tags or [])).lower()
    for kw, cat in (
        ("story", "library"),
        ("hike", "parks_outdoors"),
        ("concert", "music"),
        ("market", "food_drink"),
        ("festival", "community"),
        ("movie", "screen"),
    ):
        if kw in text:
            return cat
    return "community"


class RssConnector(Connector):
    name = "rss"

    def fetch(self, cfg: dict, client: httpx.Client | None = None) -> list[Event]:
        import ingest.base as base

        body = base.fetch(cfg["url"], client)
        if not body:
            return []
        feed = feedparser.parse(body)
        out = []
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            if not title:
                continue
            when = None
            for k in ("published_parsed", "updated_parsed"):
                if entry.get(k):
                    when = datetime(*entry[k][:6])
                    break
            if when is None and entry.get("published"):
                when = dtparse.parse(entry["published"], fuzzy=True)
            sid = entry.get("id") or entry.get("link", title)
            out.append(
                make_event(
                    source_id=cfg["id"],
                    source_event_id=sid,
                    title=title,
                    url=entry.get("link", ""),
                    description=entry.get("summary", "")[:1500],
                    start=when,
                    category=_cat_from_tags(entry.get("tags")),
                )
            )
        return out


class IcsConnector(Connector):
    name = "ics"

    def fetch(self, cfg: dict, client: httpx.Client | None = None) -> list[Event]:
        import ingest.base as base

        body = base.fetch(cfg["url"], client)
        if not body:
            return []
        cal = Calendar.from_ical(body)
        out = []
        for comp in cal.walk("VEVENT"):
            title = str(comp.get("SUMMARY", "")).strip()
            if not title:
                continue
            start = comp.decoded("DTSTART")
            end = comp.decoded("DTEND") if comp.get("DTEND") else None
            if isinstance(start, datetime) and start.tzinfo is None:
                # floating local times on library calendars are US Pacific in our region
                start = start.replace(tzinfo=None)
            out.append(
                make_event(
                    source_id=cfg["id"],
                    source_event_id=str(comp.get("UID", title)),
                    title=title,
                    url=str(comp.get("URL", "") or ""),
                    description=str(comp.get("DESCRIPTION", "") or "")[:1500],
                    start=start,
                    end=end if isinstance(end, datetime) else None,
                    category=_cat_from_tags([{"term": str(comp.get("CATEGORIES", ""))}]),
                )
            )
        return out
