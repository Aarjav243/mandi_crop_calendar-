"""Backfills 2021-2023 daily arrivals into volume/*.json from a verified
Kaggle mirror of the same Agmarknet data, instead of waiting on the CEDA API
for those years (see scrape_ceda_qty.py, which now only pulls 2024-2025).

Verified: summed this dataset's Wheat/Punjab arrivals for April 2022 and it
matched scrape_ceda_qty.py's CEDA pull for the same pair to the tonne.

SAFE BY DESIGN: this script only ever merges into a volume/<crop>__<state>.json
file that ALREADY EXISTS (created by scrape_ceda_qty.py). It never creates a
new file. scrape_ceda_qty.py's resumability check is "does this file already
exist", so if this script created files for pairs CEDA hasn't reached yet,
the live scraper would skip them and permanently miss their 2024-2025 data.
Re-run this script after scrape_ceda_qty.py finishes to catch every pair.

Needs the same Kaggle setup as the rest of this machine:
~/.kaggle/access_token (already configured; not touched here)
"""

import csv
import datetime
import json
import re
import subprocess
import zipfile
from pathlib import Path

import ceda_map
import crops

DATASET = "vandeetshah/india-commodity-wise-mandi-dataset"
RAW_DIR = Path(__file__).parent / "kaggle_raw"
OUT_DIR = Path(__file__).parent / "volume"
KAGGLE_FROM = datetime.date(2021, 1, 1)
KAGGLE_TO = datetime.date(2023, 12, 31)  # inclusive; CEDA covers 2024-01-01 on

# Our crop name -> the exact Kaggle CSV filename for that Agmarknet commodity.
# Coriander Seed has no CEDA mapping either (skipped project-wide already).
# Turmeric has no equivalent file in this dataset -- it keeps getting its
# full 2021-2025 range straight from CEDA (see FROM_DATE_OVERRIDE there).
CROP_TO_KAGGLE_FILE = {
    "Apple": "Apple.csv",
    "Bajra": "Bajra(Pearl Millet-Cumbu).csv",
    "Banana": "Banana.csv",
    "Barley": "Barley (Jau).csv",
    "Brinjal": "Brinjal.csv",
    "Cabbage": "Cabbage.csv",
    "Castor": "Castor Seed.csv",
    "Cauliflower": "Cauliflower.csv",
    "Coriander Leaf": "Coriander(Leaves).csv",
    "Cotton": "Cotton.csv",
    "Dry Chilli": "Dry Chillies.csv",
    "Garlic": "Garlic.csv",
    "Ginger": "Ginger(Green).csv",
    "Gram": "Bengal Gram(Gram)(Whole).csv",
    "Green Chilli": "Green Chilli.csv",
    "Groundnut": "Groundnut.csv",
    "Jowar": "Jowar(Sorghum).csv",
    "Maize": "Maize.csv",
    "Mango": "Mango.csv",
    "Masoor": "Lentil (Masur)(Whole).csv",
    "Moong": "Green Gram (Moong)(Whole).csv",
    "Mustard": "Mustard.csv",
    "Okra": "Bhindi(Ladies Finger).csv",
    "Onion": "Onion.csv",
    "Paddy": "Paddy(Dhan)(Common).csv",
    "Potato": "Potato.csv",
    "Sesamum": "Sesamum(SesameGingellyTil).csv",
    "Soybean": "Soyabean.csv",
    "Sugarcane": "Sugarcane.csv",
    "Sunflower": "Sunflower.csv",
    "Tomato": "Tomato.csv",
    "Tur/Arhar": "Arhar (Tur-Red Gram)(Whole).csv",
    "Urad": "Black Gram (Urd Beans)(Whole).csv",
    "Wheat": "Wheat.csv",
}

# Kaggle's raw State Name spellings that differ from crops.STATE_ZONE's keys.
STATE_ALIASES = {
    "NCT of Delhi": "Delhi",
    "Chattisgarh": "Chhattisgarh",
    "Uttrakhand": "Uttarakhand",
}


def safe_name(s):
    return s.replace(" ", "_").replace("/", "-")


def ensure_downloaded(filename):
    csv_path = RAW_DIR / filename
    if csv_path.exists():
        return csv_path
    RAW_DIR.mkdir(exist_ok=True)
    result = subprocess.run(
        [
            "kaggle",
            "datasets",
            "download",
            "-d",
            DATASET,
            "-f",
            filename,
            "-p",
            str(RAW_DIR),
            "--force",  # otherwise kaggle silently skips a stale leftover from
        ],  # a previous crashed run, and nothing gets (re)downloaded at all
        check=True,
        capture_output=True,
        text=True,
    )
    # kaggle isn't consistent about the on-disk name it downloads to: spaces
    # get %20-encoded, and small files sometimes skip zipping entirely. Read
    # the name back from kaggle's own "Downloading <name> to <path>" line
    # instead of guessing its rules (a plain before/after directory diff
    # breaks the moment a stale file from an earlier crash is already there).
    m = re.search(r"^Downloading (.+) to ", result.stdout + result.stderr, re.MULTILINE)
    assert m, (
        f"couldn't find the downloaded filename in kaggle's output for {filename!r}"
    )
    new_path = RAW_DIR / m.group(1)
    if new_path.suffix == ".zip":
        with zipfile.ZipFile(new_path) as z:
            z.extractall(RAW_DIR)
        new_path.unlink()
    else:
        new_path.rename(csv_path)
    return csv_path


# This dataset's per-commodity files aren't consistent about formatting --
# some use "27 Aug 2005", others "2005-08-24" (Apple.csv, throughout, not
# just old rows -- confirmed by sampling). Try both rather than assuming one.
DATE_FORMATS = ("%d %b %Y", "%Y-%m-%d")


def parse_date(s):
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def day_sums_by_state(csv_path):
    """{state_name: {date_str: total_arrivals}} for rows in [KAGGLE_FROM, KAGGLE_TO]."""
    out = {}
    with open(csv_path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        # Same inconsistency for the arrivals column: "Arrivals (Tonnes)" in
        # some files, plain "Arrivals" in others (Apple.csv, Banana.csv).
        arrivals_col = next(c for c in reader.fieldnames if c.startswith("Arrivals"))
        for row in reader:
            state = STATE_ALIASES.get(row["State Name"], row["State Name"])
            if state not in crops.STATE_ZONE:
                continue
            d = parse_date(row["Reported Date"])
            if d is None:
                continue
            if not (KAGGLE_FROM <= d <= KAGGLE_TO):
                continue
            qty = float(row[arrivals_col] or 0)
            out.setdefault(state, {}).setdefault(d.isoformat(), 0.0)
            out[state][d.isoformat()] += qty
    return out


def merge_into_file(out_path, crop, state, day_sums):
    data = json.loads(out_path.read_text())
    existing_dates = {r["date"][:10] for r in data["records"]}
    added = 0
    for date_str, qty in day_sums.items():
        if date_str in existing_dates:
            continue  # CEDA's own value wins on any overlap
        data["records"].append(
            {
                "date": f"{date_str}T00:00:00.000Z",
                "commodity_id": ceda_map.COMMODITY_ID.get(crop),
                "census_state_id": ceda_map.STATE_ID.get(state),
                "quantity": round(qty, 2),
            }
        )
        added += 1
    if added:
        data["records"].sort(key=lambda r: r["date"])
        data["record_count"] = len(data["records"])
        data["from_date"] = min(data["from_date"], KAGGLE_FROM.isoformat())
        data["kaggle_backfill"] = f"{DATASET} ({KAGGLE_FROM} to {KAGGLE_TO})"
        out_path.write_text(json.dumps(data))
    return added


def main():
    merged_pairs = pending_pairs = 0
    for crop, filename in CROP_TO_KAGGLE_FILE.items():
        csv_path = ensure_downloaded(filename)
        by_state = day_sums_by_state(csv_path)
        crop_merged = crop_pending = 0
        for state, day_sums in by_state.items():
            out_path = OUT_DIR / f"{safe_name(crop)}__{safe_name(state)}.json"
            if not out_path.exists():
                crop_pending += (
                    1  # CEDA hasn't reached this pair yet -- skip, don't create
                )
                continue
            added = merge_into_file(out_path, crop, state, day_sums)
            if added:
                crop_merged += 1
        merged_pairs += crop_merged
        pending_pairs += crop_pending
        print(
            f"{crop}: merged {crop_merged}, pending (no CEDA file yet) {crop_pending}"
        )
    print(
        f"\nDone. {merged_pairs} pairs backfilled with 2021-2023, {pending_pairs} pairs "
        f"still waiting on CEDA -- re-run this script after scrape_ceda_qty.py finishes."
    )


if __name__ == "__main__":
    main()
