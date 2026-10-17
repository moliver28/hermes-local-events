"""LLM listwise re-rank of an already-built slate (RankGPT-lineage pattern,
sized to one call per digest). The model may only reorder picks WITHIN their
band section — sections are structurally preserved, so eligibility, bands, and
slate membership can never change. Position bias is mitigated by shuffling the
candidate order before prompting (seeded rng → reproducible); verbosity bias by
capping each reason at 90 chars. Any malformed response returns the input
order unchanged — fail closed, same contract as the suitability LLM."""

from __future__ import annotations

import random

from .effort import effort_summary

SYSTEM_PROMPT = (
    "You re-rank a short list of local outing options for one household. "
    'Return ONLY JSON: {"order": [{"id": "...", "reason": "..."}]} containing '
    "every given id exactly once, best first. Each reason: one plain line, max "
    "90 characters, concrete (effort, drive, kid/dog fit, novelty, timing). "
    "Never add or drop ids. Never invent ids."
)

MAX_REASON = 90


def rerank_slate(sections: dict[str, list], llm, rng: random.Random) -> dict[str, list]:
    """sections: label -> list[Scored]. Returns the same dict, possibly with
    items reordered within each label. llm None → unchanged."""
    if llm is None or not sections:
        return sections
    items = []
    for label, slate in sections.items():
        for s in slate:
            ev = s.event
            items.append(
                {
                    "id": ev.id,
                    "section": label,
                    "title": ev.title,
                    "when": ev.start.strftime("%a %I:%M %p") if ev.start else "TBA",
                    "effort": effort_summary(ev, s.computed),
                    "why": ", ".join(s.reasons),
                }
            )
    if not items:
        return sections
    shuffled = items[:]
    rng.shuffle(shuffled)
    user = "\n".join(
        f"{it['id']} | {it['title']} | {it['when']} | {it['effort']} | {it['why']}" for it in shuffled
    )
    try:
        data = llm(SYSTEM_PROMPT, user)
    except Exception:
        return sections
    if not isinstance(data, dict) or not isinstance(data.get("order"), list):
        return sections

    by_id = {s.event.id: (label, s) for label, slate in sections.items() for s in slate}
    seen: set[str] = set()
    reasons: dict[str, str] = {}
    for entry in data["order"]:
        if not isinstance(entry, dict):
            return sections
        eid = entry.get("id")
        if eid not in by_id or eid in seen:
            return sections  # unknown or duplicated id → malformed, fail closed
        seen.add(eid)
        reason = str(entry.get("reason", "")).strip()[:MAX_REASON]
        if reason:
            reasons[eid] = reason
    if seen != set(by_id):
        return sections  # dropped or added ids → malformed, fail closed

    out: dict[str, list] = {label: [] for label in sections}
    for entry in data["order"]:
        label, s = by_id[entry["id"]]
        if entry["id"] in reasons:
            s.reasons = [reasons[entry["id"]], *s.reasons][:3]
        out[label].append(s)
    return out
