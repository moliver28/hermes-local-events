# SPEC — requirements (distilled from the attendance-first research specification)

This plugin adapts a research-grade attendance-first recommender specification to **single-household scale**. Where the research spec demands industrial machinery, we keep the principle and drop the weight.

## Objective

Rank local events by **P(family attends & is glad they went)** — not clicks, not popularity. Secondary objectives, in order: effort-fit for the household's current energy, kid/dog suitability honesty, slate diversity, family whitespace.

## What we kept from the research spec

| Research principle | Personal-scale implementation |
|---|---|
| Event **instances**, not series (price/time/status vary per occurrence) | `series_id` links recurrences; every instance ranks separately |
| Hard eligibility before learning | `rank.is_eligible`: status, window, kid/dog strictness, age_min, drive tolerance, overnight-per-mood |
| Four-state suitability (`verified_yes/verified_no/likely/unknown`), unknown ≠ yes | `core/suitability.py` + evidence quotes required for `verified_*` |
| Calibrated slate with diversity (MMR/DPP) | MMR-lite, λ=0.3 fixed, caps ≤2 per venue/organizer/day, 1 anchor/day |
| Multi-channel retrieval | 6 connector types (ICS/RSS/JSON-LD/HTML/API/custom) via declarative `sources.yaml` |
| Travel-tolerance survival curves | Logistic acceptance curves per mood; seeded from Google Timeline; updated by feedback |
| Entity resolution with cannot-link semantics | Blocking (date±1d × geohash × title tokens) + RapidFuzz ≥0.85; different start times (>90 min) never merge |
| Typed negative feedback | `too_far / too_pricey / too_much_effort / not_interested / dismissed` — each adjusts a specific model component |
| Point-in-time correctness | Freshness from `first_seen`; volatile refresh by cadence per source |
| Location-confirmed attendance (consented) | Google Timeline import: venue-visit overlap ±90 min → high-weight positive |
| Group/family modeling with vetoes | Household = declared party; kid `verified_no` and dog `verified_no` are hard vetoes |
| Explanations from audited contributions, max 3 reasons | `reasons` list per pick ("matches your patterns · 18-min drive · dogs ok (verified)") |
| Guardrails on exploration | Digest = pure exploit (posterior lower bound); one labeled Thompson wildcard in search only |

## What we dropped (and why)

| Dropped | Why |
|---|---|
| Two-stage retrieval + ANN indexes | ≤2k live events; SQLite FTS5 + Python cosine is the right tool |
| LambdaMART / learning-to-rank | 5–50 labels/month cannot train it; Beta-Binomial posteriors improve from day one |
| IPS / debiasing / propensity logs | No randomized serving; would be decoration |
| Probability calibration claims | Posterior lower bounds only — ordinal, honest, shrinkage-native |
| Learned seasonality / burst detection | School calendar + festival season are hard-coded; trends mined from catalog supply instead |
| Holdout splits / offline replay | A holdout at this volume doubles time-to-signal with zero statistical power |

## Interfaces

- **Tools** (LLM-facing, JSON in/out): see `schemas.py` — refresh, search, digest, feedback, calendar ICS, email ingest, sources, share, import/forget locations.
- **Skills** (agent behavior): `onboard`, `find-events`, `recap`.
- **Automations**: Thursday digest (20:00), morning-of nudge (Sat/Sun 08:30), long-weekend escape prompt.
- **CLI**: same operations without Hermes.

## Non-functional

- Fully offline-capable core; network only for refresh, geocoding (cached), travel-time (cached), weather.
- No LLM required: rules-only fallback produces `unknown` states honestly.
- Polite scraping: per-source cadence, UA, robots-respect intent, recorded-fixture tests (CI never hits the network).
- Local-first privacy: household data only in plugin storage; derived-only location import; transient email.

## Success criteria (v1)

1. Thursday digest arrives with ≥3 vetted picks on a normal weekend (South Sound source set).
2. Zero known-false "dog ok"/"kid ok" claims in an audited sample (unknown is shown as unknown).
3. After 4 weeks of feedback, tired-mode picks are within the household's actual tired-day drive tolerance ≥80% of the time.
4. `python cli.py digest` works on a fresh install with no Hermes, no API keys.
