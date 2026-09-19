"""Fill a date range of prices and arrivals from Agmarknet, in resumable chunks.

The nightly run (daily_update.py) only reaches five days back. This is for the
one-off gap: arrivals stopped at 2025-10-30 when CEDA froze, so roughly eleven
months of volume are missing even though Agmarknet has them.

    python backfill_agmarknet.py --from 2025-10-31 --to 2026-09-13
    python backfill_agmarknet.py --from 2025-10-31 --to 2026-09-13 --chunk 20
    python backfill_agmarknet.py --from 2021-01-01 --to 2025-10-30 --prices-only

Writes after every chunk rather than at the end, so a crash four hours in costs
one chunk, not the whole crawl. Re-running a range is safe: store.py merges by
date, so a day already present is overwritten with the same values.

Roughly 45s per day of data (one paged query covering all 35 crops), so a year
is about four hours. Leave it running; it logs to daily_update.log.
"""

import argparse
import datetime as dt
import logging
import sys
import time
import urllib.error
from pathlib import Path

import agmarknet
import store

PROJECT_DIR = Path(__file__).parent
LOG_PATH = PROJECT_DIR / "daily_update.log"

log = logging.getLogger("backfill_agmarknet")


def setup_logging():
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in (
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ):
        h.setFormatter(fmt)
        log.addHandler(h)


def daterange(start, end):
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def run(start, end, chunk_days, prices_only, volumes_only):
    days = daterange(start, end)
    log.info(
        "backfill %s .. %s (%d days, %d-day chunks)",
        start,
        end,
        len(days),
        chunk_days,
    )
    began = time.time()
    done = 0
    wrote_p = wrote_v = 0
    for group in chunks(days, chunk_days):
        prices, volumes = {}, {}
        for date in group:
            try:
                rows = agmarknet.fetch_day(date)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                # Write what this chunk has before giving up, so the next run
                # resumes from further along.
                log.error("fetch failed on %s: %s: %s", date, type(e).__name__, e)
                _flush(prices, volumes, prices_only, volumes_only)
                return 1
            p, v = agmarknet.to_buckets_all(rows, date)
            prices.update(p)
            volumes.update(v)
            done += 1
            time.sleep(agmarknet.THROTTLE)
        pf, vf = _flush(prices, volumes, prices_only, volumes_only)
        wrote_p += pf
        wrote_v += vf
        rate = (time.time() - began) / max(done, 1)
        left = dt.timedelta(seconds=int(rate * (len(days) - done)))
        log.info(
            "through %s: %d/%d days, %d price + %d arrival records so far, ~%s left",
            group[-1],
            done,
            len(days),
            wrote_p,
            wrote_v,
            left,
        )
    log.info(
        "backfill done: %d price records, %d arrival records over %d days in %s",
        wrote_p,
        wrote_v,
        len(days),
        dt.timedelta(seconds=int(time.time() - began)),
    )
    return 0


def _flush(prices, volumes, prices_only, volumes_only):
    pr = vr = 0
    if prices and not volumes_only:
        _, pr = store.write_prices(prices, source_tag=agmarknet.SOURCE_TAG)
    if volumes and not prices_only:
        _, vr = store.write_volumes(volumes, source_tag=agmarknet.SOURCE_TAG)
    return pr, vr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD")
    ap.add_argument(
        "--chunk",
        type=int,
        default=10,
        help="days to gather before each write (default 10). Smaller loses less "
        "to a crash but rewrites the JSON files more often.",
    )
    ap.add_argument("--prices-only", action="store_true")
    ap.add_argument("--volumes-only", action="store_true")
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    if end < start:
        raise SystemExit("--to is before --from")
    # Anything inside the revision window is still filling in; leave those days
    # to the nightly run, which knows to come back and correct them.
    newest = dt.date.today() - dt.timedelta(days=agmarknet.LAG_DAYS)
    if end > newest:
        end = newest
        print(f"clamped --to to {end} (later days are not published yet)")

    setup_logging()
    return run(start, end, args.chunk, args.prices_only, args.volumes_only)


if __name__ == "__main__":
    raise SystemExit(main())
