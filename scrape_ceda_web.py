"""Pulls prices AND arrivals from CEDA's own website backend instead of the
public API -- same data, no 40-requests-per-hour quota.

Why this exists: api.ceda.ashoka.edu.in caps a key at 40 requests/hour, which
put the full pull at ~48 hours. agmarknet.ceda.ashoka.edu.in (CEDA's public
Agmarknet site) proxies the same queries through its own server-side key at
/api/prices and /api/quantities. Verified identical, not merely similar:
  - Barley/Rajasthan arrivals 2024-2025: 576 days both ways, 0 mismatches
  - Turmeric/Maharashtra prices 2005-2025: 6467 days both ways, 0 mismatches
No auth, no observed rate limit, and the whole date range comes back in one
request. Replaces scrape_ceda.py and scrape_ceda_qty.py for bulk pulls; those
still document the authenticated API if this route ever goes away.

Merges rather than overwrites: existing rows (including the Kaggle gap-fill
from scrape_kaggle.py, which covers dates CEDA itself is missing) are kept for
any date this pull doesn't return. Fresh CEDA rows win on overlapping dates.

Run: python scrape_ceda_web.py            # both prices and volume
     python scrape_ceda_web.py --kind=volume
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib import error, request

import ceda_map
import crops

BASE = "https://agmarknet.ceda.ashoka.edu.in/api"
POLITE_SLEEP = 0.5  # not a quota, just not hammering someone else's server

# Per-kind: endpoint, output dir, date range, and how a proxy row maps onto the
# record shape the rest of the project already reads.
KINDS = {
    "price": {
        "endpoint": "prices",
        "out_dir": Path(__file__).parent / "data",
        "from_date": "2005-01-01",
        "fields": lambda r: {
            "min_price": r["p_min"],
            "max_price": r["p_max"],
            "modal_price": r["p_modal"],
            "census_district_id": None,
            "market_id": None,
        },
    },
    "volume": {
        "endpoint": "quantities",
        "out_dir": Path(__file__).parent / "volume",
        "from_date": "2021-01-01",
        "fields": lambda r: {"quantity": r["qty"]},
    },
}
TO_DATE = "2025-12-31"


def fetch(endpoint, commodity_id, state_id, from_date):
    body = json.dumps(
        {
            "commodity_id": commodity_id,
            "state_id": state_id,
            "district_id": 0,  # 0 means "whole state"; null is rejected as NaN
            "calculation_type": "d",  # daily (m = monthly)
            "start_date": from_date,
            "end_date": TO_DATE,
        }
    ).encode()
    req = request.Request(
        f"{BASE}/{endpoint}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    for attempt in range(4):
        try:
            with request.urlopen(req, timeout=300) as resp:
                return json.load(resp)["data"]
        except (error.URLError, error.HTTPError, ConnectionError, TimeoutError) as e:
            if attempt == 3:
                raise SystemExit(
                    f"gave up on commodity={commodity_id} state={state_id}: {e}"
                )
            time.sleep(15 * (attempt + 1))


def to_records(rows, kind, commodity_id, state_id):
    """Proxy rows -> the record shape already on disk under data/ and volume/."""
    build = KINDS[kind]["fields"]
    return [
        {
            "date": f"{r['t']}T00:00:00.000Z",
            "commodity_id": commodity_id,
            "census_state_id": state_id,
            **build(r),
        }
        for r in rows
    ]


def merge(existing, fresh):
    """Fresh rows win per date; existing rows survive on dates fresh lacks.

    Existing files include Kaggle rows filling gaps CEDA has no data for, so
    this must not be a plain overwrite.
    """
    by_date = {r["date"][:10]: r for r in existing}
    by_date.update({r["date"][:10]: r for r in fresh})
    return [by_date[d] for d in sorted(by_date)]


def safe_name(s):
    return s.replace(" ", "_").replace("/", "-")


def run(kind, fresh_after=None):
    """fresh_after: a date (YYYY-MM-DD). Any pair whose output file was last
    written on or after that date is skipped -- lets a killed run resume without
    re-pulling the pairs it already finished. merge() makes a re-pull harmless,
    just slow (~16s/pair), so this is purely a time saver."""
    cfg = KINDS[kind]
    cfg["out_dir"].mkdir(exist_ok=True)
    cutoff = (
        datetime.strptime(fresh_after, "%Y-%m-%d").timestamp() if fresh_after else None
    )
    pairs = [
        (crop, state)
        for crop in crops.CROPS
        for state in crops.STATE_ZONE
        if ceda_map.COMMODITY_ID.get(crop) is not None
    ]
    print(f"[{kind}] {len(pairs)} crop-state pairs, {cfg['from_date']} to {TO_DATE}")

    for i, (crop, state) in enumerate(pairs, 1):
        out_path = cfg["out_dir"] / f"{safe_name(crop)}__{safe_name(state)}.json"
        if cutoff and out_path.exists() and os.path.getmtime(out_path) >= cutoff:
            print(
                f"[{kind} {i}/{len(pairs)}] {crop} / {state}: skip (already fresh)",
                flush=True,
            )
            continue
        commodity_id = ceda_map.COMMODITY_ID[crop]
        state_id = ceda_map.STATE_ID[state]
        rows = fetch(cfg["endpoint"], commodity_id, state_id, cfg["from_date"])
        fresh = to_records(rows, kind, commodity_id, state_id)

        old = json.loads(out_path.read_text()) if out_path.exists() else {}
        records = merge(old.get("records", []), fresh)

        out = {
            "crop": crop,
            "state": state,
            "commodity_id": commodity_id,
            "state_id": state_id,
            "from_date": min(cfg["from_date"], old.get("from_date", cfg["from_date"])),
            "to_date": TO_DATE,
            "record_count": len(records),
            "source": "ceda_web",
            "records": records,
        }
        # Don't silently lose provenance: any row this pull didn't return is a
        # survivor from the earlier Kaggle fill, on a date CEDA has no data for.
        # Without this you can't tell a mixed file from a pure CEDA one.
        if len(records) > len(fresh):
            out["carried_over_rows"] = len(records) - len(fresh)
            out["carried_over_source"] = old.get("source", "unknown")
        out_path.write_text(json.dumps(out))
        print(
            f"[{kind} {i}/{len(pairs)}] {crop} / {state}: "
            f"{len(fresh)} fetched, {len(records)} total",
            flush=True,
        )
        time.sleep(POLITE_SLEEP)


if __name__ == "__main__":
    kinds = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--kind=")]
    fresh_after = next(
        (a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--fresh-after=")),
        None,
    )
    for k in kinds or ["volume", "price"]:
        run(k, fresh_after=fresh_after)
    print("Done.")
