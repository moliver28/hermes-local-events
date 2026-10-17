"""Generic list-shaped HTML calendar connector (selector config in sources.yaml)
and the Tacoma Weekly digest-page scraper (date label + linked title + prose)."""

from __future__ import annotations

import re
from datetime import datetime

import httpx
from dateutil import parser as dtparse
from selectolax.parser import HTMLParser

from core.models import Event

from .base import Connector, fetch, make_event, venue_from


class HtmlListConnector(Connector):
    """cfg keys: url, item (CSS), title (CSS within item, default 'a'),
    link ('a'), date (CSS or 'auto'), description (CSS), venue_name, venue_type."""

    name = "html_list"

    def fetch(self, cfg: dict, client: httpx.Client | None = None) -> list[Event]:
        body = fetch(cfg["url"], client)
        return self.parse(body, cfg) if body else []

    def parse(self, body: str, cfg: dict) -> list[Event]:
        parser = HTMLParser(body)
        out = []
        for node in parser.css(cfg.get("item", "article, .event, li")):
            title_node = node.css_first(cfg.get("title", "a"))
            if title_node is None:
                continue
            title = title_node.text(strip=True)
            if not title or len(title) < 4:
                continue
            link_node = node.css_first(cfg.get("link", "a"))
            href = (link_node.attributes.get("href") if link_node else "") or ""
            if href.startswith("/"):
                from urllib.parse import urljoin

                href = urljoin(cfg.get("url", ""), href)
            desc_node = node.css_first(cfg.get("description", "p"))
            desc = desc_node.text(strip=True)[:1500] if desc_node else ""
            start = self._parse_date(node, cfg)
            venue = (
                self.register_venue(
                    venue_from(cfg["id"], cfg["venue_name"], venue_type=cfg.get("venue_type", ""))
                )
                if cfg.get("venue_name")
                else None
            )
            out.append(
                make_event(
                    source_id=cfg["id"],
                    source_event_id=href or title,
                    title=title,
                    url=href,
                    description=desc,
                    start=start,
                    venue=venue,
                    category=cfg.get("category", "community"),
                    is_free=cfg.get("is_free", False),
                )
            )
        return out

    def _parse_date(self, node, cfg) -> datetime | None:
        sel = cfg.get("date")
        text = ""
        if sel:
            n = node.css_first(sel)
            if n:
                text = n.text(strip=True)
        else:  # auto: whole item text; dateutil fuzzy picks the first date-ish phrase
            text = node.text(separator=" ", strip=True)[:400]
        if not text:
            return None
        try:
            return dtparse.parse(text, fuzzy=True, default=_default())
        except (ValueError, OverflowError):
            return None


class TacomaWeeklyConnector(Connector):
    """tacomaweekly.com/index216.htm: repeating blocks of date label + linked
    title + prose paragraph. Static HTML behind Cloudflare; polite fetch."""

    name = "tacomaweekly"
    DATE_RE = re.compile(
        r"(?:mon|tues?|wed(?:nesday)?|thurs?|fri|sat(?:urday)?|sun)[a-z]*,?\s+"
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?",
        re.IGNORECASE,
    )

    def fetch(self, cfg: dict, client: httpx.Client | None = None) -> list[Event]:
        body = fetch(cfg["url"], client)
        return self.parse(body, cfg) if body else []

    def parse(self, body: str, cfg: dict) -> list[Event]:
        parser = HTMLParser(body)
        out = []
        for a in parser.css("a[href]"):
            title = a.text(strip=True)
            href = a.attributes.get("href") or ""
            if not title or len(title) < 8 or not href:
                continue
            parent = a.parent
            prose = ""
            cursor = parent
            for _ in range(3):  # date label + title + paragraph live in sibling nodes
                if cursor is None:
                    break
                nxt = cursor.next if hasattr(cursor, "next") else None
                if nxt is None:
                    break
                text = nxt.text(separator=" ", strip=True) if hasattr(nxt, "text") else ""
                if self.DATE_RE.search(text or ""):
                    cursor = nxt
                    continue
                if len(text) > 60:
                    prose = text[:1500]
                    break
                cursor = nxt
            context = " ".join(
                filter(None, [parent.text(separator=" ", strip=True) if parent else "", prose])
            )[:400]
            dm = self.DATE_RE.search(context)
            start = dtparse.parse(dm.group(0), fuzzy=True, default=_default()) if dm else None
            out.append(
                make_event(
                    source_id=cfg["id"],
                    source_event_id=href,
                    title=title,
                    url=href if href.startswith("http") else cfg["url"],
                    description=prose,
                    start=start,
                    category=cfg.get("category", "community"),
                )
            )
            if len(out) >= 60:
                break
        return out


def _default():
    """dateutil default: next Friday (events pages list upcoming)."""
    from datetime import timedelta

    now = datetime.now()
    return (now + timedelta(days=(4 - now.weekday()) % 7)).replace(hour=18, minute=0, second=0, microsecond=0)
