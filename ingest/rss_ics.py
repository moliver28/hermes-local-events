"""Generic RSS and ICS connectors — library event platforms (BiblioCommons,
Communico) and Luma public calendars expose these directly. ICS feeds expand
RRULE recurrences into instances (library story times), all sharing series_id."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import feedparser
import httpx
import recurring_ical_events
from dateutil import parser as dtparse
from icalendar import Calendar

from core.models import Event

from .base import Connector, make_event

RECURRING_HORIZON_DAYS = 90


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
        return self.parse(body, cfg) if body else []

    def parse(self, body: str, cfg: dict) -> list[Event]:
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
        return self.parse(body, cfg) if body else []

    def parse(self, body: str, cfg: dict) -> list[Event]:
        cal = Calendar.from_ical(body)
        has_rrule = any(comp.get("RRULE") for comp in cal.walk("VEVENT"))
        if has_rrule:
            return self._expanded(cal, cfg)
        return self._plain(cal, cfg)

    def _plain(self, cal, cfg: dict) -> list[Event]:
        out = []
        for comp in cal.walk("VEVENT"):
            ev = self._instance(comp, cfg, occurrence_key=None)
            if ev:
                out.append(ev)
        return out

    def _expanded(self, cal, cfg: dict) -> list[Event]:
        """RRULE feeds become instances: one event per occurrence within the
        horizon, all sharing series_id so trends can tell series from one-offs."""
        now = datetime.now(UTC)
        window_end = now + timedelta(days=RECURRING_HORIZON_DAYS)
        out = []
        for comp in recurring_ical_events.of(cal).between(now - timedelta(days=1), window_end):
            uid = str(comp.get("UID", ""))
            ev = self._instance(comp, cfg, occurrence_key=uid)
            if ev:
                out.append(ev)
        return out

    def _instance(self, comp, cfg: dict, occurrence_key: str | None) -> Event | None:
        title = str(comp.get("SUMMARY", "")).strip()
        if not title:
            return None
        try:
            start = comp.decoded("DTSTART")
        except Exception:
            return None
        end = comp.decoded("DTEND") if comp.get("DTEND") else None
        uid = str(comp.get("UID", title))
        # per-occurrence identity: without the date suffix, dedup's same-source
        # rule would collapse every occurrence of a series into one event
        source_event_id = f"{uid}:{start.isoformat()}" if occurrence_key else uid
        series_id = f"{cfg['id']}:{uid}" if occurrence_key else None
        return make_event(
            source_id=cfg["id"],
            source_event_id=source_event_id,
            title=title,
            url=str(comp.get("URL", "") or ""),
            description=str(comp.get("DESCRIPTION", "") or "")[:1500],
            start=start,
            end=end if isinstance(end, datetime) else None,
            category=_cat_from_tags([{"term": str(comp.get("CATEGORIES", ""))}]),
            series_id=series_id,
        )
