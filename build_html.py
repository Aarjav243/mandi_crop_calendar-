"""Step 4: bake the whole analysis into one self-contained HTML file.

Runs seasonality.analyse() over every (crop, state) file in data/ plus a
pooled all-India analysis per crop, slims each result down to what the page
draws, and writes it as a JSON blob inside crop_calendar.html. The page does
no analysis in the browser -- it only renders these baked numbers.

Curves come out of seasonality.py unitless (each year divided by its own mean).
Here each one is multiplied back by its BASE_YEAR (2025) rupee level, so the
chart's y axis reads in real Rs/quintal with every year indexed to 2025.

Run: python build_html.py  ->  writes crop_calendar.html next to this file.
"""

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import crops
import seasonality
import settledness

PROJECT_DIR = Path(__file__).parent
DATA_DIR = PROJECT_DIR / "data"
OUT = PROJECT_DIR / "crop_calendar.html"

MONTH_NAMES = [
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]

# keys copied straight from a seasonality result dict onto the slim one
_PASS_THROUGH = (
    "crop",
    "state",
    "insufficient_data",
    "reason",
    "years_of_data",
    "weeks_covered",
    "peak_week",
    "sell_window",
    "is_flat_top",
    "reliability",
    "stl_disagrees",
    "stability_agree",
    "stability_old_peak",
    "stability_new_peak",
    "plant_week",
    "plant_window",
    "peak_is_naive",
    "lead_weeks",
)


def curve_to_list(curve, ndigits=3):
    """{week: value} -> [52 items] indexed week-1, rounded. A week with no data
    is None (not 0) so the plot breaks the line across the gap instead of
    diving to the floor -- normalised prices sit near 1.0, never at 0."""
    if not curve:
        return None
    # ndigits=0 -> round() with no digits, i.e. a plain int: whole rupees
    return [
        (round(curve[w], ndigits) if ndigits else round(curve[w]))
        if w in curve
        else None
        for w in range(1, 53)
    ]


def slim(result, base=None, src=None, from_year=None):
    """base is that pair's BASE_YEAR rupee level, or None if we couldn't pin
    one down. With it the curve ships in Rs/quintal; without it, as the raw
    ratio it always was -- labelled either way so the page can't mislabel."""
    out = {k: result[k] for k in _PASS_THROUGH if k in result}
    curve = result.get("curve")
    if base and curve:
        out["curve"] = curve_to_list({w: v * base for w, v in curve.items()}, ndigits=0)
        out["unit"] = "inr"
        out["base_level"] = round(base)
        out["base_src"] = src
        out["base_year_used"] = from_year
    else:
        out["curve"] = curve_to_list(curve)
        out["unit"] = "ratio"
    return out


STALE_DAYS = 10  # a pair silent longer than this has no "right now" worth showing
STALE_WARNING_DAYS = 2  # whole page shows a warning if nothing anywhere is this fresh


def latest_snapshot(series, curve, newest, base_level):
    """The latest reported price and how it sits against the normal for that week.

    The comparison is done in ratio space on purpose. The curve is in BASE_YEAR
    (2025) rupees; today's price is in today's rupees. Comparing those two
    figures directly would report this year's inflation as a seasonal signal --
    a crop 6% dearer across the board would look "6% above normal" every week of
    the year. So today's price is divided by its own year's level first, and
    only scaled back to BASE_YEAR rupees for the dot on the chart.

    Returns None for a pair that has gone quiet: a June price is not "right now".
    """
    if not series:
        return None
    day, price = series[-1]
    if (newest - day).days > STALE_DAYS:
        return None
    week = seasonality.iso_week(day)
    out = {"date": day.isoformat(), "price": round(price), "week": week}
    level = seasonality.year_level(series, day.year, curve) if curve else None
    expected = curve.get(week) if curve else None
    if level and expected:
        ratio = price / level
        out["normal"] = round(expected * level)
        out["pct"] = round((ratio / expected - 1) * 100)
        out["plot"] = round(ratio * base_level) if base_level else round(ratio, 3)
    return out


def state_base(series, curve, nat_level):
    """-> (BASE_YEAR rupee level, how we got it, which year it came from).

    Three tiers, best first:
      "own"      - this pair reported enough of BASE_YEAR itself.
      "bridged"  - it didn't, so take its last good year and carry that level
                   forward by the ratio of the crop's all-India level then to
                   all-India now. The crop's own national series is a better
                   inflator than a general CPI/WPI: it tracks this crop.
      "national" - no usable year at all; fall back to the all-India level and
                   say so, because the shape is still this state's own.
    """
    if not curve:
        return None, None, None
    own = seasonality.year_level(series, seasonality.BASE_YEAR, curve)
    if own is not None:
        return own, "own", seasonality.BASE_YEAR
    nat_now = nat_level(seasonality.BASE_YEAR)
    if not nat_now:
        return None, None, None
    for y in sorted({d.year for d, _ in series}, reverse=True):
        if y >= seasonality.BASE_YEAR:
            continue
        lvl = seasonality.year_level(series, y, curve)
        nat_then = nat_level(y)
        if lvl is not None and nat_then:
            return lvl * nat_now / nat_then, "bridged", y
    return nat_now, "national", seasonality.BASE_YEAR


def attach_volume(entry, crop, state):
    """Adds a `volume` field (52 weekly arrivals figures in tonnes, plotted
    straight on the chart's y axis) to an entry dict, in place, if enough
    arrivals history exists for this pair. Independent of whether the price
    side had enough data -- a fallback/suppressed price pair can still have
    its own real volume chart."""
    vol = seasonality.volume_curve(crop, state)
    if vol is not None:
        entry["volume"] = curve_to_list(vol, ndigits=1)  # 52 weekly tonnes, None gaps


def attach_national_volume(entry, crop, states):
    """All-India arrivals = every state's own weekly curve, ADDED together.

    Not a pooled daily median, which is what the price side does (correctly --
    a unitless ratio shouldn't let one big state outvote the rest). Tonnes are
    different: mandis report on different days, so the pooled series spans a
    different day set than any single state's, and its per-week median can land
    BELOW that state's. Castor came out at 7,530 t all-India against Gujarat's
    8,019 t alone. Adding the per-state curves cannot do that -- the national
    line contains each state line -- and it keeps all-India consistent with the
    state charts one dropdown away.

    Weeks no state covers stay absent, so the plot breaks the line there.
    """
    total = defaultdict(float)
    for st in states:
        curve = seasonality.volume_curve(crop, st)
        if curve:
            for w, tonnes in curve.items():
                total[w] += tonnes
    if len(total) >= seasonality.MIN_WEEKS_COVERED:
        entry["volume"] = curve_to_list(total, ndigits=1)


def plant_window_from_week(plant_week):
    if plant_week is None:
        return None
    return [((plant_week - 2 + i - 1) % 52) + 1 for i in range(4)]


def fallback_entry(crop, state, national_raw):
    """A thin (crop, state) borrows the all-India selling pattern, but its
    planting window is still snapped to THIS state's own plantable months
    (crops.py, never data-dependent) -- so the advice stays region-correct."""
    peak = national_raw["peak_week"]
    pw, _peak_used, naive = seasonality.feasible_planting_week(crop, state, peak)
    return {
        "crop": crop,
        "state": state,
        "insufficient_data": False,
        "fallback": True,
        "reason": national_raw.get("_thin_reason", "not enough state data"),
        "peak_week": peak,
        "sell_window": national_raw["sell_window"],
        "is_flat_top": national_raw["is_flat_top"],
        "reliability": national_raw["reliability"],
        "curve": None,  # page falls back to national[crop].curve for the plot
        "plant_week": pw,
        "plant_window": plant_window_from_week(pw),
        "peak_is_naive": naive,
        "lead_weeks": crops.lead_weeks(crop),
        "stl_disagrees": False,
        "stability_agree": None,
    }


def build_data():
    crop_states = {}
    newest = date.min  # the freshest date anywhere, i.e. what "right now" means
    for f in sorted(DATA_DIR.glob("*.json")):
        doc = json.loads(f.read_text())
        crop_states.setdefault(doc["crop"], set()).add(doc["state"])
        if doc["records"]:
            newest = max(newest, date.fromisoformat(doc["records"][-1]["date"][:10]))

    national, pairs = {}, {}
    all_states = set()
    n_ok = n_fallback = n_suppressed = 0

    for crop, states in sorted(crop_states.items()):
        # loaded once per state and reused: analyse() would re-read every file
        series_by_state = {st: seasonality.load_daily_series(crop, st) for st in states}
        all_states.update(states)
        pooled = sorted(
            (rec for s in series_by_state.values() for rec in s), key=lambda t: t[0]
        )
        nat_raw = seasonality._analyse_series(crop, "All India", pooled)
        nat_curve = nat_raw.get("curve")

        lvl_cache = {}

        def nat_level(y, _cache=lvl_cache, _pooled=pooled, _curve=nat_curve):
            """This crop's all-India rupee level in year y -- the bridge every
            state without its own 2025 leans on, so it's worth memoising."""
            if y not in _cache:
                _cache[y] = (
                    seasonality.year_level(_pooled, y, _curve) if _curve else None
                )
            return _cache[y]

        national[crop] = slim(nat_raw, *state_base(pooled, nat_curve, nat_level))
        attach_national_volume(national[crop], crop, states)
        national[crop]["now"] = latest_snapshot(
            pooled, nat_curve, newest, national[crop].get("base_level")
        )

        nat_usable = not nat_raw.get("insufficient_data")
        for st in sorted(states):
            series = series_by_state[st]
            r = seasonality._analyse_series(crop, st, series)
            key = f"{crop}|{st}"
            if not r.get("insufficient_data"):
                pairs[key] = slim(r, *state_base(series, r.get("curve"), nat_level))
                n_ok += 1
            elif nat_usable and nat_raw.get("peak_week"):
                nat_raw["_thin_reason"] = r.get("reason")
                pairs[key] = fallback_entry(crop, st, nat_raw)
                # it plots the national curve, so it must carry national's units
                pairs[key]["unit"] = national[crop]["unit"]
                pairs[key]["base_level"] = national[crop].get("base_level")
                pairs[key]["base_src"] = "national"
                n_fallback += 1
            else:
                pairs[key] = slim(r)  # no national either -> honest blank
                n_suppressed += 1
            attach_volume(pairs[key], crop, st)
            # a thin pair borrows the national shape for the comparison, the
            # same way it borrows it for the recommendation
            pairs[key]["now"] = latest_snapshot(
                series,
                r.get("curve") or nat_curve,
                newest,
                pairs[key].get("base_level"),
            )

    crop_meta = {}
    for crop in crop_states:
        c = crops.CROPS[crop]
        crop_meta[crop] = {
            "note": c.get("note", ""),
            "season": c.get("season", ""),
            "leadWeeks": crops.lead_weeks(crop),
            "tree": bool(c.get("tree")),
            "perennial": bool(c.get("perennial")),
            "confidence": c.get("confidence", ""),
        }

    print(
        f"{n_ok} state recommendations, {n_fallback} national fallbacks, "
        f"{n_suppressed} fully suppressed (no data anywhere)"
    )

    live = sum(1 for p in pairs.values() if p.get("now"))
    print(f"{live} pairs reported within {STALE_DAYS} days of {newest}")

    return {
        "generated": date.today().isoformat(),
        "newest": newest.isoformat(),
        "staleDays": STALE_DAYS,
        "staleWarningDays": STALE_WARNING_DAYS,
        # {age_in_days: est. % of that date's mandis reported so far}, so the
        # page can say "as of today, about X% of mandis have reported for
        # yesterday" instead of showing a fresh day as if it were final.
        "settleCurve": {str(k): v for k, v in settledness.settle_curve().items()},
        "settleRevisionDays": settledness.REVISE_DAYS,
        "crops": sorted(crop_states),
        "states": sorted(all_states),
        "monthOfWeek": [seasonality.week_to_month(w) for w in range(1, 53)],
        "monthNames": MONTH_NAMES,
        "cropMeta": crop_meta,
        "national": national,
        "pairs": pairs,
    }


def render(data):
    blob = json.dumps(data, separators=(",", ":"))
    return HTML_TEMPLATE.replace("/*DATA*/", blob)


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>India crop price calendar &mdash; when to sell, when to plant</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 16px/1.55 -apple-system, "Segoe UI", Roboto, sans-serif;
         color: #1c2321; background: #f6f5f1; }
  .wrap { max-width: 900px; margin: 0 auto; padding: 24px 18px 80px; }
  h1 { font-size: 1.55rem; margin: 0 0 6px; }
  h2 { font-size: 1.15rem; margin: 28px 0 10px; }
  p.lede { margin: 0 0 20px; color: #48504d; }
  .controls { display: flex; flex-wrap: wrap; gap: 14px; margin: 18px 0 8px;
              padding: 16px; background: #fff; border: 1px solid #e2e0d8;
              border-radius: 10px; }
  .controls label { display: block; font-size: .8rem; font-weight: 600;
                    text-transform: uppercase; letter-spacing: .03em;
                    color: #6b716d; margin-bottom: 4px; }
  select { font: inherit; padding: 8px 10px; border: 1px solid #c7c5bb;
           border-radius: 7px; background: #fff; min-width: 200px; }
  .card { background: #fff; border: 1px solid #e2e0d8; border-radius: 10px;
          padding: 18px 20px; margin: 16px 0; }
  .rec { display: flex; flex-wrap: wrap; gap: 18px; }
  .rec > div { flex: 1 1 260px; }
  .rec .big { font-size: 1.25rem; font-weight: 700; margin: 2px 0 4px; }
  .muted { color: #6b716d; font-size: .92rem; }
  .flag { display: block; margin-top: 10px; padding: 9px 12px; border-radius: 7px;
          font-size: .92rem; background: #fdf3e0; border: 1px solid #f0dcae; }
  .flag.warn { background: #fdeceb; border-color: #f2c9c4; }
  .badge { display: inline-block; font-size: .78rem; font-weight: 600;
           padding: 2px 8px; border-radius: 20px; background: #eef3ee;
           border: 1px solid #cfe0cf; color: #3c6b45; vertical-align: middle; }
  .badge.fallback { background: #eef1f7; border-color: #cdd6ea; color: #3c517e; }
  svg { width: 100%; height: auto; display: block; margin: 6px 0 2px; }
  table { border-collapse: collapse; width: 100%; font-size: .92rem; }
  th, td { text-align: left; padding: 7px 9px; border-bottom: 1px solid #e8e6de; }
  th { font-size: .78rem; text-transform: uppercase; letter-spacing: .03em;
       color: #6b716d; }
  tr.pick { cursor: pointer; }
  tr.pick:hover td { background: #f2f6f2; }
  td.thin { color: #a08a5a; }
  .now { border-left: 3px solid #c2612f; padding: 1px 0 1px 12px; margin: 0 0 14px; }
  .now .big { font-size: 1.25rem; font-weight: 700; margin: 2px 0 4px; }
  .up { color: #b04a22; font-weight: 600; }
  .down { color: #3c6b45; font-weight: 600; }
  details.latest { background: #fff; border: 1px solid #e2e0d8; border-radius: 10px;
                   padding: 12px 16px; margin: 16px 0; }
  details.latest summary { cursor: pointer; font-weight: 600; }
  .scroll { max-height: 460px; overflow-y: auto; }
  ul.limits { padding-left: 20px; }
  ul.limits li { margin: 7px 0; }
  footer { margin-top: 40px; font-size: .85rem; color: #8a8f8b; }
</style>
</head>
<body>
<div class="wrap">
  <h1>India crop price calendar</h1>
  <p class="lede">For each crop and state, this shows the weeks of the year when
  mandi prices have been highest over the last ~20 years, and &mdash; working
  backwards from how long the crop takes to grow &mdash; when to plant so the
  harvest lands on that high. It assumes you sell at harvest (no storage). It is
  a look at past patterns, not a price forecast.</p>

  <div id="freshness"></div>

  <div class="controls">
    <div>
      <label for="crop">Crop</label>
      <select id="crop"></select>
    </div>
    <div>
      <label for="state">State</label>
      <select id="state"><option value="">All India</option></select>
    </div>
  </div>

  <div id="panel"></div>

  <div id="stateTableWrap"></div>

  <h2>Latest reported prices</h2>
  <div id="latestWrap"></div>

  <h2>Things to keep in mind</h2>
  <ul class="limits">
    <li><strong>If everyone follows this, it stops working.</strong> The high
    price exists because few farmers harvest then. If everyone plants for the
    same peak, the harvest floods the market and the peak collapses. Treat this
    as an edge for one farm, not a plan for a district.</li>
    <li><strong>Early years are patchy.</strong> Few mandis reported prices in
    2005&ndash;2008, and some crops barely appear before about 2010. Years shown
    is how many years of usable data went into each answer &mdash; a low number
    means less certainty.</li>
    <li><strong>Most crop&ndash;state pairs don't have enough data.</strong>
    Where a state's own history is too thin, the page falls back to the all-India
    pattern and marks it <span class="badge fallback">all-India</span>. The
    planting window in that case is still fitted to your state's own sowing
    months.</li>
    <li><strong>Prices are 2025 rupees, per quintal (100&nbsp;kg).</strong> Every
    year is indexed to 2025 so old and new years can be compared directly &mdash;
    the shape comes from twenty years of history, the rupee level from 2025.
    2025's own data runs January&ndash;October, so the November and December
    levels are what the seasonal pattern implies, not prices actually observed.</li>
    <li><strong>"Against the usual" is not a good deal or a bad one.</strong> It
    only says how this week's price compares with what this crop normally does in
    this week of the year, after taking out how expensive the whole year has been.
    A price 8% above the usual could still be below last month's. It is one day's
    average across the state's mandis, and a thin trading day can move it a lot.</li>
    <li><strong>Planting months are approximate.</strong> They come from ICAR and
    state-department norms, grouped into six broad agro-climatic zones, not
    tuned per district.</li>
    <li><strong>A state average hides local differences.</strong> Nashik is not
    the rest of Maharashtra. Prices near you may peak a week or two off.</li>
    <li><strong>All-India prices pool every reporting state equally</strong> and
    are not weighted by how much each market actually sold.</li>
  </ul>

  <footer>Built <span id="gen"></span> from CEDA Ashoka and Agmarknet mandi price
  history via a fixed pipeline (median across years, each year normalised to its
  own average, 3-week smoothing, with an STL cross-check), then priced back into
  2025 rupees per quintal. No data is fetched when this page opens.</footer>
</div>

<script id="blob" type="application/json">/*DATA*/</script>
<script>
const DATA = JSON.parse(document.getElementById("blob").textContent);
const $ = s => document.querySelector(s);
document.getElementById("gen").textContent = DATA.generated;

// How fresh is the data, right now (computed from the viewer's clock, not the
// build's, so this stays honest no matter how long ago the page was built).
(function renderFreshness() {
  const el = document.getElementById("freshness");
  const newest = new Date(DATA.newest + "T00:00:00Z");
  const ageDays = Math.floor((Date.now() - newest.getTime()) / 86400000);
  const curve = DATA.settleCurve || {};
  const settlePct = ageDays <= 0 ? null
    : ageDays >= DATA.settleRevisionDays ? 100
    : Math.round((curve[String(ageDays)] ?? 0.75) * 100);

  let html = `<div class="muted">Prices and arrivals current through
    <strong>${fmtDate(DATA.newest)}</strong>.</div>`;
  if (settlePct != null && settlePct < 99) {
    html += `<div class="flag">About ${settlePct}% of mandis have reported for that day
      so far &mdash; mandis file late, so the most recent days are still being corrected
      as more come in. This figure settles over the next ${DATA.settleRevisionDays} days.</div>`;
  }
  if (ageDays > DATA.staleWarningDays) {
    html += `<div class="flag warn">No newer data has arrived in ${ageDays} days
      (expected daily). The scheduled update may not be running &mdash; worth checking.</div>`;
  }
  el.innerHTML = html;
})();

const cropSel = $("#crop"), stateSel = $("#state"), panel = $("#panel"),
      tableWrap = $("#stateTableWrap"), latestWrap = $("#latestWrap");

for (const c of DATA.crops) cropSel.add(new Option(c, c));
for (const s of DATA.states) stateSel.add(new Option(s, s));

function monthLabel(week) {
  return DATA.monthNames[DATA.monthOfWeek[week - 1]];
}
function windowLabel(weeks) {
  if (!weeks || !weeks.length) return "—";
  // group into contiguous runs, treating week 52 -> week 1 as adjacent
  const u = [...new Set(weeks)].sort((a, b) => a - b);
  let runs = [[u[0], u[0]]];
  for (let i = 1; i < u.length; i++) {
    if (u[i] === runs[runs.length - 1][1] + 1) runs[runs.length - 1][1] = u[i];
    else runs.push([u[i], u[i]]);
  }
  if (runs.length > 1 && runs[0][0] === 1 && runs[runs.length - 1][1] === 52) {
    const first = runs.shift();
    runs[runs.length - 1] = [runs[runs.length - 1][0], first[1]]; // wrapped run
  }
  const parts = runs.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`));
  const lo = runs[0][0], hi = runs[runs.length - 1][1];
  const m1 = monthLabel(lo), m2 = monthLabel(hi);
  const months = m1 === m2 ? m1 : m1 + "–" + m2;
  return `week${u.length > 1 ? "s" : ""} ${parts.join(", ")} (${months})`;
}
function pct(x) { return x == null ? "—" : Math.round(x * 100) + "%"; }

const MONTHS_FULL = ["January", "February", "March", "April", "May", "June", "July",
                     "August", "September", "October", "November", "December"];
function fmtDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS_FULL[m - 1]} ${y}`;
}
// How the latest price sits against the twenty-year normal for that week.
// Inside an SVG <title> markup would show up as literal text, so pass plain.
function vsNormal(p, plain) {
  if (p >= -2 && p <= 2) return "about the usual level for this week";
  const amt = `${Math.abs(p)}% ${p > 0 ? "above" : "below"}`;
  return "about " + (plain ? amt : `<span class="${p > 0 ? "up" : "down"}">${amt}</span>`) +
         " the usual level for this week";
}

// --- 52-week plot: price pattern line + shaded sell / plant bands ---------
function plot(curve, sellWeeks, plantWeeks, peakWeek, unit, baseLevel, now) {
  // In rupee mode the curve is real Rs/quintal in 2025 prices; otherwise it is
  // the old unitless ratio, where 1.0 is the year's own average.
  const isInr = unit === "inr" && baseLevel > 0;
  const refV = isInr ? baseLevel : 1;
  const fmtY = v => isInr ? "₹" + Math.round(v).toLocaleString("en-IN") : v.toFixed(2);
  const W = 860, H = 232, padL = isInr ? 62 : 40, padR = 12, padT = 12, padB = 40;
  const iw = W - padL - padR, ih = H - padT - padB;
  const xs = w => padL + (w - 1) / 51 * iw;
  let out = "", ymin = refV, ymax = refV;
  const pts = curve ? curve.filter(v => v != null) : [];
  // refV is folded into the range so the "average" line can never fall off-chart
  if (pts.length) { ymin = Math.min(refV, ...pts); ymax = Math.max(refV, ...pts); }
  // ...and so is the latest-price dot: an unusual price is exactly the case
  // worth seeing, so it must not be the one that lands outside the chart
  const dot = now && now.plot != null ? now.plot : null;
  if (dot != null) { ymin = Math.min(ymin, dot); ymax = Math.max(ymax, dot); }
  const pad = (ymax - ymin) * 0.15 || refV * 0.1;
  ymin -= pad; ymax += pad;
  const ys = v => padT + ih - (v - ymin) / (ymax - ymin) * ih;

  const band = (weeks, fill) => {
    if (!weeks || !weeks.length) return "";
    const sorted = [...weeks].sort((a, b) => a - b);
    let runs = [[sorted[0], sorted[0]]];
    for (let i = 1; i < sorted.length; i++) {
      if (sorted[i] === runs[runs.length - 1][1] + 1) runs[runs.length - 1][1] = sorted[i];
      else runs.push([sorted[i], sorted[i]]);
    }
    return runs.map(([a, b]) =>
      `<rect x="${xs(a) - iw / 102}" y="${padT}" width="${(b - a) * iw / 51 + iw / 51}" height="${ih}" fill="${fill}"/>`
    ).join("");
  };

  out += band(sellWeeks, "rgba(76,150,80,0.16)");
  out += band(plantWeeks, "rgba(70,110,180,0.14)");

  let lastM = 0;
  for (let w = 1; w <= 52; w++) {
    const m = DATA.monthOfWeek[w - 1];
    if (m !== lastM) {
      lastM = m;
      out += `<line x1="${xs(w)}" y1="${padT}" x2="${xs(w)}" y2="${padT + ih}" stroke="#ececec"/>`;
      out += `<text x="${xs(w) + 2}" y="${padT + ih + 14}" font-size="11" fill="#9aa09b">${DATA.monthNames[m]}</text>`;
    }
  }
  for (let i = 0; i <= 3; i++) {
    const v = ymin + (ymax - ymin) * i / 3, y = ys(v).toFixed(1);
    out += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="#f4f3ee"/>`;
    out += `<text x="${padL - 6}" y="${(+y + 3).toFixed(1)}" text-anchor="end" font-size="10" fill="#9aa09b">${fmtY(v)}</text>`;
  }
  out += `<line x1="${padL}" y1="${ys(refV)}" x2="${W - padR}" y2="${ys(refV)}" stroke="#d7d5cb" stroke-dasharray="3 3"/>`;
  out += `<text x="${W - padR - 2}" y="${ys(refV) - 4}" text-anchor="end" font-size="10" fill="#9aa09b">${
    isInr ? "2025 average " + fmtY(refV) : "year average"}</text>`;
  if (isInr)
    out += `<text x="${padL}" y="${H - 4}" font-size="10" fill="#9aa09b">₹ per quintal (100 kg), in 2025 prices</text>`;

  if (pts.length) {
    let d = "", pen = "M";
    curve.forEach((v, i) => {
      if (v == null) { pen = "M"; return; }  // gap: lift the pen, restart the line
      d += `${pen}${xs(i + 1).toFixed(1)},${ys(v).toFixed(1)}`;
      pen = "L";
    });
    out += `<path d="${d}" fill="none" stroke="#2f7a37" stroke-width="2"/>`;
    // one hoverable dot per week -- native <title> shows the exact figure,
    // same trick as plotVolume, no tooltip library needed
    curve.forEach((v, i) => {
      if (v == null) return;
      const w = i + 1;
      out += `<circle cx="${xs(w).toFixed(1)}" cy="${ys(v).toFixed(1)}" r="4" fill="#2f7a37">` +
             `<title>Week ${w} (${monthLabel(w)}): ${isInr ? fmtY(v) + "/quintal" : v.toFixed(2) + "x year average"}</title></circle>`;
    });
  }
  if (peakWeek) {
    out += `<line x1="${xs(peakWeek)}" y1="${padT}" x2="${xs(peakWeek)}" y2="${padT + ih}" stroke="#2f7a37" stroke-width="1.5" stroke-dasharray="4 3"/>`;
  }
  // the latest reported price, marked where it falls on the year. Drawn last so
  // it sits above the curve, and in its own colour so it reads as "not the
  // pattern" -- it is one day, the green line is twenty years.
  if (dot != null) {
    const x = xs(now.week), y = ys(dot);
    out += `<line x1="${x}" y1="${padT}" x2="${x}" y2="${padT + ih}" stroke="#c2612f" stroke-width="1" stroke-dasharray="2 3" opacity=".7"/>`;
    out += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="6" fill="#fff" stroke="#c2612f" stroke-width="2.5">` +
           `<title>Latest reported price, ${fmtDate(now.date)}: ₹${now.price.toLocaleString("en-IN")}/quintal` +
           `${now.pct == null ? "" : ` — ${vsNormal(now.pct, true)}`}</title></circle>`;
  }
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Weekly price pattern">${out}</svg>`;
}

// --- 52-week plot: arrivals (volume) pattern, hoverable for the raw tonnes --
function plotVolume(vals) {
  const W = 860, H = 170, padL = 48, padR = 12, padT = 12, padB = 24;
  const iw = W - padL - padR, ih = H - padT - padB;
  const xs = w => padL + (w - 1) / 51 * iw;
  const pts = vals ? vals.filter(v => v != null) : [];
  if (!pts.length) return "";
  const ymin = 0, ymax = Math.max(...pts) * 1.1 || 1;  // tonnes, zero baseline
  const ys = v => padT + ih - (v - ymin) / (ymax - ymin) * ih;
  const fmtT = v => v >= 1000 ? (Math.round(v / 100) / 10) + "k" : String(Math.round(v));

  let out = "", lastM = 0;
  for (let w = 1; w <= 52; w++) {
    const m = DATA.monthOfWeek[w - 1];
    if (m !== lastM) {
      lastM = m;
      out += `<line x1="${xs(w)}" y1="${padT}" x2="${xs(w)}" y2="${padT + ih}" stroke="#ececec"/>`;
      out += `<text x="${xs(w) + 2}" y="${H - 6}" font-size="11" fill="#9aa09b">${DATA.monthNames[m]}</text>`;
    }
  }
  // y-axis: three gridlines labelled in tonnes ("t" on each label, so there's
  // no separate unit caption to collide with the top tick)
  for (let k = 0; k <= 2; k++) {
    const v = ymin + (ymax - ymin) * k / 2, y = ys(v);
    out += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="#f0efe9"/>`;
    out += `<text x="4" y="${(y + 3).toFixed(1)}" font-size="10" fill="#9aa09b">${fmtT(v)} t</text>`;
  }

  let d = "", pen = "M";
  vals.forEach((v, i) => {
    if (v == null) { pen = "M"; return; }  // gap: lift the pen
    d += `${pen}${xs(i + 1).toFixed(1)},${ys(v).toFixed(1)}`;
    pen = "L";
  });
  out += `<path d="${d}" fill="none" stroke="#a06a2f" stroke-width="2"/>`;
  // one hoverable dot per week -- native <title> shows the clean tonnes figure
  vals.forEach((v, i) => {
    if (v == null) return;
    const w = i + 1;
    out += `<circle cx="${xs(w).toFixed(1)}" cy="${ys(v).toFixed(1)}" r="4" fill="#a06a2f">` +
           `<title>Week ${w} (${monthLabel(w)}): ${Math.round(v).toLocaleString("en-IN")} tonnes</title></circle>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Weekly mandi arrivals in tonnes">${out}</svg>`;
}

function flags(r) {
  let out = "";
  if (!r.fallback && r.years_of_data && r.years_of_data < 8)
    out += `<span class="flag warn">Only ${r.years_of_data} years of usable price data for this pair &mdash; treat this as a rough hint, not a firm answer.</span>`;
  if (r.fallback)
    out += `<span class="flag">Not enough price history for this state (${r.reason}). Showing the all-India selling pattern; the planting window below is still fitted to this state.</span>`;
  if (r.is_flat_top)
    out += `<span class="flag">Prices are fairly flat across the best weeks &mdash; there is no single sharp peak. Treat the whole shaded range as "good months", not one exact week.</span>`;
  if (r.stability_agree === false)
    out += `<span class="flag warn">The pattern shifted between 2005&ndash;2014 and 2015&ndash;2024 (old peak week ${r.stability_old_peak}, recent ${r.stability_new_peak}). The figures above use recent years only.</span>`;
  if (r.base_src === "bridged")
    out += `<span class="flag">This state last reported a full year of prices in ${r.base_year_used}. The rupee levels shown are that year's, carried forward to 2025 using this crop's all-India price trend. The shape of the curve is unaffected.</span>`;
  if (r.base_src === "national" && !r.fallback)
    out += `<span class="flag">No recent price level for this state to anchor to, so the rupee figures are all-India 2025 levels. The pattern itself is still this state's own.</span>`;
  if (r.stl_disagrees)
    out += `<span class="flag warn">The two methods disagree on the peak week &mdash; the data for this pair is thin, so treat the answer loosely.</span>`;
  if (r.peak_is_naive === false && r.plant_week != null)
    out += `<span class="flag">The exact price peak can't be reached from a feasible sowing date here, so the planting window targets the nearest reachable good stretch.</span>`;
  return out;
}

function recBlock(r, curveForPlot, crop) {
  const yrs = r.years_of_data ? ` &middot; ${r.years_of_data} years of data` : "";
  // volume is analysed per (crop, state) independently of the price side, so
  // a pair can have its own arrivals chart even when price falls back to the
  // national pattern -- but if this pair's own volume history is too thin,
  // fall back to the national arrivals pattern the same way price does.
  const nat = DATA.national[crop] || {};
  const ownVol = r.volume;
  const vol = ownVol || nat.volume;
  const volNote = ownVol
    ? `Typical mandi arrivals in tonnes for each week of the year.
       Hover a point for that week's figure. Volume data covers 2021 onward, shorter history than prices.`
    : `This is the all-India arrivals pattern &mdash; ${r.state || "this state"} doesn't have enough
       arrivals data of its own yet. Hover a point for that week's figure.`;
  const volSection = vol ? `
    <h3 style="font-size:.95rem;margin:16px 0 4px;color:#48504d">Arrivals pattern (mandi volume)</h3>
    ${plotVolume(vol)}
    <div class="muted" style="margin-top:2px">${volNote}</div>` : `
    <h3 style="font-size:.95rem;margin:16px 0 4px;color:#48504d">Arrivals pattern (mandi volume)</h3>
    <div class="muted">No arrivals data available for this crop and state.</div>`;
  const relText = r.reliability == null ? "" :
    ` <span class="muted">In ${pct(r.reliability)} of those years, prices that week beat the year's average.</span>`;
  let plant;
  if (r.lead_weeks == null)
    plant = `<div class="muted">This is a tree or perennial crop &mdash; it fruits for years, so there is no useful "plant now" week. Price pattern only.</div>`;
  else if (r.plant_window)
    plant = `<div class="big">${windowLabel(r.plant_window)}</div>
             <div class="muted">Crop needs about ${r.lead_weeks} weeks from sowing (incl. nursery) to the heavy harvest.</div>`;
  else if (r._national)
    plant = `<div class="muted">Planting timing depends on your region &mdash; pick a state above to get a sowing window.</div>`;
  else
    plant = `<div class="muted">No feasible planting window &mdash; this crop isn't grown in this zone, or the peak can't be reached from any allowed sowing month.</div>`;

  // the one figure on this page that is today's rupees, not 2025's -- said so
  // in the caption, because a reader comparing it to the chart will otherwise
  // read the inflation gap as a seasonal signal
  const now = r.now;
  const nowBlock = !now ? "" : `<div class="now">
    <div class="muted">Latest reported price</div>
    <div class="big">₹${now.price.toLocaleString("en-IN")} per quintal on ${fmtDate(now.date)}</div>
    <div class="muted">${now.pct == null
      ? "Not enough of this year's prices yet to say how that compares with normal."
      : vsNormal(now.pct) + (now.normal
          ? ` &mdash; the usual for week ${now.week} works out to about ₹${now.normal.toLocaleString("en-IN")} at this year's prices.`
          : ".")}</div>
  </div>`;

  return `<div class="card">
    ${nowBlock}
    ${plot(curveForPlot, r.sell_window, r.plant_window, r.peak_week, r.unit, r.base_level, r.now)}
    <div class="rec">
      <div>
        <div class="muted">Sells highest</div>
        <div class="big">${windowLabel(r.sell_window)}</div>
        <div class="muted">Peak week ${r.peak_week || "—"}.${relText}</div>
      </div>
      <div>
        <div class="muted">Plant around</div>
        ${plant}
      </div>
    </div>
    ${flags(r)}
    ${volSection}
    <div class="muted" style="margin-top:12px">${r._label || ""}${yrs}</div>
  </div>`;
}

function stateTable(crop) {
  const rows = DATA.states.map(st => {
    const r = DATA.pairs[crop + "|" + st];
    if (!r) return "";
    if (r.insufficient_data)
      return `<tr class="pick" data-state="${st}"><td>${st}</td><td class="thin" colspan="4">no data</td></tr>`;
    const tag = r.fallback ? ' <span class="badge fallback">all-India</span>' : "";
    return `<tr class="pick" data-state="${st}">
      <td>${st}${tag}</td>
      <td>${windowLabel(r.sell_window)}</td>
      <td>${r.plant_window ? windowLabel(r.plant_window) : "—"}</td>
      <td>${r.fallback ? "—" : (r.years_of_data || "—")}</td>
      <td>${pct(r.reliability)}</td>
    </tr>`;
  }).join("");
  return `<h2>Every state for ${crop}</h2>
    <p class="thin"><span class="badge fallback">all-India</span> means that state had too little
    price history of its own, so the selling weeks shown are the all-India pattern, not that
    state's. A dash means there is no answer for that cell.</p>
    <div class="card" style="padding:6px 8px">
    <table><thead><tr><th>State</th><th>Sells highest</th><th>Plant around</th>
    <th>Years</th><th>Reliability</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

// --- what every still-reporting pair last sold for ------------------------
// Built from DATA.pairs, so it needs nothing the cards don't already carry.
function latestTable() {
  const rows = [];
  for (const key in DATA.pairs) {
    const now = DATA.pairs[key].now;
    if (!now) continue;
    const i = key.lastIndexOf("|");  // crop names contain "/", never "|"
    rows.push(Object.assign({ crop: key.slice(0, i), state: key.slice(i + 1) }, now));
  }
  if (!rows.length) return `<div class="card">No pair has reported a price in the
    last ${DATA.staleDays} days. The daily updater may not have run.</div>`;
  rows.sort((a, b) => a.crop.localeCompare(b.crop) || a.state.localeCompare(b.state));
  const body = rows.map(r => `<tr class="pick" data-crop="${r.crop}" data-state="${r.state}">
      <td>${r.crop}</td><td>${r.state}</td><td>${fmtDate(r.date)}</td>
      <td>₹${r.price.toLocaleString("en-IN")}</td>
      <td>${r.pct == null ? "—" : vsNormal(r.pct)}</td></tr>`).join("");
  return `<details class="latest">
    <summary>${rows.length} crop&ndash;state pairs, newest ${fmtDate(DATA.newest)}</summary>
    <p class="thin">Averaged across every market in that state that reported that day.
    These are today's rupees, not the 2025 rupees the charts use, so don't compare the
    two figures directly &mdash; the "against the usual" column already does that
    comparison properly. Click any row to open that crop and state. Pairs silent for
    more than ${DATA.staleDays} days are left out.</p>
    <div class="scroll"><table><thead><tr><th>Crop</th><th>State</th><th>Reported</th>
    <th>Price /quintal</th><th>Against the usual</th></tr></thead>
    <tbody>${body}</tbody></table></div>
  </details>`;
}

function render() {
  const crop = cropSel.value, st = stateSel.value;
  const meta = DATA.cropMeta[crop];
  let r, curveForPlot, label;
  if (st) {
    r = DATA.pairs[crop + "|" + st];
    if (!r || r.insufficient_data) {
      panel.innerHTML = `<div class="card"><strong>${crop} in ${st}:</strong>
        no usable price history, and no all-India pattern to fall back on.</div>`;
      tableWrap.innerHTML = stateTable(crop);
      wireRows();
      return;
    }
    curveForPlot = r.curve || DATA.national[crop].curve;
    label = `${crop} &mdash; ${st}` + (r.fallback ? ' <span class="badge fallback">all-India fallback</span>' : "");
  } else {
    r = DATA.national[crop];
    curveForPlot = r.curve;
    label = `${crop} &mdash; all India <span class="badge">country-wide</span>`;
    if (r.insufficient_data) {
      panel.innerHTML = `<div class="card"><strong>${crop}:</strong> not enough
        data nationally to show a pattern.</div>`;
      tableWrap.innerHTML = "";
      return;
    }
  }
  r = Object.assign({ _label: label, _national: !st }, r);
  panel.innerHTML =
    `<p class="muted" style="margin:14px 0 0">${meta.note || ""}</p>` +
    recBlock(r, curveForPlot, crop);
  tableWrap.innerHTML = stateTable(crop);
  wireRows();
}

// scoped to the per-crop table: it is rebuilt on every render, so its listeners
// go with it. The latest-prices table is built once and wired once, below.
function wireRows() {
  tableWrap.querySelectorAll("tr.pick").forEach(tr =>
    tr.addEventListener("click", () => { stateSel.value = tr.dataset.state; render(); }));
}

cropSel.addEventListener("change", render);
stateSel.addEventListener("change", render);
cropSel.value = DATA.crops[0];
render();

latestWrap.innerHTML = latestTable();
latestWrap.querySelectorAll("tr.pick").forEach(tr =>
  tr.addEventListener("click", () => {
    cropSel.value = tr.dataset.crop;
    stateSel.value = tr.dataset.state;
    render();
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }));
</script>
</body>
</html>
"""


def demo():
    """Self-check for latest_snapshot -- the only maths on the live-price path."""
    from datetime import timedelta

    curve = {w: 1.0 for w in range(1, 53)}  # flat: every week is the year's average
    start = date(2026, 1, 5)
    days = [start + timedelta(days=7 * i) for i in range(30)]  # 30 distinct weeks
    series = [(d, 1000.0) for d in days]
    newest = days[-1]

    snap = latest_snapshot(series, curve, newest, 2000)
    assert snap["price"] == 1000, snap
    assert snap["pct"] == 0, snap  # same as its own year's level -> normal
    assert snap["plot"] == 2000, snap  # rescaled to the chart's BASE_YEAR level
    assert snap["week"] == seasonality.iso_week(days[-1])

    # a dearer last day reads as above normal, and the year level barely moves
    dear = series[:-1] + [(days[-1], 1200.0)]
    assert 18 <= latest_snapshot(dear, curve, newest, 2000)["pct"] <= 20

    # inflation must not read as a seasonal signal: every price up 50% is normal
    hot = [(d, 1500.0) for d in days]
    assert latest_snapshot(hot, curve, newest, 2000)["pct"] == 0

    # gone quiet -> no "right now" at all
    assert (
        latest_snapshot(series, curve, newest + timedelta(days=STALE_DAYS + 1), 2000)
        is None
    )
    assert latest_snapshot([], curve, newest, 2000) is None

    # too few weeks to trust a year level -> price shown, comparison withheld
    thin = latest_snapshot(series[-3:], curve, newest, 2000)
    assert thin["price"] == 1000 and "pct" not in thin, thin
    print("demo ok")


CACHE = PROJECT_DIR / "build_cache.json"

if __name__ == "__main__":
    # The analysis run takes ~10 min (STL over ~750 pairs + 36 pooled national
    # series). Cache it so tweaking only the HTML template is instant. Delete
    # build_cache.json (or pass --rebuild) to force a fresh analysis.
    import sys

    if "--demo" in sys.argv:
        demo()
        raise SystemExit

    if CACHE.exists() and "--rebuild" not in sys.argv:
        data = json.loads(CACHE.read_text())
        print(f"loaded {CACHE.name} (pass --rebuild to recompute)")
    else:
        data = build_data()
        CACHE.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        print(f"wrote {CACHE.name}")

    OUT.write_text(render(data), encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"wrote {OUT.name} ({kb:.0f} KB)")
