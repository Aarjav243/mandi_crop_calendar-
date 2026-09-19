"""Pull one day of mandi prices from data.gov.in, append to data/, rebuild the page.

Meant to run unattended from Windows Task Scheduler once a day. Mandi prices
publish once daily and trickle in through the afternoon, so "real time" here
means yesterday's prices, fetched automatically -- there is no intraday feed to
be more current than.

Reuses backfill_2026's name lookups, aggregation shape and merge(), so a day
pulled from the API lands identically to a day pulled from the Kaggle CSVs.

    python daily_update.py                 # yesterday, then rebuild
    python daily_update.py --date 2026-09-08
    python daily_update.py --days 7        # last 7 days (catch up after downtime)
    python daily_update.py --no-build      # skip the HTML rebuild

Exits non-zero on failure so a failed scheduled run is visible in Task Scheduler.
Logs to daily_update.log next to this file.
"""

import argparse
import datetime as dt
import json
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import agmarknet
import crops
import settledness
import store
from backfill_2026 import (
    DATA_DIR,
    agmark_name,
    build_lookups,
    last_date,
    mean_by_pair,
    merge,
    norm,
)

PROJECT_DIR = Path(__file__).parent
LOG_PATH = PROJECT_DIR / "daily_update.log"
KEY_PATH = Path.home() / ".config" / "wellabs" / "datagovin_api_key.txt"

# "Variety-wise Daily Market Prices Data of Commodity" -- the full archive,
# 81.7M rows and still growing daily. The obvious-looking resource
# 9ef84268-d588-465a-a308-a864a43d0070 ("Current Daily Price ... (Mandi)") holds
# only *today*, so a missed run loses that day for good. This one has history,
# so a missed run is caught up on the next one.
RESOURCE_ID = "35985678-0d79-46b4-9ed6-6f13308a1d24"
API = f"https://api.data.gov.in/resource/{RESOURCE_ID}"
PAGE = 1000
# The API refuses offset+limit > 10000, so a whole day (up to 23k rows) cannot be
# paged in one query. Fetching a crop at a time keeps the biggest slice near 1200
# and skips the ~60% of rows that are commodities we don't track.
CAP = 10000
TIMEOUT = 120
# Measured: the API allows roughly 60 requests per rolling minute. A burst of
# ~60 back-to-back calls gets 429s that then take ~30s to clear, which killed a
# 91-day crawl on its second day. One request per second ran 111 straight calls
# with zero rejections -- that is the pace, don't raise it to "speed things up".
THROTTLE = 1.0
RETRY_WAIT = 5.0
MAX_RETRY_WAIT = 60.0
RETRIES = 8  # up to ~5 minutes of waiting; a long crawl must ride out a block
# Mandis report late: today has a handful of rows, yesterday is still filling,
# two days back is complete. merge() writes each date once, so pulling a date
# too early would freeze a half-empty day into the history.
LAG_DAYS = 2
MAX_DAYS = 150  # guard against a silly date range hammering the API
SOURCE_TAG = "data.gov.in:variety-wise"

# data.gov.in's gateway 502s (after a 60s stall) on the default "Python-urllib/x"
# User-Agent and answers in 0.3s with any ordinary one. Cost two sessions of
# chasing a phantom outage -- do not remove this header.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

log = logging.getLogger("daily_update")


def setup_logging():
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in (
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ):
        h.setFormatter(fmt)
        log.addHandler(h)


def load_key():
    if not KEY_PATH.exists():
        raise SystemExit(f"missing API key at {KEY_PATH}")
    return KEY_PATH.read_text().strip()


def _get(url, tries=RETRIES):
    """One API call, waiting out the rate limiter rather than failing the run."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == tries - 1:
                raise
            wait = min(RETRY_WAIT * 2**attempt, MAX_RETRY_WAIT)
            log.info("rate limited, waiting %.0fs", wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def fetch_crop_day(key, crop, date):
    """Every row for one crop on one date, following pages.

    Both filters need the ".keyword" suffix. Without it the API token-matches
    instead: filters[arrival_date]=01/08/2026 also returns 08/01/2026, and
    filters[commodity]=Dry Chillies also returns Ginger(Dry) and Dry Grapes.
    """
    rows = []
    offset = 0
    while True:
        qs = urllib.parse.urlencode(
            {
                "api-key": key,
                "format": "json",
                "limit": PAGE,
                "offset": offset,
                "filters[arrival_date.keyword]": date.strftime("%d/%m/%Y"),
                "filters[commodity.keyword]": agmark_name(crop),
            }
        )
        batch = _get(f"{API}?{qs}").get("records") or []
        # This resource capitalises its field names (Modal_Price, Arrival_Date)
        # where the daily one lower-cases them. Fold to lower so everything
        # downstream, demo rows included, reads one shape.
        rows.extend({k.lower(): v for k, v in row.items()} for row in batch)
        if len(batch) < PAGE:
            return rows
        offset += PAGE
        if offset + PAGE > CAP:
            log.warning(
                "%s %s hit the %d-row API cap -- rows beyond it were not fetched",
                date,
                crop,
                CAP,
            )
            return rows


def fetch_day(key, date):
    """Every row we care about for one date, one crop at a time."""
    rows = []
    for crop in crops.CROPS:
        rows.extend(fetch_crop_day(key, crop, date))
        time.sleep(THROTTLE)
    return rows


def to_buckets(rows, date, crop_by_norm, state_by_norm):
    """Same aggregation as backfill: mean of min/max/modal per (crop, state, date)."""
    buckets = defaultdict(lambda: ([], [], []))
    skipped = 0
    iso = date.isoformat()
    for row in rows:
        crop = crop_by_norm.get(norm(str(row.get("commodity", ""))))
        state = state_by_norm.get(norm(str(row.get("state", ""))))
        if not crop or not state:
            skipped += 1
            continue
        try:
            mn = float(row["min_price"])
            mx = float(row["max_price"])
            md = float(row["modal_price"])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        # Zero prices are missing-data placeholders, not real prices.
        if mn <= 0 or mx <= 0 or md <= 0:
            skipped += 1
            continue
        mins, maxs, mods = buckets[(crop, state, iso)]
        mins.append(mn)
        maxs.append(mx)
        mods.append(md)
    return buckets, skipped


def rebuild():
    """Recompute the seasonality curves and re-render the page."""
    r = subprocess.run(
        [sys.executable, str(PROJECT_DIR / "build_html.py"), "--rebuild"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
    )
    for line in (r.stdout or "").strip().splitlines()[-3:]:
        log.info("build_html: %s", line)
    if r.returncode:
        log.error("build_html failed: %s", (r.stderr or "")[-500:])
    return r.returncode == 0


def newest_date_on_disk():
    """The most recent date any data file holds, as YYYY-MM-DD."""
    return max(
        (last_date(json.loads(p.read_text())) or "" for p in DATA_DIR.glob("*.json")),
        default="",
    )


def dates_to_fetch(args):
    """Which days are missing. Empty means the data is already current."""
    end = (
        dt.date.fromisoformat(args.date)
        if args.date
        else dt.date.today() - dt.timedelta(days=LAG_DAYS)
    )
    if args.days:
        start = end - dt.timedelta(days=args.days - 1)
    else:
        have = newest_date_on_disk()
        if not have:
            raise SystemExit(f"no dated records in {DATA_DIR}; pass --days")
        start = dt.date.fromisoformat(have) + dt.timedelta(days=1)
    if (end - start).days >= MAX_DAYS:
        start = end - dt.timedelta(days=MAX_DAYS - 1)
        log.warning("range longer than %d days; starting at %s", MAX_DAYS, start)
    # a negative span yields an empty range, i.e. "already current"
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def run_agmarknet(args):
    """Pull prices *and* arrivals from Agmarknet, revising the recent days.

    Unlike the data.gov.in path this overwrites the days it re-fetches. Mandis
    report late -- a day is only ~75% filed when it is one day old -- so the
    last few days on disk are provisional and must be corrected, not appended
    past. See agmarknet.py for the measured reporting curve.
    """
    if args.date:
        end = dt.date.fromisoformat(args.date)
        span = args.days or agmarknet.REVISE_DAYS
        dates = [end - dt.timedelta(days=i) for i in range(span - 1, -1, -1)]
    else:
        dates = agmarknet.dates_to_fetch(
            have=newest_date_on_disk() or None, revise_days=args.days
        )
    if len(dates) > MAX_DAYS:
        dates = dates[-MAX_DAYS:]
        log.warning("range longer than %d days; starting at %s", MAX_DAYS, dates[0])

    log.info(
        "agmarknet: fetching %s .. %s (%d days, revising as late returns arrive)",
        dates[0],
        dates[-1],
        len(dates),
    )

    prices, volumes = {}, {}
    total_rows = 0
    failed = None
    for date in dates:
        try:
            rows = agmarknet.fetch_day(date)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # Keep whatever completed rather than losing a long catch-up to one
            # transient error.
            log.error("fetch failed on %s: %s: %s", date, type(e).__name__, e)
            failed = date
            break
        p, v = agmarknet.to_buckets_all(rows, date)
        prices.update(p)
        volumes.update(v)
        total_rows += len(rows)
        log.info(
            "%s: %d market rows -> %d price points, %d arrival points",
            date,
            len(rows),
            len(p),
            len(v),
        )
        # How many markets reported for this date, at this age -- lets the
        # page show "about X% of mandis have reported so far" instead of
        # treating a freshly-fetched day as final. See settledness.py.
        settledness.record(date, dt.date.today(), len(rows))
        time.sleep(agmarknet.THROTTLE)

    if not prices and not volumes:
        log.warning("no usable rows for %s..%s", dates[0], dates[-1])
        return 1 if failed else 0

    pf, pr = store.write_prices(prices, source_tag=agmarknet.SOURCE_TAG)
    vf, vr = store.write_volumes(volumes, source_tag=agmarknet.SOURCE_TAG)
    log.info(
        "prices: %d records across %d files | arrivals: %d records across %d files "
        "(%d market rows)",
        pr,
        pf,
        vr,
        vf,
        total_rows,
    )

    if (pr or vr) and not args.no_build:
        rebuild()
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--source",
        choices=("agmarknet", "datagovin"),
        default="agmarknet",
        help="agmarknet (default): prices + arrivals, one day fresher, revises "
        "recent days. datagovin: the older prices-only path, kept as a fallback.",
    )
    ap.add_argument("--date", help="YYYY-MM-DD, the last day to pull")
    ap.add_argument(
        "--days",
        type=int,
        help="how many days back to pull, ending at --date "
        "(default: everything since the newest date already on disk)",
    )
    ap.add_argument("--no-build", action="store_true")
    args = ap.parse_args()

    setup_logging()
    if args.source == "agmarknet":
        return run_agmarknet(args)

    key = load_key()
    dates = dates_to_fetch(args)
    if not dates:
        log.info(
            "already current through %s -- nothing to fetch", newest_date_on_disk()
        )
        return 0
    log.info("fetching %s .. %s (%d days)", dates[0], dates[-1], len(dates))

    crop_by_norm, state_by_norm = build_lookups()
    all_buckets = {}
    total_rows = total_skipped = 0
    failed = None
    for date in dates:
        try:
            rows = fetch_day(key, date)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # Keep whatever completed: on a long catch-up, losing 60 good days to
            # one transient error would mean starting the whole crawl again.
            log.error("fetch failed for %s: %s: %s", date, type(e).__name__, e)
            failed = date
            break
        buckets, skipped = to_buckets(rows, date, crop_by_norm, state_by_norm)
        all_buckets.update(buckets)
        total_rows += len(rows)
        total_skipped += skipped
        log.info(
            "%s: %d rows, %d usable groups (%d rows skipped)",
            date,
            len(rows),
            len(buckets),
            skipped,
        )

    if not all_buckets:
        log.warning(
            "no usable rows for %s..%s -- nothing to append", dates[0], dates[-1]
        )
        return 1 if failed else 0

    updated, added = merge(mean_by_pair(all_buckets), SOURCE_TAG)
    log.info(
        "appended %d records across %d files (%d API rows total)",
        added,
        updated,
        total_rows,
    )

    if added and not args.no_build:
        rebuild()
    return 1 if failed else 0


def demo():
    """Offline check: fake API rows must aggregate exactly like the backfill does."""
    crop_by_norm, state_by_norm = build_lookups()
    date = dt.date(2026, 9, 10)
    rows = [
        # two Onion markets in Maharashtra on the same day -> one averaged record
        {
            "commodity": "Onion",
            "state": "Maharashtra",
            "min_price": "1000",
            "max_price": "2000",
            "modal_price": "1500",
        },
        {
            "commodity": "Onion",
            "state": "Maharashtra",
            "min_price": "1200",
            "max_price": "2200",
            "modal_price": "1700",
        },
        # source spellings the lookups must still resolve
        {
            "commodity": "Arhar(Tur/Red Gram)(Whole)",
            "state": "Keralam",
            "min_price": "5000",
            "max_price": "6000",
            "modal_price": "5500",
        },
        # dropped: zero price, unknown crop, unparseable price
        {
            "commodity": "Onion",
            "state": "Maharashtra",
            "min_price": "0",
            "max_price": "0",
            "modal_price": "0",
        },
        {
            "commodity": "Dragon Fruit",
            "state": "Maharashtra",
            "min_price": "1",
            "max_price": "2",
            "modal_price": "3",
        },
        {
            "commodity": "Onion",
            "state": "Maharashtra",
            "min_price": "",
            "max_price": "x",
            "modal_price": None,
        },
    ]
    buckets, skipped = to_buckets(rows, date, crop_by_norm, state_by_norm)
    assert skipped == 3, skipped
    by_pair = mean_by_pair(buckets)
    assert by_pair[("Onion", "Maharashtra")]["2026-09-10"] == (1100.0, 2100.0, 1600.0)
    assert by_pair[("Tur/Arhar", "Kerala")]["2026-09-10"] == (5000.0, 6000.0, 5500.0)

    # merge() must never rewrite history: a date already present is not re-appended
    sample = json.loads((DATA_DIR / "Onion__Maharashtra.json").read_text())
    old_last = sample["records"][-1]["date"][:10]
    stale = {("Onion", "Maharashtra"): {old_last: (1.0, 2.0, 3.0)}}
    assert merge(stale, "test") == (0, 0)

    # fetch must follow every page, not just the first -- a paging bug here would
    # silently drop rows on busy days with no error to notice. Rows come back
    # capitalised and must be folded to lower case on the way out.
    pages = [
        [{"Modal_Price": i} for i in range(PAGE)],
        [{"Modal_Price": i} for i in range(PAGE)],
        [{"Modal_Price": i} for i in range(7)],
    ]
    seen = []

    class FakeResp:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen.append(req.full_url)
        return FakeResp(json.dumps({"records": pages[len(seen) - 1]}).encode())

    real = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        got = fetch_crop_day("k", "Onion", dt.date(2026, 9, 10))
    finally:
        urllib.request.urlopen = real
    assert len(got) == 2 * PAGE + 7, len(got)
    assert len(seen) == 3, seen
    assert "offset=0" in seen[0] and f"offset={PAGE}" in seen[1]
    assert all("modal_price" in r for r in got), got[0]
    # the API wants DD/MM/YYYY, not ISO -- getting this wrong returns zero rows
    assert urllib.parse.quote("10/09/2026", safe="") in seen[0], seen[0]
    # ...and both filters need .keyword, or the API token-matches and returns
    # the wrong dates and the wrong crops without complaining
    assert "arrival_date.keyword" in urllib.parse.unquote(seen[0]), seen[0]
    assert "commodity.keyword" in urllib.parse.unquote(seen[0]), seen[0]

    # the range picker: no --days means "everything missing since disk", and a
    # data set already current must ask for nothing rather than refetching a day
    class A:
        date, days = "2026-09-09", None

    have = dt.date.fromisoformat(newest_date_on_disk())
    want = dates_to_fetch(A)
    assert want[0] == have + dt.timedelta(days=1), (want[0], have)
    assert want[-1] == dt.date(2026, 9, 9), want[-1]

    class B:
        date, days = have.isoformat(), None

    assert dates_to_fetch(B) == [], dates_to_fetch(B)
    print("demo ok")


if __name__ == "__main__":
    sys.exit(demo() if "--demo" in sys.argv else main())
