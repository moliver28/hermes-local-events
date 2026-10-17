# hermes-local-events

An **effort-first local event finder** for [Hermes Agent](https://hermes-agent.nousresearch.com) — a native Hermes plugin that finds things for your family (kids + dog) to actually *do*, learns your tastes and energy tolerance over time, and never makes you sift a catalog.

Designed around one metric: **life satisfaction per unit of planning effort.**

## Why "effort-first"

Most event finders rank by popularity. This one treats **effort/energy as the primary, first-class dimension**:

- Every event carries a structured **effort vector** — drive minutes, duration, physical intensity, kid logistics (nap overlaps, stroller), planning lead, cost, overnight flag.
- Effort is **relative to your household**: the same festival is "low" for eager adults on a free Saturday and "high" with a nap-time toddler in the rain.
- The Thursday digest is **organized by the energy dial**: "If you're wiped → … / Solid outing → … / Adventure → …", one anchor per day, explicit whitespace ("Sunday open on purpose"), rain swaps pre-attached to every outdoor pick.
- Queries speak mood: *"we're wiped but stir-crazy"* → low drive, low intensity, high novelty. *"let's get out of town"* → escape mode (overnight, ~150 mi, booking-lead flags).

## Install (Hermes)

```bash
hermes plugins install moliver28/hermes-local-events --no-enable
hermes plugins enable local-events
```

Then say to Hermes: **"onboard events"** — the `local-events-onboard` skill walks through household profile, energy patterns, source discovery for your region, and (optionally, explicitly) a Google Timeline import.

Validate any time:

```bash
hermes plugins validate ~/.hermes/plugins/hermes-local-events --install-deps
hermes plugins doctor ~/.hermes/plugins/hermes-local-events --ci
```

## Standalone (no Hermes)

```bash
pip install -e ".[dev]"
python cli.py refresh
python cli.py digest
python cli.py search --energy tired
python cli.py import-locations takeout.zip   # Google Timeline import
python cli.py stats
```

## What it does

| Capability | Tool |
|---|---|
| Polite multi-source refresh (ICS/RSS/JSON-LD/HTML, Ticketmaster API) + agent-fed sources | `events_refresh` |
| Mood-aware search with kid/dog/party filters, wildcards, "more like this" | `events_search` |
| Plan-first Thursday digest by energy band (rain/smoke line on outdoor picks) | `events_digest` |
| **Budgets & spend trend** — per-person + party-total pricing, mood budgets, `too_pricey` teaches | `events_budget` |
| Morning-of conditions: rain %, US AQI (smoke), NWS alerts | `events_conditions` |
| Hermes-found pages enter the same pipeline (browser-fed events) | `events_ingest_content` |
| Two-tap feedback that teaches ranker + tolerance curves | `events_feedback` |
| Calendar handoff (ICS → Hermes Google Calendar skill) | `events_calendar_ics` |
| Email mining: newsletters → candidates, receipts → attendance | `events_ingest_email` |
| Invite cards to forward to friends | `events_share` |
| **Google Maps Timeline (Takeout) import** — seeds real drive tolerances, backfills attended events, never stores raw GPS | `events_import_locations` / `events_forget_locations` |
| Source registry + URL validation for any new region | `events_sources` |

## Learning (right-sized, no ML infra)

- **Beta-Binomial facet posteriors** (~100 lines of stdlib math) over category, venue, organizer, effort band, price band, time slot. `went` weighs 2, `interested` 1, negatives 1. Scores shrink to the prior below ~5 observations, and **evidence forgets on a 120-day halflife** — old tastes fade honestly.
- **Catalog trends, not feedback-mined trends**: recurring series (ICS RRULE expansion + title/venue linking) and per-category upcoming-supply shifts are mined once per refresh; they can win the labeled search wildcard (bounded +0.10 boost) but never touch the exploit score. Flaky organizers (≥30 % cancellations over ≥3 events) get an honest warning line.
- **Tolerance curves per mood** (logistic acceptance over drive/duration) — tightened by "too far / too much", loosened by attendance past the center, and **seeded from your actual Google Timeline driving** if you opt in.
- **Cost is first-class (the money twin of effort)**: per-person prices become party totals with kid discounts (under-N free, kid ratio); mood budgets scale from your baseline (tired 0.6× … escape 3×) with learned per-mood curves; a 14-day **spend trend** rescales them (splurgy stretch → leaner picks, frugal stretch → room for a treat). "too pricey" tightens, over-budget attendance loosens. An unstated price never counts as free, and only an explicit `max_total` hard-caps — mood budgets reorder, they don't hide.
- **Category momentum** decays with a 60-day halflife on real elapsed time — what you loved last summer fades honestly.
- Trends are mined from the **event catalog** (supply, one-off vs recurring, organizer track record), not your sparse feedback log.
- The digest is pure exploitation; exploration is one labeled wildcard in search only, chosen by a Thompson draw over the facet posteriors (rank 4+, labeled "wildcard", deterministic per day). An optional LLM pass (`llm_rerank`, on by default) reorders digest picks **within** each energy band and writes the one-line reasons — it can never add, drop, or move picks across bands, and fails closed to the scored order.
- Every pick shows its score components (preference / effort fit / freshness / novelty / budget fit) — the scorer is its own explanation.
- Optional `pip install "hermes-local-events[embeddings]"` adds semantic "more like this" search and similarity-aware slate diversity (model2vec, ~8 MB); without it everything runs unchanged.
- `python cli.py replay` re-scores your feedback journal to regression-check weight changes (served-events-only bias, stated plainly).
- Guardrail: dismiss rate > 15% alarms; weekly LLM-label audit detects prompt drift.

## Honesty rules

Suitability is four-state — `verified_yes | verified_no | likely | unknown` — and **unknown never passes a strict filter**. `verified_*` requires an evidence quote from the source page. The digest reports how many events were excluded as unverified ("…say 'show unknowns' to see them"). A dog-policy "not stated" renders as *"assume no"*, never as a maybe.

## Privacy

- All household data local (SQLite via Hermes plugin storage). The repo ships zero personal data.
- Google Timeline import is **opt-in, explicit, derived-data-only**: home guess (you confirm), drive-distance histogram, per-place visit counts. Raw GPS points are never persisted; the imported file is deleted on confirm. `events_forget_locations` wipes it all. Google offers no export API (verified Oct 2026) — Hermes navigates the device/Takeout export, then automates everything after; its Downloads scan is read-only, name-based, recency-bounded, and never auto-imports.
- Email is processed transiently — only extracted event fields persist.

## Sources (verified Oct 2026)

Curated South Sound set ships as default (see [docs/SOURCES.md](docs/SOURCES.md)): library platforms (ICS), ParentMap + Seattle's Child (kid-by-construction), Point Defiance Zoo (explicit dog events), Parks Tacoma, SouthSoundTalk, Tacoma Weekly (custom scraper), Ticketmaster Discovery (free key, optional), plus generic schema.org/JSON-LD extraction that retrofits many sites. Onboarding validates and registers sources for **any** region — new region = config, not code. Eventbrite/Meetup/Amadeus APIs are closed or paid as of 2026 and are intentionally not used.

## Automation blueprints

- **Weekend events digest** — Thursday 20:00 (co-decision night + ticket lead time)
- **Morning-of nudge** — Sat/Sun 08:30: weather recheck, leave-by time, what-to-bring
- **Long weekend escape prompt** — Monday noon, ahead of 3-day weekends

## Skills (new user → fully adopted)

- **`local-events-onboard`** — first-run: household profile, energy patterns, source discovery for any region, optional Google Timeline import, digest setup
- **`local-events-find`** — daily driver: mood→energy mapping, party modes, digest delivery, feedback parsing, calendar + invite cards, email mining (newsletters → candidates, receipts → attendance), privacy commands
- **`local-events-recap`** — Sunday two-tap recap: savoring + the signals that teach the ranker
- **`local-events-adopt`** — the adoption ladder: introduces exactly one next feature (calendar sync → Timeline import → email mining → Ticketmaster key → escapes → adults mode) at a natural moment, never as a feature tour

## Docs

- [docs/SPEC.md](docs/SPEC.md) — requirements distilled from the attendance-first research spec
- [docs/ALGORITHM.md](docs/ALGORITHM.md) — effort model, tolerance curves, Beta-Binomial ranker, metrics
- [docs/ONBOARDING.md](docs/ONBOARDING.md) — onboarding + source discovery + Timeline import flows
- [docs/SOURCES.md](docs/SOURCES.md) — source catalog, access methods, ToS notes

## Disclaimer

Suitability verdicts are evidence-based heuristics, not guarantees — always verify kid/dog policies with the venue before you go. Scraping is rate-limited and robots-respecting; respect each source's terms.

MIT — see [LICENSE](LICENSE).
