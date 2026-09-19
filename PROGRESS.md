# Progress Report

**Project:** Crop Price Seasonality & Planting Calendar (India)
**Date:** 2026-09-03
**Phase:** Specification complete. No code written. No data collected.

---

## Where things stand

Requirements gathering is done. The scope went through several revisions in
conversation and has settled. Nothing has been built yet — the next agent starts
at Step 1 with a clear spec.

## Decisions made (and why)

| # | Decision | Reasoning |
|---|---|---|
| 1 | **Scrape Agmarknet, not data.gov.in** | The data.gov.in Agmarknet resource is current-day only; it cannot be backfilled. Historic data requires scraping agmarknet.gov.in directly. |
| 2 | **State-wise, not just all-India** | Planting months genuinely differ by state — all-India advice is slightly wrong for everyone. All-India retained as a fallback for thin state data. |
| 3 | **Sell-at-harvest; no storage assumed** | User's call, and correct. A farmer with no godown cannot act on "store and sell later" advice. Sell-at-harvest advice still serves a farmer who *does* have storage; the reverse is not true. |
| 4 | **Onion/potato/garlic stay in scope** | Considered dropping them because storage decouples selling from harvest. User rejected: someone starting with zero onions still needs a planting date. Same sell-at-harvest logic applies. |
| 5 | **Seasonality, not forecasting** | Seasonal *pattern* is driven by harvest timing and repeats reliably. Price *level* depends on monsoon, export policy, others' sowing — unpredictable a year ahead. Forecasting is a later addition, and only worth it once there is a baseline to beat. |
| 6 | **Single self-contained HTML file** | Python does all computation up front; HTML only displays results. No install, no server, emailable. |
| 7 | **34 crops, over-provisioned** | Thin-coverage crops will drop out during processing. Starting above 30 is deliberate. |
| 8 | **Perennials retained but flagged** | Sugarcane, banana, mango, apple: planting advice is meaningless for a tree that fruits for 30 years. Kept as price-curve entries. |

## Course corrections during specification

Worth recording so they are not re-litigated:

- **Initial misreading of "best time to buy."** Spent two exchanges on
  buyer-vs-seller framing. The actual question is neither: it is *when to plant so
  that harvest lands on the price peak.* Settled.
- **"For each year" was ambiguous.** User clarified: one answer per crop per state,
  built from all years combined — not a separate answer per year.
- **Overstated seasonal stability.** Claimed seasonal patterns don't change. They
  do, slowly — cold storage capacity has grown since 2005, export policy shifted.
  Corrected by adding the 2005–2014 vs 2015–2024 stability check (PRD §5.3). This
  was a real gap in the original plan.

## Open questions

1. **User's own model ideas.** Asked twice, not yet answered. The user said they
   have their own approach for picking best sell/plant times. Worth hearing before
   finalising §5 — they may know something about the domain that changes the design.
2. **Scrape volume unverified.** ~34 crops × ~25 states × 20 years is the
   theoretical maximum. Actual request count depends on Agmarknet's pagination and
   whether it accepts multi-year date ranges. Estimate of "a few days" is unverified
   and should be tested on one crop-state before committing to a full run.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Agmarknet blocks or rate-limits scraping | High — blocks everything downstream | Resumable scraper, one CSV per crop-state-year, polite delays. Fallback: CEDA Ashoka pre-cleaned CSVs. |
| Thin data in most crop-state pairs | Medium — fewer usable answers | Record reporting counts; suppress recommendations below threshold rather than emitting garbage. Fall back to national. |
| Plantable-month table is guesswork at state level | Medium — wrong planting advice | Key by agro-climatic zone, not per-state. Mark well-sourced vs inferred entries. |
| Growing durations wrong | High — every planting recommendation is wrong | Crop table is Step 1 and gets user review *before* the long scrape. |

## Next action

Build the 34-crop table (duration + plantable months + zone mapping) and put it in
front of the user for review. It is cheap to produce and it gates the correctness
of every planting recommendation — so it must be checked before the multi-day
scrape starts.
