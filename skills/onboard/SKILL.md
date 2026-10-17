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

Tell the user: "I'll find local event sources for your area." You are the scraper here — your web tools are first-class parts of this pipeline.

### 3a. Discover with your web tools

1. `web_search` a battery of region queries: "[city] family events calendar", "[county] library events", "[city] parks & recreation activities", "[city] events this weekend", "[city] weekly newspaper events", "[city] museum events calendar".
2. For each promising hit, `web_extract` the page (fast, cheap). Look for the signal vocabulary: `application/ld+json` with `"@type": "Event"`, RSS/Atom feeds, `BEGIN:VCALENDAR` / `.ics` links, or list-shaped HTML (article/.event/li calendars).
3. **JS-heavy calendars** (ActiveNet, Localist widgets, Communico) that return shells or 403/429/bot walls to raw fetch: route through `browser_exec` (and the blocked-page-recovery pattern) to render and read the list. If a portal needs a login, it happens **only inside your browser with the host vault, with the user present for 2FA** — never ask for or handle their password, never store credentials.
4. Probe what you found with `events_sources action=validate` when the URL answers plain HTTP — it reports the `suggested_connector`. Skip validation for browser-only sources; register them directly.

### 3b. Register what you found

`events_sources action=register {url, name, source_id?, connector?, cadence_hours?}` — user-registered sources live in the plugin's data dir (the shipped South Sound set stays untouched) and merge into every refresh. Ticketmaster Discovery needs a free key — ask; skip gracefully otherwise. Family-by-construction sites (ParentMap-style) are worth hunting specifically.

### 3c. First pull + one-time backfill

1. `events_refresh` — show the count honestly ("found 214 events from 7 sources").
2. For any source that errored or looks JS-rendered: fetch it yourself (`web_extract` → `browser_exec` fallback) and feed the body through `events_ingest_content {source_id, url, content, format?}` — those events rank identically. Tell the user which sources are "browser-fed".
3. If a browser-fed source worked well, say they can keep it that way; the register entry with `via: browser` marks the path.

## 4. Google Timeline import (optional, opt-in — never infer)

Offer: "If you use Google Maps, you can export your Timeline and I'll learn your real drive tolerance + find events you already attended. The export itself is a ~60-second manual step — Google doesn't allow apps to pull it — then I do everything else."

### 4a. Help them export (be the navigator)

Be upfront: **there is no Google API for this** (verified Oct 2026) — only the device export and the web Takeout flow. Offer the easiest path for their device, with exact clicks:

- **Android (fastest):** Settings → Location → Location services → Timeline → **Export Timeline data** → save to Downloads / share it. On a PC sync, it lands in Downloads.
- **iPhone:** Google Maps app → profile picture → Your data in Maps → Content → **Export timeline data** (goes through Takeout; arrives as a download).
- **Desktop / full history:** takeout.google.com → "Deselect all" → tick **Location History (Timeline)** → All data included → Next → Export → download the zip when the email arrives.

Then either of:
- They say **"import my timeline"** with nothing more — run `events_import_locations {"path": "auto"}`: it checks the plugin's imports folder first, then **scans Downloads** for recent Takeout/Timeline files and lists candidates. Ask which one, re-run with its explicit path.
- Or they drop the file into the plugin's `imports` folder (path shown in the tool error).

Optional, only if this Hermes instance has a computer-use/browser tool: offer to drive the Takeout website *with the user present* — they do the login and 2FA themselves, Hermes does the clicking. Never ask for or handle their Google password.

### 4b. Analyze, confirm, apply

1. Run `events_import_locations {"path": "..."}` — present the summary (visits, median drive, home guess, matched events) in plain language and **ask before** `confirm=true`.
2. Confirm applies: seeds drive tolerances from their actual driving, sets home (only with their OK), backfills attended events as high-weight positives, deletes the imported copy. Raw GPS never stored — say that sentence; it's why people say yes.

## 5. Finish

- Offer the automation blueprint: "Want the weekend digest Thursday evenings?" → enable `local-events:thursday_digest` (default Thu 20:00).
- Show `events_digest` output as the first digest; remind them the first month is calibration and every 🔥/👌/✖ teaches it.
- **Stop here.** Onboarding is complete — do NOT pitch calendar sync, email mining, escapes, or the Ticketmaster key now. The `local-events-adopt` skill introduces those one at a time, at natural moments.
- If the user declines the Timeline import here, leave it; the adopt skill may raise it once more after ~2 weeks of feedback, then never again.
