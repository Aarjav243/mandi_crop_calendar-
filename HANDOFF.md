# HANDOFF — Crop Price Seasonality & Planting Calendar (India)

**Last updated:** 2026-09-10
**State:** Steps 1–4 DONE. `data/` has **1,144** price files, `volume/` has
**1,120** arrivals files. `seasonality.py` and `build_html.py` both built,
self-tested, and run clean over the full dataset. **`crop_calendar.html` is
generated and current** (~920 KB, `build_cache.json` alongside it).

**Latest work (2026-09-10): base-year 2025 indexing — DONE.** The
instructor's instruction "Set 2025 as base year, so each year's data is
indexed to 2025" is implemented. The HTML chart's y-axis now shows **real
₹/quintal in 2025 prices** instead of unitless ratios, and each week's point
is hoverable for the exact figure (same trick the volume chart already used).
See the "Base-year 2025 indexing" section below.

**Step 2b (volume/arrivals) — scrape appears complete** (1,120 files = the
full grid). Confirm the Kaggle 2021–2023 backfill was re-run after the CEDA
scrape finished (see Step 2b below); if unsure, re-run
`python build_volume_from_kaggle.py` — it is idempotent.

Read `PRD.md` for the full spec and `PROGRESS.md` for decision history.
This file is the short version — and the one to trust over both of those
for the CEDA/Kaggle data-source story, since that pivoted hard after they
were written.

---

## Goal

For each of 36 crops, in each Indian state, answer two questions from ~20 years of
mandi price history:

- **When does it sell highest?** (week-of-year range)
- **When should it be planted** so harvest lands on that peak?

No storage assumed (sell-at-harvest, even for onion/potato/garlic). No
forecasting — seasonality only.

Output: **one self-contained HTML file** — country panel, per-state panels, crop
selector, 52-week price plot, both recommendations, plus a lookup section
(pick state → pick crop → get answers).

---

## Step 1 decisions — user-approved 2026-09-03

Implemented in `crops.py`, 36 crops, covered by its `demo()` self-check.
Do not re-litigate:

1. **East UP split from UP** (`DISTRICT_ZONE` override) — different crop mix,
   prices track Bihar not Punjab. Use `zone_for(state, district)`.
2. **Multi-pick crops target heavy volume, not first picking**
   (`peak_pick_offset_weeks`) — tomato, brinjal, okra, green chilli, cotton,
   castor, coriander leaf.
3. **Lead time counts from seed, including nursery** (`nursery_weeks`) —
   onion, tomato, brinjal, green/dry chilli, cauliflower, cabbage. The
   non-negotiable one of the six.
4. **Coriander split**: `Coriander Seed` vs `Coriander Leaf` (16wk vs 7wk,
   storable vs perishable).
5. **Chilli split**: `Green Chilli` vs `Dry Chilli` (dry trades on Guntur
   cold-store calendar, ~7wk longer).
6. **Mango and apple are price-curve-only** — trees, 4–8yr to bearing,
   `lead_weeks()` returns `None`.

**Consequence for Step 3:** subtract `lead_weeks(crop)` from the peak
selling week, NOT `duration_weeks`. Tomato is 25 weeks, not 13; brinjal 31,
not 15.

Crop count: **36**.

---

## Step 2 — data pull: what actually happened (this is the important part)

The original plan (CEDA Ashoka API only, ~14hr estimate) did not survive
contact with reality. Two pivots happened, in order:

### Pivot 1: Agmarknet's live site → CEDA Ashoka
Agmarknet rebuilt its site — only 2021+ data survives there, and the one
endpoint with real daily data is captcha-gated. **CEDA Ashoka** (a
university-hosted archive of the same government data, back to 2005) was
used instead. Free API, needs a one-time signup (email + 6-digit code) —
already done; key lives at `~/.config/wellabs/ceda_api_key.txt`, **not** in
this repo. `ceda_map.py` maps our 36 crop names / 32 states to CEDA's ids.
`scrape_ceda.py` is the puller (`POST /agmarknet/prices`, one call per
crop-state pair).

### Pivot 2: CEDA alone was too slow → added Kaggle as primary source
CEDA turned out to be rate-limited to **40 requests/hour** — the "~44 sec
per pair" estimate in the original plan didn't account for this; a full
36×32 pull would have taken many days, not 14 hours. Only **36 CEDA files**
were ever pulled this way (Onion×2 states, Tomato×2 states, Turmeric×32
states) — kept as a validated ground-truth baseline, not thrown away.

Everything else now comes from a Kaggle dataset:
**`vandeetshah/india-commodity-wise-mandi-dataset`** — one zip, 325 CSVs
(one per commodity), Jan 2000–Feb 2024, market-level rows. Pulled via HTTP
Range requests against individual zip members (no need to download the
full 423MB archive) using a pre-fetched Zip64 central directory. Puller is
**`scrape_kaggle.py`** — resumable, skips any (crop, state) pair that
already has a file from either source.

**Cross-validated against CEDA** (2 of 36 crops directly checked — Tomato
and Onion): median day-to-day difference 0–0.9%. Good enough to trust the
same aggregation method (mean of min/max/modal price across markets
reporting that day — NOT arrival-weighted) for the rest.

**Current data coverage: 912 files in `data/`** — 36 CEDA-sourced (no
`source` key in the JSON) + 876 Kaggle-sourced (`"source": "kaggle"` key
present). All 36 target crops have at least some state coverage. Turmeric
has zero Kaggle rows (not in that dataset) but is 100% CEDA-covered anyway,
so it's fine as-is.

### Three real bugs found and fixed while building the Kaggle pipeline
The 325 CSVs in the Kaggle zip are **not uniformly formatted** — these
cost real debugging time and are worth knowing about if the pipeline needs
touching again:

1. **Column headers differ.** Most files: `"Min Price (Rs./Quintal)"` etc.
   Some (Apple, Banana, Cabbage, Mustard, Paddy): bare `"Min Price"`.
   Fixed in `aggregate_rows()` by resolving the real header name from
   `reader.fieldnames` instead of hardcoding one variant.
2. **Date formats differ.** Most files: `"09 Feb 2006"`. Some (Apple,
   Bajra, Cabbage, Tur/Arhar): ISO `"2005-08-24"`. Not correlated with the
   header bug — a file can have either combination. Fixed in `parse_date()`
   by branching on whether `"-"` is in the string.
3. **State name mismatch.** Kaggle uses `"Pondicherry"`, our state list
   uses `"Puducherry"` — rows were silently dropped. Fixed by adding to
   `STATE_NAME_FIX`.

Before trusting the pipeline, all 35 Kaggle-sourced crop files were swept
for both inconsistencies in one pass — no further undiscovered variants.

---

## Step 2b — volume (arrivals) data pull `[IN PROGRESS]`

**New scope, added by the user after Step 3 finished.** Not in PRD.md. The
ask: "we need the volume data for the last 5 years" — daily mandi
*arrivals* (how much physically showed up at market), 2021–2025, same
36-crop × 32-state grid as the price data.

**This is a separate dataset from Step 2's prices.** Prices live in
`data/`; arrivals live in `volume/`. Don't mix them up — same filename
convention (`<Crop>__<State>.json`), different directory, different schema.

`volume/` file schema (synthetic example):
```json
{"crop": "Wheat", "state": "Punjab", "commodity_id": 1, "state_id": 3,
 "from_date": "2024-01-01", "to_date": "2025-12-31", "record_count": 2,
 "records": [{"date": "2024-01-01T00:00:00.000Z", "commodity_id": 1,
              "census_state_id": 3, "quantity": 218755.6}]}
```
`quantity` is **tonnes** — this was previously unconfirmed and is now
verified (see cross-check below). Files backfilled from Kaggle also carry a
`"kaggle_backfill"` key naming the dataset and date range.

### The two-source split (user's explicit decision)
CEDA's `/agmarknet/quantities` endpoint serves this data, but at 40
requests/hour a full 5-year × 1120-pair pull would take days. Alternatives
were investigated and a Kaggle mirror was found that covers most of the
window, so the user was given the choice and **chose the hybrid** over
letting CEDA grind through all five years:

- **2021–2023 → Kaggle** (`vandeetshah/india-commodity-wise-mandi-dataset`,
  the same dataset Step 2 used for prices — its `Arrivals` column).
  Free, instant, no rate limit.
- **2024–2025 → CEDA only** (`scrape_ceda_qty.py`). The Kaggle dataset
  effectively **stops around Feb 2024** (Onion: 167k rows in 2021, 200k in
  2022, 196k in 2023, then only 17k in 2024 and nothing after), so these
  two years have no substitute.

**Accuracy cross-check before committing to this**: summed the Kaggle
Wheat/Punjab daily arrivals for April 2022 against a CEDA API pull for the
identical pair — matched to the tonne (e.g. 218,755.6 on 2022-04-11 from
both). Same underlying Agmarknet "Arrivals" column, served two ways. This
also confirmed the units.

### Files
- **`scrape_ceda_qty.py`** — the CEDA arrivals puller. Mirror of
  `scrape_ceda.py` but hits `/agmarknet/quantities`. Same API key, same
  resumability (skips any pair that already has a file). Date range is
  **per-crop**: `FROM_DATE_DEFAULT = "2024-01-01"` for everything, with
  `FROM_DATE_OVERRIDE = {"Turmeric": "2021-01-01"}`.
- **`build_volume_from_kaggle.py`** — the 2021–2023 backfill. Downloads one
  Kaggle CSV per crop into `kaggle_raw/`, sums daily arrivals per state,
  merges into `volume/*.json`.
- **`test_build_volume_from_kaggle.py`** — 7 assert-based checks, no
  framework. `python test_build_volume_from_kaggle.py`. Covers both CSV
  format variants, the merge/overlap rule, and all three kaggle-CLI
  download quirks below.

### The merge is deliberately race-safe — do not "fix" this
`build_volume_from_kaggle.py` **only ever merges into a `volume/` file that
already exists. It never creates one.** This is load-bearing, not an
oversight: `scrape_ceda_qty.py`'s resume check is "does this file already
exist?", so if the backfill created files for pairs CEDA hadn't reached
yet, the live scraper would treat them as done and **permanently skip their
2024–2025 data**. Pairs with no CEDA file yet are counted as "pending" and
picked up by re-running the backfill later.

On overlapping dates, **CEDA's value wins** — the backfill skips any date
already present in the file.

### Status right now
- CEDA scraper: **170 / 1120 pairs**, running detached as PID 53884, logs
  to `volume_scrape.out` / `.err`.
- Kaggle backfill: **one full pass done** — 43 pairs backfilled with
  2021–2023, **554 pairs still pending** because CEDA hasn't created their
  files yet. **It must be re-run after the CEDA scrape finishes** to catch
  all of those. This is the single most forgettable step here.

### Known coverage gaps
- **Turmeric** — not in the Kaggle dataset at all (verified against the
  full 326-file catalogue). It alone still pulls 2021–2025 from CEDA, hence
  the `FROM_DATE_OVERRIDE`. Slower, but no alternative.
- **Coriander Seed** — no CEDA commodity mapping, so no arrivals data from
  either source. Pre-existing gap, same as in Step 2.

### THE SPEEDUP THAT HASN'T BEEN APPLIED YET — read this first
The scrape is slower than it needs to be, and the reason was initially
misdiagnosed (as "CEDA is just slow per request" — **that was wrong**).

Measured from the logs: the scraper completes **exactly 40 pairs, then gets
rate limited. Every time, twice so far.** That is a hard 40-requests/hour
quota — the same one already documented for the price scraper — not
per-request latency.

The waste is in the recovery, not the requests. On a 429 the code sleeps
through an escalating `RETRY_WAITS_429 = [60, 120, 300, 600, 1200, 1800]`,
i.e. 1+2+5+10+20 = **38 minutes of sleeping** before it gets back in, when
the quota clears sooner. Net effect: **22.4 pairs/hour actual vs a 40/hour
ceiling** — running at ~56% of what's allowed.

**The fix**: stop bursting. Pace deliberately at ~95 seconds between
requests (3600/40 = 90s, plus margin so a rolling window never trips) and
the escalating backoff never fires at all. Change `RATE_LIMIT_SLEEP = 1.5`
to `95` in `scrape_ceda_qty.py`.

Expected impact on the ~950 remaining pairs: **~42 hours → ~25 hours,
saving roughly 18 hours.** Not yet applied — the user was told the current
status and this was found immediately after, while writing this handoff.
Applying it means killing PID 53884 and relaunching (safe — the scraper is
fully resumable, it'll skip everything already in `volume/`).

### Relaunching the scraper (it must survive this session)
Use PowerShell `Start-Process`, fully detached — **not** the agent
harness's background-task mechanism, which caps out around 10–25 minutes
and will kill a multi-hour job:
```powershell
Start-Process python -ArgumentList "-u","scrape_ceda_qty.py" `
  -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput volume_scrape.out `
  -RedirectStandardError volume_scrape.err
```

---

## Data-quality audit — user challenged the averaging methodology, here's what came of it

The user asked a sharp, correct question: each crop's rows carry a
`"Variety"` field (quality grade — e.g. "Other" vs "Dara" barley, "Delicious"
vs "Other" apple). The pipeline averages all grades together into one price
per state/day. **Is that safe, or could a shifting mix of grades fake a
price cycle that isn't real?**

**The real test built to answer this** (`vcheck.py`, currently only in the
scratchpad, not the repo — see Next Steps): for each crop, build two
52-week price curves —
- **Blended**: mean of whatever grades show up that week (what the
  pipeline actually does).
- **Frozen-mix**: per-grade weekly means, recombined using each grade's
  *fixed overall share* — mathematically incapable of reflecting a mix
  shift, since the mix never moves in this version.

If both curves pick the same peak week, blending is proven safe for that
crop. This is a materially stronger test than an earlier weak first-pass
check (which only tracked the single most-expensive grade's month-to-month
share swing) — that earlier check was explicitly wrong to rely on and was
replaced, not patched.

**Result across all 35 Kaggle-sourced crops: 34 fine, 1 broken.**

- **Barley was broken and has been fixed.** Blended peak was week 5
  (fake), frozen-mix peak was week 48 (real) — a 9-week divergence.
  Root cause: cheap "Other"-grade barley (59% of the market) floods in
  right after spring harvest; pricier "Dara" (32%) share rises toward
  winter. Blending the two makes the harvest glut look like the price
  peak. **Fix applied**: `scrape_kaggle.py` now has a
  `DOMINANT_GRADE_ONLY = {"Barley"}` set — for crops in that set,
  `aggregate_rows()` filters down to only the single most-reported grade
  before averaging, instead of blending all grades. Barley's `data/`
  files were deleted and regenerated with this fix. Verified: national
  peak is now a flat, believable Nov–Feb winter plateau, matching the
  frozen-mix ground truth — no more fake February spike.
- **Cotton and Urad flagged, then cleared — not a bug.** Their single
  peak week was stable between the two methods, but the top-5-week
  overlap was only 1/5 and 2/5, which looked suspicious. Checked the
  actual weekly prices directly: the top 8-10 weeks for both crops are
  within 1-2% of each other — a genuine flat plateau, not a sharp peak.
  Which exact week wins "#1" wobbles due to that flatness, not because
  the data is wrong. **No data fix needed** — but Step 3/4 should present
  these (and now Barley too, same shape) as a "good months" range rather
  than pretending there's one sharp best week.
- **The other 33 Kaggle-sourced crops are individually unverified against
  an independent source** (only Tomato and Onion have direct CEDA
  cross-checks) but all passed the blend-safety test, so the averaging
  methodology itself is trustworthy even where a second source doesn't
  exist to compare against.

---

## Step 3 — analysis pipeline: what actually happened

`seasonality.py` is built, self-tested (`python seasonality.py` runs a
`demo()` self-check against synthetic data only — never touches real
`data/`), and validated against all 912 real crop-state files.

**Pipeline** (per PRD.md §5): load daily prices → fold into ISO weeks 1–52
→ normalise each year to its own mean (needs ≥20 points/year) → median
per (week, year), then cross-year median per week → 3-week circular
rolling-median smoothing → STL decomposition (`statsmodels`) as an
independent cross-check, flagged when it disagrees by more than 6 weeks →
reliability score (fraction of years that week beat its year's average) →
stability check (2005–2014 vs 2015–2024) → generic flat-top detection
(top-5 weeks within 3% of each other → "good months" band instead of one
peak week) → back-calculate planting week via `crops.lead_weeks(crop)`
(never `duration_weeks`) → snap to the nearest agronomically feasible week
via `crops.plantable()` if the naive week isn't plantable → suppress the
whole recommendation if a crop-state pair has fewer than 3 years of data
or covers fewer than 30 of 52 weeks.

**Full-dataset smoke test result: 721 of 912 files produced a
recommendation, 190 were correctly and intentionally suppressed for
insufficient data, 0 unhandled errors.**

**Two real pipeline bugs found and fixed while validating against real
data** (not just the synthetic self-check — these only showed up once
real, messier data was run through):

1. **Peak week and sell window came from mismatched data.** When the
   2005–2014 vs 2015–2024 halves disagreed, the code picked the peak week
   from the recent-years-only curve but built the sell window from the
   full-history curve — so the reported peak week sometimes wasn't even
   inside its own sell window (seen on Barley/UP and Cotton/Gujarat).
   **Fix**: pick one "effective" curve up front (recent-half if unstable,
   else full history) and derive the peak, sell window, and reliability
   score all from that same curve.
2. **A flat, noisy half could fake a "real" instability signal.** Barley's
   recent-years half only had 5 actual years of data (gaps in 2021/2024),
   and its top-10 weeks were within ~6% of each other — noise, not a
   pattern. Comparing that noise-picked "best week" against the older
   half's peak wrongly flagged a seasonal shift, contradicting the
   Step 2 audit's already-confirmed Barley winter plateau. **Fix**: if
   either half is itself flat-topped, treat the two halves as agreeing —
   a flat half has no specific peak worth comparing. Re-verified: Barley
   now correctly returns a winter plateau (weeks 47–52) matching the
   Step 2 ground truth; Cotton likewise shows a believable flat plateau.

**One real data bug found and fixed**: one corrupted date string
(`"201-02-19"` — truncated year) in `data/Coriander_Leaf__Jharkhand.json`
crashed that file's processing. **Fix**: `load_daily_series()` now skips
any single row with an unparseable date instead of failing the whole
file, consistent with the existing bad-row tolerance in `scrape_kaggle.py`.

**Spot-checked against known-good expectations** (Step 2/PRD findings):
Onion peaks week 44 (Nov, matches HANDOFF); Mango peaks weeks 23–26
(Apr–Jun, matches PRD's harvest window) and correctly has no planting
recommendation (`lead_weeks` is `None` for tree crops); Barley and Cotton
both now show flat "good months" plateaus instead of a fabricated sharp
peak, matching the Step 2 flat-top findings; Wheat/Punjab plantings snap
to Nov–Dec, matching `crops.py`'s NW-zone `plant_months`.

---

## Step 4 — HTML build ✅ DONE

`build_html.py` → `crop_calendar.html`. Runs `seasonality._analyse_series()`
over every `data/*.json` plus one pooled all-India series per crop, slims
each result to what the page draws, and bakes it as a JSON blob inside the
HTML. The browser does zero analysis — it only renders the baked numbers.

- **Run:** `python build_html.py` (re-renders from `build_cache.json`,
  instant) or `python build_html.py --rebuild` (recompute everything, ~10
  min — STL over ~760 pairs + 36 pooled national series). Delete
  `build_cache.json` to force a rebuild.
- **Latest run:** 760 state recommendations, 384 national fallbacks, 0 fully
  suppressed. Output ~920 KB.
- Country panel, per-state panels, crop selector, 52-week price plot (SVG,
  hand-rolled, no chart library), volume/arrivals plot below it when data
  exists, both recommendations, flags, limitations from PRD §8, and a lookup
  section (pick state → pick crop). Flat-top crops (Barley, Cotton, Urad, …)
  render as a "good months" band, not a fake single week.
- `fallback_entry()` builds a thin pair's entry from the national curve but
  snaps its planting window to the state's own plantable months.

## Base-year 2025 indexing ✅ DONE (2026-09-10)

**Instructor's instruction, verbatim:** *"Set 2025 as base year. So each
year's data will be indexed to 2025."* Goal the user restated repeatedly:
**actual rupee numbers on the chart's y-axis**, not the unitless seasonal
ratios it showed before.

### Why this was safe to do as a presentation-only change

`seasonality.py` step 2 divides every year by its own mean, so all the
analysis (peak week, sell window, planting advice, flat-top detection,
reliability, STL cross-check) runs in ratio space and **stays there**. The
only thing that changed is the curve that gets *shipped* to the HTML: it is
multiplied by one scalar per pair — that pair's 2025 rupee level. Multiplying
a curve by a positive scalar cannot move its peak, its window, or any rank —
`median(a·k) = k·median(a)`. Verified on Onion: peak week 45 before and after.

### `seasonality.py` — new `year_level(series, year, curve)`

Estimates a year's average ₹/quintal level, curve-corrected for which weeks
the year actually covers. `curve[w]` is the typical price in week w as a
multiple of the annual mean, so `price / curve[w]` estimates the annual level
from any single day, whatever week it falls in. This is what makes 2025
usable even though the data **stops at 2025-10-30** — a naive Jan–Oct mean
reads low for a crop that peaks in Nov/Dec; the curve correction fixes that.
Needs ≥ `MIN_WEEKS_FOR_LEVEL` (20) weeks in the year or returns `None`. New
constants: `BASE_YEAR = 2025`, `MIN_WEEKS_FOR_LEVEL = 20`. `demo()` has four
new asserts including the partial-year case (year truncated at week 40 must
still recover the full-year level). `python seasonality.py` passes.

### `build_html.py` — new `state_base()`, three tiers

Picks each pair's 2025 anchor, best first, and **labels which tier was used**
so the page can't pass off a national number as a state one:

1. **`own`** — the pair reported ≥20 weeks of 2025 itself. (494 state pairs)
2. **`bridged`** — it didn't, so take its last good year and carry that level
   forward by `national_2025 / national_that_year`, using the crop's own
   all-India series as the inflator (tracks the specific crop better than a
   general CPI/WPI, and needs no external table). Flagged on the page with
   the year used. (250 state pairs)
3. **`national`** — no usable year at all; fall back to the all-India 2025
   level, flagged. Shape is still the state's own. (387 state pairs)

13 pairs have a curve but no year with ≥20 weeks anywhere — they keep the old
ratio and the axis labels itself as such. `slim()` now takes
`(result, base, src, from_year)`; with a base it emits whole-rupee curve
values plus `unit:"inr"`, `base_level`, `base_src`, `base_year_used`, else
`unit:"ratio"`. `build_data()` loads each state's series once into
`series_by_state` (was double-loading), builds a memoised `nat_level(y)`
closure, and calls `state_base()` per pair.

### HTML/JS (`plot()` in the template)

- `plot()` signature gained `unit, baseLevel`. In `inr` mode: `padL` widened
  to 62, 4 y-axis tick labels in `₹` with en-IN digit grouping + gridlines,
  the dashed reference line moved from `ys(1)` to `ys(baseLevel)` and
  labelled `2025 average ₹X,XXX`, an axis caption `₹ per quintal (100 kg),
  in 2025 prices`. `refV` is folded into the y-range so the reference line
  can't draw off-chart.
- **Per-week hover dots** — one small `<circle>` per week carrying an SVG
  `<title>` (`Week 45 (Nov): ₹2,322/quintal`), exactly how `plotVolume()`
  already did it. No tooltip library.
- `flags()` gained bridged / national-base flag text. Footer + a new
  limitations bullet updated: prices are 2025 rupees per quintal, and
  Nov/Dec levels are what the seasonal shape implies (2025 data ends October),
  not observed prices.

Only the template changed for the hover dots, so it was re-rendered from
cache (no `--rebuild`).

## Freshness backfill: Oct 2025 -> Jun 2026 ✅ DONE (2026-09-11)

Manager asked for the tool to "become more real time". The analysis was never
the problem — the data simply stopped on **2025-10-30**, ~10 months stale.

**Why the old sources couldn't help.** CEDA's own archive also stops at
2025-10-30 (`scrape_ceda.py` asked for data through 2025-12-31 and got nothing
past October), and the project's CEDA key now returns 401 anyway. data.gov.in
is registered and the key is saved, but every endpoint times out — the platform
is down, not us. Agmarknet 2.0 has prices *and* arrivals but is behind an
official login and encrypts its request bodies; solving its captcha does not
help. Left alone deliberately.

**What worked.** Kaggle dataset `sagar2522/indian-local-market-crop-price` —
same Agmarknet vocabulary as the archive `scrape_kaggle.py` already uses, but
Title-Cased with different spacing. Covers 2025-01-01 -> 2026-06-10 at full
density.

`backfill_2026.py` merges it into `data/*.json`:

- Crop names resolved by **normalising** `KAGGLE_NAME` (lowercase, strip
  non-alphanumerics) instead of maintaining a second 36-entry map. 35 of 36
  match that way; only `Paddy` needed an override (`Paddy(Common)` — this
  source splits Paddy by variety). Bonus: **Turmeric now has a Kaggle source**,
  which the archive lacked.
- States reuse `STATE_NAME_FIX` plus one new entry (`Keralam` -> `Kerala`).
- Aggregation matches `aggregate_rows()` exactly: mean of min/max/modal across
  every market reporting that (state, date); non-positive prices dropped as
  missing-data placeholders.
- Appends only dates strictly after each file's **last actual record**, so it
  is idempotent and never rewrites CEDA history.

**Cross-validated before trusting it.** Compared the new source against
existing `data/` over the 2025-01 -> 2025-10 overlap: 113,421 matched records,
**median modal-price difference 0.0%**, p90 2.6%, 94% within 5%. It is the same
underlying Agmarknet data.

**Result:** 605 files extended, ~65.6k records appended. 501 files now run to
2026-06, and `build_html.py --rebuild` produces the identical coverage as
before (760 state recommendations, 384 national fallbacks, 0 suppressed) —
expected, since 8 extra months barely move a 20-year curve. Backup of the
pre-backfill `data/` is at `../Well Labs 10 data-backup-20260911`.

### Traps found here (worth remembering)

- **`to_date` in the CEDA files is the *requested* end date (2025-12-31), not
  the last date actually returned.** Filtering "already have it" on that field
  silently skipped Nov–Dec 2025 for 355 files on the first run. Use
  `records[-1]["date"]`. The stale `to_date` field is still wrong in ~340 files
  whose series genuinely died years ago (Apple in Andhra Pradesh last reported
  2014-05-30) — cosmetic, pre-existing, not fixed here.
- **`DataFrame.itertuples()` renames any column starting with `_`**, so the
  working columns are `xcrop`/`xstate`, not `_crop`/`_state`.
- **Barley's `DOMINANT_GRADE_ONLY` grade is recomputed over the backfill window
  only** (the original run never recorded its choice). It picks `'Other'`
  (8,812/23,125 rows). Flagged with a `ponytail:` comment — hardcode the grade
  if a future run disagrees and seams the series.

### Still open

- **`BASE_YEAR` stays 2025.** Roll it once a *full* year is in, or the y-axis
  keeps saying "2025 prices". 2026 has a three-month hole (see "Forward-only"),
  so the earliest roll is January 2028, using 2027.
- **Arrivals/volume still has no post-Oct-2025 source.** The live feed carries
  prices only — no arrivals column. Only Agmarknet 2.0 has it.

## Live daily updates ✅ DONE (2026-09-11)

`daily_update.py`, run by Windows Task Scheduler at 07:00 daily. It works out
which days are missing, pulls them from data.gov.in, appends to `data/`, and
re-runs `build_html.py --rebuild`. No manual step.

### "data.gov.in is down" was WRONG — it was a User-Agent block

Two sessions were spent reporting the API as dead (60s stalls, then 502s) and
hunting replacement sources on that basis. It was never down. Its gateway
**502s on the default `Python-urllib/x` User-Agent** and answers in 0.3s with
any ordinary browser or curl UA. Reproducible, alternating, every time.
`UA` in `daily_update.py` exists for this — **do not remove it.**

The cost of that mistake: a pointless crawl through Kaggle re-uploads, e-NAM's
endpoints, and CEDA's public site, plus a near-miss on emailing helpdesks for
access we already had. Verify the transport before declaring a source dead.

### The two resources — pick the right one

| | Rows | Holds |
|---|---|---|
| `9ef84268-d588-465a-a308-a864a43d0070` "Current Daily Price … (Mandi)" | ~18k | **only today** |
| `35985678-0d79-46b4-9ed6-6f13308a1d24` "Variety-wise Daily Market Prices" | **81.7M** | full archive, updated daily |

The first one is the obvious-looking one and it is the wrong one: a missed run
loses that day permanently. We use the second, so a missed run is simply caught
up by the next one.

### API traps, all of them verified the hard way

- **`filters[field]` is a fuzzy token match, not equality.** Asking for
  `arrival_date=01/08/2026` also returns 08/01/2026 (5.2M rows instead of
  19k); `commodity=Dry Chillies` also returns `Ginger(Dry)` and `Dry Grapes`.
  **Always use `filters[field.keyword]`.**
- **The `.keyword` filter name is lower-case even though the returned fields
  are capitalised.** `filters[arrival_date.keyword]` works;
  `filters[Arrival_Date.keyword]` returns 0 rows silently. The resource's
  `field` metadata and its index names disagree with each other.
- **This resource capitalises row keys** (`Modal_Price`, `Arrival_Date`) where
  the daily one lower-cases them. `fetch_crop_day()` folds keys to lower case
  so nothing downstream has to care.
- **`offset + limit > 10000` is refused**, and a busy day is 23k rows — a whole
  day cannot be paged in one query. We fetch **one crop at a time**: the
  biggest slice is ~1,200 rows, and it skips the ~60% of rows that are
  commodities we don't track.
- **It rate-limits (429).** `_get()` retries with backoff; `THROTTLE` spaces
  requests. One day costs ~36 requests and ~30 seconds.
- **Nine crop names differ again from the Kaggle spellings** — Kaggle replaced
  `/` with `-` and added spaces before brackets, so the Kaggle name returns
  zero rows with no error. `AGMARK_NAME` in `backfill_2026.py` holds the nine;
  the other 27 match once `norm()` strips punctuation. Two could not be
  norm-matched at all (`Red gram/Arhar/Tur(whole)`,
  `Sunflower/Sunflower Seed`) — word order differs.
- **Mandis report late.** Today has a handful of rows, yesterday is still
  filling, two days back is complete. `LAG_DAYS = 2`. This matters because
  `merge()` writes each date once — pulling a date too early would freeze a
  half-empty day into the history permanently.
- **Castor and Sugarcane legitimately have zero-row days.** Castor is 4,417
  rows in the entire 81.7M archive. Not a bug, don't chase it.

### How the catch-up works

With no `--days`, the script starts at the day after the newest date on disk
and runs to `today - LAG_DAYS`. So: nothing missing → fetches nothing; three
months missing → fetches three months. The same code path did the seed run
and does the nightly one-day run.

**But it was NOT used to fill June→September** — see "Forward-only" below.

Task settings: `StartWhenAvailable` (runs late if the PC was off),
`RestartCount 3` at 20-minute intervals, `IgnoreNew` (no overlapping runs),
2-hour limit. Exit code surfaces as `LastTaskResult`; log is
`daily_update.log`.

**The one thing that stops it:** the PC being off or asleep at 07:00 *and* not
turned on later that day. `StartWhenAvailable` covers the ordinary case — it
runs at the next boot — and the auto-range means even a week of downtime is
caught up in one run.

### Forward-only: the June→September 2026 gap is deliberately empty (user decision)

The historical resource *could* have filled 2026-06-11 → 2026-09-08. A 91-day
crawl was started, got one day in, and was stopped on the user's instruction.
Data now runs to 2025-10-30 (CEDA/Kaggle) → 2026-06-10 (Kaggle CSVs) →
2026-06-11 (the one live day that landed before the crawl was stopped) →
**gap, 2026-06-12 to 2026-09-08** → 2026-09-09 onward (live feed).
Measured on disk: 433 files end at 2026-09-09, 70 stop at 2026-06-11 (those pairs
went quiet and correctly show no live price).

I argued for filling it and my argument was wrong: I said it was the difference
between the page showing "latest: 10 June" and "latest: two days ago." At the
time the page showed no current price at all, so the gap changed nothing a
reader would see. The user caught this. The two real consequences, both accepted:

- **The gap is now permanent.** `merge()` in `backfill_2026.py` only appends
  dates *newer* than each file's last record. Now that files end on 2026-09-09+,
  no rerun can insert June–September. Filling it later means writing a
  different merge that splices into the middle of `records` — possible, not
  free, and nothing currently needs it.
- **`BASE_YEAR` can't roll to 2026.** A year with a three-month hole can't
  anchor rupee levels. The earliest sensible roll is January 2028 (using 2027),
  not January 2027.

Neither hurts the seasonality maths: it folds ~20 years by ISO week and
normalises each year to its own level, so one year missing 13 weeks just
contributes fewer weeks. `year_level()` still clears `MIN_WEEKS_FOR_LEVEL = 20`
for 2026 on 431 of the 433 live pairs — measured, not assumed.

### Where the daily data actually shows up on the page

Two additions to `build_html.py`, both fed by `latest_snapshot()`:

**B — "Latest reported price" on the card.** Above the chart: the price, its
date, and how it compares with the twenty-year normal for that week. Plus a
hollow orange dot on the chart itself at that week, with the dashed vertical
line, so you can see where today sits on the seasonal shape.

**C — "Latest reported prices" section.** A collapsible table of every
still-reporting crop–state pair (~433 rows), sorted by crop then state. Click a
row to jump to that pair's card. Built in JS from `DATA.pairs[].now`, so it
needs nothing extra in the payload.

**The one trap here, and why the code looks the way it does:** the chart is in
BASE_YEAR (2025) rupees; today's price is in today's rupees. Comparing them
directly would report inflation as a seasonal signal — a crop 6% dearer all
year would read "6% above normal" in every week of the year. So
`latest_snapshot()` divides today's price by *its own year's* level first and
compares ratios, and only multiplies back by `base_level` to place the dot.
`build_html.py --demo` asserts exactly this (an all-prices-up-50% year must come
out at 0%). Don't "simplify" it to `price / curve[week]`.

`STALE_DAYS = 10`: a pair silent longer than that gets no `now` at all — a June
price is not "right now". With the gap above, that also means the page correctly
shows nothing live for pairs that stopped reporting in June.

## What worked

- **Pushing back on ambiguity before building.** Early on, "best time to
  buy" turned out to mean "when to plant," not buy or sell.
- **Checking the data before designing on top of it.** The base-year plan
  was first pitched as "just one multiplication"; the user said "think about
  it again" and a data-verification pass overturned the naive version —
  2025 stops in October, so the level estimator had to be curve-corrected,
  and 40% of chartable pairs have no 2025 of their own and need the bridge.
- **Keeping the change presentation-only.** All analysis stays in ratio
  space; only the shipped curve is scaled. Nothing downstream could break
  because nothing downstream was touched.
- **Sequencing the crop table before the scrape.** Growing durations gate
  every planting recommendation; cheap to get right first.
- **Resumable pullers.** Both `scrape_ceda.py` and `scrape_kaggle.py` skip
  any (crop, state) pair that already has a file — crashes, rate limits,
  and an agent stopping early all cost nothing, just rerun.
- **Not defending a weak check when challenged.** The user asked "any
  proof?" for the "33 crops are fine" claim, and the honest answer was
  that the original check was weak. Building the stronger frozen-mix test
  and reporting the corrected result (34 fine, 1 broken, 2 needing a
  presentation change) was the right response — don't rationalize a
  shortcut, replace it.
- **Cross-checking a shortcut source against the slow one before trusting
  it.** Before swapping 2021–2023 arrivals over to Kaggle, one pair
  (Wheat/Punjab, April 2022) was summed from both sources and compared.
  Matching to the tonne is what made the swap safe — and it settled the
  units question for free.
- **Making the backfill refuse to create files.** Merging only into
  existing files is what lets the Kaggle backfill run *while* the CEDA
  scraper is live, with no risk of the two silently eating each other's
  work. Worth the extra "re-run it at the end" step.

## What didn't work — don't repeat these

- ~~**data.gov.in for historic prices** — current-day only, can't be
  backfilled.~~ **WRONG, corrected 2026-09-11.** True of the resource we had
  found (`9ef84268…`); there is a second one (`35985678…`) with 81.7M rows of
  history. See "Live daily updates" above. It is now the live source.
- **agmarknet.gov.in directly** — 2021+ only, daily-detail endpoint is
  captcha-gated.
- **Assuming CEDA alone could finish in reasonable time** — the ~44sec/pair
  estimate in the original plan didn't account for the 40 requests/hour
  rate limit. A full run would have taken days. Kaggle was the actual
  unlock, not a bigger CEDA budget.
- **Hardcoding one CSV header/date format across the Kaggle archive** —
  325 files, at least 2 independent format inconsistencies, not
  correlated with each other. Always resolve headers dynamically and
  branch on date format; don't assume a sample of a few files represents
  the whole archive.
- **Trusting a proxy metric for the variety-blending question** — tracking
  only the top grade's monthly share swing missed that two *other* grades
  could swap share while the top grade looked stable. Test the actual
  thing (peak week under a mix-shift-immune reconstruction), not a stand-in.
- **Naive peak-minus-duration for planting dates** — fixed in Step 3:
  `seasonality.py` subtracts `lead_weeks()`, not `duration_weeks`, and
  filters against `crops.plantable()`.
- **Plain mean across years** — fixed in Step 3: median at every
  aggregation level, each year normalised to its own mean first.
- **Picking a peak week from one data scope and a sell window from
  another** — even after Step 3's core design was right, an early version
  mixed a recent-years-only peak with a full-history sell window when the
  two halves disagreed, producing a peak that wasn't inside its own
  window. Always derive every downstream number (peak, window,
  reliability) from one single chosen curve.
- **Comparing point-peaks between two curve halves without checking
  flatness first** — a flat/noisy half's "best week" is arbitrary, so
  comparing it against the other half's peak can flag a fake instability.
  Check `is_flat_top()` on both halves before trusting a disagreement.

### From Step 2b (volume pull) — all of these cost real time

- **Parallelising the CEDA scraper.** Tried 6 `ThreadPoolExecutor` workers
  to go faster. All 6 tripped 429 simultaneously and each ate its own
  minutes-long backoff — strictly worse than serial, and it poisoned the
  quota for the following hour. **CEDA rate-limits per API key, not per
  connection.** Concurrency cannot help here. Reverted; a `ponytail:`
  comment in `scrape_ceda_qty.py` marks the dead end.
- **Blaming per-request latency for the slowness.** The real cause is the
  40 req/hour quota plus an over-long backoff (see the speedup section
  above). Measure the burst length between 429s before theorising.
- **`cmd &` together with the harness's `run_in_background: true`.** The
  trailing `&` makes the wrapper shell exit instantly, the harness reports
  "completed", and the real child gets orphaned and killed mid-job with no
  error surfaced. Pass the plain foreground command; the harness does the
  backgrounding. For anything over ~25 minutes, don't use the harness at
  all — detach with `Start-Process` (snippet above).
- **Assuming the kaggle CLI writes the filename you asked for.** Three
  separate quirks, each found only by crashing:
  1. It **%20-encodes spaces** (only spaces — parens and hyphens stay
     literal), so `-f "Barley (Jau).csv"` lands as `Barley%20(Jau).csv.zip`.
  2. It **sometimes doesn't zip at all** — smaller files arrive as a plain
     `.csv` with no `.zip` wrapper.
  3. Given any pre-existing local copy it **silently skips the download**
     ("found more recently modified local copy"), so a crash mid-run leaves
     a stale file that makes every later attempt a no-op.
  A before/after directory diff handles (1) and (2) but breaks on (3).
  What actually works: pass `--force`, capture the output, and read the
  real on-disk name out of kaggle's own `Downloading <name> to <path>`
  line. That's what `ensure_downloaded()` does now.
- **Assuming the Kaggle archive's CSVs share one format — again.** Step 2
  hit this for *prices* and it recurred identically for *arrivals*: the
  column is `Arrivals (Tonnes)` in some files and bare `Arrivals` in others
  (Apple, Banana), and dates are `27 Aug 2005` in some, ISO `2005-08-24` in
  others. The bare-`Arrivals` variant crashed loudly; **the ISO-date
  variant failed silently** — every row raised `ValueError`, got swallowed
  by a `continue`, and the whole of Apple.csv contributed zero rows with no
  error printed. Only caught by sampling the file by hand. If a crop merges
  suspiciously few rows, suspect a format variant before suspecting the data.

---

## Next steps

### Step 1 — Crop table ✅ DONE
`crops.py`. `python crops.py` runs the self-check.

### Step 2 — Price data pull ✅ DONE
912 files in `data/`, all 36 crops covered, Barley fixed, Cotton/Urad
understood. (Arrivals/volume data is Step 2b, still running — see below.) Two remaining optional-but-not-blocking gaps if more rigor is
wanted later (not required to start Step 3):
- Only 2 of 36 crops (Tomato, Onion) have a direct CEDA cross-check;
  the rest lean on the blend-safety test plus the shared pipeline.
- The blend-safety test (`vcheck.py`) pools all states nationally per
  crop — a crop could pass nationally while being skewed within one
  specific state. Not yet checked per-state. Low priority unless a
  specific state's numbers look odd later.
- `vcheck.py` currently lives only in the scratchpad
  (`C:\Users\aarja\AppData\Local\Temp\claude\...\scratchpad\vcheck.py`),
  not the repo. Worth copying into the project (e.g. as a `checks/` or
  `tools/` script) if it'll be rerun again — the scratchpad is session-
  specific and may not survive.

### Step 3 — Analysis ✅ DONE
`seasonality.py`. See the "Step 3 — analysis pipeline" section above for
full details. Entry point: `analyse(crop, state, district=None)` — returns
a dict with `peak_week`, `sell_window`, `is_flat_top`, `plant_week`,
`plant_window`, `reliability`, `stability_agree`, `stl_peak_week`,
`stl_disagrees`, `insufficient_data`, and more (see the docstring/return
block in `analyse()` for the full key list). Run `python seasonality.py`
to execute the self-check.

Two things worth knowing before building on top of it:
- Suppressed pairs (`insufficient_data: True`) still return a dict, just
  with the recommendation fields empty/None — Step 4 needs to handle that
  case (e.g. fall back to a national-level number, labelled as such, per
  PRD's fallback requirement) rather than assuming every crop-state pair
  has a usable answer.
- `curve` in the returned dict is the *effective* (possibly recent-years-
  only) smoothed curve used for the recommendation; `raw_curve` is always
  the full-history curve, kept for reference/plotting if Step 4 wants to
  show the whole history rather than just the scope the recommendation
  was drawn from.

### Step 2b — Volume/arrivals pull `[SCRAPE COMPLETE — verify the backfill]`

`volume/` has 1,120 files = the full crop-state grid, so the CEDA scrape
finished. PID 53884 is long gone. Remaining:

1. **Confirm the Kaggle 2021–2023 backfill ran after the CEDA scrape
   finished.** It only merges into files that already exist, so the first
   pass left 554 pairs without 2021–2023 data. Just re-run
   `python build_volume_from_kaggle.py` — it's idempotent (skips dates
   already present, CEDA's value wins on overlap). If it reports 0 pending
   and 0 newly-merged, it was already done.
2. **Report coverage to the user**: pairs with usable arrivals data,
   per-year day counts, units (tonnes), and the two known gaps (Turmeric
   is CEDA-only; Coriander Seed has nothing).

**Open question the user asked and has not been answered yet:** *"what's
the problem with eNAM?"* — eNAM was previously dismissed in one line as
"narrower mandi coverage, needs its own scraper". The user is entitled to
a real answer, and asked again for other sources with the same arrivals
data. That conversation was interrupted by this handoff request and should
be picked back up. Sources already ruled out, with reasons:
data.gov.in (current-day only), NHRDF (onion/potato/garlic only),
FAOSTAT/World Bank (annual granularity).

**Not yet requested — do not start without asking:** integrating arrivals
into the analysis or the HTML output. The user asked for the *data*. What
it's for hasn't been specified.

### Step 4 — HTML build ✅ DONE
`build_html.py` → `crop_calendar.html`. See the "Step 4 — HTML build" and
"Base-year 2025 indexing" sections above for the full picture.

**Not yet done — visual check in a real browser.** Every change was verified
by parsing the generated HTML/JSON, not by looking at the rendered page. Open
`crop_calendar.html`, pick a few crops/states, and confirm: the y-axis shows
`₹` values, the "2025 average" line sits sensibly inside the curve, hovering
a week shows the tooltip, and the bridged/national-base flags read correctly.
The chrome-devtools / reticle MCP servers were down this session so this
couldn't be automated.

**Open items if more rigor is wanted:**
- The 13 ratio-only pairs and Coriander Seed (no 2025 anywhere) — decide
  whether to show them at all or suppress.
- `PROGRESS.md` still says "No code written" — stale, worth a rewrite.

---

## Constraints to preserve

- **No storage assumed.** Sell-at-harvest for every crop including onion,
  potato, garlic.
- **No forecasting.** Seasonality only.
- **Recommendations are ranges** (3–4 weeks), never single weeks — and now
  explicitly wider "good months" bands for flat-top crops (see Step 3).
- **Suppress low-data recommendations.** Don't emit confident answers from
  three data points.
- **Surface the limitations**, especially that the advice self-defeats if
  every farmer follows it — the peak collapses when everyone harvests at
  once.
- **Communication style: plain, simple English, no jargon.** The user has
  asked for this repeatedly and explicitly across sessions. Explain
  findings the way you'd explain them to someone who doesn't work in a
  mandi — short sentences, no trader vocabulary, no unexplained acronyms.

## Credentials — locations, not values

- CEDA API key: `~/.config/wellabs/ceda_api_key.txt`
- Kaggle token: `~/.kaggle/access_token`
- data.gov.in API key: `~/.config/wellabs/datagovin_api_key.txt`

Both outside the project directory, both referenced by path only. Never
move into the repo, hardcode, or print in full.
