"""Fill CEDA data gaps using the Kaggle mandi-price dataset.

Downloads individual CSV members out of a single large Kaggle zip archive via
HTTP Range requests (no need to download the whole 423MB zip), aggregates
market-level rows to one record per (crop, state, date) the same way CEDA
does (mean of min/max/modal prices across markets that day), and writes
output files in the same shape as scrape_ceda.py so Step 3 can consume either
source identically.

Resumable: skips any (crop, state) pair that already has a data/ file,
whether from CEDA or a previous Kaggle run.

Requires:
  - Kaggle token at ~/.kaggle/access_token
  - zip_tail.bin (last 4MB of the archive's central directory) already
    downloaded to the scratchpad dir -- this script does not re-fetch it.
"""

import csv
import io
import json
import struct
import sys
import zlib
from collections import defaultdict
from pathlib import Path
from urllib import request

import crops
import ceda_map

PROJECT_DIR = Path(__file__).parent
DATA_DIR = PROJECT_DIR / "data"
SCRATCH_DIR = Path(
    r"C:\Users\aarja\AppData\Local\Temp\claude\c--Users-aarja-OneDrive-Desktop-Well-Labs-10"
    r"\5d804d34-f525-4174-a4ff-ed6d7f599231\scratchpad"
)
ZIP_TAIL = SCRATCH_DIR / "zip_tail.bin"
TOKEN_PATH = Path.home() / ".kaggle" / "access_token"
DOWNLOAD_URL = (
    "https://www.kaggle.com/api/v1/datasets/download/"
    "vandeetshah/india-commodity-wise-mandi-dataset"
)

# crops.py name -> Kaggle CSV filename (without .csv). None means Kaggle has
# no matching file for this crop -- verified by inspecting all 325 filenames.
# Turmeric has no Kaggle file at all; that's fine, it's already 100% covered
# by CEDA (all 32 states already in data/).
KAGGLE_NAME = {
    "Apple": "Apple",
    "Bajra": "Bajra(Pearl Millet-Cumbu)",
    "Banana": "Banana",
    "Barley": "Barley (Jau)",
    "Brinjal": "Brinjal",
    "Cabbage": "Cabbage",
    "Castor": "Castor Seed",
    "Cauliflower": "Cauliflower",
    "Coriander Leaf": "Coriander(Leaves)",
    "Coriander Seed": "Corriander seed",  # bonus: not on CEDA at all
    "Cotton": "Cotton",
    "Dry Chilli": "Dry Chillies",
    "Garlic": "Garlic",
    "Ginger": "Ginger(Dry)",
    "Gram": "Bengal Gram(Gram)(Whole)",
    "Green Chilli": "Green Chilli",
    "Groundnut": "Groundnut",
    "Jowar": "Jowar(Sorghum)",
    "Maize": "Maize",
    "Mango": "Mango",
    "Masoor": "Lentil (Masur)(Whole)",
    "Moong": "Green Gram (Moong)(Whole)",
    "Mustard": "Mustard",
    "Okra": "Bhindi(Ladies Finger)",
    "Onion": "Onion",
    "Paddy": "Paddy(Dhan)(Common)",
    "Potato": "Potato",
    "Sesamum": "Sesamum(SesameGingellyTil)",
    "Soybean": "Soyabean",
    "Sugarcane": "Sugarcane",
    "Sunflower": "Sunflower",
    "Tomato": "Tomato",
    "Tur/Arhar": "Arhar (Tur-Red Gram)(Whole)",
    "Turmeric": None,  # not present in this Kaggle dataset; already covered by CEDA
    "Urad": "Black Gram (Urd Beans)(Whole)",
    "Wheat": "Wheat",
}

# Crops where blending all quality grades together fakes a seasonal price
# cycle that's really just the mix of grades shifting (proven via vcheck.py:
# blended vs frozen-grade-mix peak week diverged by 9 weeks for Barley, all
# other 34 crops matched within 0-2 weeks). For these, keep only the single
# most-reported grade instead of averaging every grade together.
DOMINANT_GRADE_ONLY = {"Barley"}

# Kaggle "State Name" -> our STATE_ID key. Kaggle spells a few states
# differently or uses different administrative boundaries than our 32-state
# CEDA-aligned list. States NOT in this map (Chandigarh, Andaman and Nicobar,
# Lakshadweep) have no home in our 32 states and their rows are dropped.
STATE_NAME_FIX = {
    "Chattisgarh": "Chhattisgarh",  # Kaggle typo
    "Uttrakhand": "Uttarakhand",  # Kaggle typo
    "NCT of Delhi": "Delhi",
    "Pondicherry": "Puducherry",  # Kaggle uses the old name in some crop files
    # identity entries for states that already match crops.STATE_ZONE spelling
    # are not needed -- only mismatches go here.
}

MONTHS = {
    m: i
    for i, m in enumerate(
        "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), start=1
    )
}


def safe_name(s):
    return s.replace(" ", "_").replace("/", "-")


def parse_central_directory():
    """Parse zip_tail.bin -> {filename: (local_header_offset, compressed_size, uncompressed_size)}."""
    data = ZIP_TAIL.read_bytes()
    entries = {}
    i = 0
    sig = b"PK\x01\x02"
    while True:
        i = data.find(sig, i)
        if i == -1:
            break
        (
            _sig,
            ver_made_by,
            ver_needed,
            flags,
            method,
            mod_time,
            mod_date,
            crc32,
            comp_size,
            uncomp_size,
            fname_len,
            extra_len,
            comment_len,
            disk_start,
            int_attrs,
            ext_attrs,
            local_header_offset,
        ) = struct.unpack("<4s6H3L5H2L", data[i : i + 46])

        name_start = i + 46
        name = data[name_start : name_start + fname_len].decode("utf-8", "replace")
        extra_start = name_start + fname_len
        extra = data[extra_start : extra_start + extra_len]

        z_uncomp, z_comp, z_offset = uncomp_size, comp_size, local_header_offset
        ep = 0
        while ep + 4 <= len(extra):
            tag, sz = struct.unpack("<HH", extra[ep : ep + 4])
            block = extra[ep + 4 : ep + 4 + sz]
            if tag == 0x0001:
                bp = 0
                if uncomp_size == 0xFFFFFFFF:
                    z_uncomp = struct.unpack("<Q", block[bp : bp + 8])[0]
                    bp += 8
                if comp_size == 0xFFFFFFFF:
                    z_comp = struct.unpack("<Q", block[bp : bp + 8])[0]
                    bp += 8
                if local_header_offset == 0xFFFFFFFF:
                    z_offset = struct.unpack("<Q", block[bp : bp + 8])[0]
                    bp += 8
            ep += 4 + sz

        entries[name] = (z_offset, z_comp, z_uncomp)
        i = name_start + fname_len + extra_len + comment_len
    return entries


def load_token():
    token = TOKEN_PATH.read_text().strip()
    if not token:
        raise SystemExit(f"Empty token at {TOKEN_PATH}")
    return token


def download_range(token, start, end):
    """Range GET [start, end] inclusive."""
    req = request.Request(
        DOWNLOAD_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Range": f"bytes={start}-{end}",
        },
    )
    with request.urlopen(req, timeout=120) as resp:
        if resp.status not in (200, 206):
            raise SystemExit(f"Unexpected status {resp.status} for range {start}-{end}")
        return resp.read()


def extract_csv_text(raw_slice):
    """raw_slice starts at the LOCAL file header. Skip header, inflate raw deflate."""
    fname_len, extra_len = struct.unpack("<HH", raw_slice[26:30])
    start = 30 + fname_len + extra_len
    d = zlib.decompressobj(-15)
    out = d.decompress(raw_slice[start:])
    out += d.flush()
    return out.decode("utf-8", "replace")


def parse_date(s):
    # Most files use "09 Feb 2006"; some (Apple, Bajra, Cabbage, Tur/Arhar,
    # ...) are already ISO "2006-02-09" -- same archive, no consistent format.
    if "-" in s:
        year, mon, day = s.split("-")
        return f"{int(year):04d}-{int(mon):02d}-{int(day):02d}"
    day, mon, year = s.split()
    return f"{year}-{MONTHS[mon]:02d}-{int(day):02d}"


def aggregate_rows(csv_text, commodity_id, dominant_grade_only=False):
    """Group rows by (fixed_state, date), mean the three price columns.

    If dominant_grade_only, rows are first filtered down to only the single
    most-reported "Variety" grade in the whole file -- see DOMINANT_GRADE_ONLY.

    Returns dict: state -> list of records (sorted by date), plus counters
    for dropped/unmapped state names and unparseable rows.
    """
    reader = csv.DictReader(io.StringIO(csv_text))

    # Column names aren't consistent across the 325 CSVs in this archive --
    # most use "Min Price (Rs./Quintal)" but some (Mustard, Banana, Apple,
    # Cabbage, Paddy, Tur/Arhar) use bare "Min Price". Resolve the real
    # header once instead of hardcoding one variant, or every row in a
    # short-header file silently becomes an uncounted "bad row".
    def resolve(*candidates):
        for c in candidates:
            if c in reader.fieldnames:
                return c
        raise KeyError(f"none of {candidates} in {reader.fieldnames}")

    col_min = resolve("Min Price (Rs./Quintal)", "Min Price")
    col_max = resolve("Max Price (Rs./Quintal)", "Max Price")
    col_modal = resolve("Modal Price (Rs./Quintal)", "Modal Price")

    all_rows = list(reader)
    keep_grade = None
    if dominant_grade_only:
        counts = defaultdict(int)
        for row in all_rows:
            counts[(row.get("Variety") or "").strip()] += 1
        keep_grade = max(counts, key=counts.get)

    # (state, date) -> [min_prices, max_prices, modal_prices]
    buckets = defaultdict(lambda: ([], [], []))
    dropped_states = defaultdict(int)
    bad_rows = 0

    for row in all_rows:
        if keep_grade is not None and (row.get("Variety") or "").strip() != keep_grade:
            continue
        raw_state = row.get("State Name", "").strip()
        state = STATE_NAME_FIX.get(raw_state, raw_state)
        if state not in ceda_map.STATE_ID:
            dropped_states[raw_state] += 1
            continue
        try:
            date = parse_date(row["Reported Date"].strip())
            mn = float(row[col_min])
            mx = float(row[col_max])
            md = float(row[col_modal])
        except (ValueError, KeyError, TypeError, AttributeError):
            # malformed/short CSV row (e.g. trailing blank line, missing
            # field) -- csv.DictReader fills missing trailing fields with
            # None rather than raising.
            bad_rows += 1
            continue
        # Zero prices are missing-data placeholders in this dataset, not real
        # zero-rupee mandi prices -- drop them so they don't distort the mean.
        if mn <= 0 or mx <= 0 or md <= 0:
            bad_rows += 1
            continue
        mins, maxs, mods = buckets[(state, date)]
        mins.append(mn)
        maxs.append(mx)
        mods.append(md)

    by_state = defaultdict(list)
    for (state, date), (mins, maxs, mods) in buckets.items():
        by_state[state].append(
            {
                "date": f"{date}T00:00:00.000Z",
                "commodity_id": commodity_id,
                "census_state_id": ceda_map.STATE_ID[state],
                "census_district_id": None,
                "market_id": None,
                "min_price": sum(mins) / len(mins),
                "max_price": sum(maxs) / len(maxs),
                "modal_price": sum(mods) / len(mods),
                "_sort": date,
            }
        )

    for state in by_state:
        by_state[state].sort(key=lambda r: r["_sort"])
        for r in by_state[state]:
            del r["_sort"]

    return by_state, dropped_states, bad_rows


def main():
    crop_filter = None
    for arg in sys.argv[1:]:
        if arg.startswith("--crops="):
            crop_filter = set(arg.split("=", 1)[1].split(","))
    target_crops = crop_filter if crop_filter else crops.CROPS.keys()

    if not ZIP_TAIL.exists():
        raise SystemExit(
            f"Missing {ZIP_TAIL} -- fetch the zip's central directory first."
        )

    central_dir = parse_central_directory()
    print(f"Central directory: {len(central_dir)} files in the Kaggle zip")

    token = load_token()
    DATA_DIR.mkdir(exist_ok=True)

    all_dropped_states = defaultdict(int)
    total_written = 0
    total_skipped_existing = 0

    for crop in target_crops:
        kaggle_name = KAGGLE_NAME.get(crop)
        commodity_id = ceda_map.COMMODITY_ID.get(crop)

        if kaggle_name is None:
            print(f"{crop}: no Kaggle file mapped, skipping")
            continue
        csv_filename = f"{kaggle_name}.csv"
        if csv_filename not in central_dir:
            print(f"{crop}: mapped name {csv_filename!r} not found in zip! skipping")
            continue

        # Check which states are already missing a file for this crop --
        # if all 32 already exist (e.g. from CEDA), skip the whole download.
        needed_states = [
            s
            for s in crops.STATE_ZONE
            if not (DATA_DIR / f"{safe_name(crop)}__{safe_name(s)}.json").exists()
        ]
        if not needed_states:
            print(
                f"{crop}: all {len(crops.STATE_ZONE)} state files already exist, skipping download"
            )
            total_skipped_existing += len(crops.STATE_ZONE)
            continue

        offset, comp_size, uncomp_size = central_dir[csv_filename]
        print(
            f"{crop}: downloading {csv_filename} "
            f"({comp_size:,} compressed bytes, {len(needed_states)} states needed)"
        )
        raw = download_range(token, offset, offset + comp_size - 1)
        csv_text = extract_csv_text(raw)

        by_state, dropped, bad_rows = aggregate_rows(
            csv_text, commodity_id, dominant_grade_only=crop in DOMINANT_GRADE_ONLY
        )
        for s, n in dropped.items():
            all_dropped_states[s] += n
        if bad_rows:
            print(f"  {bad_rows} rows dropped (unparseable date/price or zero price)")

        for state in needed_states:
            records = by_state.get(state, [])
            out_path = DATA_DIR / f"{safe_name(crop)}__{safe_name(state)}.json"
            if not records:
                print(
                    f"  {state}: 0 records in Kaggle data, skipping (no file written)"
                )
                continue
            from_date = records[0]["date"][:10]
            to_date = records[-1]["date"][:10]
            out_path.write_text(
                json.dumps(
                    {
                        "crop": crop,
                        "state": state,
                        "commodity_id": commodity_id,
                        "state_id": ceda_map.STATE_ID[state],
                        "from_date": from_date,
                        "to_date": to_date,
                        "record_count": len(records),
                        "records": records,
                        "source": "kaggle",
                    }
                )
            )
            total_written += 1
            print(f"  {state}: {len(records)} records ({from_date} to {to_date})")

    print(
        f"\nDone. {total_written} files written, {total_skipped_existing} already had files."
    )
    if all_dropped_states:
        print(
            "\nState names present in Kaggle data but NOT in our 32 STATE_ID keys (rows dropped):"
        )
        for s, n in sorted(all_dropped_states.items(), key=lambda kv: -kv[1]):
            print(f"  {s!r}: {n} rows")


if __name__ == "__main__":
    main()
