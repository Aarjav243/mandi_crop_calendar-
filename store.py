"""Write revised price and arrival days into data/ and volume/.

Why not backfill_2026.merge()
-----------------------------
That one appends only dates strictly newer than a file's last record, and skips
files with no records at all. Both rules are wrong for a live feed:

  * Mandis file late, so the newest days on disk are provisional. A day pulled
    at one day old holds roughly three-quarters of its markets. Append-only
    means that three-quarters-empty day is frozen in place forever, because the
    next night's run sees the date is "already present" and moves on.
  * The skip-if-empty rule is what kept 186 price files and 401 volume files as
    permanent empty shells: a pair that has no history can never gain any.

So these writers merge by date -- fresh rows win, older dates survive -- and
treat an empty file as a file waiting for its first day. backfill_2026.merge()
is left alone; the historical backfills still want append-only semantics.
"""

import json
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
DATA_DIR = PROJECT_DIR / "data"
VOLUME_DIR = PROJECT_DIR / "volume"


def _merge_by_date(existing, fresh):
    """Fresh rows win per date; existing rows survive on dates fresh lacks."""
    by_date = {r["date"][:10]: r for r in existing}
    by_date.update({r["date"][:10]: r for r in fresh})
    return [by_date[d] for d in sorted(by_date)]


def _write(buckets, out_dir, source_tag, build_record):
    """Apply {(crop, state, date): value} to every file in out_dir.

    Returns (files_touched, records_written). Files with no incoming data are
    left exactly as they are, tag included, so a feed that quietly stops
    covering a crop shows up as a stale source tag rather than a silent
    restamp.
    """
    by_pair = {}
    for (crop, state, date), value in buckets.items():
        by_pair.setdefault((crop, state), {})[date] = value

    files = records = 0
    for path in sorted(Path(out_dir).glob("*.json")):
        doc = json.loads(path.read_text())
        new = by_pair.get((doc["crop"], doc["state"]))
        if not new:
            continue
        fresh = [build_record(date, value, doc) for date, value in sorted(new.items())]
        doc["records"] = _merge_by_date(doc["records"], fresh)
        doc["record_count"] = len(doc["records"])
        doc["to_date"] = doc["records"][-1]["date"][:10]
        # from_date describes where this pair's history begins. A live day is
        # always at the recent end, so it must never drag that backwards -- but
        # a file that was empty has no start yet, so give it one.
        if not doc.get("from_date"):
            doc["from_date"] = doc["records"][0]["date"][:10]
        doc["backfill_source"] = source_tag
        path.write_text(json.dumps(doc))
        files += 1
        records += len(fresh)
    return files, records


def _price_record(date, modal, doc):
    # min/max stay None: Agmarknet's dashboard publishes a single modal price
    # per market and nothing downstream reads min/max (seasonality.py uses
    # modal_price alone). Writing modal into all three would invent a spread
    # that does not exist.
    return {
        "date": f"{date}T00:00:00.000Z",
        "commodity_id": doc["commodity_id"],
        "census_state_id": doc["state_id"],
        "min_price": None,
        "max_price": None,
        "modal_price": modal,
        "census_district_id": None,
        "market_id": None,
    }


def _volume_record(date, quantity, doc):
    return {
        "date": f"{date}T00:00:00.000Z",
        "commodity_id": doc["commodity_id"],
        "census_state_id": doc["state_id"],
        "quantity": quantity,
    }


def write_prices(buckets, out_dir=DATA_DIR, source_tag="agmarknet"):
    return _write(buckets, out_dir, source_tag, _price_record)


def write_volumes(buckets, out_dir=VOLUME_DIR, source_tag="agmarknet"):
    return _write(buckets, out_dir, source_tag, _volume_record)
