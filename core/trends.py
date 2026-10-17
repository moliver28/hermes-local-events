"""Catalog-side trend mining. The research-spec principle: trends come from the
event catalog (supply, recurrence, organizer behavior), not from the sparse
feedback log. Read-only SQL over the store; refreshed once per refresh run and
cached in kv. Consumers: wildcard exploration boost (search only), flaky-
organizer warnings in reasons, `cli.py stats`. The exploit score never reads
any of this."""

from __future__ import annotations

from datetime import timedelta

from .models import now_utc
from .store import Store

TREND_WINDOW_DAYS = 30
BASELINE_WINDOW_DAYS = 90
FLAKY_MIN_EVENTS = 3
FLAKY_CANCEL_RATE = 0.30


def catalog_stats(store: Store, now=None) -> dict:
    now = now or now_utc()
    # upcoming-supply windows: [now, now+30d) vs [now, now+90d)
    hi30 = (now + timedelta(days=TREND_WINDOW_DAYS)).isoformat()
    hi90 = (now + timedelta(days=BASELINE_WINDOW_DAYS)).isoformat()
    lo = now.isoformat()
    total_30 = _count(store, lo, hi30)
    total_90 = _count(store, lo, hi90)

    trending: dict[str, float] = {}
    for cat, c30, c90 in _per_category(store, lo, hi30, hi90):
        share_30 = c30 / total_30 if total_30 else 0.0
        share_90 = c90 / total_90 if total_90 else 0.0
        if c30 >= 2 and share_90 > 0:
            trending[cat] = round(share_30 / share_90, 2)

    recurring: dict[str, float] = {}
    for cat, n, with_series in _recurring_share(store, lo, hi30):
        recurring[cat] = round(with_series / n, 2) if n else 0.0

    return {
        "as_of": now.isoformat(),
        "catalog_size": total_30,
        "trending": trending,
        "recurring_share": recurring,
        "organizers": _organizers(store),
    }


def flaky_organizers(store: Store) -> set[str]:
    """Organizers whose recent track record is ≥30% cancelled/postponed over
    ≥3 catalog appearances."""
    return {r["organizer"] for r in _organizer_rows(store) if _flaky(r)}


def _flaky(row) -> bool:
    n = row["n"] or 0
    cx = row["cx"] or 0
    return n >= FLAKY_MIN_EVENTS and cx / n >= FLAKY_CANCEL_RATE


def _count(store: Store, lo_iso: str, hi_iso: str) -> int:
    return store.conn.execute(
        "SELECT COUNT(*) FROM events WHERE status='scheduled' AND start>=? AND start<?",
        (lo_iso, hi_iso),
    ).fetchone()[0]


def _per_category(store: Store, lo_iso: str, hi30: str, hi90: str):
    return store.conn.execute(
        """SELECT category,
                  SUM(CASE WHEN start<? THEN 1 ELSE 0 END) c30,
                  SUM(CASE WHEN start<? THEN 1 ELSE 0 END) c90
           FROM events WHERE status='scheduled' AND start>=?
           GROUP BY category""",
        (hi30, hi90, lo_iso),
    ).fetchall()


def _recurring_share(store: Store, lo_iso: str, hi30: str):
    return store.conn.execute(
        """SELECT category, COUNT(*) n,
                  SUM(CASE WHEN series_id IS NOT NULL THEN 1 ELSE 0 END) with_series
           FROM events WHERE status='scheduled' AND start>=? AND start<?
           GROUP BY category""",
        (lo_iso, hi30),
    ).fetchall()


def _organizer_rows(store):
    return store.conn.execute(
        """SELECT organizer, COUNT(*) n,
                  SUM(CASE WHEN status IN ('cancelled','postponed') THEN 1 ELSE 0 END) cx
           FROM events WHERE organizer != ''
           GROUP BY organizer HAVING n >= ? ORDER BY n DESC LIMIT 50""",
        (FLAKY_MIN_EVENTS,),
    ).fetchall()


def _organizers(store: Store) -> dict[str, dict]:
    out = {}
    for r in _organizer_rows(store):
        n, cx = r["n"] or 0, r["cx"] or 0
        out[r["organizer"]] = {
            "events": n,
            "cancel_rate": round(cx / n, 2) if n else 0.0,
            "flaky": _flaky(r),
        }
    return out
