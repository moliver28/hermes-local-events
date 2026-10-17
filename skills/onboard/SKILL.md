---
name: local-events-onboard
description: Onboard a household into the local-events plugin — profile, energy patterns, source discovery for any region, and optional Google Timeline import. Use when the user first sets up events, says "set up events", "onboard events", or moves cities.
---

# Onboarding the local-events plugin

Goal: a working profile in one conversation, ~5 questions max, zero forms. Ask in plain talk; confirm before writing. Set plugin settings via the plugin's config (or `events_*` tools) as you go.

## 1. Household profile (ask conversationally, not as an interrogation)

1. **Home base** — area label + coordinates ("roughly where do you start drives from?"). Geocode with Nominatim if they give an address; confirm before saving.
2. **Kids** — ages ("how old are the kids?"). Sets `kid_ages`. If under ~5, ask nap windows ("when's naptime on weekends?").
3. **Dog** — yes/no + size. Sets `has_dog`, `dog_size`.
4. **Budget** — "what's a comfortable spend for a family outing?" Sets `budget_per_outing`.
5. **Interests seed** — "what does your family already love? (zoo, trains, art, hikes…)" → save to interests (free text list; they map to categories later).

## 2. Energy patterns (first-class — ask explicitly)

- "When you're wiped but want to get out, what's a win?" (e.g. "15-min drive, coffee + playground") → seeds the *tired* tolerance.
- "Longest drive you'd actually do on a normal weekend?" → seeds *normal*.
- "Would you do a big adventure day — how far, how long?" → seeds *adventurous*.
- "Overnight trips — ever? How far would you go?" → seeds *escape*.

Write answers to `drive_tolerance`/`duration_tolerance` in the household profile (kv `household`), or leave defaults and let the Google Timeline import (next) calibrate from real driving.

## 3. Source discovery (any region — this is what makes the plugin portable)

Tell the user: "I'll find local event sources for your area."

1. Probe the **national platforms** first with `events_sources action=validate`:
   - Ticketmaster Discovery (needs free key — ask if they want it; skip gracefully otherwise)
   - AllEvents.in city feed (`https://allevents.in/<city>/rss`)
2. **Library platforms** — most US libraries run BiblioCommons, Communico, or Evanced; search the web for "<county> library events" and validate the calendar URL (validate reports `suggested_connector`).
3. **Local outlets** — web-search "[city] events this weekend", "[city] family calendar", "[city] parks events", "[city] weekly newspaper events"; validate each candidate URL.
4. Only add sources that validate. Add each to `data/sources.yaml` in the same shape as the shipped South Sound set (id, name, connector, url, cadence_hours, defaults). Family-by-construction sources (ParentMap-style) get `defaults: {kid: verified_yes}`.
5. Run `events_refresh` and show the count honestly ("found 214 events from 7 sources").

## 4. Google Timeline import (optional, opt-in — never infer)

Offer: "If you use Google Maps, you can export your Timeline and I'll learn your real drive tolerance + find events you already attended."

1. User exports at takeout.google.com → Location History (or Android: Settings → Location → Timeline → Export).
2. They drop the zip into the plugin's `imports` folder (tell them the path from the tool error) or pass a path.
3. Run `events_import_locations {"path": "..."}` — present the summary (visits, median drive, home guess, matched events) in plain language and **ask before** `confirm=true`.
4. Confirm applies: seeds drive tolerances from their actual driving, sets home (only with their OK), backfills attended events as high-weight positives, deletes the imported copy. Raw GPS never stored.

## 5. Finish

- Offer the automation blueprint: "Want the weekend digest Thursday evenings?" → enable `local-events:thursday_digest` (default Thu 20:00).
- Show `events_digest` output as the first digest; remind them the first month is calibration and every 🔥/👌/✖ teaches it.
- **Stop here.** Onboarding is complete — do NOT pitch calendar sync, email mining, escapes, or the Ticketmaster key now. The `local-events-adopt` skill introduces those one at a time, at natural moments.
- If the user declines the Timeline import here, leave it; the adopt skill may raise it once more after ~2 weeks of feedback, then never again.
