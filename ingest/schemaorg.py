"""Generic schema.org/Event JSON-LD extractor. Many ticketed/venue pages embed
<script type="application/ld+json"> — this handles single events and listing
pages (Event/ItemList/@graph), plus one hop into detail links."""

from __future__ import annotations

import json
import re
from datetime import datetime

import httpx
from selectolax.parser import HTMLParser

from core.models import Event

from .base import Connector, fetch, make_event, venue_from


def _iter_jsonld(html: str):
    parser = HTMLParser(html)
    for node in parser.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text())
        except (json.JSONDecodeError, ValueError):
            continue
        yield from _flatten(data)


def _flatten(data) -> list[dict]:
    if isinstance(data, list):
        out = []
        for d in data:
            out.extend(_flatten(d))
        return out
    if isinstance(data, dict):
        out = []
        if "@graph" in data:
            out.extend(_flatten(data["@graph"]))
        if data.get("@type"):
            types = data["@type"]
            types = [types] if isinstance(types, str) else types
            if any(t.lower() in ("event", "eventseries") for t in types):
                out.append(data)
        return out
    return []


def _dt(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _price(offers) -> tuple[float | None, float | None, bool]:
    offers = offers or []
    if isinstance(offers, dict):
        offers = [offers]
    lows, highs, free = [], [], False
    for o in offers:
        p = o.get("price")
        if p is None:
            continue
        try:
            val = float(p)
        except (TypeError, ValueError):
            continue
        if val == 0:
            free = True
        lows.append(val)
        highs.append(val)
    return (min(lows) if lows else None, max(highs) if highs else None, free)


class JsonLdConnector(Connector):
    name = "jsonld"

    def fetch(self, cfg: dict, client: httpx.Client | None = None) -> list[Event]:
        out: list[Event] = []
        for url in cfg.get("urls", [cfg.get("url")] if cfg.get("url") else []):
            html = fetch(url, client)
            if not html:
                continue
            found = self.parse(html, cfg)
            out.extend(found)
            if not found and cfg.get("follow_links", True):
                for e in self._follow_detail_links(url, html, client, max_links=cfg.get("max_links", 15)):
                    ev = self._to_event(cfg, e)
                    if ev:
                        out.append(ev)
        return out

    def parse(self, html: str, cfg: dict) -> list[Event]:
        out = []
        for e in _iter_jsonld(html or ""):
            ev = self._to_event(cfg, e)
            if ev:
                out.append(ev)
        return out

    def _follow_detail_links(self, base_url: str, html: str, client, max_links: int) -> list[dict]:
        out = []
        parser = HTMLParser(html)
        seen = 0
        for a in parser.css("a[href]"):
            href = a.attributes.get("href") or ""
            if not href or seen >= max_links:
                break
            if not re.search(r"(event|ticket|detail)", href, re.IGNORECASE):
                continue
            if href.startswith("/"):
                from urllib.parse import urljoin

                href = urljoin(base_url, href)
            if not href.startswith("http"):
                continue
            detail = fetch(href, client)
            if detail:
                out.extend(_iter_jsonld(detail))
            seen += 1
            import time

            time.sleep(0.8)  # politeness
        return out

    def _to_event(self, cfg: dict, e: dict) -> Event | None:
        title = e.get("name")
        if not title or e.get("eventStatus", "").endswith("EventCancelled"):
            status = e.get("eventStatus", "")
            if status.endswith("EventCancelled"):
                return None
            if not title:
                return None
        start = _dt(e.get("startDate"))
        end = _dt(e.get("endDate"))
        if not start and not end:
            return None
        loc = e.get("location") or {}
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        venue = None
        if loc.get("name"):
            geo = loc.get("geo") or {}
            venue = self.register_venue(
                venue_from(
                    cfg["id"],
                    loc["name"],
                    lat=float(geo["latitude"]) if geo.get("latitude") else None,
                    lon=float(geo["longitude"]) if geo.get("longitude") else None,
                    venue_type=cfg.get("venue_type", ""),
                    address=loc.get("address", {}).get("streetAddress", "")
                    if isinstance(loc.get("address"), dict)
                    else str(loc.get("address", "")),
                )
            )
        lo, hi, free = _price(e.get("offers"))
        cats = e.get("about") or []
        if isinstance(cats, dict):
            cats = [cats]
        category = cfg.get("category") or (
            cats[0].get("name", "").lower().replace(" ", "_") if cats else "other"
        )
        desc = e.get("description", "")
        if isinstance(desc, dict):
            desc = desc.get("text", "")
        return make_event(
            source_id=cfg["id"],
            source_event_id=str(e.get("@id") or e.get("url") or title),
            title=title,
            url=e.get("url", ""),
            description=(desc or "")[:2000],
            start=start,
            end=end,
            venue=venue,
            category=category or "other",
            organizer=(e.get("organizer") or {}).get("name", "")
            if isinstance(e.get("organizer"), dict)
            else str(e.get("organizer", "")),
            price_min=lo,
            price_max=hi,
            is_free=free or cfg.get("is_free", False),
        )
