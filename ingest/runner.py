"""Refresh pipeline: sources.yaml → connector.fetch → static effort → suitability
rules (+LLM assist) → cross-source dedup → store upsert. Deterministic and
polite: one fetch per source per cadence, robots-respecting UA, sleep between
detail-page hops."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from core import effort as effort_mod
from core import suitability
from core.dedup import dedupe
from core.llm import make_llm_fn
from core.models import now_utc
from core.store import Store
from core.trends import catalog_stats

from .base import Connector
from .html_list import HtmlListConnector, TacomaWeeklyConnector
from .rss_ics import IcsConnector, RssConnector
from .schemaorg import JsonLdConnector
from .ticketmaster import TicketmasterConnector

CONNECTORS: dict[str, type[Connector]] = {
    "rss": RssConnector,
    "ics": IcsConnector,
    "jsonld": JsonLdConnector,
    "html_list": HtmlListConnector,
    "tacomaweekly": TacomaWeeklyConnector,
    "ticketmaster": TicketmasterConnector,
}

SOURCES_PATH = Path(__file__).resolve().parent.parent / "data" / "sources.yaml"


@dataclass
class RefreshReport:
    ran: list[dict]
    skipped: list[dict]
    total_new: int
    total_merged: int


def load_sources(path: Path | None = None) -> list[dict]:
    p = path or SOURCES_PATH
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def user_sources_path(runtime) -> Path:
    return Path(runtime.data_dir) / "user_sources.yaml"


def load_user_sources(runtime) -> list[dict]:
    """Sources registered at runtime (agent discovery / onboarding) — never the
    shipped sources.yaml. Missing or malformed file → empty list."""
    p = user_sources_path(runtime)
    if not p.exists():
        return []
    try:
        with open(p, encoding="utf-8") as f:
            out = yaml.safe_load(f) or []
        return out if isinstance(out, list) else []
    except Exception:
        return []


def write_user_sources(runtime, sources: list) -> None:
    """Persist the runtime-registered source list (register/forget flows).
    Containment-guarded: the normalized path must stay inside the plugin data
    dir and never contain '..' parts."""
    base = Path(runtime.data_dir).resolve()
    p = (base / "user_sources.yaml").resolve()
    if p.parent != base or ".." in p.parts:
        raise ValueError("user sources path escaped the plugin data dir")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(sources, allow_unicode=True, sort_keys=False), encoding="utf-8")


def prepare(events: list, store: Store, llm=None, defaults: dict | None = None) -> list:
    """Static effort + suitability for each event — the step every ingestion
    path (refresh, agent content) shares."""
    for ev in events:
        venue = store.get_venue(ev.venue_id) if ev.venue_id else None
        effort_mod.static_effort(ev, venue)
        suitability.classify(ev, venue, defaults, llm)
    return events


def store_merged(store: Store, merged: list) -> int:
    """Id-stable upsert + optional embeddings; returns the new-event count."""
    from core import embed as embed_mod
    from core.store import vec_to_blob

    new_count = 0
    embeddable = embed_mod.available()
    for ev in merged:
        existing = store.events_by_source_ids(ev.source_id, ev.source_event_id)
        if existing:
            ev.id = existing[0].id
            if ev.start is None:
                ev.start = existing[0].start
        else:
            new_count += 1
        if embeddable and ev.embedding is None:
            venue = store.get_venue(ev.venue_id) if ev.venue_id else None
            vec = embed_mod.event_embedding(ev, venue)
            if vec:
                ev.embedding = vec_to_blob(vec)
        store.upsert_event(ev)
    return new_count


def refresh(
    store: Store, runtime, source_ids: list[str] | None = None, sources_path: Path | None = None
) -> RefreshReport:
    import json as _json

    import httpx

    sources = load_sources(sources_path)
    known_ids = {s.get("id") for s in sources}
    sources += [s for s in load_user_sources(runtime) if s.get("id") not in known_ids]
    llm = make_llm_fn(runtime) if runtime.get_setting("llm_suitability", True) else None
    ran, skipped = [], []
    collected: list = []
    last_run: dict = {}
    try:
        last_run = _json.loads(store.kv_get("last_refresh") or "{}")
    except Exception:
        last_run = {}

    with httpx.Client(follow_redirects=True, timeout=25.0) as client:
        for cfg in sources:
            sid = cfg.get("id", "?")
            if source_ids and sid not in source_ids:
                continue
            if not cfg.get("enabled", True):
                skipped.append({"id": sid, "reason": "disabled"})
                continue
            conn_name = cfg.get("connector", "rss")
            cls = CONNECTORS.get(conn_name)
            if cls is None:
                skipped.append({"id": sid, "reason": f"unknown connector {conn_name}"})
                continue
            cadence_h = float(cfg.get("cadence_hours", 12))
            prev = last_run.get(sid)
            if (
                prev
                and (now_utc() - __import__("datetime").datetime.fromisoformat(prev)).total_seconds()
                < cadence_h * 3600
            ):
                skipped.append({"id": sid, "reason": "cadence not due"})
                continue
            connector = cls()
            try:
                if conn_name == "ticketmaster":
                    key = runtime.get_secret("TICKETMASTER_API_KEY") or ""
                    events = connector.fetch(cfg, client, api_key=key)  # type: ignore[call-arg]
                else:
                    events = connector.fetch(cfg, client)
            except TypeError:
                events = connector.fetch(cfg)
            except Exception as exc:  # noqa: BLE001
                skipped.append({"id": sid, "reason": f"error: {exc}"})
                continue
            for v in getattr(connector, "fetched_venues", []):
                store.upsert_venue(v)
            prepare(events, store, llm, cfg.get("defaults"))
            collected.extend(events)
            last_run[sid] = now_utc().isoformat()
            ran.append({"id": sid, "fetched": len(events)})
            time.sleep(1.0)  # politeness between sources

    merged = dedupe(collected)
    new_count = store_merged(store, merged)
    store.kv_set("last_refresh", _json.dumps(last_run))
    store.kv_set("catalog_stats", _json.dumps(catalog_stats(store)))
    return RefreshReport(ran=ran, skipped=skipped, total_new=new_count, total_merged=len(merged))
