# PRD — Crop Price Seasonality & Planting Calendar (India)

**Status:** Not started — spec agreed, no code written
**Last updated:** 2026-09-03

---

## 1. Problem

An Indian farmer deciding what to plant, and when, has no easy way to see when
a crop historically sells high. Mandi price data exists (Agmarknet, 2005→present)
but it is daily, per-market, and unaggregated — useless without processing.

## 2. What we're building

For each crop, in each state, answer two questions:

- **When does this crop sell highest?** (a week-of-year range)
- **When should it be planted to harvest into that peak?** (peak week minus growing duration)

Worked example the user gave: tomato sells best in November; tomato takes ~3 months
to grow; therefore plant in August.

Delivered as **a single self-contained HTML file** — data baked in, opens in any
browser, no install, no server.

## 3. Scope

### In scope
- ~34 crops (list in §6)
- State-wise, with an all-India panel and all-India fallback where state data is thin
- Weekly price curve, week 1–52, averaged across available years
- Two recommendations per crop-state: best selling window, best planting window
- A reliability indicator per recommendation
- A lookup section: pick state → pick crop → get both answers

### Out of scope (deliberately)
- **Price forecasting** (ARIMA/ML predicting 2027 prices). Seasonal *pattern* is
  stable and measurable; price *level* depends on monsoon, export policy and
  others' sowing decisions — unpredictable a year ahead. Revisit only after the
  seasonal baseline exists and can be used as a comparison benchmark.
- Input costs, MSP comparison, profit margin
- District-level granularity
- Live/auto-updating data

## 4. Users and the key assumption

Target user: any Indian farmer, including one with **no storage capacity**.

**Assumption: sell-at-harvest.** We do not assume the farmer can store the crop.
Decided explicitly by the user: a farmer starting with zero onions needs to know
when to *plant* so harvest lands on a good price. Advice built on "harvest anytime,
store, sell later" is useless to someone with no godown. Sell-at-harvest advice
still works for a farmer who does have storage; the reverse is not true.

This applies to onion, potato and garlic too — they stay in scope with the same
sell-at-harvest logic.

## 5. Method

### 5.1 Weekly curve
Daily modal prices → ISO week 1–52 → aggregate per crop-state-week.

Three corrections over a naive mean, each fixing a specific failure:

1. **Median, not mean** — one shock year (2020 onion +300%) drags a mean and
   creates a phantom peak. Median is unmoved.
2. **Normalise each year to its own mean before combining** — otherwise rupee
   inflation makes recent years dominate and fakes an upward trend. Converts raw
   price into "how far above normal was this week", which is the actual signal.
3. **3-week rolling median smoothing** — without it, week 34 beats week 33 for
   reasons that are pure noise from thin reporting.

Primary method: **STL decomposition** (`statsmodels`), which separates trend /
seasonal / residual in one step. Manual normalise-and-average retained as a
cross-check. **Where the two disagree materially, the crop's data is too thin to
trust** — that disagreement is itself a useful signal and should be surfaced.

### 5.2 Reliability
For each week, count how many years it sat above that year's average.
17/20 years high = real seasonal pattern. 11/20 = coin flip.
Report this number next to every recommendation.

### 5.3 Pattern stability check
Seasonality is *not* perfectly stable — cold storage capacity has grown since 2005
and export policy has changed repeatedly. Compare seasonality computed on
2005–2014 against 2015–2024:
- Patterns agree → 20-year average is trustworthy
- Patterns diverged → use recent years only, and flag it
Weight recent years more heavily regardless.

### 5.4 Planting recommendation
`plant_week = peak_sell_week - growing_duration_weeks`

Then **filter against agronomic feasibility**. Naive back-calculation produces
impossible advice — e.g. "sow wheat in June" because prices peak in October, when
wheat cannot survive the monsoon. If the global peak is unreachable, recommend the
best *reachable* peak and say so explicitly.

### 5.5 Output as ranges
Recommend a 3–4 week window, not a single week. No farmer plants on exactly week 33,
and single-week precision overstates what the data supports.

## 6. Crop list (34)

Wheat, Paddy, Maize, Bajra, Jowar, Barley, Gram, Tur/Arhar, Moong, Urad, Masoor,
Soybean, Groundnut, Mustard, Sunflower, Sesamum, Castor, Cotton, Sugarcane, Potato,
Onion, Tomato, Brinjal, Cauliflower, Cabbage, Okra, Green Chilli, Garlic, Ginger,
Turmeric, Coriander, Banana, Apple, Mango.

Starting above 30 deliberately — thin-coverage crops will drop out.

Sugarcane, banana, mango and apple are retained but flagged: planting advice is
weak for perennials and tree crops (a mango tree fruits for 30 years), so they are
primarily price-curve entries.

## 7. Data sources

**Primary: Agmarknet (agmarknet.gov.in)** — daily min/max/modal prices per
commodity per market, back to ~2005. No official API; requires form-POST scraping.

**Not data.gov.in.** The Agmarknet resource there
(`9ef84268-d588-465a-a308-a864a43d0070`) is **current-day only** and cannot be
backfilled. Usable only for going-forward daily collection.

Alternatives considered: CEDA Ashoka (pre-cleaned Agmarknet CSVs — a fallback if
scraping proves painful), NHRDF (onion/garlic/potato only), FAOSTAT/World Bank
(annual only — useless for weekly seasonality).

**Growing duration and plantable months have no API.** Hardcoded from ICAR / state
agriculture department norms. Plantable months are keyed by agro-climatic zone
rather than one row per state — 850 hand-filled cells would be mostly guesswork.

## 8. Known limitations — state these in the output, do not hide them

1. **The advice self-defeats at scale.** If every farmer plants onion in August,
   they all harvest together, supply floods, and the November peak collapses.
   Valid for individuals, breaks if universally followed.
2. **2005–2008 Agmarknet coverage is patchy.** Few mandis reported; some
   commodities barely appear before ~2010. Record per-crop-year reporting counts
   so thin years are visible rather than silently averaged in.
3. **Most crop-state pairs will be unusable.** ~850 possible combinations,
   realistically 200–400 with enough data. Nobody grows apples in Kerala. Suppress
   recommendations below a data threshold rather than emitting confident garbage
   from three data points.
4. **Plantable-month tables are approximate**, especially at state level. Mark
   which entries are well-sourced and which are zone-inferred.
5. **State averages still hide intra-state variation** (Nashik ≠ rest of Maharashtra).

## 9. Acceptance criteria

- [ ] 34-crop table with growing duration + plantable months, sources noted
- [ ] Agmarknet scraper: 2005→present, state-wise, resumable, one CSV per crop-state-year
- [ ] Per-crop-state-year reporting counts recorded and surfaced
- [ ] Weekly aggregation with median + year-normalisation + smoothing
- [ ] STL as primary, manual method as cross-check, disagreement flagged
- [ ] Reliability score per recommendation
- [ ] 2005–2014 vs 2015–2024 stability check per crop-state
- [ ] Planting recommendation filtered for agronomic feasibility
- [ ] Recommendations as 3–4 week windows
- [ ] Single HTML file: country panel, state panels, crop selector, 52-week plot,
      both recommendations, lookup section
- [ ] National fallback when state data is thin, labelled as such
- [ ] Limitations from §8 visible in the output
