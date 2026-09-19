"""Pulls the last 5 years of daily mandi ARRIVALS (volume) from the CEDA
Agmarknet API -- the /agmarknet/quantities sibling of scrape_ceda.py.

Same shape as the price scraper: one POST per crop-state pair, state level,
resumable (one file per pair under volume/; rerun after a crash and it skips
what's already there). Delete a single file to force a re-pull of that pair.

Date range is per-crop -- see FROM_DATE_DEFAULT / FROM_DATE_OVERRIDE below.
Most crops only need 2024-2025 here because 2021-2023 comes from a Kaggle
mirror instead (build_volume_from_kaggle.py).

Needs the same API key as scrape_ceda.py:
~/.config/wellabs/ceda_api_key.txt
"""

import json
import sys
import time
from pathlib import Path
from urllib import error, request

import ceda_map
import crops

API_BASE = "https://api.ceda.ashoka.edu.in/v1"
KEY_PATH = Path.home() / ".config" / "wellabs" / "ceda_api_key.txt"
OUT_DIR = Path(__file__).parent / "volume"
TO_DATE = "2025-12-31"
# 2021-2023 now comes from a verified Kaggle mirror of the same Agmarknet
# arrivals data instead (see build_volume_from_kaggle.py) -- cross-checked
# against a CEDA pull for Wheat/Punjab and it matched to the tonne. So CEDA
# only needs to cover the years Kaggle doesn't: 2024-2025. Turmeric has no
# equivalent file in that Kaggle dataset, so it alone still needs the full
# range from CEDA.
FROM_DATE_DEFAULT = "2024-01-01"
FROM_DATE_OVERRIDE = {"Turmeric": "2021-01-01"}
RATE_LIMIT_SLEEP = 1.5  # seconds between requests; 429 backs off further
# ponytail: tried 6 parallel workers to speed this up -- CEDA rate-limits per
# key regardless of concurrency, so parallel requests just all 429 together
# and each eats a minutes-long backoff. Serial is the actually-faster option.


def load_api_key():
    if not KEY_PATH.exists():
        raise SystemExit(
            f"No API key at {KEY_PATH}. See HANDOFF.md for how to get one."
        )
    return KEY_PATH.read_text().strip()


def fetch_quantities(api_key, commodity_id, state_id, from_date, to_date):
    body = json.dumps(
        {
            "commodity_id": commodity_id,
            "state_id": state_id,
            "from_date": from_date,
            "to_date": to_date,
        }
    ).encode()
    req = request.Request(
        f"{API_BASE}/agmarknet/quantities",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    # Same deliberately long 429 backoff as the price scraper: CEDA's limit
    # took well over 10 minutes to reset, so a long unattended run must wait
    # it out rather than die. 1+2+5+10+20+30 min = ~68 min of patience.
    RETRY_WAITS_429 = [60, 120, 300, 600, 1200, 1800]
    last_error = None
    for attempt in range(len(RETRY_WAITS_429)):
        try:
            with request.urlopen(req, timeout=90) as resp:
                payload = json.load(resp)
                return payload["output"]["data"]
        except error.HTTPError as e:
            if e.code == 429:
                wait = RETRY_WAITS_429[attempt]
                last_error = "429 rate limited"
                print(f"    rate limited, waiting {wait // 60}m before retry")
                time.sleep(wait)
                continue
            raise SystemExit(
                f"HTTP {e.code} for commodity={commodity_id} state={state_id}: "
                f"{e.read()[:300]}"
            )
        except (error.URLError, ConnectionError, TimeoutError) as e:
            last_error = f"{type(e).__name__}: {e}"
            time.sleep(15 * (attempt + 1))
            continue
    raise SystemExit(
        f"Gave up on commodity={commodity_id} state={state_id} "
        f"after {len(RETRY_WAITS_429)} attempts. Last error: {last_error}"
    )


def safe_name(s):
    return s.replace(" ", "_").replace("/", "-")


def main():
    crop_filter = None
    for arg in sys.argv[1:]:
        if arg.startswith("--crops="):
            crop_filter = set(arg.split("=", 1)[1].split(","))
    target_crops = crop_filter if crop_filter else crops.CROPS.keys()

    api_key = load_api_key()
    OUT_DIR.mkdir(exist_ok=True)

    pairs = [
        (crop, state)
        for crop in target_crops
        for state in crops.STATE_ZONE
        if ceda_map.COMMODITY_ID.get(crop) is not None
    ]
    print(
        f"{len(pairs)} crop-state pairs to fetch (per-crop from-date, default {FROM_DATE_DEFAULT}, to {TO_DATE})"
    )

    skipped_no_mapping = sorted(
        {c for c in target_crops if ceda_map.COMMODITY_ID.get(c) is None}
    )
    if skipped_no_mapping:
        print(f"Skipping (no CEDA mapping): {skipped_no_mapping}")

    done = fetched = empty = 0
    checked_shape = False
    for crop, state in pairs:
        out_path = OUT_DIR / f"{safe_name(crop)}__{safe_name(state)}.json"
        done += 1
        if out_path.exists():
            continue

        commodity_id = ceda_map.COMMODITY_ID[crop]
        state_id = ceda_map.STATE_ID[state]
        from_date = FROM_DATE_OVERRIDE.get(crop, FROM_DATE_DEFAULT)
        records = fetch_quantities(api_key, commodity_id, state_id, from_date, TO_DATE)

        # one-time sanity check on the first non-empty response
        if not checked_shape and records:
            r = records[0]
            assert "date" in r and "quantity" in r, f"unexpected shape: {r}"
            checked_shape = True

        out_path.write_text(
            json.dumps(
                {
                    "crop": crop,
                    "state": state,
                    "commodity_id": commodity_id,
                    "state_id": state_id,
                    "from_date": from_date,
                    "to_date": TO_DATE,
                    "record_count": len(records),
                    "records": records,
                }
            )
        )
        fetched += 1
        if not records:
            empty += 1
        print(f"[{done}/{len(pairs)}] {crop} / {state}: {len(records)} days")
        time.sleep(RATE_LIMIT_SLEEP)

    print(
        f"Done. {fetched} pairs fetched this run ({empty} had zero arrivals), "
        f"{done - fetched} already had files."
    )


if __name__ == "__main__":
    main()
