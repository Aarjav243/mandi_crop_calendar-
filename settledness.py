"""How complete is a recent day's mandi data, right now?

Mandis file late. A date pulled the day after it happened holds only some
fraction of its eventual markets; the rest arrive over the next few days,
which is exactly why daily_update.py re-fetches the last REVISE_DAYS days
every night (see agmarknet.dates_to_fetch). This module turns that fact into
a number a reader can see: "as of today, about 78% of mandis have reported
for yesterday" -- instead of just showing yesterday's price as if it were
final.

How the percentage is estimated
--------------------------------
Every time daily_update.py fetches a date, it logs how many market rows came
back that day (reporting_log.jsonl, one line per fetch: which date, which day
we fetched it on, how many rows). Once a date has been fetched five times --
once at each age from 1 to REVISE_DAYS, sliding through the revision window --
we know both its early count and its later, more-settled count, so we can
compute what fraction of the "final" count showed up at each age.

Averaged across enough dates, that becomes a measured curve: age 1 day old is
usually X% complete, age 2 is Y%, and so on. Until there is enough history to
measure it (MIN_SAMPLES dates observed at that age), FALLBACK_CURVE stands in
-- it comes from a one-off manual check earlier in this project (a handful of
crop/state pairs sampled at 1, 2 and 3 days old), so treat it as a rough
starting guess, not a calibrated figure.
"""

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
LOG_PATH = PROJECT_DIR / "reporting_log.jsonl"

REVISE_DAYS = 5
MIN_SAMPLES = 5  # dates needed at a given age before the measured curve wins

# Rough starting estimate; see module docstring. Replaced per-age once enough
# real observations exist.
FALLBACK_CURVE = {1: 0.75, 2: 0.85, 3: 0.93, 4: 0.98, 5: 1.0}


def _shift(iso_date, days):
    return (dt.date.fromisoformat(iso_date) + dt.timedelta(days=days)).isoformat()


def record(date, run_date, market_rows, log_path=LOG_PATH):
    """Append one fetch observation. `date` is the day the data is about;
    `run_date` is the day we fetched it (so run_date - date = age in days)."""
    line = json.dumps(
        {
            "date": str(date)[:10],
            "run_date": str(run_date)[:10],
            "market_rows": market_rows,
        }
    )
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_log(log_path=LOG_PATH):
    if not Path(log_path).exists():
        return []
    rows = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def settle_curve(log=None, log_path=LOG_PATH):
    """{age_in_days: fraction of eventual rows typically seen by then}.

    "Eventual" means the largest row count ever logged for that date -- a
    proxy for "settled", since REVISE_DAYS is when we stop re-checking it.
    Falls back to FALLBACK_CURVE per age until MIN_SAMPLES dates back it up.
    """
    log = load_log(log_path) if log is None else log
    best = {}
    for e in log:
        best[e["date"]] = max(best.get(e["date"], 0), e["market_rows"])

    ratios_by_age = defaultdict(list)
    for e in log:
        final = best[e["date"]]
        if final <= 0:
            continue
        age = (
            dt.date.fromisoformat(e["run_date"]) - dt.date.fromisoformat(e["date"])
        ).days
        if 1 <= age <= REVISE_DAYS:
            ratios_by_age[age].append(e["market_rows"] / final)

    curve = dict(FALLBACK_CURVE)
    for age, ratios in ratios_by_age.items():
        if len(ratios) >= MIN_SAMPLES:
            curve[age] = sum(ratios) / len(ratios)
    return curve


def pct_reported(age_days, curve=None):
    """Estimated % of a date's mandis reported so far, given its age in days.

    None if the date is 0 days old or in the future -- LAG_DAYS means we never
    fetch a date that recent, so there is nothing to estimate yet. 100 once a
    date is old enough to have left the revision window (assumed settled).
    """
    if age_days is None or age_days <= 0:
        return None
    if age_days >= REVISE_DAYS:
        return 100
    curve = curve if curve is not None else FALLBACK_CURVE
    frac = curve.get(age_days, FALLBACK_CURVE.get(age_days, 1.0))
    return round(frac * 100)
