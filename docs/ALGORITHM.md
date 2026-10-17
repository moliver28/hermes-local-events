# ALGORITHM — effort model, learning, and ranking

## 1. Effort is the first-class dimension

### Effort vector (static, computed at ingest)

| Component | Derivation |
|---|---|
| `duration_hours` | event start/end |
| `physical_intensity` ∈ [0,1] | keyword table (story time 0.05 → hike 0.85 → climb 0.95), venue-type prior fallback (library 0.10, trailhead 0.85), LLM assist when unknown |
| `overnight` | keywords (camp/lodge/retreat) or duration ≥ 8 h |
| `indoor_outdoor` | venue-type prior; keyword fallback |
| `stroller_friendly` | explicit keyword only (never inferred) |
| `cost_min/max` | offers, per person |

### Effort computation (dynamic, per event × party × context)

Weighted sum → `effort_score` ∈ [0,100], with `band` low <35, high >65:

```
score = 100·(0.30·drive_norm + 0.20·duration_norm + 0.20·intensity
            + 0.15·cost_norm + 0.10·kid_logistics + 0.05·lead_penalty)
```

- `drive_norm` is **relative to the household's drive tolerance at the current mood** — the same 30-minute drive is low-effort for an adventurous mood and high for a tired one.
- `kid_logistics` = nap-window overlap minutes (capped) + age-based baseline for very young kids.
- `lead_penalty` punishes booking-required events with <3 days lead.
- `overnight` forces score ≥ 80 (always "high").
- State: `computed` (drive + duration known) / `likely` / `unknown`. Unknown never silently matches an energy request.

## 2. Tolerance curves (learned, per mood)

Logistic acceptance over drive minutes and duration hours, centered at per-mood tolerances:

- Defaults: drive {tired 25, normal 45, adventurous 80, escape 160} min.
- **Seeding from Google Timeline**: median of the observed drive-distance histogram → `normal`; tired = 0.6×, adventurous = 1.8×, escape = 3.2×.
- Updates: `too_far`/`too_much_effort` tighten (weighted 0.8·μ + 0.2·observed·0.5); attending beyond the current center loosens halfway toward the observation.

## 3. Preference learning — Beta-Binomial facets

For facets (category, subcategory, venue, organizer, effort band, price band, time slot), maintain Beta(α, β) with prior (2, 3):

- `went` → α += 2; `interested` → α += 1; any typed negative → β += 1.
- Score = posterior **lower bound** (mean − 1.96·σ) — certain tastes rank high; one-off flukes don't. Auto-shrinks to prior below ~5 observations.
- Category momentum only: exponentially decayed EWMA, halflife 60 d. No time-of-day decay (confounded with category — kid events are all mornings).

## 4. Ranking and slate

```
score = 0.50·pref_lb + 0.30·effort_fit + 0.10·freshness + 0.10·novelty − unknown_penalty
```

- `effort_fit` = drive acceptance ⊕ duration acceptance ⊕ mood-intensity match (0.5/0.3/0.2).
- `unknown_penalty` (0.15) when kid/dog/effort state is unknown — honesty is priced in.
- Slate: greedy calibrated-MMR (λ = 0.3 fixed), hard caps: 1 anchor/day, ≤2 per venue/organizer/day.
- Digest: one anchor per energy band + alternates + explicit whitespace; exploration never enters the digest (one labeled wildcard in `events_search` only).

## 5. Signals

| Signal | Effect |
|---|---|
| `went` (recap, calendar, receipt, or location backfill) | facet α+2, tolerance loosening if beyond center |
| `interested` | facet α+1 |
| `too_far` / `too_much_effort` | facet β+1 **and** tolerance tightening at that mood |
| `too_pricey` | facet β+1 (price-band facet) |
| Receipts / calendar confirms | high-weight positives only — absence never implies disinterest |
| Timeline venue-visit during event window | `went` backfill (location-confirmed attendance) |

## 6. Monitoring (no holdouts at this scale)

- Engagement rate = (interested + went) / delivered; dismiss-rate guardrail alarms > 15%.
- Effort-match rate: digest pick's band == requested/mood band.
- Weekly 10-item audit of LLM kid/dog labels (prompt-drift detector — labels are cached by content hash + prompt version).
- Dedup spot-checks; catalog supply trends per category/band.
