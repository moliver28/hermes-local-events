"""Copy-paste invite card for forwarding to friends in any chat."""

from __future__ import annotations

from .effort import effort_summary
from .models import Event


def invite_card(event: Event, when_str: str = "") -> str:
    lines = [f"🎉 {event.title}"]
    lines.append(f"📅 {when_str or _when(event)}")
    if event.url:
        lines.append(f"🔗 {event.url}")
    price = (
        "free"
        if event.is_free or (event.price_min or 0) == 0
        else (f"${event.price_min:g}" + (f"–{event.price_max:g}" if event.price_max else ""))
    )
    lines.append(f"💰 {price}")
    summary = effort_summary(event, None)
    if summary and summary != "effort unknown":
        lines.append(f"🚗 {summary}")
    lines.append("_made with hermes-local-events_")
    return "\n".join(lines)


def _when(event: Event) -> str:
    if not event.start:
        return "time TBA"
    return event.start.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")
