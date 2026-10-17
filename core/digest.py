"""Plan-first digest, organized by the energy dial — the system's primary output.

Thursday 8pm: "If you're wiped → … / Solid outing → … / Adventure → …" with one
anchor per band, explicit whitespace, rain swaps for outdoor picks, and a
numbered list so feedback is one line ("went to #2 🔥")."""

from __future__ import annotations

from datetime import datetime, timedelta

from .effort import effort_summary
from .models import Energy, Event, Household, Party, Suitability, now_utc
from .rank import Query, Scored, rank, select_slate
from .store import Store

BAND_SECTIONS = [
    (Energy.TIRED, "🛋 If you're wiped"),
    (Energy.NORMAL, "🌳 Solid outing"),
    (Energy.ADVENTUROUS, "⛰ Adventure"),
    (Energy.ESCAPE, "🚗 Escape (book soon)"),
]

AUDIENCE_COPY = {
    Suitability.UNKNOWN: "dog policy not stated — assume no",
    Suitability.LIKELY: "dogs likely ok",
    Suitability.VERIFIED_YES: "dogs ok",
    Suitability.VERIFIED_NO: "no dogs",
}


def digest_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The upcoming Fri–Mon window (digest runs Thursday evening)."""
    now = now or now_utc()
    days_ahead = (4 - now.weekday()) % 7  # Friday
    friday = (now + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
    return friday, friday + timedelta(days=4)


def suitability_copy(ev: Event) -> str:
    kid = {
        "verified_yes": "kids ok ✓",
        "likely": "kids probably ok",
        "verified_no": "adults only",
        "unknown": "kid-suitability unknown",
    }[ev.kid.value]
    dog = AUDIENCE_COPY.get(ev.dog, "") if ev.dog != Suitability.VERIFIED_YES else "dogs ok ✓ (verified)"
    return " · ".join(x for x in (kid, dog) if x)


def build_digest(
    hh: Household,
    store: Store,
    now: datetime | None = None,
    window: tuple[datetime, datetime] | None = None,
    party: Party = Party.FAMILY,
    anchors_per_band: int = 1,
    alternates: int = 2,
) -> dict:
    now = now or now_utc()
    start, end = window or digest_window(now)
    events = store.events_between(start.isoformat(), end.isoformat())
    if party == Party.ADULTS:
        q = Query(start=start, end=end, party=party, energy=Energy.NORMAL, strict_kid=False, max_results=60)
        scored = rank(events, q, hh, store)
        slate = select_slate(scored, k=3 + alternates, per_day_cap=1)
        return _render({"adults": slate}, hh, start, end, now, excluded=scored, party=party)

    sections: dict[str, list[Scored]] = {}
    all_scored: list[Scored] = []
    for energy, label in BAND_SECTIONS[:3]:  # tired/normal/adventurous; escape only on long weekends
        q = Query(
            start=start, end=end, party=Party.FAMILY, energy=energy, max_results=60, strict_dog=hh.has_dog
        )
        scored = rank(events, q, hh, store)
        all_scored.extend(scored)
        slate = select_slate(scored, k=anchors_per_band + 1, per_day_cap=1)
        sections[label] = slate

    # alternates: best remaining across all bands not already picked
    picked = {s.event.id for sl in sections.values() for s in sl}
    rest = [s for s in sorted(all_scored, key=lambda x: -x.score) if s.event.id not in picked]
    sections["↔ Alternates"] = rest[:alternates]

    unknown_kid = sum(1 for e in events if e.kid == Suitability.UNKNOWN)
    return _render(sections, hh, start, end, now, excluded_unknown=unknown_kid, party=party)


def _render(
    sections: dict[str, list[Scored]],
    hh: Household,
    start: datetime,
    end: datetime,
    now: datetime,
    excluded=None,
    excluded_unknown: int = 0,
    party: Party = Party.FAMILY,
) -> dict:
    lines = [
        f"📅 **Weekend plan — {start:%a %b %d}–{end:%a %b %d}**",
        "",
        "_One anchor per day; the rest is open on purpose._",
        "",
    ]
    numbered: list[dict] = []
    n = 0
    for label, slate in sections.items():
        if not slate:
            continue
        lines.append(f"**{label}**")
        for s in slate:
            n += 1
            ev = s.event
            numbered.append({"n": n, "event_id": ev.id, "title": ev.title, "band": label})
            lines.append(f"{n}. **{ev.title}** — {_when(ev)}")
            lines.append(f"   {effort_summary(ev, s.computed)} · {suitability_copy(ev)}")
            if s.reasons:
                lines.append(f"   _why: {', '.join(s.reasons)}_")
            if ev.effort.indoor_outdoor == "outdoor":
                lines.append("   ☔ rain swap: see alternates / indoor picks above")
            lines.append("")
    if n == 0:
        lines.append("_No events in the window yet — run events_refresh first (or widen the window)._")
        lines.append("")
    if party == Party.FAMILY and excluded_unknown:
        lines.append(
            f"ℹ️ {excluded_unknown} events couldn't be kid-checked — say “show unknowns” to see them."
        )
    lines.append("_Reply like “interested in #2”, “too far for #1”, or “went to #2 🔥 (+ right effort)”._")
    return {
        "text": "\n".join(lines),
        "picks": numbered,
        "window": [start.isoformat(), end.isoformat()],
        "party": party.value,
    }


def _when(ev: Event) -> str:
    if not ev.start:
        return "time TBA"
    return ev.start.strftime("%a %b %d · %I:%M %p").replace(" 0", " ")
