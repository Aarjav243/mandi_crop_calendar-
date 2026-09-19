"""Fill whatever arrivals days are still missing after run_gap_chain.py.

run_gap_chain.py works from a list of months written by hand, which is fine
until a range dies partway. The 2026-06-12..2026-09-08 range got a 429 at
2026-08-22 and took 2026-08-22..2026-09-08 down with it -- days no queued
month covers, so nothing would ever have gone back for them.

So this measures instead of assuming: it asks the volume files which days are
actually absent, fills those, then measures again and says what is left. Run
it as often as you like; on a complete dataset it does nothing.
"""

import datetime as dt
import glob
import os
import subprocess
import sys
import time
from pathlib import Path

import seasonality

HERE = Path(__file__).parent
CHAIN_LOG = HERE / "backfill_chain.out"
CHAIN_DONE = "ALL QUEUED MONTHS DONE"
FIRST, LAST = dt.date(2025, 11, 11), dt.date(2026, 9, 10)
MAX_RUN = 21  # days per backfill call; a 429 then costs three weeks, not three months


def missing_days(first=FIRST, last=LAST):
    """Days in [first, last] that no volume file has a row for."""
    have = set()
    for f in glob.glob(str(HERE / "volume" / "*.json")):
        base = os.path.basename(f)[:-5]
        if "__" not in base:
            continue
        crop, state = base.split("__", 1)
        for d, _ in seasonality.load_daily_volume_series(crop, state):
            have.add(d)
    n = (last - first).days + 1
    return [d for d in (first + dt.timedelta(days=i) for i in range(n)) if d not in have]


def to_ranges(days, cap=MAX_RUN):
    """Contiguous runs of `days`, chopped so no single call spans too much."""
    out, start, prev = [], None, None
    for d in days:
        if start is None:
            start, prev = d, d
        elif (d - prev).days == 1 and (d - start).days + 1 < cap:
            prev = d
        else:
            out.append((start, prev))
            start, prev = d, d
    if start is not None:
        out.append((start, prev))
    return out


def wait_for_chain():
    """Block while run_gap_chain.py is still going, so we never write concurrently."""
    if not CHAIN_LOG.exists():
        return
    while CHAIN_DONE not in CHAIN_LOG.read_text(encoding="utf-8", errors="replace"):
        time.sleep(60)


def main():
    args = sys.argv[1:]
    if "--no-wait" not in args:
        wait_for_chain()
    # Agmarknet rate-limits a client that has been pulling hard for an hour,
    # and once it does, retrying inside the same window just keeps the limit
    # alive. Resting before the first call is the difference between starting
    # clean and spending the evening collecting 429s.
    if "--rest" in args:
        mins = int(args[args.index("--rest") + 1])
        print(f"{dt.datetime.now():%H:%M:%S} resting {mins} min to clear the rate limit",
              flush=True)
        time.sleep(mins * 60)
    print(f"{dt.datetime.now():%H:%M:%S} measuring gaps", flush=True)

    for round_no in (1, 2):
        gaps = to_ranges(missing_days())
        if not gaps:
            print("no arrivals days missing", flush=True)
            break
        print(f"--- round {round_no}: {len(gaps)} range(s) to fill ---", flush=True)
        for a, b in gaps:
            print(f"=== {a} .. {b} ===", flush=True)
            rc = subprocess.call(
                [sys.executable, "-u", str(HERE / "backfill_agmarknet.py"),
                 "--from", a.isoformat(), "--to", b.isoformat(), "--chunk", "10"],
                cwd=str(HERE),
            )
            print(f"=== {a} .. {b} exit={rc} ===", flush=True)
            if rc != 0:
                # Almost always a rate limit. agmarknet.py now backs off for
                # minutes on a 429, but if it still gave up, give the API a
                # proper rest before the next range rather than piling on.
                print("failed; resting 10 minutes before the next range", flush=True)
                time.sleep(600)

    left = missing_days()
    print("GAP FILL DONE", flush=True)
    print(f"still missing: {len(left)} days"
          + (f" ({left[0]} .. {left[-1]})" if left else ""), flush=True)


if __name__ == "__main__":
    main()
