"""Connector base: polite fetching (UA, timeout, per-source rate limit), venue
upserts, Event construction. Connectors yield raw dicts; runner normalizes."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from core.models import Event, Venue, uid

USER_AGENT = "hermes-local-events/0.1 (personal family event finder; polite)"


def fetch(url: str, client: httpx.Client | None = None, timeout: float = 20.0) -> str | None:
    try:
        c = client or httpx.Client(timeout=timeout, follow_redirects=True)
        resp = c.get(url, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None
    finally:
        if client is None:
            try:
                c.close()
            except Exception:
                pass


def venue_from(
    source_id: str,
    name: str,
    lat=None,
    lon=None,
    venue_type: str = "",
    address: str = "",
    venue_id: str | None = None,
) -> Venue:
    vid = venue_id or f"ven_{source_id}_{abs(hash(name)) % 10**10}"
    return Venue(
        id=vid, name=name, lat=lat, lon=lon, address=address, venue_type=venue_type, source=source_id
    )


def make_event(
    source_id: str,
    source_event_id: str,
    title: str,
    url: str = "",
    description: str = "",
    start: datetime | None = None,
    end: datetime | None = None,
    venue: Venue | None = None,
    category: str = "other",
    organizer: str = "",
    price_min: float | None = None,
    price_max: float | None = None,
    is_free: bool = False,
    subcategory: str = "",
    series_id: str | None = None,
) -> Event:
    prov = [
        {
            "source_id": source_id,
            "source_event_id": source_event_id,
            "source_url": url,
            "retrieved_at": datetime.now(UTC).isoformat(),
        }
    ]
    if venue and venue.lat is not None:
        prov[0]["lat"], prov[0]["lon"] = venue.lat, venue.lon
    return Event(
        id=uid(),
        title=title.strip() or "(untitled)",
        url=url,
        description=description,
        source_id=source_id,
        source_event_id=source_event_id,
        start=start,
        end=end,
        venue_id=venue.id if venue else None,
        category=category,
        subcategory=subcategory,
        organizer=organizer,
        price_min=price_min,
        price_max=price_max,
        is_free=is_free,
        series_id=series_id,
        provenance=prov,
    )


class Connector:
    """Each connector: fetch(conn_cfg) -> list[Event] with venues attached where possible.
    parse(body, cfg) is the fetch-free seam — agent-fetched content reuses it."""

    name = "base"

    def __init__(self):
        self.fetched_venues: list[Venue] = []

    def register_venue(self, v: Venue | None) -> Venue | None:
        """Track venues built during parse/fetch so the runner can persist them."""
        if v is not None and all(x.id != v.id for x in self.fetched_venues):
            self.fetched_venues.append(v)
        return v

    def fetch(self, cfg: dict, client: httpx.Client | None = None) -> list[Event]:
        raise NotImplementedError

    def parse(self, body: str, cfg: dict) -> list[Event]:
        """Fetch-free parsing seam (override); raise rather than silently re-fetch."""
        raise NotImplementedError
