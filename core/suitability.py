"""Kid / dog / audience suitability: deterministic rules first, LLM assist for
the remainder. verified_* requires an evidence quote. unknown never passes a
strict filter — the digest surfaces an 'excluded unknown' count instead."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable

from .effort import tax
from .models import Audience, Event, Evidence, Suitability, Venue

LLM_SYSTEM = (
    "You classify local events for a family with kids and a dog. Reply with JSON only: "
    '{"kid": "verified_yes|likely|verified_no|unknown", "dog": "...", "audience": "verified_adult|adults_leaning|all_ages|unknown", '
    '"quote": "<shortest quote from the text supporting the most important verdict>"}. '
    "Rules: verified_* requires an explicit statement in the text. If the text doesn't say, answer unknown. "
    "Service animals ≠ pets. Venue type informs leaning, never verification."
)


def _scan(text: str) -> dict:
    t = tax()
    text_l = (text or "").lower()
    out: dict = {}
    if re.search(t["kid_negative_keywords"], text_l):
        out["kid"] = Suitability.VERIFIED_NO
    elif re.search(t["kid_positive_keywords"], text_l):
        out["kid"] = Suitability.VERIFIED_YES
    if re.search(t["dog_negative_keywords"], text_l):
        out["dog"] = Suitability.VERIFIED_NO
    elif re.search(t["dog_positive_keywords"], text_l):
        out["dog"] = Suitability.VERIFIED_YES
    for row in t["adults_keywords"]:
        if re.search(row["re"], text_l):
            out["audience"] = Audience(row["audience"])
            break
    return out


def _quote_for(pattern_group: str, text: str, limit: int = 90) -> str:
    m = re.search(pattern_group, (text or ""), re.IGNORECASE)
    if not m:
        return ""
    s = max(0, m.start() - 30)
    return "…" + text[s : m.end() + 60].replace("\n", " ").strip()[:limit]


def classify(
    event: Event,
    venue: Venue | None,
    source_defaults: dict | None = None,
    llm: Callable[[str, str], dict | None] | None = None,
) -> Event:
    """Apply source defaults → rules → LLM. Mutates and returns event."""
    defaults = source_defaults or {}
    text = f"{event.title} {event.description}"
    found = _scan(text)

    def apply(field: str, value, method: str, quote: str = "") -> None:
        if field == "kid":
            event.kid = value
            event.provenance = event.provenance  # keep; evidence below
        setattr(event, "_" + field + "_evidence", Evidence(state=value, quote=quote, method=method))

    ev_map: dict[str, Evidence] = {}

    # 1) source defaults (e.g. parentmap is kid-by-construction)
    if "kid" in defaults and "kid" not in found:
        ev_map["kid"] = Evidence(
            state=Suitability(defaults["kid"]),
            method="source_default",
            quote=f"source {event.source_id} lists family events only",
        )
    if "dog" in defaults and "dog" not in found:
        ev_map["dog"] = Evidence(
            state=Suitability(defaults["dog"]),
            method="source_default",
            quote=f"source {event.source_id} default",
        )
    if "audience" in defaults and "audience" not in found:
        ev_map["audience"] = Evidence(state=Audience(defaults["audience"]), method="source_default")

    # 2) explicit rules
    t = tax()
    if "kid" not in ev_map:
        if "kid" in found:
            pat = (
                t["kid_negative_keywords"]
                if found["kid"] == Suitability.VERIFIED_NO
                else t["kid_positive_keywords"]
            )
            ev_map["kid"] = Evidence(state=found["kid"], method="rule", quote=_quote_for(pat, text))
        elif event.age_min and event.age_min >= 18:
            ev_map["kid"] = Evidence(
                state=Suitability.VERIFIED_NO, method="rule", quote=f"age minimum {event.age_min}+"
            )
    if "dog" not in ev_map and "dog" in found:
        pat = (
            t["dog_negative_keywords"]
            if found["dog"] == Suitability.VERIFIED_NO
            else t["dog_positive_keywords"]
        )
        ev_map["dog"] = Evidence(state=found["dog"], method="rule", quote=_quote_for(pat, text))
    if "audience" not in ev_map and "audience" in found:
        ev_map["audience"] = Evidence(state=found["audience"], method="rule")

    # 3) adults-leaning venue prior (never verification)
    if "audience" not in ev_map and venue and venue.venue_type in t["adults_venue_types"]:
        ev_map["audience"] = Evidence(
            state=Audience.ADULTS_LEANING, method="rule", quote=f"venue type {venue.venue_type}"
        )

    # 4) LLM assist for the unknowns
    if llm is not None:
        missing = [f for f in ("kid", "dog", "audience") if f not in ev_map]
        if missing:
            prompt = (
                f"TITLE: {event.title}\nDESCRIPTION: {event.description or '(none)'}\n"
                f"VENUE: {venue.name if venue else 'unknown'} ({venue.venue_type if venue else '?'})\n"
                f"Fields needed: {', '.join(missing)}"
            )
            out = llm(LLM_SYSTEM, prompt)
            if out:
                for f in ("kid", "dog"):
                    if (
                        f not in ev_map
                        and out.get(f) in ("verified_yes", "likely", "verified_no", "unknown")
                        and out[f] != "unknown"
                    ):
                        ev_map[f] = Evidence(
                            state=Suitability(out[f]), method="llm", quote=out.get("quote", "")
                        )
                if (
                    "audience" not in ev_map
                    and out.get("audience") in ("verified_adult", "adults_leaning", "all_ages", "unknown")
                    and out["audience"] != "unknown"
                ):
                    ev_map["audience"] = Evidence(
                        state=Audience(out["audience"]), method="llm", quote=out.get("quote", "")
                    )

    # commit
    if "kid" in ev_map:
        event.kid = (
            ev_map["kid"].state
            if isinstance(ev_map["kid"].state, Suitability)
            else Suitability(ev_map["kid"].state)
        )
    if "dog" in ev_map:
        event.dog = (
            ev_map["dog"].state
            if isinstance(ev_map["dog"].state, Suitability)
            else Suitability(ev_map["dog"].state)
        )
    if "audience" in ev_map:
        a = ev_map["audience"].state
        event.audience = a if isinstance(a, Audience) else Audience(a)
    return event


def prompt_version_hash(text: str) -> str:
    """Cache key for LLM labels: content + prompt version, so prompt edits invalidate."""
    return hashlib.sha256((LLM_SYSTEM + text).encode()).hexdigest()[:16]


def excluded_unknown_count(events: list[Event], field: str) -> int:
    return sum(1 for e in events if getattr(e, field) == Suitability.UNKNOWN)
