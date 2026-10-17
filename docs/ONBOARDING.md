# ONBOARDING — first run in any region

The `local-events-onboard` skill drives this conversationally. Here is the reference flow (what it does and why).

## Stage 1 — Household profile (~5 questions)

| Ask | Sets |
|---|---|
| Home area (label + coords; geocode an address via Nominatim, confirm) | `home_label`, `home_lat/lon` |
| Kids' ages (+ nap windows if under ~5) | `kid_ages`, `nap_windows` |
| Dog? size? | `has_dog`, `dog_size` |
| Comfortable budget per outing | `budget_per_outing` |
| Interests in plain words ("zoo, trains, art, hikes") | `interests` |

## Stage 2 — Energy patterns (the first-class dimension, asked explicitly)

- "When you're wiped but want out, what's a win?" → seeds the **tired** tolerance
- "Longest drive you'd actually do on a normal weekend?" → **normal**
- "Big adventure day — how far, how long?" → **adventurous**
- "Overnight trips — how far?" → **escape**

Skip-able: the Google Timeline import (Stage 4) calibrates these from real driving.

## Stage 3 — Source discovery (any region; this is what makes the plugin portable)

1. **National platforms**: Ticketmaster Discovery (optional free key), AllEvents city feed.
2. **Library platforms**: most US libraries run BiblioCommons / Communico / Evanced — search "<county> library events" and validate.
3. **Local outlets**: "[city] events this weekend", "[city] family calendar", "[city] parks events", "[city] weekly newspaper events".
4. **Validate every candidate** with `events_sources {"action": "validate", "url": …}` — the probe reports JSON-LD / RSS / ICS / list-HTML and suggests a connector.
5. Add validated sources to `data/sources.yaml` (same shape as the shipped South Sound set). Family-by-construction sources (ParentMap-style) get `defaults: {kid: verified_yes}`.
6. `events_refresh`, then show the honest count.

The `validate` action is SSRF-guarded (http/https only, host must resolve public, no redirects).

## Stage 4 — Google Timeline import (optional, opt-in, never inferred)

**Why**: real drive tolerance from actual driving; location-confirmed attendance backfill; home confirmation; venue familiarity.

**Programmatic status (verified Oct 2026)**: Google offers **no API** for Timeline export. The supported routes are the device export (Android: Settings → Location → Location services → Timeline → Export Timeline data; iPhone: Google Maps → Your data in Maps → Export) and the manual web flow (takeout.google.com → Location History (Timeline)). Hermes therefore acts as navigator for the ~60-second manual step, then automates everything downstream. If the host has a computer-use/browser tool, Hermes may drive the Takeout site *with the user present* (user performs login + 2FA; never handle their Google credentials).

1. User exports via one of the routes above.
2. They say "import my timeline" — `events_import_locations {"path": "auto"}` checks the plugin's `imports` folder first, then **scans Downloads read-only** for recent Takeout/Timeline files (`takeout-*.zip`, `Timeline*.json`, `Records.json`, semantic-history zips; ≤60 days old) and returns candidates. The user picks one; re-run with its explicit path. Downloads are never auto-imported — listing only, import requires an explicit chosen path. Alternatively drop any path/zip/file.
3. `events_import_locations {"path": …}` — **analyze pass**: returns plain-language summary (visits, places, drives, median drive, home guess, matched events). Present it; get explicit OK.
4. `events_import_locations {…, "confirm": true}` — applies:
   - drive histogram → tolerance curves (median → normal; tired 0.6×, adventurous 1.8×, escape 3.2×)
   - home guess (only if user hasn't set one)
   - venue visits overlapping event windows ±90 min → `went` backfills (high-weight positives)
   - per-place visit counts → familiarity/novelty features
   - **deletes the imported copy**

**Privacy invariants**: parsing streams through; only derived data persists (histogram, place counts, confirmed home). Raw GPS points are never written to disk. The Downloads scan is name-based, recency-bounded, read-only. `events_forget_locations` wipes all derived location data. Zip extraction is zip-slip-guarded; import paths reject `..` traversal.

## Stage 5 — Automations + first digest

- Offer the **Thursday digest** blueprint (default Thu 20:00) and the **morning-of nudge** (Sat/Sun 08:30).
- Run the first `events_digest`; remind: first month is calibration — every 🔥/👌/✖ teaches it.
