"""Extend data/*.json past the CEDA/Kaggle cutoff (2025-10-30) using the
sagar2522/indian-local-market-crop-price CSVs, which run to 2026-06-10.

Same source vocabulary as scrape_kaggle.py's archive (Agmarknet names) but
Title-Cased with different spacing, so crop names are matched by normalising
KAGGLE_NAME rather than maintaining a second map. Aggregation matches
aggregate_rows(): mean of min/max/modal across all markets per (state, date).

Appends only dates strictly after each file's existing to_date, so it is
idempotent and never rewrites CEDA history.
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

import ceda_map
import crops
from scrape_kaggle import DOMINANT_GRADE_ONLY, KAGGLE_NAME, STATE_NAME_FIX

DATA_DIR = Path(__file__).parent / "data"
SCRATCH = Path(
    r"C:\Users\aarja\AppData\Local\Temp\claude\c--Users-aarja-OneDrive-Desktop-Well-Labs-10"
    r"\1ed974fc-9087-4105-a9c5-1be5e7075045\scratchpad"
)
CSVS = [SCRATCH / "crop_prices_2025.csv", SCRATCH / "crop_prices_2026.csv"]
COLS = [
    "arrival_date",
    "state",
    "commodity",
    "variety",
    "min_price",
    "max_price",
    "modal_price",
]

# This source splits Paddy by variety into separate commodities; KAGGLE_NAME's
# "Paddy(Dhan)(Common)" has no counterpart. Common is the food-grain series.
NAME_OVERRIDE = {"Paddy": "Paddy(Common)"}
# Kerala is spelt "Keralam" here; STATE_NAME_FIX covers the rest of the gaps.
EXTRA_STATE_FIX = {"Keralam": "Kerala"}

# data.gov.in's live feed spells nine crops differently again -- Kaggle's export
# replaced "/" with "-" and added spaces before brackets, so asking the API for
# the Kaggle spelling silently returns zero rows. Verified against the live
# resource one crop at a time; the other 27 match once norm() strips punctuation.
# Only names that differ are listed. Keep in sync by re-running --demo.
AGMARK_NAME = {
    "Bajra": "Bajra(Pearl Millet/Cumbu)",
    "Barley": "Barley(Jau)",
    "Masoor": "Lentil(Masur)(Whole)",
    "Sesamum": "Sesamum(Sesame,Gingelly,Til)",
    "Tur/Arhar": "Red gram/Arhar/Tur(whole)",
    "Moong": "Green Gram(Moong)(Whole)",
    "Urad": "Black Gram(Urd Beans)(Whole)",
    "Sunflower": "Sunflower/Sunflower Seed",
}


def agmark_name(crop):
    """The live feed's own spelling for one of our crops."""
    return (
        AGMARK_NAME.get(crop)
        or NAME_OVERRIDE.get(crop)
        or KAGGLE_NAME.get(crop)
        or crop
    )


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def build_lookups():
    """crop/state lookups keyed on normalised source spellings."""
    crop_by_norm = {}
    for crop in crops.CROPS:
        name = NAME_OVERRIDE.get(crop) or KAGGLE_NAME.get(crop) or crop
        crop_by_norm[norm(name)] = crop
        crop_by_norm.setdefault(norm(crop), crop)
    for crop, name in AGMARK_NAME.items():
        crop_by_norm[norm(name)] = crop
    state_by_norm = {norm(s): s for s in ceda_map.STATE_ID}
    for wrong, right in {**STATE_NAME_FIX, **EXTRA_STATE_FIX}.items():
        if right in ceda_map.STATE_ID:
            state_by_norm[norm(wrong)] = right
    return crop_by_norm, state_by_norm


def _scan(cutoff, crop_by_norm, state_by_norm, only_variety=None, only_crops=None):
    """Yield (crop, state, date, min, max, modal) for usable rows after cutoff."""
    for path in CSVS:
        if not path.exists():
            raise SystemExit(f"missing {path}")
        for chunk in pd.read_csv(path, usecols=COLS, chunksize=500_000):
            chunk = chunk[chunk.arrival_date.astype(str).str[:10] > cutoff]
            if only_variety is not None:
                chunk = chunk[chunk.variety.astype(str) == only_variety]
            if chunk.empty:
                continue
            chunk = chunk.assign(
                xcrop=chunk.commodity.map(lambda c: crop_by_norm.get(norm(str(c)))),
                xstate=chunk.state.map(lambda s: state_by_norm.get(norm(str(s)))),
            ).dropna(subset=["xcrop", "xstate"])
            if only_crops is not None:
                chunk = chunk[chunk.xcrop.isin(only_crops)]
            # Zero/negative prices are missing-data placeholders, not real prices.
            chunk = chunk[
                (chunk.min_price > 0) & (chunk.max_price > 0) & (chunk.modal_price > 0)
            ]
            for row in chunk.itertuples(index=False):
                yield (
                    row.xcrop,
                    row.xstate,
                    str(row.arrival_date)[:10],
                    row.min_price,
                    row.max_price,
                    row.modal_price,
                    str(row.variety),
                )


def collect(cutoff):
    """(crop, state, date) -> ([min], [max], [modal]) for dates after cutoff."""
    crop_by_norm, state_by_norm = build_lookups()
    buckets = defaultdict(lambda: ([], [], []))
    grade_counts = defaultdict(int)

    for crop, state, date, mn, mx, md, variety in _scan(
        cutoff, crop_by_norm, state_by_norm
    ):
        if crop in DOMINANT_GRADE_ONLY:
            grade_counts[variety] += 1
            continue  # re-read below, restricted to the dominant grade
        mins, maxs, mods = buckets[(crop, state, date)]
        mins.append(mn)
        maxs.append(mx)
        mods.append(md)

    # ponytail: dominant grade is recomputed over the backfill window only --
    # scrape_kaggle.py picked it over 20 years and never recorded its choice, so
    # a grade flip would seam Barley. Printed so it can be eyeballed against the
    # existing history; hardcode the grade here if it ever disagrees.
    if grade_counts:
        keep = max(grade_counts, key=grade_counts.get)
        print(
            f"Barley dominant variety: {keep!r} "
            f"({grade_counts[keep]}/{sum(grade_counts.values())} rows)"
        )
        for crop, state, date, mn, mx, md, _ in _scan(
            cutoff,
            crop_by_norm,
            state_by_norm,
            only_variety=keep,
            only_crops=DOMINANT_GRADE_ONLY,
        ):
            mins, maxs, mods = buckets[(crop, state, date)]
            mins.append(mn)
            maxs.append(mx)
            mods.append(md)
    return buckets


def last_date(doc):
    return doc["records"][-1]["date"][:10] if doc["records"] else None


def mean_by_pair(buckets):
    """{(crop, state, date): ([min],[max],[modal])} -> {(crop, state): {date: means}}"""
    by_pair = defaultdict(dict)
    for (crop, state, date), (mins, maxs, mods) in buckets.items():
        by_pair[(crop, state)][date] = (
            sum(mins) / len(mins),
            sum(maxs) / len(maxs),
            sum(mods) / len(mods),
        )
    return by_pair


def merge(by_pair, source_tag):
    """Append dates newer than each file's last record. Returns (files, records).

    Shared by backfill_2026 and daily_update -- both append the same record
    shape and both must skip dates already present, so this lives in one place.
    """
    updated = added = 0
    for path in sorted(DATA_DIR.glob("*.json")):
        doc = json.loads(path.read_text())
        new = by_pair.get((doc["crop"], doc["state"]))
        have = last_date(doc)
        if not new or not have:
            continue
        fresh = sorted(d for d in new if d > have)
        if not fresh:
            continue
        for date in fresh:
            mn, mx, md = new[date]
            doc["records"].append(
                {
                    "date": f"{date}T00:00:00.000Z",
                    "commodity_id": doc["commodity_id"],
                    "census_state_id": doc["state_id"],
                    "min_price": mn,
                    "max_price": mx,
                    "modal_price": md,
                    "census_district_id": None,
                    "market_id": None,
                }
            )
        doc["to_date"] = fresh[-1]
        doc["record_count"] = len(doc["records"])
        doc["backfill_source"] = source_tag
        path.write_text(json.dumps(doc))
        updated += 1
        added += len(fresh)
    return updated, added


def main():
    files = sorted(DATA_DIR.glob("*.json"))
    if not files:
        raise SystemExit(f"no data files in {DATA_DIR}")
    # scrape_ceda.py writes to_date as the *requested* end date (2025-12-31),
    # not the last date actually returned, so trust the records instead or the
    # last two months of real data get skipped as already-present.
    cutoff = min(last_date(json.loads(f.read_text())) or "9999" for f in files)
    print(f"Reading CSVs for dates after {cutoff} ...")
    buckets = collect(cutoff)
    print(f"{len(buckets):,} (crop, state, date) groups in window")
    updated, added = merge(
        mean_by_pair(buckets), "kaggle:sagar2522/indian-local-market-crop-price"
    )
    print(f"Updated {updated} files, appended {added:,} records")


def demo():
    crop_by_norm, state_by_norm = build_lookups()
    assert crop_by_norm[norm("Arhar(Tur/Red Gram)(Whole)")] == "Tur/Arhar"
    assert crop_by_norm[norm("Paddy(Common)")] == "Paddy"
    assert crop_by_norm[norm("Bhindi(Ladies Finger)")] == "Okra"
    assert crop_by_norm[norm("Sesamum(Sesame,Gingelly,Til)")] == "Sesamum"
    assert state_by_norm[norm("Keralam")] == "Kerala"
    assert state_by_norm[norm("Chattisgarh")] == "Chhattisgarh"
    assert state_by_norm[norm("Nct Of Delhi")] == "Delhi"
    assert norm("Lentil (Masur)(Whole)") == norm("Lentil(Masur)(Whole)")
    # the live feed's spellings must resolve too, or a day's rows land as zero
    assert crop_by_norm[norm("Red gram/Arhar/Tur(whole)")] == "Tur/Arhar"
    assert crop_by_norm[norm("Sunflower/Sunflower Seed")] == "Sunflower"
    assert crop_by_norm[norm("Bajra(Pearl Millet/Cumbu)")] == "Bajra"
    assert agmark_name("Castor") == "Castor Seed"  # unchanged, verified live
    assert agmark_name("Onion") == "Onion"
    assert {crop_by_norm[norm(agmark_name(c))] for c in crops.CROPS} == set(crops.CROPS)
    # every crop resolves back to itself, and no two crops collide
    resolved = {
        crop_by_norm[norm(NAME_OVERRIDE.get(c) or KAGGLE_NAME.get(c) or c)]
        for c in crops.CROPS
    }
    assert resolved == set(crops.CROPS), resolved ^ set(crops.CROPS)
    print("demo ok")


if __name__ == "__main__":
    (demo if "--demo" in sys.argv else main)()
