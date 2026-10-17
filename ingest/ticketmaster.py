"""Ticketmaster Discovery API connector (free key, 5k calls/day).
Key is optional: source is skipped with a notice when absent."""

from __future__ import annotations

from datetime import datetime

import httpx

from core.models import Event

from .base import Connector, make_event, venue_from

DISCOVERY_URL = "https://app.ticketmaster.com/discovery/v2/events"


class TicketmasterConnector(Connector):
    name = "ticketmaster"

    def fetch(self, cfg: dict, client: httpx.Client | None = None, api_key: str = "") -> list[Event]:
        if not api_key:
            return []
        params = {
            "apikey": api_key,
            "latlong": cfg.get("latlong", "47.2529,-122.4443"),  # Tacoma default
            "radius": str(cfg.get("radius_miles", 30)),
            "size": str(cfg.get("page_size", 50)),
            "sort": "date,asc",
        }
        if cfg.get("start_date"):
            params["startDateTime"] = cfg["start_date"]
        try:
            c = client or httpx.Client(timeout=20)
            resp = c.get(DISCOVERY_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        finally:
            if client is None:
                try:
                    c.close()
                except Exception:
                    pass
        out = []
        for e in data.get("_embedded", {}).get("events", []):
            name = e.get("name", "")
            if not name:
                continue
            dates = e.get("dates", {})
            start = dates.get("start", {})
            when = None
            try:
                when = datetime.fromisoformat(
                    f"{start.get('localDate')}T{start.get('localTime', '00:00:00')}"
                )
            except (ValueError, TypeError):
                pass
            vens = e.get("_embedded", {}).get("venues", [{}])
            v = vens[0] if vens else {}
            geo = v.get("location") or {}
            venue = None
            if v.get("name"):
                venue = venue_from(
                    cfg["id"],
                    v["name"],
                    lat=float(geo["latitude"]) if geo.get("latitude") else None,
                    lon=float(geo["longitude"]) if geo.get("longitude") else None,
                    venue_type="arena",
                    address=" ".join(
                        filter(
                            None, [v.get("address", {}).get("line1", ""), v.get("city", {}).get("name", "")]
                        )
                    ),
                )
            price_ranges = e.get("priceRanges", [{}])
            lo = price_ranges[0].get("min") if price_ranges else None
            hi = price_ranges[0].get("max") if price_ranges else None
            age = e.get("ageMin") or e.get("accessibility", {}).get("ageMin")
            classifications = e.get("classifications", [{}])
            genre = (classifications[0].get("genre") or {}).get("name", "") if classifications else ""
            out.append(
                make_event(
                    source_id=cfg["id"],
                    source_event_id=e.get("id", name),
                    title=name,
                    url=e.get("url", ""),
                    description="",
                    start=when,
                    venue=venue,
                    category=_map_genre(genre, cfg),
                    organizer=(e.get("promoter") or {}).get("name", ""),
                    price_min=lo,
                    price_max=hi,
                    is_free=bool(lo == 0),
                )
            )
            if age:
                out[-1].age_min = int(age) if str(age).isdigit() else None
        return out


def _map_genre(genre: str, cfg: dict) -> str:
    g = genre.lower()
    for kw, cat in (
        ("music", "music"),
        ("theatre", "performing_arts"),
        ("theater", "performing_arts"),
        ("comedy", "performing_arts"),
        ("basketball", "sports"),
        ("football", "sports"),
        ("baseball", "sports"),
        ("hockey", "sports"),
        ("family", "community"),
    ):
        if kw in g:
            return cat
    return cfg.get("category", "music")
