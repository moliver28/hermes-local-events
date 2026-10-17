"""Email mining: newsletters → candidate events; ticket/order receipts →
commitment labels. The agent fetches mail with its own email tooling and passes
TEXT here — bodies are processed transiently, only extracted fields persist."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

import dateparser


@dataclass
class EmailCandidate:
    title: str
    when: datetime | None
    url: str = ""
    venue_hint: str = ""


@dataclass
class Receipt:
    order_id: str = ""
    title: str = ""
    when: datetime | None = None
    total: float | None = None


URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
DATE_HINT_RE = re.compile(
    r"\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)
RECEIPT_HINTS = (
    "order confirmation",
    "your tickets",
    "ticket order",
    "receipt",
    "you're going",
    "order #",
    "confirmation #",
)


def extract_candidates(subject: str, body: str, max_items: int = 10) -> list[EmailCandidate]:
    out: list[EmailCandidate] = []
    # Newsletters usually list items as lines with a date-ish phrase + link nearby.
    lines = [raw.strip() for raw in body.splitlines() if raw.strip()]
    for i, line in enumerate(lines):
        if len(line) < 12 or len(line) > 140:
            continue
        dm = DATE_HINT_RE.search(line) or (DATE_HINT_RE.search(lines[i + 1]) if i + 1 < len(lines) else None)
        if not dm:
            continue
        url_m = URL_RE.search(line) or (URL_RE.search(lines[i + 1]) if i + 1 < len(lines) else None)
        title = re.sub(r"https?://\S+", "", line).strip(" -–—•*\t")
        title = re.sub(r"\s{2,}", " ", title)
        if not title or title.lower().startswith(("http", "www")):
            continue
        when = dateparser.parse(dm.group(0)) if dm else None
        out.append(EmailCandidate(title=title[:120], when=when, url=url_m.group(0) if url_m else ""))
        if len(out) >= max_items:
            break
    return out


def extract_receipt(subject: str, body: str) -> Receipt | None:
    s = f"{subject}\n{body}".lower()
    if not any(h in s for h in RECEIPT_HINTS):
        return None
    order = re.search(r"(?:order|confirmation)[ #:]*([A-Z0-9-]{6,})", f"{subject}\n{body}", re.IGNORECASE)
    dm = DATE_HINT_RE.search(f"{subject}\n{body}")
    total = re.search(r"total[:\s]*\$?(\d+(?:\.\d{2})?)", f"{subject}\n{body}", re.IGNORECASE)
    title_line = ""
    for raw in body.splitlines():
        line = raw.strip()
        if 12 < len(line) < 100 and not line.lower().startswith(("http", "order", "total", "date")):
            title_line = line
            break
    return Receipt(
        order_id=order.group(1) if order else "",
        title=title_line,
        when=dateparser.parse(dm.group(0)) if dm else None,
        total=float(total.group(1)) if total else None,
    )
