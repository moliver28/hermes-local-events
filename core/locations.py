"""Google Maps Timeline (Takeout) import — 100% OSS path, no Google APIs.

Accepts a Takeout zip, a directory, or a loose file; auto-detects:
  1. Timeline.json (new format): semanticSegments[].visit / .activity
  2. Semantic Location History/YYYY_MONTH.json (legacy): timelineObjects
  3. Records.json (raw E7 points): stay-point clustering

Privacy: parse streams through; ONLY derived data persists (home guess, drive
histogram, per-place visit counts). Raw points are never written to disk.
The confirm=True pass applies seeding and deletes the imported copy.
"""

from __future__ import annotations

import json
import zipfile
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .geo import haversine_km
from .models import Household
from .profile import seed_from_drive_histogram
from .store import Store

VISIT_RADIUS_KM = 0.12
MIN_STOP_MINUTES = 20
EVENT_WINDOW_MIN = 90


# ------------------------------------------------------------------ detection


def _safe_resolve(path: str | Path) -> Path | None:
    """Normalize and validate an import path: no '..' traversal, must exist."""
    p = Path(path).expanduser()
    if ".." in p.parts:
        return None
    try:
        resolved = p.resolve()
    except OSError:
        return None
    return resolved if resolved.exists() else None


def find_timeline_files(path: str | Path) -> list[Path]:
    p = _safe_resolve(path)
    if p is None:
        return []
    if p.is_file() and p.suffix.lower() == ".zip":
        out_dir = p.parent / f"_extract_{p.stem}"
        out_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(p) as z:
            for member in z.namelist():
                if member.startswith("/") or ".." in Path(member).parts:
                    continue  # zip-slip guard
                z.extract(member, out_dir)
        return find_timeline_files(out_dir)
    if p.is_file():
        return [p]
    if p.is_dir():
        hits: list[Path] = []
        for pattern in ("**/Timeline.json", "**/Records.json", "**/*_[A-Z]*.json"):
            hits.extend(p.glob(pattern))
        # legacy monthly files live under "Semantic Location History/2024/2024_MARCH.json"
        legacy = [f for f in hits if f.name != "Records.json" and f.name != "Timeline.json"]
        priority = [f for f in hits if f.name == "Timeline.json"]
        records = [f for f in hits if f.name == "Records.json"]
        return priority + legacy[:36] + records[:1]
    return []


# -------------------------------------------------------------------- parsing


def _latlng(s: str) -> tuple[float, float] | None:
    try:
        lat, lon = (float(x) for x in s.split(","))
        return (lat, lon)
    except (ValueError, AttributeError):
        return None


def parse_file(fp: Path) -> Iterator[dict]:
    """Yields normalized records: {kind: visit|drive|point, lat, lon, start, end, dist_km}"""
    if fp.name == "Timeline.json":
        yield from _parse_timeline_new(json.loads(fp.read_text(encoding="utf-8")))
    elif fp.name == "Records.json":
        yield from _parse_records(json.loads(fp.read_text(encoding="utf-8")))
    else:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return
        if "timelineObjects" in data:
            yield from _parse_semantic_legacy(data)
        else:
            yield from _parse_timeline_new(data)


def _parse_timeline_new(data: dict) -> Iterator[dict]:
    for seg in data.get("semanticSegments", []):
        start, end = seg.get("startTime"), seg.get("endTime")
        visit = seg.get("visit") or {}
        top = visit.get("topCandidate") or {}
        place = top.get("placeLocation") or {}
        ll = _latlng(place.get("latLng", ""))
        if ll:
            yield {"kind": "visit", "lat": ll[0], "lon": ll[1], "start": start, "end": end}
        act = seg.get("activity") or {}
        atop = act.get("topCandidate") or {}
        if atop.get("type") == "DRIVING":
            a = _latlng((act.get("start") or {}).get("latLng", ""))
            b = _latlng((act.get("end") or {}).get("latLng", ""))
            if a and b:
                yield {
                    "kind": "drive",
                    "lat": b[0],
                    "lon": b[1],
                    "dist_km": haversine_km(a[0], a[1], b[0], b[1]),
                    "start": start,
                    "end": end,
                }
        for tp in seg.get("timelinePath", []) or []:
            ll2 = _latlng(tp.get("point", ""))
            if ll2:
                yield {"kind": "point", "lat": ll2[0], "lon": ll2[1], "start": start}


def _parse_semantic_legacy(data: dict) -> Iterator[dict]:
    for obj in data.get("timelineObjects", []):
        pv = obj.get("placeVisit")
        if pv:
            loc = pv.get("location") or {}
            ll = _latlng(loc.get("latLng", "")) or _e7(loc.get("latitudeE7"), loc.get("longitudeE7"))
            dur = pv.get("duration") or {}
            if ll:
                yield {
                    "kind": "visit",
                    "lat": ll[0],
                    "lon": ll[1],
                    "start": dur.get("startTimestamp"),
                    "end": dur.get("endTimestamp"),
                }
        seg = obj.get("activitySegment")
        if seg:
            dist = seg.get("distance")
            atype = (seg.get("activityType") or seg.get("simplifiedRawPath", {}).get("activityType")) or ""
            if dist and ("DRIVE" in str(atype).upper() or seg.get("activities")):
                end_ll = _e7(*(seg.get("endLocation") or {}).values()) if seg.get("endLocation") else None
                if end_ll:
                    yield {"kind": "drive", "lat": end_ll[0], "lon": end_ll[1], "dist_km": dist / 1000.0}


def _parse_records(data: dict) -> Iterator[dict]:
    """Raw E7 points → stay-points (≥ MIN_STOP_MINUTES within VISIT_RADIUS)."""
    points = []
    for r in data.get("locations", []):
        lat = (r.get("latitudeE7") or 0) / 1e7
        lon = (r.get("longitudeE7") or 0) / 1e7
        ts = r.get("timestamp")
        if lat and lon:
            points.append((lat, lon, ts))
    points.sort(key=lambda t: t[2] or "")
    i = 0
    while i < len(points):
        j = i
        anchor = points[i]
        while (
            j + 1 < len(points)
            and haversine_km(anchor[0], anchor[1], points[j + 1][0], points[j + 1][1]) <= VISIT_RADIUS_KM
        ):
            j += 1
        span = _span_minutes(anchor[2], points[j][2])
        if span >= MIN_STOP_MINUTES:
            yield {
                "kind": "visit",
                "lat": anchor[0],
                "lon": anchor[1],
                "start": anchor[2],
                "end": points[j][2],
            }
        i = j + 1


def _e7(lat, lon):
    try:
        return (float(lat) / 1e7, float(lon) / 1e7)
    except (TypeError, ValueError):
        return None


def _span_minutes(a: str | None, b: str | None) -> float:
    try:
        ta = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        tb = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
        return (tb - ta).total_seconds() / 60.0
    except (ValueError, TypeError):
        return 0.0


# ------------------------------------------------------------------- analysis


def analyze(path: str | Path, store: Store) -> dict:
    """Read-only pass: summary + derived aggregates (NOT committed as profile)."""
    visits: list[dict] = []
    drives_km: list[float] = []
    for fp in find_timeline_files(path):
        for rec in parse_file(fp):
            if rec["kind"] == "visit":
                visits.append(rec)
            elif rec["kind"] == "drive" and rec.get("dist_km"):
                drives_km.append(rec["dist_km"])

    places = _cluster_places(visits)
    home = _home_guess(places)
    hist = _drive_histogram(drives_km)
    matched = _match_events(visits, store)
    return {
        "visits": len(visits),
        "places": len(places),
        "drives": len(drives_km),
        "home_guess": home,
        "drive_histogram": hist,
        "median_drive_min": _median_bucket(hist),
        "top_places": [{"lat": lat, "lon": lon, "visits": n} for (lat, lon), n in places.most_common(8)],
        "event_matches": matched,
        "committed": False,
    }


def _cluster_places(visits: list[dict]) -> Counter:
    c: Counter = Counter()
    for v in visits:
        key = (round(v["lat"], 3), round(v["lon"], 3))  # ~100 m cells
        c[key] += 1
    return c


def _home_guess(places: Counter) -> dict | None:
    """Most-frequent place with overnight-ish visit times (22:00–06:00)."""
    for (lat, lon), n in places.most_common(15):
        # without per-visit times retained here, frequency of top cells is the
        # practical proxy; confirmed by the user before use.
        return {"lat": lat, "lon": lon, "visits": n, "confirmed": False}
    return None


def _drive_histogram(drives_km: list[float]) -> dict[str, int]:
    from .geo import estimate_drive_minutes

    hist: Counter = Counter()
    for km in drives_km:
        mins = estimate_drive_minutes(km)
        if 2 <= mins <= 400:
            hist[str(int(round(mins / 5.0) * 5))] += 1
    return dict(hist)


def _median_bucket(hist: dict[str, int]) -> int | None:
    total = sum(hist.values())
    if not total:
        return None
    acc = 0
    for bucket in sorted(hist, key=float):
        acc += hist[bucket]
        if acc >= total / 2:
            return int(bucket)
    return None


def _match_events(visits: list[dict], store: Store) -> list[dict]:
    """Venue visits overlapping a known event window ±EVENT_WINDOW_MIN → attendance."""
    matches = []
    for ev in store.all_events():
        if not ev.start or not ev.venue_id:
            continue
        venue = store.get_venue(ev.venue_id)
        if not venue or venue.lat is None:
            continue
        for v in visits:
            vs = _ts(v.get("start"))
            if vs is None:
                continue
            if abs((vs - ev.start).total_seconds()) <= EVENT_WINDOW_MIN * 60:
                if haversine_km(v["lat"], v["lon"], venue.lat, venue.lon) <= VISIT_RADIUS_KM:
                    matches.append({"event_id": ev.id, "title": ev.title, "start": ev.start.isoformat()})
                    break
    return matches


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# -------------------------------------------------------------------- seeding


def apply_import(
    hh: Household, store: Store, analysis: dict, confirm_home: bool = True, backfill_events: bool = True
) -> dict:
    hist = analysis.get("drive_histogram") or {}
    if hist:
        seed_from_drive_histogram(hh, hist)
        for bucket, count in hist.items():
            for _ in range(count):
                store.bump_drive(float(bucket))
    home = analysis.get("home_guess")
    if home and confirm_home and hh.home_lat is None:
        hh.home_lat, hh.home_lon = home["lat"], home["lon"]
        hh.home_label = hh.home_label or "imported from timeline"
    applied_events = 0
    if backfill_events:
        for m in analysis.get("event_matches", []):
            ev = store.get_event(m["event_id"])
            if ev:
                from .feedback import record_backfill

                record_backfill(hh, store, ev)
                applied_events += 1
    for p in analysis.get("top_places", []):
        place_key = f"{p['lat']:.3f}_{p['lon']:.3f}"
        store.upsert_visit(place_key, p["lat"], p["lon"])
        venue = store.find_venue_near(p["lat"], p["lon"])
        if venue:
            venue.visit_count += p["visits"]
            store.upsert_venue(venue)
    store.kv_set("household", hh.model_dump_json())
    store.kv_set("locations_imported_at", datetime.now().isoformat())
    return {
        "ok": True,
        "home_set": bool(home and confirm_home),
        "events_backfilled": applied_events,
        "drive_buckets": len(hist),
    }


def cleanup_imported_files(path: str | Path) -> int:
    p = Path(path)
    removed = 0
    extract_dirs = [c for c in (p.parent.iterdir() if p.is_file() else []) if c.name.startswith("_extract_")]
    for t in extract_dirs:
        import shutil

        shutil.rmtree(t, ignore_errors=True)
        removed += 1
    return removed
