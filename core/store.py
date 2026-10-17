"""SQLite store: canonical events + venues + feedback + kv profile + geo caches.
WAL mode; FTS5 for text; embeddings as BLOBs with pure-python cosine (≤2k rows)."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Audience, Event, EventStatus, Feedback, Suitability, Venue

SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, lat REAL, lon REAL, address TEXT DEFAULT '',
  venue_type TEXT DEFAULT '', indoor_outdoor TEXT DEFAULT 'unknown', source TEXT DEFAULT '',
  visit_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, series_id TEXT, title TEXT NOT NULL, description TEXT DEFAULT '',
  url TEXT DEFAULT '', source_id TEXT DEFAULT '', source_event_id TEXT DEFAULT '',
  start TEXT, end TEXT, timezone TEXT DEFAULT 'America/Los_Angeles',
  venue_id TEXT, category TEXT DEFAULT 'other', subcategory TEXT DEFAULT '',
  organizer TEXT DEFAULT '', price_min REAL, price_max REAL, is_free INTEGER DEFAULT 0,
  age_min INTEGER, status TEXT DEFAULT 'scheduled',
  kid TEXT DEFAULT 'unknown', dog TEXT DEFAULT 'unknown', dog_leash_required INTEGER,
  audience TEXT DEFAULT 'unknown',
  effort_json TEXT DEFAULT '{}', provenance_json TEXT DEFAULT '[]',
  first_seen TEXT, last_verified TEXT, embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source_id, source_event_id);
CREATE INDEX IF NOT EXISTS idx_events_cat ON events(category);
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
  id UNINDEXED, title, description, tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS feedback (
  ts TEXT, event_id TEXT, signal TEXT, effort_match TEXT, mood TEXT, note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS visits_agg (
  place_key TEXT PRIMARY KEY, lat REAL, lon REAL, name TEXT DEFAULT '',
  visit_count INTEGER DEFAULT 0, overnight_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS drive_hist (bucket TEXT PRIMARY KEY, count INTEGER DEFAULT 0);
"""


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------ kv
    def kv_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return row["v"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value)
        )
        self.conn.commit()

    # -------------------------------------------------------------- venues
    def upsert_venue(self, v: Venue) -> str:
        self.conn.execute(
            """INSERT INTO venues(id,name,lat,lon,address,venue_type,indoor_outdoor,source)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, lat=COALESCE(excluded.lat, venues.lat),
                 lon=COALESCE(excluded.lon, venues.lon), address=COALESCE(excluded.address, venues.address),
                 venue_type=COALESCE(excluded.venue_type, venues.venue_type)""",
            (v.id, v.name, v.lat, v.lon, v.address, v.venue_type, v.indoor_outdoor, v.source),
        )
        self.conn.commit()
        return v.id

    def get_venue(self, venue_id: str) -> Venue | None:
        r = self.conn.execute("SELECT * FROM venues WHERE id=?", (venue_id,)).fetchone()
        return Venue(**dict(r)) if r else None

    def find_venue_near(self, lat: float, lon: float, max_km: float = 0.12) -> Venue | None:
        from .geo import haversine_km

        for r in self.conn.execute("SELECT * FROM venues WHERE lat IS NOT NULL"):
            v = Venue(**dict(r))
            if haversine_km(lat, lon, v.lat, v.lon) <= max_km:
                return v
        return None

    # -------------------------------------------------------------- events
    def upsert_event(self, e: Event) -> str:
        self.conn.execute(
            """INSERT INTO events(id,series_id,title,description,url,source_id,source_event_id,start,end,
                 timezone,venue_id,category,subcategory,organizer,price_min,price_max,is_free,age_min,status,
                 kid,dog,dog_leash_required,audience,effort_json,provenance_json,first_seen,last_verified,embedding)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, description=excluded.description, url=excluded.url,
                 start=excluded.start, end=excluded.end, status=excluded.status,
                 kid=excluded.kid, dog=excluded.dog, audience=excluded.audience,
                 effort_json=excluded.effort_json, provenance_json=excluded.provenance_json,
                 last_verified=excluded.last_verified, embedding=excluded.embedding""",
            (
                e.id,
                e.series_id,
                e.title,
                e.description,
                e.url,
                e.source_id,
                e.source_event_id,
                e.start.isoformat() if e.start else None,
                e.end.isoformat() if e.end else None,
                e.timezone,
                e.venue_id,
                e.category,
                e.subcategory,
                e.organizer,
                e.price_min,
                e.price_max,
                int(e.is_free),
                e.age_min,
                e.status.value,
                e.kid.value,
                e.dog.value,
                e.dog_leash_required,
                e.audience.value,
                e.effort.model_dump_json(),
                json.dumps(e.provenance),
                e.first_seen,
                e.last_verified,
                e.embedding,
            ),
        )
        self.conn.execute("DELETE FROM events_fts WHERE id=?", (e.id,))  # FTS5 has no UPSERT
        self.conn.execute(
            "INSERT INTO events_fts(id,title,description) VALUES(?,?,?)",
            (e.id, e.title, e.description),
        )
        self.conn.commit()
        return e.id

    def row_to_event(self, r: sqlite3.Row) -> Event:
        from .models import EffortStatic

        return Event(
            id=r["id"],
            series_id=r["series_id"],
            title=r["title"],
            description=r["description"],
            url=r["url"],
            source_id=r["source_id"],
            source_event_id=r["source_event_id"],
            start=datetime.fromisoformat(r["start"]) if r["start"] else None,
            end=datetime.fromisoformat(r["end"]) if r["end"] else None,
            timezone=r["timezone"],
            venue_id=r["venue_id"],
            category=r["category"],
            subcategory=r["subcategory"],
            organizer=r["organizer"],
            price_min=r["price_min"],
            price_max=r["price_max"],
            is_free=bool(r["is_free"]),
            age_min=r["age_min"],
            status=EventStatus(r["status"]),
            kid=Suitability(r["kid"]),
            dog=Suitability(r["dog"]),
            dog_leash_required=r["dog_leash_required"],
            audience=Audience(r["audience"]),
            effort=EffortStatic.model_validate_json(r["effort_json"] or "{}"),
            provenance=json.loads(r["provenance_json"] or "[]"),
            first_seen=r["first_seen"],
            last_verified=r["last_verified"],
            embedding=r["embedding"],
        )

    def get_event(self, event_id: str) -> Event | None:
        r = self.conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return self.row_to_event(r) if r else None

    def events_between(self, start_iso: str, end_iso: str) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE start IS NOT NULL AND start>=? AND start<=? AND status='scheduled' "
            "ORDER BY start",
            (start_iso, end_iso),
        ).fetchall()
        return [self.row_to_event(r) for r in rows]

    def events_by_source_ids(self, source_id: str, source_event_id: str) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE source_id=? AND source_event_id=?", (source_id, source_event_id)
        ).fetchall()
        return [self.row_to_event(r) for r in rows]

    def search_text(self, query: str, limit: int = 50) -> list[Event]:
        rows = self.conn.execute(
            "SELECT id FROM events_fts WHERE events_fts MATCH ? ORDER BY rank LIMIT ?", (query, limit)
        ).fetchall()
        out = []
        for r in rows:
            ev = self.get_event(r["id"])
            if ev:
                out.append(ev)
        return out

    def all_events(self) -> list[Event]:
        rows = self.conn.execute("SELECT * FROM events").fetchall()
        return [self.row_to_event(r) for r in rows]

    # ------------------------------------------------------------ feedback
    def add_feedback(self, fb: Feedback) -> None:
        self.conn.execute(
            "INSERT INTO feedback(ts,event_id,signal,effort_match,mood,note) VALUES(?,?,?,?,?,?)",
            (fb.ts, fb.event_id, fb.signal, fb.effort_match, fb.mood, fb.note),
        )
        self.conn.commit()

    def feedback_for(self, event_id: str) -> list[Feedback]:
        rows = self.conn.execute(
            "SELECT * FROM feedback WHERE event_id=? ORDER BY ts", (event_id,)
        ).fetchall()
        return [Feedback(**dict(r)) for r in rows]

    def pending_recap(self, now_iso: str) -> list[tuple[Feedback, Event]]:
        """Past events with positive signals but no final 'went'/'dismissed' verdict."""
        rows = self.conn.execute(
            """SELECT DISTINCT event_id FROM feedback
               WHERE signal='interested' AND event_id NOT IN
                 (SELECT event_id FROM feedback WHERE signal IN ('went','dismissed','not_interested'))"""
        ).fetchall()
        out = []
        for r in rows:
            ev = self.get_event(r["event_id"])
            if ev and ev.start and ev.start.isoformat() < now_iso:
                out.append((self.feedback_for(ev.id)[-1], ev))
        return out

    # --------------------------------------------------- location aggregates
    def upsert_visit(
        self, place_key: str, lat: float, lon: float, name: str = "", overnight: bool = False
    ) -> None:
        self.conn.execute(
            """INSERT INTO visits_agg(place_key,lat,lon,name,visit_count,overnight_count)
               VALUES(?,?,?,?,1,?)
               ON CONFLICT(place_key) DO UPDATE SET
                 visit_count=visit_count+1,
                 overnight_count=overnight_count+excluded.overnight_count""",
            (place_key, lat, lon, name, int(overnight)),
        )
        self.conn.commit()

    def top_places(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM visits_agg ORDER BY visit_count DESC LIMIT ?", (limit,)
        ).fetchall()

    def clear_location_data(self) -> None:
        self.conn.execute("DELETE FROM visits_agg")
        self.conn.execute("DELETE FROM drive_hist")
        self.conn.commit()

    def has_location_data(self) -> bool:
        r = self.conn.execute("SELECT EXISTS(SELECT 1 FROM visits_agg)").fetchone()
        return bool(r[0])

    def bump_drive(self, minutes: float) -> None:
        bucket = str(int(round(minutes / 5.0) * 5))
        self.conn.execute(
            "INSERT INTO drive_hist(bucket,count) VALUES(?,1) ON CONFLICT(bucket) DO UPDATE SET count=count+1",
            (bucket,),
        )
        self.conn.commit()

    def drive_histogram(self) -> dict[str, int]:
        return {r["bucket"]: r["count"] for r in self.conn.execute("SELECT * FROM drive_hist")}

    # ------------------------------------------------------------ embeddings
    def cosine_neighbors(self, vec: list[float], limit: int = 10) -> list[tuple[Event, float]]:
        scored = []
        for r in self.conn.execute("SELECT id, embedding FROM events WHERE embedding IS NOT NULL"):
            other = _blob_to_vec(r["embedding"])
            if other:
                scored.append((r["id"], _cosine(vec, other)))
        scored.sort(key=lambda t: -t[1])
        out = []
        for eid, score in scored[:limit]:
            ev = self.get_event(eid)
            if ev:
                out.append((ev, score))
        return out

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def close(self) -> None:
        self.conn.close()


def vec_to_blob(vec: list[float]) -> bytes:
    import struct

    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> list[float] | None:
    import struct

    if not blob:
        return None
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b, strict=False))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0
