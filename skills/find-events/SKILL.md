---
name: local-events-find
description: How to answer event questions and deliver digests with the local-events plugin — energy dial, party modes, honesty rules, feedback capture. Use whenever the user asks about events, weekends, things to do, date nights, or replies to a digest.
---

# Finding & delivering events

## Core rules

1. **Effort/energy is the primary axis.** Map mood words into `events_search`: "wiped/exhausted/lazy/low-key" → `energy=tired`; default `normal`; "adventure/hike/big day" → `adventurous`; "weekend trip/getaway" → `escape` (+ `days_ahead: 35`).
2. **Party mode.** "date night / out with friends / no kids" → `events_search party=adults`. Family queries keep kid+dog filters on.
3. **More like that one.** "something like #2" → `events_search like=#2` (semantic similarity; needs the optional `model2vec` extra — if it errors, say so and fall back to same-category search). Results may include one event marked `wildcard: true` — that's deliberate exploration from rank 4+; present it as "a wildcard pick" and let them dismiss it cheaply.
4. **Honesty beats hedging.** Never say "your dog can come" unless `dog=verified_yes`. If `unknown`, say "dog policy not stated — assume no." Suitability states: verified_yes / likely / unknown / verified_no. Unknown never silently passes a filter; mention the excluded-unknown count when relevant.
5. **Lead with the plan, not a catalog.** For "what's this weekend": run `events_refresh` (if stale) then `events_digest`, deliver the digest text nearly verbatim — it's shaped (anchors per energy band, alternates, whitespace, rain swaps, numbered picks). Don't append a 20-event list.
6. **Whitespace is a feature.** Never propose filling every day. If the user asks for more, add at most 2 alternates.

## Feedback capture (this is how it learns)

Parse natural replies into `events_feedback` calls:
- "🔥 / loved #2 / let's do it" → `signal=interested`
- "went to #2, perfect amount of effort" → `signal=went, effort_match=right_effort`
- "that was too much" → `signal=too_much_effort, effort_match=too_much`
- "#1 is too far / too pricey / not for us" → `signal=too_far|too_pricey|not_interested`
- Include `mood` (tired/normal/adventurous/escape) when the user's state is clear.

Pick numbers ("#2") resolve via the last digest — pass them as `event_ref`.

## Calendar + sharing

- "add to calendar" → `events_calendar_ics {"event_ids": ["..."]}`, then add via Hermes's Google Calendar skill. Attendance confirmed later via `signal=went`.
- "invite friends" → `events_share {"event_id": "..."}` → hand back the card text for forwarding.

## Morning-of conditions (`events_conditions`)

Before they leave ("should we still go?") or in the morning nudge: `events_conditions {"event_ref": "#1"}` → rain % at start, US AQI (wildfire smoke), active NWS alerts. Say the line plainly; AQI 150+ or a severe alert → recommend skipping strenuous outdoor plans and offer an indoor alternate (`events_search energy=tired`).

## Blocked sources — your web tools are part of the pipeline

When `events_refresh` reports a source error (403/429/bot wall/JS shell), don't just report it — recover it:

1. `web_extract` the calendar page; if it's JS-rendered or blocked, retry through `browser_exec` (blocked-page-recovery pattern).
2. Feed the fetched body through `events_ingest_content {source_id, url, content, format?}` — those events enter the same store and rank identically. Events carry `method="agent-browser"` provenance.
3. If a login is required: only inside your browser with the host vault, user present for 2FA. Never handle their password.
4. Mention once that the source is now "browser-fed" so the user knows why refresh looks different.

## Escapes (top of the energy dial)

`energy=escape` widens the radius (~150 mi, Olympic Peninsula / Rainier / coast scale) and flags booking lead time ("book lodging by X"). Only surface proactively ahead of 3-day weekends (blueprint does this) or when asked.

## Failure recovery (trust repair)

- Event cancelled/wrong: acknowledge plainly, `signal=dismissed`, correct in next digest.
- System was wrong about suitability: apologize once, log `not_interested` with note, move on. No groveling.

## Email mining (`events_ingest_email`)

Trigger on: the user forwarding/pasting an event email, "check my newsletters for events", or a ticket purchase mention ("I booked the aquarium").

- **Newsletters** → pass subject+body; the tool extracts candidate events (title/date/link). Present verified-looking ones as suggestions; say they're unverified candidates. Never auto-add to the store without the user's nod.
- **Receipts** (order confirmation, "your tickets") → the tool returns order/title/date; match it to a known event, then `events_feedback signal=went` — this is a high-weight attendance signal that tunes tolerances. If no matching event exists, say so and offer a one-line manual note.
- Pass only the message text; bodies are never stored — only extracted fields.

## Privacy commands

- "forget my location data / delete the timeline stuff" → `events_forget_locations`, then confirm plainly what was removed (visit counts + drive histogram; feedback history retained).
- "where does my data live?" → locally, in the plugin's own storage on this machine; nothing personal is in the GitHub repo.
- Never surface location-derived facts ("you've been to X 12 times") in front of others unprompted — treat as household-private.

