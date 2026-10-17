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

## 2b. Cost is first-class too (`core/cost.py` — the money twin of effort)

Prices are per-person at ingest; everything downstream is **party-relative**, like effort:

- **Party math** (`breakdown`): party = 2 adults + kids; kids under `kid_free_under` (default 2) enter free, others pay `kid_price_ratio` (default 0.5) of the adult price. The breakdown carries per-adult / per-kid / party-weighted **per-person**, a typical total (price_min basis) and a **conservative total** (price_max basis) — budget checks always score the conservative number, so the family is never surprised at the gate. Price unstated → `known=false`, never silently "free" (the price facet is `price:unknown`, not `price:free`).
- **Mood budgets**: the declared `budget_per_outing` (default $60) is the *normal*-mood party-total baseline; other moods derive from it (tired 0.6×, adventurous 1.8×, escape 3×). A learned absolute curve per mood (`budget_tolerance`) overrides the derivation — exactly parallel to drive/duration tolerance.
- **Spend trend**: the trailing 14 days of `went` ledger entries are priced with the same party math; `factor = clamp(1 + (baseline − avg_per_outing)/baseline, 0.6, 1.5)`. A splurgy stretch tightens every mood's effective budget, a frugal one earns headroom, no history is neutral.
- **Scoring**: `cost_fit` = logistic acceptance of the conservative total centered at the mood's effective budget (weight 0.10, see §4). Unknown price is neutral 0.5 — absence of evidence is not cheapness.
- **Hard cap**: `events_search max_total=X` (and `Query.max_total`) excludes events whose conservative total exceeds X; price-unknown events fail a cap (same honesty rule as strict suitability filters). A mood budget alone never hides events — it reorders them.
- **Learning**: `too_pricey` tightens that mood's curve a quarter of the way down from the complained-about total (floored at 30 % of baseline); attending something over budget loosens halfway toward the total, mirroring drive.

## 3. Preference learning — Beta-Binomial facets

For facets (category, subcategory, venue, organizer, effort band, price band, time slot), maintain Beta(α, β) with prior (2, 3):

- `went` → α += 2; `interested` → α += 1; any typed negative → β += 1.
- Score = posterior **lower bound** (mean − 1.96·σ) — certain tastes rank high; one-off flukes don't. Auto-shrinks to prior below ~5 observations.
- **Evidence forgetting**: facet α/β decay toward the prior with a **120-day halflife at read time** (timestamps per facet, same idiom as momentum) — last summer's venue obsession fades honestly. Thompson draws use decayed posteriors too.
- Category momentum only: exponentially decayed EWMA, halflife 60 d, decayed by **elapsed wall-clock time** (timestamps stored per category). No time-of-day decay (confounded with category — kid events are all mornings).

## 3b. Catalog trends (`core/trends.py`)

The research-spec principle: trends come from the **catalog**, not the sparse feedback log. Once per refresh: per-category upcoming-supply share (next 30 d vs next 90 d), recurring share (`series_id` coverage), and organizer track record (≥3 appearances, ≥30 % cancelled/postponed = flaky). Consumers — and the only consumers:
- **exploration boost**: the search wildcard adds `0.10·clamp(trend−1)` to its sampled draw (never the exploit score);
- **honesty annotation**: `rank()` appends "organizer has cancellations — double-check" (a warning, never a score change);
- **`cli.py stats`** prints the full picture.

## 4. Ranking and slate

```
score = 0.45·pref_lb + 0.25·effort_fit + 0.10·freshness + 0.10·novelty + 0.10·cost_fit − unknown_penalty
```

- `effort_fit` = drive acceptance ⊕ duration acceptance ⊕ mood-intensity match (0.5/0.3/0.2).
- `cost_fit` = budget acceptance of the conservative party total at the mood's (trend-rescaled) budget center; unknown price → neutral 0.5 (§2b).
- `unknown_penalty` (0.15) when kid/dog/effort state is unknown — honesty is priced in.
- Slate: greedy calibrated-MMR (λ = 0.3 fixed), hard caps: 1 anchor/day, ≤2 per venue/organizer/day. With the optional embeddings extra (`pip install "hermes-local-events[embeddings]"`), strong semantic similarity between two events (cosine ≥ 0.85 over stored vectors) counts as redundancy at level 0.6.
- Digest: one anchor per energy band + alternates + explicit whitespace; exploration never enters the digest (one labeled wildcard in `events_search` only). Outdoor picks may carry a best-effort conditions line (rain %, US AQI, NWS alerts) via an injectable `conditions_fn` — offline digests are byte-identical.
- Exploration (search only): `wildcard_pick` selects from rank 4+ by a **Thompson draw** over the (decayed) facet posteriors, optionally boosted by catalog trends (see §3b), labeled "wildcard" in results. Seeded by the query date → deterministic within a day.
- Optional LLM pass (config `llm_rerank`, default on): one listwise re-rank per digest may reorder picks **within** each energy band (never across bands, never add/drop, eligibility untouched), writing the one-line reasons. Candidate order is shuffled before prompting (position-bias mitigation), reasons capped at 90 chars (verbosity bias), any malformed response falls back to the scored order. The digest stays pure exploitation.
- Every pick carries its score **components** (`pref / effort_fit / freshness / novelty / cost_fit / unknown_pen` + cost_total, cost_per_person, budget_effective, trend_factor, drive, band) — the additive scorer is its own explanation; no SHAP needed.

## 5. Signals

| Signal | Effect |
|---|---|
| `went` (recap, calendar, receipt, or location backfill) | facet α+2, tolerance loosening if beyond center; money curve also loosens if the priced total ran past the budget center (§2b) |
| `interested` | facet α+1 |
| `too_far` / `too_much_effort` | facet β+1 **and** tolerance tightening at that mood |
| `too_pricey` | facet β+1 (price-band facet) **and** money-curve tightening at that mood (§2b) |
| Receipts / calendar confirms | high-weight positives only — absence never implies disinterest |
| Timeline venue-visit during event window | `went` backfill (location-confirmed attendance) |

## 6. Monitoring (no holdouts at this scale)

- Engagement rate = (interested + went) / delivered; dismiss-rate guardrail alarms > 15%.
- **Effort-match rate** = share of rated `went` feedback saying `right_effort`. **Band-mood match rate** = share of positives with a known mood whose digest band equals that mood (`Feedback.band` is stamped from the digest pick's energy at feedback time). Both computed in `feedback.metrics`.
- Weekly 10-item audit of LLM kid/dog labels (prompt-drift detector — labels are cached by content hash + prompt version).
- Catalog trends per category (`core/trends.py`, refreshed to kv each run; `cli.py stats` renders them).
- **Journal replay** (`python cli.py replay [--weights-a JSON] [--weights-b JSON]`): re-scores every logged feedback event against its same-day eligible pool under current vs. candidate weights; reports attended vs. dismissed mean percentiles and the A/B delta. Served-events-only bias — a weight regression check, not an unbiased policy estimate.

## 7. Evaluated and rejected libraries (verified Oct 2026 — do not re-litigate)

RecBole (stale, torch+ray, academic-only restriction), Cornac / DaisyRec / implicit / scikit-surprise (collaborative filtering is vacuous with one household), Vowpal Wabbit (million-example machinery), MABWiser (stale; drags pandas/sklearn/seaborn), pybandits (jax), contextualbandits (cython build friction), FAISS/hnswlib (linear scan at ≤2k rows is microseconds), bayesian_changepoint_detection + statsforecast (prior-dominated at weekly signal sparsity — the momentum halflife already is the forecaster), rank_llm / llm-rankers (IR-benchmark infra / abandoned), Open Bandit Pipeline (dormant; OPE at n≈dozens has wider-than-effect confidence intervals), MAPIE/conformal (needs exchangeable calibration volumes we don't have), SHAP (an additive scorer is already its own exact attribution), xQuAD (MMR is the end of the line at slate size 10), TIGER/semantic IDs (research-only, needs GPU training). The only healthy candidate — `river` (BSD-3, online LinUCB + ADWIN drift) — stays out: the 60-day halflife is more responsive than ADWIN at a few signals per week; revisit if signal volume grows.
