"""Step 3: turn daily mandi prices into a weekly seasonality curve, a peak
selling window, and a planting recommendation, per (crop, state).

Pipeline, per PRD.md section 5:

  1. Daily modal price -> ISO week (1-52; ISO week 53 folded into 52).
  2. Normalise each day's price to its own year's mean (kills rupee inflation
     and one-off shock years from dominating the curve -- see PRD 5.1.2).
  3. Median across years, per week -> the raw weekly curve (PRD 5.1.1: median
     beats mean because one shock year can't drag it).
  4. 3-week rolling median, circular (week 52 wraps to week 1) -> smoothed curve.
  5. STL decomposition on the same year-normalised daily series, resampled
     weekly, as an independent cross-check. If its seasonal peak lands far
     from the manual method's peak, the data is too thin to trust -- flag it,
     don't silently average the disagreement away.
  6. Reliability: of the years with data, what fraction had this week above
     that year's mean.
  7. Stability: compare 2005-2014 vs 2015-2024 peak week; recent years win
     when they disagree (PRD 5.3).
  8. Flatness: if the top-N weeks are all within a few % of each other, this
     crop doesn't have one sharp peak -- report a "good months" band instead
     of pretending precision the data doesn't support (HANDOFF's Barley/
     Cotton/Urad finding, generalised rather than special-cased by name).
  9. Planting week = peak week - crops.lead_weeks(crop), NOT duration_weeks.
     Filtered against crops.plantable() -- an unreachable peak falls back to
     the best reachable one.
 10. Base year: step 2 divided the rupees out, so the curve is unitless.
     year_level() estimates what one year's rupee level actually was, and
     callers multiply the curve by BASE_YEAR's level to put real rupees back
     on the y axis. Every year is thereby indexed to the same 2025 anchor.

Suppresses a recommendation entirely below a minimum data bar (PRD 5, "don't
emit confident answers from three data points") rather than emitting one.
"""

import json
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from statsmodels.tsa.seasonal import STL

import crops

PROJECT_DIR = Path(__file__).parent
DATA_DIR = PROJECT_DIR / "data"
VOLUME_DIR = PROJECT_DIR / "volume"

MIN_YEARS = 3  # fewer years than this and a "peak week" is noise, not signal
MIN_WEEKS_COVERED = 30  # of 52; below this the curve has too many holes to trust
FLAT_TOP_N = 5
FLAT_SPREAD_PCT = 3.0  # top-N weeks within this % of each other -> no sharp peak
STL_DISAGREEMENT_WEEKS = 6  # peak weeks further apart than this -> flag thin data

BASE_YEAR = 2025  # every curve is priced in this year's rupees
MIN_WEEKS_FOR_LEVEL = 20  # weeks a year needs before its price level is trusted


def safe_name(s):
    return s.replace(" ", "_").replace("/", "-")


def load_daily_series(crop, state):
    """-> [(date, price)] sorted, or [] if no file."""
    path = DATA_DIR / f"{safe_name(crop)}__{safe_name(state)}.json"
    if not path.exists():
        return []
    doc = json.loads(path.read_text())
    out = []
    for r in doc["records"]:
        try:
            d = date.fromisoformat(r["date"][:10])
        except ValueError:
            continue  # rare corrupted source row, e.g. a truncated year
        p = r.get("modal_price")
        if p is None or p <= 0:
            continue
        out.append((d, float(p)))
    out.sort(key=lambda t: t[0])
    return out


def load_daily_volume_series(crop, state):
    """-> [(date, quantity_tonnes)] sorted, or [] if no file. Mirrors
    load_daily_series but reads volume/*.json's `quantity` field (daily mandi
    arrivals) instead of data/*.json's `modal_price`."""
    path = VOLUME_DIR / f"{safe_name(crop)}__{safe_name(state)}.json"
    if not path.exists():
        return []
    doc = json.loads(path.read_text())
    out = []
    for r in doc["records"]:
        try:
            d = date.fromisoformat(r["date"][:10])
        except ValueError:
            continue
        q = r.get("quantity")
        if q is None or q <= 0:
            continue
        out.append((d, float(q)))
    out.sort(key=lambda t: t[0])
    return out


def iso_week(d):
    """1-52. ISO week 53 (leap weeks) folds into 52 -- a once-in-~6-years
    53rd week would otherwise create a near-empty bucket the curve can't use."""
    w = d.isocalendar()[1]
    return 52 if w == 53 else w


def normalise_by_year(series):
    """[(date, price)] -> [(date, price / that_year's_mean_price)].

    Converts absolute rupees into "how far above/below normal was this day",
    so inflation and one bad/good year don't dominate the multi-year average
    (PRD 5.1.2). A year needs at least 20 data points to get a stable mean --
    below that, its "normalised" values would swing on noise.
    """
    by_year = defaultdict(list)
    for d, p in series:
        by_year[d.year].append(p)
    year_mean = {y: statistics.mean(ps) for y, ps in by_year.items() if len(ps) >= 20}
    return [(d, p / year_mean[d.year]) for d, p in series if d.year in year_mean]


def weekly_curve(normalised):
    """-> ({week: median_normalised_price}, {week: n_years_with_data})

    Each year contributes at most one value per week (that year's median for
    days falling in that week) before the cross-year median, so a week with
    lots of reports in one single year can't outvote every other year.
    """
    by_week_year = defaultdict(list)
    for d, p in normalised:
        by_week_year[(iso_week(d), d.year)].append(p)

    per_week_year_median = defaultdict(dict)
    for (w, y), ps in by_week_year.items():
        per_week_year_median[w][y] = statistics.median(ps)

    curve, n_years = {}, {}
    for w in range(1, 53):
        yearly = per_week_year_median.get(w, {})
        if not yearly:
            continue
        curve[w] = statistics.median(yearly.values())
        n_years[w] = len(yearly)
    return curve, n_years


def rolling_median_circular(curve, window=3):
    """3-week centred rolling median, wrapping week 52 to week 1 -- there's no
    real seam between December and January, so a hard edge would be a bug."""
    half = window // 2
    smoothed = {}
    for w in range(1, 53):
        neighbours = [
            curve.get(((w - 1 + off) % 52) + 1) for off in range(-half, half + 1)
        ]
        vals = [v for v in neighbours if v is not None]
        if vals:
            smoothed[w] = statistics.median(vals)
    return smoothed


def year_level(series, year, curve):
    """Estimate that year's average price level in rupees, using the seasonal
    curve to correct for which weeks the year actually covers.

    A partial year (2025 stops at October) would otherwise average only the
    months it has -- for a crop that peaks in November that reads far too low.
    curve[w] is the typical price in week w as a multiple of the annual
    average, so price / curve[w] estimates the annual level from any single
    day, whatever week it falls in.
    """
    seen = {}
    for d, p in series:
        if d.year != year:
            continue
        c = curve.get(iso_week(d))
        if not c or c <= 0:
            continue
        seen.setdefault(iso_week(d), []).append(p / c)
    if len(seen) < MIN_WEEKS_FOR_LEVEL:
        return None
    # one value per week first, so a heavily-reported week can't dominate
    return statistics.median([statistics.median(v) for v in seen.values()])


def stl_peak_week(normalised):
    """Independent cross-check: resample the year-normalised daily series to
    weekly means (continuous calendar, not ISO-week-of-year) and run STL.
    Returns the ISO week-of-year the seasonal component peaks in, or None if
    there isn't enough continuous history for STL to fit (needs >= 2 full
    periods of 52 weeks).
    """
    if not normalised:
        return None
    by_period = defaultdict(list)
    start = min(d for d, _ in normalised)
    for d, p in normalised:
        by_period[(d - start).days // 7].append(p)
    n_periods = max(by_period) + 1
    if n_periods < 104:  # need 2+ full 52-week cycles for STL's period=52
        return None

    series = np.array(
        [
            statistics.mean(by_period[i]) if i in by_period else np.nan
            for i in range(n_periods)
        ]
    )
    # STL can't handle NaN gaps -- linearly interpolate thin weeks rather than
    # dropping them, which would desync the period=52 seasonal alignment.
    nans = np.isnan(series)
    if nans.all() or nans.sum() > len(series) * 0.4:
        return None
    idx = np.arange(len(series))
    series[nans] = np.interp(idx[nans], idx[~nans], series[~nans])

    try:
        result = STL(series, period=52, robust=True).fit()
    except ValueError:
        return None
    seasonal = result.seasonal
    # average each ISO-week-of-year's seasonal contribution across all cycles
    week_of_year = [
        iso_week(start + timedelta(days=7 * i + 3)) for i in range(len(series))
    ]
    by_woy = defaultdict(list)
    for w, s in zip(week_of_year, seasonal):
        by_woy[w].append(s)
    if not by_woy:
        return None
    return max(by_woy, key=lambda w: statistics.mean(by_woy[w]))


def circular_dist(a, b):
    d = abs(a - b)
    return min(d, 52 - d)


def top_n_weeks(curve, n):
    return sorted(curve, key=lambda w: -curve[w])[:n]


def is_flat_top(curve, n=FLAT_TOP_N, spread_pct=FLAT_SPREAD_PCT):
    """True if the crop has no single sharp peak: the top-N weeks are all
    within spread_pct of each other. Generic threshold, not a per-crop list --
    HANDOFF found Barley/Cotton/Urad this way and asked for it to generalise.
    """
    top = top_n_weeks(curve, n)
    if len(top) < n:
        return False
    vals = [curve[w] for w in top]
    return (max(vals) - min(vals)) / max(vals) * 100 <= spread_pct


def peak_window(curve, width=4):
    """Best single week, widened to a `width`-week window centred on it
    (PRD 5.5: never recommend a single week). Wraps across the year end."""
    if not curve:
        return None
    best = max(curve, key=lambda w: curve[w])
    half_lo = (width - 1) // 2
    lo = ((best - 1 - half_lo) % 52) + 1
    return [((lo - 1 + i) % 52) + 1 for i in range(width)]


def reliability(by_week_year_median, week):
    """Fraction of years where this week's price sat above that year's mean
    (i.e. normalised value > 1.0). 17/20 = real pattern, 11/20 = coin flip."""
    vals = by_week_year_median.get(week)
    if not vals:
        return None
    above = sum(1 for v in vals.values() if v > 1.0)
    return above / len(vals)


def stability_check(normalised):
    """Compare peak week 2005-2014 vs 2015-2024. Returns (agree: bool|None,
    old_peak, new_peak, new_smoothed_curve). new_smoothed_curve is the
    2015-2024-only smoothed curve -- returned (not just its peak) so that
    when the halves disagree, everything downstream (flatness, sell window,
    reliability) can be recomputed on the same recent-years-only data the
    peak itself came from, instead of mixing a recent-only peak with a
    full-history curve. All-None if either half lacks enough years.

    A flat-topped half (see is_flat_top) has no real single peak -- its
    "best week" is noise, so comparing that noise-picked week against the
    other half would call a real shift where there isn't one. Either half
    being flat short-circuits straight to agree=True (keep the full-history
    curve; a flat half has nothing more specific to say).
    """
    old = [(d, p) for d, p in normalised if d.year <= 2014]
    new = [(d, p) for d, p in normalised if d.year >= 2015]
    old_curve, _ = weekly_curve(old)
    new_curve, _ = weekly_curve(new)
    old_years = len({d.year for d, _ in old})
    new_years = len({d.year for d, _ in new})
    if old_years < MIN_YEARS or new_years < MIN_YEARS or not old_curve or not new_curve:
        return None, None, None, None
    old_smoothed = rolling_median_circular(old_curve)
    new_smoothed = rolling_median_circular(new_curve)
    old_peak = max(old_smoothed, key=lambda w: old_smoothed[w])
    new_peak = max(new_smoothed, key=lambda w: new_smoothed[w])
    if is_flat_top(old_smoothed) or is_flat_top(new_smoothed):
        return True, old_peak, new_peak, new_smoothed
    agree = circular_dist(old_peak, new_peak) <= STL_DISAGREEMENT_WEEKS
    return agree, old_peak, new_peak, new_smoothed


def week_to_month(week):
    """Approximate ISO week-of-year -> calendar month (1-12), via the
    Thursday (day 4, the ISO week's "anchor" day) of a non-leap year."""
    d = date.fromisocalendar(2023, week, 4)
    return d.month


def feasible_planting_week(crop, state, target_peak_week, district=None):
    """peak_week - lead_weeks(crop), then snapped into a feasible planting
    month for this crop/zone. If the naive week's month isn't plantable, walk
    outward (both directions, closest first) to the nearest reachable peak
    week whose implied planting month is allowed -- PRD 5.4: don't recommend
    sowing wheat into the monsoon just because the math says so.

    Returns (planting_week, actual_peak_week_used, peak_is_naive) or
    (None, None, None) if this crop has no planting advice (tree crops) or
    isn't grown in this zone at all.
    """
    lead = crops.lead_weeks(crop)
    if lead is None:
        return None, None, None
    plantable_months = crops.plantable(crop, state, district)
    if not plantable_months:
        return None, None, None

    for offset in range(0, 27):  # search both directions, up to half a year out
        candidates = (
            {target_peak_week}
            if offset == 0
            else {target_peak_week + offset, target_peak_week - offset}
        )
        for peak in candidates:
            peak = ((peak - 1) % 52) + 1
            plant_week = ((peak - lead - 1) % 52) + 1
            if week_to_month(plant_week) in plantable_months:
                return plant_week, peak, offset == 0
    return None, None, None


def analyse(crop, state, district=None):
    """Full pipeline for one (crop, state). Returns a result dict; sets
    insufficient_data=True (with `reason`) below the minimum data bar rather
    than emitting a confident-looking recommendation from thin data."""
    return _analyse_series(crop, state, load_daily_series(crop, state), district)


def _analyse_series(crop, state, series, district=None):
    """The pipeline itself, given an already-loaded [(date, price)] series.
    Split out so the Step 4 all-India panel can pool every state's daily
    series and run the identical analysis for a national fallback."""
    n_years = len({d.year for d, _ in series})

    if n_years < MIN_YEARS:
        return {
            "crop": crop,
            "state": state,
            "insufficient_data": True,
            "reason": f"only {n_years} year(s) of data",
        }

    normalised = normalise_by_year(series)
    curve, _ = weekly_curve(normalised)

    if len(curve) < MIN_WEEKS_COVERED:
        return {
            "crop": crop,
            "state": state,
            "insufficient_data": True,
            "reason": f"only {len(curve)}/52 weeks have data",
        }

    smoothed = rolling_median_circular(curve)
    manual_peak = max(smoothed, key=lambda w: smoothed[w])

    stl_peak = stl_peak_week(normalised)
    stl_disagrees = (
        stl_peak is not None
        and circular_dist(manual_peak, stl_peak) > STL_DISAGREEMENT_WEEKS
    )

    stable, old_peak, new_peak, new_smoothed = stability_check(normalised)
    # PRD 5.3: weight recent years more heavily regardless -- if the pattern
    # shifted, everything downstream (flatness, sell window, reliability)
    # switches to the 2015-2024-only curve, not just the peak week alone --
    # mixing a recent-only peak with a full-history sell window would let the
    # two disagree (e.g. "best week" outside the "best window").
    if stable is False and new_smoothed is not None:
        effective_curve, effective_peak = new_smoothed, new_peak
    else:
        effective_curve, effective_peak = smoothed, manual_peak

    by_week_year_median = defaultdict(dict)
    for d, p in normalised:
        if stable is False and new_smoothed is not None and d.year < 2015:
            continue  # match effective_curve's scope: recent-years-only
        by_week_year_median[iso_week(d)].setdefault(d.year, []).append(p)
    by_week_year_median = {
        w: {y: statistics.median(ps) for y, ps in yy.items()}
        for w, yy in by_week_year_median.items()
    }
    rel = reliability(by_week_year_median, effective_peak)

    flat = is_flat_top(effective_curve)
    sell_window = (
        sorted(top_n_weeks(effective_curve, FLAT_TOP_N))
        if flat
        else peak_window(effective_curve)
    )

    plant_week, peak_used, peak_is_naive = feasible_planting_week(
        crop, state, effective_peak, district
    )
    plant_window = None
    if plant_week is not None:
        plant_window = [((plant_week - 2 + i - 1) % 52) + 1 for i in range(4)]

    return {
        "crop": crop,
        "state": state,
        "insufficient_data": False,
        "years_of_data": n_years,
        "weeks_covered": len(curve),
        "curve": effective_curve,
        "raw_curve": curve,
        "peak_week": effective_peak,
        "sell_window": sell_window,
        "is_flat_top": flat,
        "reliability": rel,
        "stl_peak_week": stl_peak,
        "stl_disagrees": stl_disagrees,
        "stability_agree": stable,
        "stability_old_peak": old_peak,
        "stability_new_peak": new_peak,
        "plant_week": plant_week,
        "plant_window": plant_window,
        "peak_used_for_planting": peak_used,
        "peak_is_naive": peak_is_naive,
        "lead_weeks": crops.lead_weeks(crop),
    }


def volume_curve(crop, state):
    """Weekly arrivals seasonality for one (crop, state), file-backed.
    See _volume_curve_series for the pipeline itself."""
    return _volume_curve_series(load_daily_volume_series(crop, state))


def _volume_curve_series(series):
    """Weekly arrivals curve for the report: each ISO week's arrivals in
    tonnes, taken as that week's median WITHIN each year first, then the
    median ACROSS years (one heavy-reporting year can't outvote the rest --
    same discipline as weekly_curve), then the same 3-week circular smoothing
    the price curve gets (rolling_median_circular).

    Unlike the price curve this is NOT year-normalised: the chart plots
    actual tonnes on the y axis, so the number a hover shows is a real
    figure, not a ratio. Split from volume_curve() so build_html.py's
    all-India panel can pool every state's daily series and run this
    identically, the way analyse()/_analyse_series() split for price (Step 4).

    Volume history only goes back to 2021 (data/ goes back to 2005), so the
    same MIN_YEARS / MIN_WEEKS_COVERED bars as the price pipeline apply here
    too, just against a shorter run.

    Same-date rows are SUMMED before the weekly step, never medianed: a day's
    arrivals are its rows added up. volume/ currently holds exactly one row per
    date per state, so this is a no-op today -- it's here so a future per-mandi
    scrape can't silently turn "the state's day" into "the middle mandi's day".

    All-India is NOT built by pooling every state through here: mandis report on
    different days, so a pooled day set can median out below one of its own
    states. See build_html.attach_national_volume, which adds the state curves.

    Returns {week: tonnes} (smoothed) or None if under the data bar.
    """
    n_years = len({d.year for d, _ in series})
    if n_years < MIN_YEARS:
        return None

    daily = defaultdict(float)
    for d, q in series:
        daily[d] += q

    by_week_year = defaultdict(list)
    for d, q in daily.items():
        by_week_year[(iso_week(d), d.year)].append(q)
    per_week = defaultdict(list)
    for (w, _y), qs in by_week_year.items():
        per_week[w].append(statistics.median(qs))
    weekly = {w: statistics.median(v) for w, v in per_week.items()}

    if len(weekly) < MIN_WEEKS_COVERED:
        return None
    return rolling_median_circular(weekly)


def demo():
    # --- synthetic data only: exercises the math without touching real files ---
    import shutil
    import tempfile

    global DATA_DIR, VOLUME_DIR
    real_data_dir, real_volume_dir = DATA_DIR, VOLUME_DIR
    tmp_dir = Path(tempfile.mkdtemp())
    DATA_DIR = tmp_dir / "data"
    VOLUME_DIR = tmp_dir / "volume"
    DATA_DIR.mkdir()
    VOLUME_DIR.mkdir()
    try:
        # A crop that peaks hard around week 45 every year (a 3-week bulge,
        # 44-46, not a single isolated week -- a lone-week spike is exactly
        # what a 3-week median smoother is supposed to erase as noise, so
        # a real fixture needs to look like a real peak). Flat/low elsewhere,
        # across 6 years with an inflation trend baked into the price level.
        # Named "Onion"/"Maharashtra" (a real crops.py entry, not a made-up
        # name) because analyse() calls crops.lead_weeks() on whatever crop
        # name it's given.
        records = []
        for year in range(2015, 2021):
            base = 1000 * (1 + 0.05 * (year - 2015))  # 5%/yr inflation
            for week in range(1, 53):
                price = base * (2.0 if 44 <= week <= 46 else 1.0)
                for wd in range(1, 4):  # a few reports per week
                    d = date.fromisocalendar(year, week, wd)
                    records.append(
                        {"date": f"{d.isoformat()}T00:00:00.000Z", "modal_price": price}
                    )
        (DATA_DIR / "Onion__Maharashtra.json").write_text(
            json.dumps({"crop": "Onion", "state": "Maharashtra", "records": records})
        )

        series = load_daily_series("Onion", "Maharashtra")
        assert len(series) > 0
        # 6 target years, but ISO week 1 sometimes falls in late December of
        # the prior Gregorian year (e.g. 2015's week 1 starts 2014-12-29), so
        # one extra calendar year (2014) legitimately shows up too.
        n_years = len({d.year for d, _ in series})
        assert n_years == 7, n_years

        normalised = normalise_by_year(series)
        # normalisation should erase the inflation: week-45 ratio same each year
        by_year = defaultdict(dict)
        for d, p in normalised:
            by_year[d.year][iso_week(d)] = p
        ratios = [by_year[y][45] for y in by_year]
        assert max(ratios) - min(ratios) < 0.01, ratios

        curve, _ = weekly_curve(normalised)
        assert len(curve) == 52
        smoothed = rolling_median_circular(curve)
        peak = max(smoothed, key=lambda w: smoothed[w])
        assert peak in (44, 45, 46), peak  # three-way tie inside the bulge is fine

        # circular wrap: week 52's neighbours must include week 1, not fall off the edge
        assert rolling_median_circular({52: 1.0, 1: 1.0, 2: 1.0})[52] == 1.0

        # base-year level: 2020's fixture is 1250 for 49 weeks and 2500 for 3
        expected_2020 = 1250 * (49 + 6) / 52
        lvl = year_level(series, 2020, smoothed)
        assert abs(lvl - expected_2020) / expected_2020 < 0.05, (lvl, expected_2020)

        # the partial-year case this exists for: a year cut off before its peak
        # (weeks 1-40, missing the 44-46 bulge) must still recover the FULL-year
        # level, not the low-season average it actually saw.
        partial = [(d, p) for d, p in series if d.year == 2020 and iso_week(d) <= 40]
        lvl_partial = year_level(partial, 2020, smoothed)
        assert abs(lvl_partial - expected_2020) / expected_2020 < 0.05, (
            lvl_partial,
            expected_2020,
        )
        # a naive mean of that same partial year lands low -- that's the bias
        naive_level = statistics.mean([p for _, p in partial])
        assert naive_level < expected_2020 * 0.98, (naive_level, expected_2020)
        # too few weeks -> no answer rather than a wrong one
        assert year_level(partial[:5], 2020, smoothed) is None

        # flat-top detector: near-identical top weeks -> flat; one sharp spike -> not flat
        flat_curve = {w: 1.0 for w in range(1, 53)}
        flat_curve[45] = 1.01
        assert is_flat_top(flat_curve) is True
        sharp_curve = {w: 1.0 for w in range(1, 53)}
        sharp_curve[45] = 5.0
        assert is_flat_top(sharp_curve) is False

        # planting feasibility: forcing wheat's target peak to week 25 (a June
        # sell, which back-calculates to a monsoon sowing) must snap to a real
        # plantable month (Punjab/NW wheat is Nov-Dec per crops.py), never
        # "sow into the monsoon" advice
        plant_week, peak_used, naive = feasible_planting_week(
            "Wheat", "Punjab", target_peak_week=25
        )
        assert plant_week is not None
        assert week_to_month(plant_week) in (11, 12), week_to_month(plant_week)
        assert (
            naive is False
        )  # week 25 itself was not reachable, so this must be non-naive

        # tree crop: no planting advice at all
        assert feasible_planting_week("Mango", "Maharashtra", target_peak_week=20) == (
            None,
            None,
            None,
        )

        result = analyse("Onion", "Maharashtra")
        assert result["insufficient_data"] is False
        assert result["peak_week"] in (44, 45, 46), result["peak_week"]
        assert (
            result["reliability"] == 1.0
        )  # every year had the peak week above its own mean
        assert len(result["sell_window"]) == 4
        assert set(result["sell_window"]) & {44, 45, 46}

        # insufficient-data guard: two data points must not produce a confident answer
        (DATA_DIR / "Thin__Nowhere.json").write_text(
            json.dumps(
                {
                    "crop": "Thin",
                    "state": "Nowhere",
                    "records": [
                        {"date": "2020-01-05T00:00:00.000Z", "modal_price": 100},
                        {"date": "2020-01-12T00:00:00.000Z", "modal_price": 100},
                    ],
                }
            )
        )
        thin = analyse("Thin", "Nowhere")
        assert thin["insufficient_data"] is True

        # national path: _analyse_series on a hand-pooled series (Step 4) must
        # run the same pipeline and land the same peak as the file-backed call
        pooled = load_daily_series("Onion", "Maharashtra")
        nat = _analyse_series("Onion", "All India", pooled)
        assert nat["insufficient_data"] is False
        assert nat["peak_week"] in (44, 45, 46), nat["peak_week"]

        # volume_curve: same shape of fixture (a bulge at weeks 44-46), across
        # 4 years (>= MIN_YEARS, unlike the price fixture's 6 -- volume history
        # is shorter in real life too), arrivals in tonnes.
        vol_records = []
        for year in range(2021, 2025):
            base = 500 * (1 + 0.1 * (year - 2021))
            for week in range(1, 53):
                qty = base * (3.0 if 44 <= week <= 46 else 1.0)
                for wd in range(1, 4):
                    d = date.fromisocalendar(year, week, wd)
                    vol_records.append(
                        {"date": f"{d.isoformat()}T00:00:00.000Z", "quantity": qty}
                    )
        (VOLUME_DIR / "Onion__Maharashtra.json").write_text(
            json.dumps(
                {"crop": "Onion", "state": "Maharashtra", "records": vol_records}
            )
        )

        vol = volume_curve("Onion", "Maharashtra")
        assert vol is not None
        vol_peak = max(vol, key=lambda w: vol[w])
        assert vol_peak in (44, 45, 46), vol_peak
        # the curve is actual tonnes, NOT a normalised ratio: the week-45 bulge
        # (base * 3, ~1725 t median across the 4 years) must sit well above a
        # non-bulge week (~575 t) and nowhere near 1.0.
        assert vol[45] > vol[1] * 2, (vol[45], vol[1])
        assert vol[45] > 100, vol[45]

        # two rows on the same date ADD, they don't median: a day's arrivals are
        # its rows summed. Feeding the series back doubled-up at 4x must come out
        # 5x, not somewhere between 1x and 4x.
        one = load_daily_volume_series("Onion", "Maharashtra")
        doubled = sorted(one + [(d, q * 4) for d, q in one], key=lambda t: t[0])
        summed = _volume_curve_series(doubled)
        assert summed is not None
        for w in (1, 45):
            assert abs(summed[w] - vol[w] * 5) < 1e-6, (w, summed[w], vol[w])

        # under the data bar: only 1 year of volume -> None, not a noisy answer
        (VOLUME_DIR / "Thin__Nowhere.json").write_text(
            json.dumps(
                {
                    "crop": "Thin",
                    "state": "Nowhere",
                    "records": [
                        {"date": "2024-01-05T00:00:00.000Z", "quantity": 100},
                        {"date": "2024-01-12T00:00:00.000Z", "quantity": 100},
                    ],
                }
            )
        )
        assert volume_curve("Thin", "Nowhere") is None

        print("ok - seasonality pipeline self-check passed")
    finally:
        DATA_DIR, VOLUME_DIR = real_data_dir, real_volume_dir
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    demo()
