"""Geocoding (Nominatim, cached + rate-limited), drive-time estimates
(OpenRouteService at api.heigit.org when a key is set, haversine fallback),
and Open-Meteo rain checks. No Google dependencies anywhere."""

from __future__ import annotations

import json
import math
import time

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ORS_MATRIX_URL = "https://api.heigit.org/api/v2/matrix/driving-car"  # old endpoint shut Aug 2026
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

AVG_CITY_SPEED_KMH = 38.0
DETOUR_FACTOR = 1.3


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def estimate_drive_minutes(km: float) -> float:
    return (km * DETOUR_FACTOR) / AVG_CITY_SPEED_KMH * 60.0


class GeoCache:
    """SQLite-backed caches live in store.py; this one is in-memory for tests."""

    def __init__(self, getter=None, setter=None):
        self._get, self._set = getter, setter

    def get(self, key: str) -> dict | None:
        if self._get:
            raw = self._get(f"geo:{key}")
            return json.loads(raw) if raw else None
        return None

    def put(self, key: str, value: dict) -> None:
        if self._set:
            self._set(f"geo:{key}", json.dumps(value))


def geocode(
    address: str, cache: GeoCache | None = None, client: httpx.Client | None = None
) -> tuple[float, float] | None:
    key = address.strip().lower()
    if cache and (hit := cache.get(f"nom:{key}")):
        return (hit["lat"], hit["lon"])
    last_err = None
    for attempt in range(3):
        try:
            c = client or httpx.Client(timeout=15)
            resp = c.get(
                NOMINATIM_URL,
                params={"q": address, "format": "json", "limit": 1},
                headers={"User-Agent": "hermes-local-events/0.1 (personal event finder)"},
            )
            resp.raise_for_status()
            rows = resp.json()
            if rows:
                lat, lon = float(rows[0]["lat"]), float(rows[0]["lon"])
                if cache:
                    cache.put(f"nom:{key}", {"lat": lat, "lon": lon})
                return (lat, lon)
            return None
        except Exception as e:  # noqa: BLE001 - network best-effort
            last_err = e
            time.sleep(1.1 * (attempt + 1))  # Nominatim policy: max 1 req/s
        finally:
            if client is None:
                try:
                    c.close()
                except Exception:
                    pass
    del last_err
    return None


def drive_minutes(
    home: tuple[float, float],
    dest: tuple[float, float],
    api_key: str | None = None,
    cache: GeoCache | None = None,
    client: httpx.Client | None = None,
) -> tuple[float, str]:
    """Returns (minutes, state). Uses ORS matrix when a key exists, else estimate."""
    ckey = f"drv:{home[0]:.4f},{home[1]:.4f}->{dest[0]:.4f},{dest[1]:.4f}"
    if cache and (hit := cache.get(ckey)):
        return (hit["minutes"], hit["state"])
    if api_key:
        try:
            c = client or httpx.Client(timeout=15)
            resp = c.post(
                ORS_MATRIX_URL,
                params={"api_key": api_key},
                json={
                    "locations": [[home[1], home[0]], [dest[1], dest[0]]],
                    "metrics": ["duration"],
                },
                headers={"Authorization": api_key},
            )
            resp.raise_for_status()
            secs = resp.json()["durations"][0][1]
            if secs is not None:
                mins = round(secs / 60.0, 1)
                if cache:
                    cache.put(ckey, {"minutes": mins, "state": "computed"})
                return (mins, "computed")
        except Exception:
            pass
        finally:
            if client is None:
                try:
                    c.close()
                except Exception:
                    pass
    mins = round(estimate_drive_minutes(haversine_km(*home, *dest)), 1)
    if cache:
        cache.put(ckey, {"minutes": mins, "state": "likely"})
    return (mins, "likely")


def rain_chance(lat: float, lon: float, when_iso: str, client: httpx.Client | None = None) -> float | None:
    """Precipitation probability (%) at the event start, if within forecast range."""
    try:
        c = client or httpx.Client(timeout=15)
        resp = c.get(
            OPEN_METEO_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "precipitation_probability",
                "start_date": when_iso[:10],
                "end_date": when_iso[:10],
            },
        )
        resp.raise_for_status()
        hours = resp.json().get("hourly", {})
        times = hours.get("time", [])
        probs = hours.get("precipitation_probability", [])
        target = when_iso[:13]
        for t, p in zip(times, probs, strict=False):
            if t.startswith(target) and p is not None:
                return float(p)
    except Exception:
        return None
    finally:
        if client is None:
            try:
                c.close()
            except Exception:
                pass
    return None
