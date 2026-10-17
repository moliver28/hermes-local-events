"""ICS generation for calendar handoff (Hermes's Google Calendar skill does the
actual add; attendance comes back via recap/confirm)."""

from __future__ import annotations

from icalendar import Calendar
from icalendar import Event as IcsEvent

from .models import Event


def events_to_ics(events: list[Event], prodid: str = "-//hermes-local-events//EN") -> str:
    cal = Calendar()
    cal.add("prodid", prodid)
    cal.add("version", "2.0")
    for ev in events:
        if not ev.start:
            continue
        item = IcsEvent()
        item.add("uid", ev.id)
        item.add("summary", ev.title)
        item.add("dtstart", ev.start)
        if ev.end:
            item.add("dtend", ev.end)
        else:
            item.add("dtend", ev.start)  # calendar needs an end; same instant
        desc_parts = [p for p in (ev.description[:500] if ev.description else "", ev.url) if p]
        if desc_parts:
            item.add("description", "\n".join(desc_parts))
        if ev.url:
            item.add("url", ev.url)
        cal.add_component(item)
    return cal.to_ical().decode("utf-8")
