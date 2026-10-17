# SOURCES — catalog and terms (verified October 2026)

Access methods verified by live research in Oct 2026. Re-verify before adding paid/closed sources; statuses change.

## Starter set status

| Source | Connector | Kid/dog taggability | Notes |
|---|---|---|---|
| Tacoma Public Library (BiblioCommons) | `ics` | audience filters built in; kid-by-construction defaults | RSS + per-event ICS |
| Pierce County Library (Communico) | `ics` | audience filters | RSS/ICS per location/audience |
| ParentMap calendar | `html_list` | **kid-by-construction** | curated family-only submissions |
| Seattle's Child calendar | `html_list` | **kid-by-construction** | kids-only listings |
| Point Defiance Zoo & Aquarium | `jsonld` | explicit "Dog Days"-style events | schema.org JSON-LD |
| Parks Tacoma | `html_list` | family programs named | HTML events page |
| SouthSoundTalk | `rss` | tag-based (WordPress) | active 2026; superseded Exit133 |
| Tacoma Weekly | `tacomaweekly` (custom) | prose only — NLP/rules | static HTML; Cloudflare in front; be polite |
| WA State Parks events | `html_list` (disabled by default — validate selector) | outdoors/escape | enable after validating |
| WTA events/work parties | `html_list` (disabled by default) | high-effort signal | hiking work parties |
| Ticketmaster Discovery | `ticketmaster` | age labels (age_min), genre | **free key required** (5k calls/day); set via plugin settings/env `TICKETMASTER_API_KEY` |
| AllEvents.in | `rss` (disabled by default) | aggregator tags | verify feed + ToS fit before enabling |

## Closed / paid — intentionally not used (as of 2026)

- **Eventbrite API v3** — closed to new developers (April 2025 support statement); scraping violates ToS. Skip.
- **Meetup** — REST keys retired Feb 2025; GraphQL needs Meetup Pro (~$55/mo). Skip.
- **Amadeus Events** — free self-service tier decommissioned 2026. Skip.
- **PredictHQ** — demand-intelligence pricing (~$500+/user/yr). Skip.
- **Luma** — REST API needs Luma Plus; public calendars still expose per-event ICS (usable via `ics` connector without a key).

## Infra endpoints (all free-tier, OSS-friendly)

- **Geocoding**: Nominatim (`nominatim.openstreetmap.org`) — 1 req/s max, cached aggressively in SQLite.
- **Travel time**: OpenRouteService **at `api.heigit.org`** (the old `api.openrouteservice.org` shut down Aug 24, 2026). Free standard tier; API key optional — haversine×detour-factor estimate is the fallback and drive times are cached per (venue, mode, day-part).
- **Weather**: Open-Meteo (no key) for rain probabilities on outdoor picks.

## Etiquette / compliance

- Per-source `cadence_hours` throttle; polite User-Agent; no robots-disregarding retries.
- Kid-by-construction defaults (`verified_yes`) are used **only** where the source curates family events exclusively (ParentMap, Seattle's Child, library audiences) — never guessed.
- Generic `jsonld` connector follows at most `max_links` detail pages per run with sleeps between.
- All connectors are testable offline via recorded fixtures; CI never fetches.

## Adding a source for a new region

Run `events_sources {"action": "validate", "url": …}` → suggested connector → append a `sources.yaml` entry. See docs/ONBOARDING.md Stage 3.
