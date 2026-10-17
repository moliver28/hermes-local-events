"""Entity resolution, right-sized: deterministic blocking + fuzzy title match.
Blocking key = (date ±1d, geohash-ish cell, title token). RapidFuzz for text.
No Splink — at ≤2k live events the blocks resolve nearly all duplicates and a
human can audit weekly. A post-pass links recurring occurrences into series."""

from __future__ import annotations

import re
from datetime import timedelta
from uuid import uuid4

from rapidfuzz import fuzz

from .models import Event

STOPWORDS = {"the", "a", "an", "at", "of", "on", "in", "and", "for", "to", "with", "2024", "2025", "2026"}
WEEK = timedelta(days=1)


def norm_title(t: str) -> str:
    t = re.sub(r"[^\w\s]", " ", (t or "").lower())
    return " ".join(w for w in t.split() if w not in STOPWORDS)


def cell(lat: float | None, lon: float | None, precision: int = 2) -> str:
    """Cheap geohash-ish cell (2 decimals ≈ 1.1 km)."""
    if lat is None or lon is None:
        return "?"
    return f"{lat:.{precision}f}_{lon:.{precision}f}"


def same_occurrence(a: Event, b: Event) -> bool:
    """True when both records describe the same real-world occurrence."""
    if a.source_id == b.source_id and a.source_event_id == b.source_event_id:
        return True
    if not a.start or not b.start:
        return False
    if abs((a.start - b.start).total_seconds()) > 90 * 60:  # different occurrence
        return False
    tsim = fuzz.token_sort_ratio(norm_title(a.title), norm_title(b.title))
    if tsim >= 85:
        return True
    if tsim >= 70:
        va = a.venue_id or "?"
        vb = b.venue_id or "??"
        if va == vb and va != "?":
            return True
        if a.provenance and b.provenance:
            ua = (a.provenance[0].get("source_url") or "").rstrip("/")
            ub = (b.provenance[0].get("source_url") or "").rstrip("/")
            if ua and ua == ub:
                return True
    return False


def dedupe(events: list[Event]) -> list[Event]:
    """O(n·k) via blocks; merges keep the richest fields and union provenance."""
    blocks: dict[tuple, list[int]] = {}
    for i, e in enumerate(events):
        day = e.start.date() if e.start else None
        keys = []
        for lat, lon in _coords(e):
            keys.append((day, cell(lat, lon)))
        keys.append((day, "nocell"))
        for k in keys:
            blocks.setdefault(k, []).append(i)

    parent = list(range(len(events)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for bucket in blocks.values():
        for ii in range(len(bucket)):
            for jj in range(ii + 1, len(bucket)):
                a, b = events[bucket[ii]], events[bucket[jj]]
                if find(bucket[ii]) != find(bucket[jj]) and same_occurrence(a, b):
                    union(bucket[ii], bucket[jj])

    clusters: dict[int, list[int]] = {}
    for i in range(len(events)):
        clusters.setdefault(find(i), []).append(i)

    merged = []
    for members in clusters.values():
        if len(members) == 1:
            merged.append(events[members[0]])
        else:
            merged.append(_merge([events[m] for m in members]))
    link_series(merged)
    merged.sort(key=lambda e: e.start or events[0].start)
    return merged


def link_series(merged: list[Event]) -> None:
    """Same normalized title + same known venue on ≥2 distinct days = a
    recurring series (farmers market, weekly meet). Assign a shared series_id
    so trends can tell series from one-offs. Never links on unknown venue —
    "Story Time" at two libraries must not become one series. Events that
    already carry series_id (ICS RRULE expansion) are left alone."""
    groups: dict[tuple, list[Event]] = {}
    for e in merged:
        if e.series_id or not e.venue_id:
            continue
        groups.setdefault((norm_title(e.title), e.venue_id), []).append(e)
    for members in groups.values():
        days = {m.start.date() for m in members if m.start}
        if len(members) >= 2 and len(days) >= 2:
            sid = f"ser_{uuid4().hex[:10]}"
            for m in members:
                m.series_id = sid


def _coords(e: Event) -> list[tuple[float | None, float | None]]:
    # venue coords ride on provenance for dedup purposes; venue_id equality handles the rest
    lat = e.provenance[0].get("lat") if e.provenance else None
    lon = e.provenance[0].get("lon") if e.provenance else None
    return [(lat, lon)] if lat is not None else []


def _merge(events: list[Event]) -> Event:
    """Field-wise canonical selection: prefer fields present, richer source wins,
    union provenance. Deterministic: first event anchors identity."""
    ev = events[0].model_copy(deep=True)
    seen_prov = {p.get("source_id") for p in ev.provenance}
    for other in events[1:]:
        for p in other.provenance:
            if p.get("source_id") not in seen_prov:
                ev.provenance.append(p)
                seen_prov.add(p.get("source_id"))
        for f in ("description", "url", "organizer", "image"):
            if not getattr(ev, f, "") and getattr(other, f, ""):
                setattr(ev, f, getattr(other, f))
        for f in ("price_min", "price_max"):
            ov = getattr(other, f)
            if ov is not None and getattr(ev, f) is None:
                setattr(ev, f, ov)
        if ev.venue_id is None and other.venue_id is not None:
            ev.venue_id = other.venue_id
        if ev.end is None and other.end is not None:
            ev.end = other.end
        # suitability: verified beats likely beats unknown; a verified_no is sticky
        ev.kid = _combine_state(ev.kid, other.kid)
        ev.dog = _combine_state(ev.dog, other.dog)
        if ev.audience.value == "unknown" and other.audience.value != "unknown":
            ev.audience = other.audience
        if other.last_verified > ev.last_verified:
            ev.last_verified = other.last_verified
    return ev


_ORDER = {"verified_yes": 3, "likely": 2, "unknown": 1, "verified_no": 0}


def _combine_state(a, b):
    """verified_no always wins (safety); otherwise highest evidence wins."""
    if a.value == "verified_no" or b.value == "verified_no":
        from .models import Suitability

        return Suitability.VERIFIED_NO
    from .models import Suitability

    return a if _ORDER[a.value] >= _ORDER[b.value] else b
